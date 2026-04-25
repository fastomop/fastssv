"""Relationship Boolean Comparison Rule.

OMOP semantic rule OMOP_150:
The relationship table has is_hierarchical and defines_ancestry columns.
These are boolean flags that should be compared with proper boolean values
(1, 0, TRUE, FALSE), not strings or other invalid types.

The Problem:
    The relationship vocabulary table uses boolean flags to indicate:
    - is_hierarchical: Whether the relationship represents a hierarchy
    - defines_ancestry: Whether the relationship defines ancestry paths

    When filtering these columns, developers sometimes use incorrect value types:
    - String literals: 'true', 'false', '1', '0'
    - Invalid integers: 2, -1, or any value other than 0 or 1

    This causes issues:
    1. Type mismatch errors in strongly-typed databases
    2. Incorrect comparisons (boolean vs string comparison semantics differ)
    3. Performance problems (prevents index usage)
    4. Silent failures or unexpected results

Why this is wrong:
    Boolean columns should be compared with boolean-compatible values:
    - In most SQL dialects, booleans are represented as 1 (TRUE) or 0 (FALSE)
    - Comparing with strings requires implicit conversion that may fail
    - Using invalid integers (2, -1, etc.) is semantically meaningless
    - String comparisons have different semantics than boolean comparisons

Violation patterns:
    SELECT * FROM relationship WHERE is_hierarchical = 'true'
    -- ERROR: String comparison on boolean column

    SELECT * FROM relationship WHERE defines_ancestry = '1'
    -- ERROR: String '1' is not the same as integer 1

    SELECT * FROM relationship WHERE is_hierarchical = 2
    -- ERROR: Invalid boolean value (only 0 or 1 allowed)

    SELECT * FROM relationship WHERE defines_ancestry IN ('true', 'false')
    -- ERROR: String values in IN clause

    SELECT r.* FROM concept_relationship cr
    JOIN relationship r ON cr.relationship_id = r.relationship_id
    WHERE r.is_hierarchical = 'Y'
    -- ERROR: 'Y'/'N' pattern is wrong for boolean

Correct patterns:
    SELECT * FROM relationship WHERE is_hierarchical = 1
    -- OK: Integer boolean comparison

    SELECT * FROM relationship WHERE is_hierarchical = TRUE
    -- OK: Boolean literal

    SELECT * FROM relationship WHERE is_hierarchical
    -- OK: Direct boolean usage

    SELECT * FROM relationship WHERE is_hierarchical = 0
    -- OK: Integer boolean comparison (FALSE)

    SELECT * FROM relationship WHERE is_hierarchical IS NOT NULL
    -- OK: NULL check is valid

    SELECT * FROM relationship WHERE is_hierarchical IN (0, 1)
    -- OK: Integer boolean values

Note:
    This is an ERROR, not a warning. Using incorrect types for boolean comparisons
    can cause query failures or incorrect results. Parameterized queries are allowed
    since we cannot validate the parameter type statically.
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import extract_aliases, normalize_name, parse_sql
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


# Map common truthy/falsy string spellings to the canonical 0/1 OMOP
# representation. Used when the analyst wrote `is_hierarchical = 'yes'` and
# we want to emit a deterministic REPLACE rather than freeform.
_TRUE_STRINGS = {"y", "yes", "true", "t", "1"}
_FALSE_STRINGS = {"n", "no", "false", "f", "0"}


def _string_to_boolean_int(value: str) -> Optional[int]:
    if not isinstance(value, str):
        return None
    norm = value.strip().lower()
    if norm in _TRUE_STRINGS:
        return 1
    if norm in _FALSE_STRINGS:
        return 0
    return None


logger = logging.getLogger(__name__)


# --- Constants ---------------------------------------------------------------

RELATIONSHIP_TABLE = "relationship"
BOOLEAN_COLUMNS = {"is_hierarchical", "defines_ancestry"}


# --- Helpers -----------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _extract_cte_names(tree: exp.Expression) -> Set[str]:
    return {
        _norm(cte.alias_or_name)
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _is_relationship_table(name: Optional[str]) -> bool:
    return name == RELATIONSHIP_TABLE


def _is_boolean_column(name: Optional[str]) -> bool:
    return name in BOOLEAN_COLUMNS


def _is_valid_boolean_value(node: exp.Expression) -> bool:
    if isinstance(node, (exp.Boolean, exp.Null)):
        return True

    if isinstance(node, (exp.Column, exp.Placeholder, exp.Parameter)):
        return True

    if isinstance(node, exp.Literal):
        if node.is_string:
            return False

        try:
            value = int(node.this)
            return value in (0, 1)
        except (ValueError, TypeError):
            return False

    if isinstance(node, (exp.Cast, exp.TryCast)):
        return True

    return False


def _resolve_table_name(
    table_name: Optional[str],
    aliases: Dict[str, str],
) -> Optional[str]:
    if not table_name:
        return None

    table_name = _norm(table_name)
    resolved = aliases.get(table_name)

    if resolved:
        return _norm(resolved)

    return table_name


def _collect_tables(tree: exp.Expression, cte_names: Set[str]) -> Set[str]:
    """Collect all table names in the query (excluding CTEs)."""
    tables = set()
    for tbl in tree.find_all(exp.Table):
        name = _norm(tbl.name)
        if name and name not in cte_names:
            tables.add(name)
    return tables


def _check_comparison(
    comparison: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    tables_in_query: Set[str],
) -> Optional[Tuple[str, Optional[exp.Expression]]]:
    """Return (message, offending_value_side) when the comparison is invalid.

    ``offending_value_side`` is the AST node holding the bad literal, used
    by the caller to build a REPLACE patch when the value is a string with
    a known yes/no spelling.
    """
    left = comparison.this
    right = comparison.expression

    if not left or not right:
        return None

    # Handle BOTH directions (critical fix)
    for col_side, value_side in [(left, right), (right, left)]:
        if not isinstance(col_side, exp.Column):
            continue

        col_name = _norm(col_side.name)
        table_name = _resolve_table_name(col_side.table, aliases)

        # Handle unqualified columns
        if not table_name:
            # Only flag if relationship is the sole table
            if tables_in_query == {RELATIONSHIP_TABLE} and _is_boolean_column(col_name):
                table_name = RELATIONSHIP_TABLE
            else:
                continue

        if table_name in cte_names:
            continue

        if not (_is_boolean_column(col_name) and _is_relationship_table(table_name)):
            continue

        if not _is_valid_boolean_value(value_side):
            if isinstance(value_side, exp.Literal):
                if value_side.is_string:
                    return (
                        f"Column '{col_name}' is boolean but compared with string "
                        f"'{value_side.this}'. Use 0/1 or TRUE/FALSE.",
                        value_side,
                    )
                else:
                    return (
                        f"Column '{col_name}' is boolean but compared with invalid "
                        f"value '{value_side.this}'. Only 0, 1, TRUE, FALSE allowed.",
                        value_side,
                    )
            else:
                return (
                    f"Column '{col_name}' is boolean but compared with "
                    f"invalid expression. Use 0/1 or TRUE/FALSE.",
                    None,
                )

    return None


def _check_in_clause(
    in_expr: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
    tables_in_query: Set[str],
) -> Optional[str]:
    left = in_expr.this

    if not isinstance(left, exp.Column):
        return None

    col_name = _norm(left.name)
    table_name = _resolve_table_name(left.table, aliases)

    # Handle unqualified columns
    if not table_name:
        # Only flag if relationship is the sole table
        if tables_in_query == {RELATIONSHIP_TABLE} and _is_boolean_column(col_name):
            table_name = RELATIONSHIP_TABLE
        else:
            return None

    if table_name in cte_names:
        return None

    if not (_is_boolean_column(col_name) and _is_relationship_table(table_name)):
        return None

    invalid_values = []

    for value in in_expr.expressions:
        if not _is_valid_boolean_value(value):
            invalid_values.append(value.sql())

    if invalid_values:
        return (
            f"Column '{col_name}' is boolean but IN/NOT IN contains invalid values: "
            f"{', '.join(invalid_values)}. Use 0/1 or TRUE/FALSE."
        )

    return None


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
    cte_names: Set[str],
) -> List[Tuple[str, Optional[exp.Expression]]]:
    """Return list of (message, offending_value_node).

    ``offending_value_node`` is the literal whose source span we'll target
    with a REPLACE patch (or ``None`` when the bad side isn't a literal).
    """
    issues: List[Tuple[str, Optional[exp.Expression]]] = []

    # Collect tables for unqualified column handling
    tables_in_query = _collect_tables(tree, cte_names)

    # Comparisons
    for comp in tree.find_all(exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE):
        result = _check_comparison(comp, aliases, cte_names, tables_in_query)
        if result:
            issues.append(result)

    # IN clauses (NOT IN is exp.Not wrapping exp.In). We don't try to patch
    # IN lists — multi-value rewrites are restructure-y; FREEFORM is honest.
    for in_expr in tree.find_all(exp.In):
        msg = _check_in_clause(in_expr, aliases, cte_names, tables_in_query)
        if msg:
            issues.append((msg, None))

    # Deduplicate by message
    seen = set()
    out: List[Tuple[str, Optional[exp.Expression]]] = []
    for msg, val in issues:
        if msg in seen:
            continue
        seen.add(msg)
        out.append((msg, val))
    return out


# --- Rule --------------------------------------------------------------------

@register
class RelationshipBooleanComparisonRule(Rule):
    """
    OMOP_150: Validate boolean column comparisons in relationship table.
    """

    rule_id = "domain_specific.vocabulary.relationship_boolean_comparison"
    name = "Relationship Boolean Comparison"

    description = (
        "relationship.is_hierarchical and defines_ancestry must be compared "
        "with valid boolean values (0,1,TRUE,FALSE), not strings."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: `is_hierarchical = 'yes'` (or 'true', 'Y', 'T') WITH `is_hierarchical = 1` (or = 0). Same for defines_ancestry. OMOP stores these as 0/1 strings, not English booleans."
    example_bad = (
        "SELECT relationship_id FROM relationship\n"
        "WHERE is_hierarchical = 'yes';"
    )
    example_good = (
        "SELECT relationship_id FROM relationship\n"
        "WHERE is_hierarchical = 1;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()

        if RELATIONSHIP_TABLE not in sql_lower:
            return []

        if not any(col in sql_lower for col in BOOLEAN_COLUMNS):
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            logger.warning(
                "SQL parsing failed for OMOP_150",
                extra={"sql": sql[:500], "dialect": dialect},
            )
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            cte_names = _extract_cte_names(tree)

            issues = _find_violations(tree, aliases, cte_names)

            for msg, value_node in issues:
                # Build a REPLACE patch only when the offending side is a
                # string literal whose spelling maps unambiguously to 0/1
                # ('yes'→1, 'no'→0, etc.). Anything else (numeric outliers
                # like 2/-1, expressions, IN-clauses) drops to FREEFORM.
                patch = None
                if isinstance(value_node, exp.Literal) and value_node.is_string:
                    mapped = _string_to_boolean_int(str(value_node.this))
                    if mapped is not None:
                        # Locate the entire quoted string literal in the SQL.
                        # We try both quote styles since the user may have
                        # written 'yes' or "yes".
                        raw = str(value_node.this)
                        for fragment in (f"'{raw}'", f'"{raw}"'):
                            span = locate(sql, fragment)
                            if span is not None:
                                patch = patch_replace(span, str(mapped))
                                break

                violations.append(
                    self.create_violation(
                        message=msg,
                        severity=self.severity,
                        suggested_fix_patch=patch,
                    )
                )

        return violations


__all__ = ["RelationshipBooleanComparisonRule"]
