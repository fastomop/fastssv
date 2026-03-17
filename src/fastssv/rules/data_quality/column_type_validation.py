"""Column Type Validation Rule.

OMOP semantic rules OMOP_004, OMOP_005, OMOP_024, OMOP_025, OMOP_026:
Validates that column data types are compatible in JOIN conditions and WHERE clauses.

Catches common type mismatch errors such as:
- OMOP_004: Joining person_id (integer) to person_source_value (varchar)
- OMOP_005: Joining visit_occurrence_id (integer) to varchar columns
- OMOP_024: Joining subject_id (integer) to person_source_value (varchar)
- OMOP_025: Filtering vocabulary_id (varchar) with integer literals
- OMOP_026: Filtering domain_id (varchar) with integer literals

These errors will pass database schema validation but fail or produce unexpected
results at runtime due to type mismatch.
"""

from typing import Dict, List, Set, Tuple, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register
from fastssv.schemas import (
    get_column_type,
    are_types_compatible,
    INTEGER,
    VARCHAR,
    DATE,
    TIMESTAMP,
    FLOAT,
)


# --- Helpers ---------------------------------------------------------------

def _get_literal_type(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal):
        if node.is_int:
            return INTEGER
        if node.is_string:
            return VARCHAR
    return None


def _is_casted(node: exp.Expression) -> bool:
    """Check if node is wrapped in a CAST."""
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Cast):
            return True
        parent = parent.parent
    return False


def _is_in_join(node: exp.Expression) -> bool:
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Join):
            return True
        if isinstance(parent, exp.Where):
            return False
        parent = parent.parent
    return False


# --- JOIN VALIDATION -------------------------------------------------------

def _find_join_type_mismatches(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str, str, str]]:
    issues = []
    seen: Set[Tuple[str, str, str, str]] = set()

    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        if not _is_in_join(eq):
            continue

        left, right = eq.this, eq.expression

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        if _is_casted(left) or _is_casted(right):
            continue  # valid explicit cast

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt, lc = normalize_name(lt), normalize_name(lc)
        rt, rc = normalize_name(rt), normalize_name(rc)

        key = (lt, lc, rt, rc)
        if key in seen:
            continue
        seen.add(key)

        ltype = get_column_type(lt, lc)
        rtype = get_column_type(rt, rc)

        if not ltype or not rtype:
            continue

        if not are_types_compatible(ltype, rtype):
            issues.append((lt, lc, ltype, rt, rc, rtype))

    return issues


# --- WHERE VALIDATION ------------------------------------------------------

def _collect_in_types(node: exp.In) -> Set[str]:
    types: Set[str] = set()

    for val in node.expressions or []:
        lit_type = _get_literal_type(val)
        if lit_type:
            types.add(lit_type)

    return types


def _find_literal_type_mismatches(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str, str]]:
    issues = []
    seen: Set[Tuple[str, str, str, str]] = set()

    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.NEQ, exp.In)):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        if _is_in_join(node):
            continue  # skip JOINs

        left = node.this
        right = getattr(node, "expression", None)

        pairs = [(left, right), (right, left)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            if _is_casted(col_node):
                continue

            table, col = resolve_table_col(col_node, aliases)
            if not (table and col):
                continue  # avoid guessing

            table, col = normalize_name(table), normalize_name(col)
            col_type = get_column_type(table, col)

            if not col_type:
                continue

            literal_types: Set[str] = set()

            # --- Handle IN ---
            if isinstance(node, exp.In):
                literal_types = _collect_in_types(node)

                # mixed types in IN
                if len(literal_types) > 1:
                    issues.append((table, col, col_type, "mixed"))
                    continue

            else:
                lit_type = _get_literal_type(val_node)
                if lit_type:
                    literal_types.add(lit_type)

            for lit_type in literal_types:
                if not are_types_compatible(col_type, lit_type):
                    key = (table, col, col_type, lit_type)
                    if key in seen:
                        continue
                    seen.add(key)

                    issues.append((table, col, col_type, lit_type))

    return issues


# --- RULE ------------------------------------------------------------------

@register
class ColumnTypeValidationRule(Rule):
    """Robust validation for column type compatibility."""

    rule_id = "data_quality.column_type_validation"
    name = "Column Type Validation"
    description = (
        "Ensures compatible data types in JOIN conditions and WHERE filters. "
        "Detects mismatches such as integer-to-varchar joins or invalid literal comparisons."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "Ensure column types are compatible. Use proper literals or CAST explicitly if needed."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)

            # --- JOIN mismatches ---
            for lt, lc, ltype, rt, rc, rtype in _find_join_type_mismatches(tree, aliases):
                violations.append(self.create_violation(
                    message=(
                        f"Type mismatch in JOIN: {lt}.{lc} ({ltype}) "
                        f"vs {rt}.{rc} ({rtype})"
                    ),
                    suggested_fix=(
                        f"Use compatible columns or apply explicit CAST."
                    ),
                    details={
                        "left_table": lt,
                        "left_column": lc,
                        "left_type": ltype,
                        "right_table": rt,
                        "right_column": rc,
                        "right_type": rtype,
                    }
                ))

            # --- WHERE mismatches ---
            for table, col, col_type, lit_type in _find_literal_type_mismatches(tree, aliases):
                if lit_type == "mixed":
                    message = (
                        f"{table}.{col} compared to mixed literal types in IN clause."
                    )
                else:
                    message = (
                        f"Type mismatch: {table}.{col} ({col_type}) vs {lit_type} literal."
                    )

                violations.append(self.create_violation(
                    message=message,
                    suggested_fix=(
                        f"Use correct literal type or CAST explicitly."
                    ),
                    details={
                        "table": table,
                        "column": col,
                        "column_type": col_type,
                        "literal_type": lit_type,
                    }
                ))

        return violations


__all__ = ["ColumnTypeValidationRule"]