"""CLI entry point for validating OMOP CDM SQL queries."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

from fastssv import validate_sql_structured
from fastssv.core.base import RuleViolation, Severity
from fastssv.core.helpers import (
    collect_locally_defined_tables,
    collect_locally_defined_unqualified_tables,
)
from fastssv.core.helpers import detect_dialect as _auto_detect_dialect
from fastssv.core.helpers import split_sql_statements
from fastssv.core.logging import (
    setup_logging,
    log_validation_start,
    log_validation_complete,
)


def _read_sql(sql_file: str | None) -> str:
    if sql_file:
        return Path(sql_file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide a SQL file path or pipe SQL via stdin.")


def _clean_llm_output(sql: str) -> str:
    """Clean SQL from LLM output by removing markdown and explanatory text."""
    fence_pattern = r"```(?:sql|postgresql)?\s*\n(.*?)\n```"
    fence_match = re.search(fence_pattern, sql, re.DOTALL)

    if fence_match:
        sql = fence_match.group(1)
    else:
        sql = re.sub(r"```(?:sql|postgresql)?\s*", "", sql)
        sql = re.sub(r"```", "", sql)

    last_semicolon = sql.rfind(";")
    if last_semicolon != -1:
        sql = sql[: last_semicolon + 1]

    sql = re.sub(r"`+\s*$", "", sql)
    return sql.strip()


_split_queries = split_sql_statements
"""Backward-compat alias: callers in tests import ``_split_queries`` directly."""


def build_validation_result(
    sql: str,
    violations: List[RuleViolation],
    dialect: str,
) -> Dict[str, Any]:
    """Build JSON validation result from structured violations."""
    errors = [v for v in violations if v.severity == Severity.ERROR]
    warnings = [v for v in violations if v.severity == Severity.WARNING]

    is_valid = len(errors) == 0

    # Clean the SQL query for display
    no_comments = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    no_comments = re.sub(r"--[^\n]*", "", no_comments)
    clean_query = " ".join(no_comments.strip().split())

    result: Dict[str, Any] = {
        "query": clean_query,
        "is_valid": is_valid,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }

    if errors:
        result["errors"] = [v.to_dict() for v in errors]

    if warnings:
        result["warnings"] = [v.to_dict() for v in warnings]

    return result


def _serve_command(argv: Sequence[str]) -> int:
    """Run the HTTP API (FastAPI) with one command."""
    parser = argparse.ArgumentParser(
        prog="fastssv serve",
        description="Run the FastSSV HTTP API + web UI.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000).")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Dev mode: auto-reload on code changes (uvicorn, single process).",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Production mode: gunicorn with uvicorn workers.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Number of worker processes in --prod mode (default: 2).",
    )
    args = parser.parse_args(argv)

    try:
        import uvicorn  # type: ignore
    except ImportError:
        print(
            "fastssv serve requires the api extras. Install with: pip install 'fastssv[api]'",
            file=sys.stderr,
        )
        return 1

    if args.prod:
        import shutil
        import subprocess

        gunicorn = shutil.which("gunicorn")
        if not gunicorn:
            print(
                "gunicorn not found. Reinstall with: pip install 'fastssv[api]'",
                file=sys.stderr,
            )
            return 1
        return subprocess.call(
            [
                gunicorn,
                "-k",
                "uvicorn.workers.UvicornWorker",
                "-w",
                str(args.workers),
                "-b",
                f"{args.host}:{args.port}",
                "--timeout",
                "30",
                "--graceful-timeout",
                "30",
                "--access-logfile",
                "-",
                "--error-logfile",
                "-",
                "fastssv.api.app:app",
            ]
        )

    uvicorn.run(
        "fastssv.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if args_list and args_list[0] == "serve":
        return _serve_command(args_list[1:])

    parser = argparse.ArgumentParser(
        description="Validate OMOP CDM SQL queries and output JSON for LLM refinement.",
        epilog="Run 'fastssv serve --help' to launch the HTTP API + web UI.",
    )
    parser.add_argument(
        "sql_file",
        nargs="?",
        help="Path to SQL file. If not provided, reads from stdin.",
    )
    parser.add_argument(
        "--dialect",
        default="auto",
        choices=[
            "auto",
            "postgres",
            "tsql",
            "oracle",
            "redshift",
            "bigquery",
            "snowflake",
            "databricks",
            "duckdb",
        ],
        help=(
            "SQL dialect for sqlglot parsing (default: auto-detect, which "
            "picks postgres or tsql; pick explicitly for the others)."
        ),
    )
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Output combined results for all queries (legacy behavior).",
    )
    parser.add_argument(
        "--rules",
        nargs="*",
        help="Specific rule IDs to run (e.g., concept_standardization.standard_concept_enforcement).",
    )
    parser.add_argument(
        "--categories",
        nargs="*",
        choices=[
            "anti_patterns",
            "concept_standardization",
            "data_quality",
            "domain_specific",
            "joins",
            "temporal",
        ],
        help="Rule categories to run (default: all).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode: escalates best-practice warnings to errors.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help=(
            "Output JSON report file path (default: output/<input-file-name>_report.json, "
            "or output/validation_report.json when reading from stdin)."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set logging level (default: INFO, or FASTSSV_LOG_LEVEL env var).",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Log to file (optional, or use FASTSSV_LOG_FILE env var).",
    )
    parser.add_argument(
        "--log-format",
        default=None,
        choices=["simple", "detailed", "json"],
        help="Log format (default: detailed, or FASTSSV_LOG_FORMAT env var).",
    )
    args = parser.parse_args(args_list)

    # Derive the default report path from the input file name so runs on
    # different files don't overwrite each other's reports.
    if args.output is None:
        if args.sql_file:
            args.output = str(Path("output") / f"{Path(args.sql_file).stem}_report.json")
        else:
            args.output = "output/validation_report.json"

    # Setup logging
    logger = setup_logging(
        level=args.log_level,
        log_file=args.log_file,
        log_format=args.log_format,
    )
    logger.debug(f"FastSSV CLI started with args: {vars(args)}")

    # Read SQL input. OSError covers the missing/unreadable-file family;
    # surface it as a one-line message + exit code 2 (usage/input error)
    # rather than a raw traceback.
    try:
        sql = _clean_llm_output(_read_sql(args.sql_file))
    except (OSError, UnicodeDecodeError) as e:
        logger.error(f"Failed to read SQL input: {e}")
        print(f"error: cannot read SQL from {args.sql_file or 'stdin'}: {e}", file=sys.stderr)
        return 2
    logger.info(f"Read SQL input: {len(sql)} characters from {args.sql_file or 'stdin'}")

    # Split into queries
    queries = _split_queries(sql)
    logger.info(f"Split into {len(queries)} query/queries")

    # Auto-detect dialect if set to 'auto'
    dialect = args.dialect
    if dialect == "auto":
        dialect = _auto_detect_dialect(sql)
        logger.info(f"Auto-detected dialect: {dialect}")
        print(f"Auto-detected dialect: {dialect}")

    # Set validation context for strict mode + cross-statement scope.
    # Seed ``local_tables`` from the *whole* input — when the CLI iterates
    # per-statement validation below, the schema rule still needs to know
    # which scratch tables were created earlier in the batch (Achilles
    # writes ``CREATE TABLE scratch.tempResults_N AS …`` once, then queries
    # ``tempResults_N`` from many later statements).
    from fastssv.core.validation_context import ValidationContext, set_validation_context

    local_tables = collect_locally_defined_tables(sql, dialect)
    local_unqualified = collect_locally_defined_unqualified_tables(sql, dialect)
    if local_tables:
        logger.info(f"Detected {len(local_tables)} locally-defined tables across the batch")

    context = ValidationContext(
        strict_mode=args.strict,
        dialect=dialect,
        local_tables=local_tables,
        local_unqualified_tables=local_unqualified,
    )
    set_validation_context(context)
    if args.strict:
        logger.info("Strict mode enabled")
        print("Strict mode enabled: best-practice warnings escalated to errors")
    else:
        # Default mode: best practices are warnings, only correctness issues are errors
        logger.info("Default validation mode (best practices = WARNING, correctness = ERROR)")

    if len(queries) <= 1 or args.combined:
        # Single query or combined mode
        log_validation_start(logger, len(sql), dialect)
        start_time = time.perf_counter()

        violations = validate_sql_structured(
            sql,
            dialect=dialect,
            rule_ids=args.rules,
            categories=args.categories,
        )

        duration_ms = (time.perf_counter() - start_time) * 1000
        validation_result = build_validation_result(sql, violations, dialect)

        log_validation_complete(
            logger,
            total_rules=len(violations),
            error_count=validation_result["error_count"],
            warning_count=validation_result["warning_count"],
            duration_ms=duration_ms,
        )

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(validation_result, indent=2), encoding="utf-8")
        logger.info(f"Validation report saved to: {output_path.absolute()}")

        status = "VALID" if validation_result["is_valid"] else "INVALID"
        print(f"Validation {status}")
        print(f"  Errors: {validation_result['error_count']}")
        print(f"  Warnings: {validation_result['warning_count']}")
        print(f"  Report saved to: {output_path.absolute()}")
        return 0 if validation_result["is_valid"] else 1

    # Multiple queries mode
    logger.info(f"Processing {len(queries)} queries individually")
    all_results = []
    any_invalid = False
    start_time = time.perf_counter()

    for idx, query in enumerate(queries, start=1):
        logger.debug(f"Validating query {idx}/{len(queries)}")
        log_validation_start(logger, len(query), dialect)

        query_start = time.perf_counter()
        violations = validate_sql_structured(
            query,
            dialect=dialect,
            rule_ids=args.rules,
            categories=args.categories,
        )
        query_duration = (time.perf_counter() - query_start) * 1000

        validation_result = build_validation_result(query, violations, dialect)
        all_results.append(validation_result)

        logger.info(
            f"Query {idx}: {'VALID' if validation_result['is_valid'] else 'INVALID'} "
            f"({validation_result['error_count']} errors, "
            f"{validation_result['warning_count']} warnings, "
            f"{query_duration:.2f}ms)"
        )

        if not validation_result["is_valid"]:
            any_invalid = True

    total_duration = (time.perf_counter() - start_time) * 1000

    output = {
        "total_queries": len(queries),
        "valid_queries": sum(1 for r in all_results if r["is_valid"]),
        "invalid_queries": sum(1 for r in all_results if not r["is_valid"]),
        "results": all_results,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    logger.info(
        f"Batch validation complete: {output['total_queries']} queries, "
        f"{output['valid_queries']} valid, {output['invalid_queries']} invalid, "
        f"{total_duration:.2f}ms total"
    )

    status = "VALID" if not any_invalid else "INVALID"
    print(f"Validation {status}")
    print(f"  Total queries: {output['total_queries']}")
    print(f"  Valid: {output['valid_queries']}")
    print(f"  Invalid: {output['invalid_queries']}")
    print(f"  Report saved to: {output_path.absolute()}")

    return 1 if any_invalid else 0
