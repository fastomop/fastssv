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
from fastssv.core.logging import (
    setup_logging,
    get_logger,
    log_validation_start,
    log_validation_complete,
)


def _read_sql(sql_file: str | None) -> str:
    if sql_file:
        return Path(sql_file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide a SQL file path or pipe SQL via stdin.")


def _auto_detect_dialect(sql: str) -> str:
    """Auto-detect SQL dialect from syntax patterns.

    Returns 'tsql' for SQL Server syntax, 'postgres' otherwise.
    """
    sql_lower = sql.lower()

    # SQL Server indicators
    tsql_indicators = [
        r'@\w+\.',  # @vocab., @cdm. (table variables)
        r'\bgetdate\s*\(',  # getdate()
        r'\bgetutcdate\s*\(',  # getutcdate()
        r'\bdatediff\s*\(',  # datediff()
        r'\bdateadd\s*\(',  # dateadd()
        r'\bisnull\s*\(',  # isnull()
        r'\blen\s*\(',  # len()
        r'\bcharindex\s*\(',  # charindex()
        r'\btop\s+\d+\s+',  # TOP N syntax
    ]

    for pattern in tsql_indicators:
        if re.search(pattern, sql_lower):
            return 'tsql'

    return 'postgres'


def _clean_llm_output(sql: str) -> str:
    """Clean SQL from LLM output by removing markdown and explanatory text."""
    fence_pattern = r'```(?:sql|postgresql)?\s*\n(.*?)\n```'
    fence_match = re.search(fence_pattern, sql, re.DOTALL)

    if fence_match:
        sql = fence_match.group(1)
    else:
        sql = re.sub(r'```(?:sql|postgresql)?\s*', '', sql)
        sql = re.sub(r'```', '', sql)

    last_semicolon = sql.rfind(';')
    if last_semicolon != -1:
        sql = sql[:last_semicolon + 1]

    sql = re.sub(r'`+\s*$', '', sql)
    return sql.strip()


def _split_queries(sql: str) -> List[str]:
    """Split SQL content into individual queries by semicolon."""

    def has_sql_content(text: str) -> bool:
        no_block_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        no_comments = re.sub(r"--[^\n]*", "", no_block_comments)
        return bool(no_comments.strip())

    queries = []
    current = []
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False
    i = 0

    while i < len(sql):
        char = sql[i]
        next_char = sql[i + 1] if i + 1 < len(sql) else ''

        if not in_single_quote and not in_double_quote and not in_block_comment:
            if char == '-' and next_char == '-':
                in_line_comment = True
                current.append(char)
                i += 1
                continue

        if in_line_comment:
            current.append(char)
            if char == '\n':
                in_line_comment = False
            i += 1
            continue

        if not in_single_quote and not in_double_quote and not in_line_comment:
            if char == '/' and next_char == '*':
                in_block_comment = True
                current.append(char)
                i += 1
                continue

        if in_block_comment:
            current.append(char)
            if char == '*' and next_char == '/':
                current.append(next_char)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote

        if char == ';' and not in_single_quote and not in_double_quote:
            current.append(char)
            query = ''.join(current).strip()
            if query and query != ';' and has_sql_content(query):
                queries.append(query)
            current = []
            i += 1
            continue

        current.append(char)
        i += 1

    remaining = ''.join(current).strip()
    if remaining and has_sql_content(remaining):
        queries.append(remaining)

    return queries


def build_validation_result(
    sql: str,
    violations: List[RuleViolation],
    dialect: str,
    query_index: int | None = None,
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate OMOP CDM SQL queries and output JSON for LLM refinement."
    )
    parser.add_argument(
        "sql_file",
        nargs="?",
        help="Path to SQL file. If not provided, reads from stdin.",
    )
    parser.add_argument(
        "--dialect",
        default="auto",
        choices=["auto", "postgres", "tsql"],
        help="SQL dialect for parsing (default: auto-detect).",
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
            "analytics",
            "anti_patterns",
            "concept_standardization",
            "data_quality",
            "domain_specific",
            "joins",
            "performance",
            "schema",
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
        default="output/validation_report.json",
        help="Output JSON report file path (default: output/validation_report.json).",
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
    args = parser.parse_args(argv)

    # Setup logging
    logger = setup_logging(
        level=args.log_level,
        log_file=args.log_file,
        log_format=args.log_format,
    )
    logger.debug(f"FastSSV CLI started with args: {vars(args)}")

    # Read SQL input
    try:
        sql = _clean_llm_output(_read_sql(args.sql_file))
        logger.info(f"Read SQL input: {len(sql)} characters from {args.sql_file or 'stdin'}")
    except Exception as e:
        logger.error(f"Failed to read SQL input: {e}")
        raise

    # Split into queries
    queries = _split_queries(sql)
    logger.info(f"Split into {len(queries)} query/queries")

    # Auto-detect dialect if set to 'auto'
    dialect = args.dialect
    if dialect == "auto":
        dialect = _auto_detect_dialect(sql)
        logger.info(f"Auto-detected dialect: {dialect}")
        print(f"Auto-detected dialect: {dialect}")

    # Set validation context for strict mode
    from fastssv.core.validation_context import ValidationContext, set_validation_context

    if args.strict:
        set_validation_context(ValidationContext(strict_mode=True, dialect=dialect))
        logger.info("Strict mode enabled")
        print("Strict mode enabled: best-practice warnings escalated to errors")
    else:
        # Default mode: best practices are warnings, only correctness issues are errors
        set_validation_context(ValidationContext(strict_mode=False, dialect=dialect))
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

        validation_result = build_validation_result(
            query, violations, dialect, query_index=idx
        )
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
