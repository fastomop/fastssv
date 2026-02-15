"""CLI entry point for validating OMOP CDM SQL queries."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

from fastssv import validate_sql_structured
from fastssv.core.base import RuleViolation, Severity


def _read_sql(sql_file: str | None) -> str:
    if sql_file:
        return Path(sql_file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide a SQL file path or pipe SQL via stdin.")


def _split_queries(sql: str) -> List[str]:
    """Split SQL content into individual queries by semicolon.

    Handles semicolons inside strings and comments.
    """
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

        # Handle line comments
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

        # Handle block comments
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

        # Handle string literals
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote

        # Handle semicolon (query separator)
        if char == ';' and not in_single_quote and not in_double_quote:
            current.append(char)
            query = ''.join(current).strip()
            if query and query != ';':
                queries.append(query)
            current = []
            i += 1
            continue

        current.append(char)
        i += 1

    # Handle last query (might not end with semicolon)
    remaining = ''.join(current).strip()
    if remaining:
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

    # Strip comments, then collapse whitespace and newlines
    no_comments = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    no_comments = re.sub(r"--[^\n]*", "", no_comments)
    clean_query = " ".join(no_comments.strip().split())

    result: Dict[str, Any] = {}

    if query_index is not None:
        result["query_index"] = query_index

    result.update({
        "query": clean_query,
        "dialect": dialect,
        "is_valid": is_valid,
        "error_count": len(errors),
        "warning_count": len(warnings),
    })

    if violations:
        result["violations"] = [v.to_dict() for v in violations]

    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate OMOP CDM SQL queries and output JSON for LLM refinement."
    )
    parser.add_argument(
        "sql_file",
        nargs="?",
        help="Path to SQL file. If not provided, reads from stdin."
    )
    parser.add_argument(
        "--dialect",
        default="postgres",
        help="SQL dialect for parsing (default: postgres).",
    )
    parser.add_argument(
        "--combined",
        action="store_true",
        help="Output combined results for all queries (legacy behavior).",
    )
    parser.add_argument(
        "--rules",
        nargs="*",
        help="Specific rule IDs to run (e.g., semantic.standard_concept_enforcement).",
    )
    parser.add_argument(
        "--categories",
        nargs="*",
        choices=["semantic", "vocabulary"],
        help="Rule categories to run (default: all).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="output/validation_report.json",
        help="Output JSON report file path (default: output/validation_report.json).",
    )
    args = parser.parse_args(argv)

    sql = _read_sql(args.sql_file)

    # Split into individual queries
    queries = _split_queries(sql)

    # If only one query or --combined flag, use original behavior
    if len(queries) <= 1 or args.combined:
        violations = validate_sql_structured(
            sql,
            dialect=args.dialect,
            rule_ids=args.rules,
            categories=args.categories,
        )
        validation_result = build_validation_result(sql, violations, args.dialect)

        # Write JSON report to file
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(validation_result, indent=2), encoding="utf-8")

        # Print summary to CLI
        status = "VALID" if validation_result["is_valid"] else "INVALID"
        print(f"Validation {status}")
        print(f"  Errors: {validation_result['error_count']}")
        print(f"  Warnings: {validation_result['warning_count']}")
        print(f"  Report saved to: {output_path.absolute()}")

        return 0 if validation_result["is_valid"] else 1

    # Multiple queries: validate each separately
    all_results = []
    any_invalid = False

    for idx, query in enumerate(queries, start=1):
        violations = validate_sql_structured(
            query,
            dialect=args.dialect,
            rule_ids=args.rules,
            categories=args.categories,
        )
        validation_result = build_validation_result(
            query, violations, args.dialect, query_index=idx
        )
        all_results.append(validation_result)
        if not validation_result["is_valid"]:
            any_invalid = True

    # Output summary
    output = {
        "total_queries": len(queries),
        "valid_queries": sum(1 for r in all_results if r["is_valid"]),
        "invalid_queries": sum(1 for r in all_results if not r["is_valid"]),
        "results": all_results,
    }

    # Write JSON report to file
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    # Print summary to CLI
    status = "VALID" if not any_invalid else "INVALID"
    print(f"Validation {status}")
    print(f"  Total queries: {output['total_queries']}")
    print(f"  Valid: {output['valid_queries']}")
    print(f"  Invalid: {output['invalid_queries']}")
    print(f"  Report saved to: {output_path.absolute()}")

    return 1 if any_invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
