"""Standard Concept Value Validation Rule.

OMOP semantic rule OMOP_037:
The standard_concept column only accepts 'S' (Standard), 'C' (Classification), or NULL
(non-standard). Filtering with other values like 'Y', 'N', 1, 0 is incorrect.

Valid values:
    - 'S': Standard concept (the preferred representation)
    - 'C': Classification concept (hierarchical grouping)
    - NULL: Non-standard concept (deprecated, source-specific, etc.)

Incorrect pattern:
    WHERE standard_concept = 'Y'  -- treating it like boolean
    WHERE standard_concept = 1    -- using integer

Correct pattern:
    WHERE standard_concept = 'S'
    WHERE standard_concept IN ('S', 'C')
    WHERE standard_concept IS NULL
"""

from typing import Dict, List, Set, Tuple, Optional

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    is_in_where_or_join_clause,
    has_table_reference,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


STANDARD_CONCEPT_COLUMN = "standard_concept"
CONCEPT_TABLE = "concept"
VALID_VALUES = {"S", "C"}


# --- Helpers ---------------------------------------------------------------


def _normalize_literal(node: exp.Expression) -> Optional[str]:
    """Extract and normalize literal value."""
    if isinstance(node, exp.Literal):
        val = str(node.this).strip("'").strip('"')
        return val.upper()
    return None


def _is_standard_concept_column(
    column: exp.Column,
    aliases: Dict[str, str],
    concept_present: bool,
) -> bool:
    """Ensure column is concept.standard_concept (alias-safe)."""
    col = normalize_name(column.name)
    if col != STANDARD_CONCEPT_COLUMN:
        return False

    if column.table:
        alias = str(column.table)
        table = aliases.get(alias, alias)
        return normalize_name(table) == CONCEPT_TABLE

    # Only allow unqualified if concept table is present
    return concept_present


def _collect_in_values(node: exp.In) -> Tuple[Set[str], Set[str]]:
    """Return (values, types) from IN clause."""
    values = set()
    types = set()

    for expr in node.expressions or []:
        if isinstance(expr, exp.Literal):
            val = _normalize_literal(expr)
            if val:
                values.add(val)
                types.add(type(expr.this).__name__)

    return values, types


# --- Core Detection --------------------------------------------------------


def _find_invalid_values(
    tree: exp.Expression,
    aliases: Dict[str, str],
    concept_present: bool,
) -> List[dict]:
    issues: List[dict] = []
    seen: Set[str] = set()

    for node in tree.walk():
        if not isinstance(node, (exp.EQ, exp.NEQ, exp.In)):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        right = getattr(node, "expression", None)

        pairs = [(left, right), (right, left)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_standard_concept_column(col_node, aliases, concept_present):
                continue

            # --- IN ---
            if isinstance(node, exp.In):
                values, types = _collect_in_values(node)

                # mixed types (e.g., 'S', 1)
                if len(types) > 1:
                    key = f"MIXED:{values}"
                    if key not in seen:
                        seen.add(key)
                        issues.append(
                            {
                                "message": (
                                    f"Mixed literal types in standard_concept IN clause: {values}. "
                                    f"Use only 'S', 'C', or NULL."
                                ),
                                "kind": "IN_MIXED",
                            }
                        )

                invalid = [v for v in values if v not in VALID_VALUES]
                if invalid:
                    key = f"IN:{','.join(sorted(invalid))}"
                    if key not in seen:
                        seen.add(key)
                        issues.append(
                            {
                                "message": (
                                    f"Invalid standard_concept values: {invalid}. Valid values are 'S', 'C', or NULL."
                                ),
                                "kind": "IN_INVALID",
                            }
                        )

            # --- Equality / inequality ---
            else:
                value = _normalize_literal(val_node)
                if value and value not in VALID_VALUES:
                    key = f"VAL:{value}"
                    if key not in seen:
                        seen.add(key)
                        issues.append(
                            {
                                "message": (
                                    f"Invalid standard_concept value: '{value}'. Valid values are 'S', 'C', or NULL."
                                ),
                                "kind": "EQ" if isinstance(node, exp.EQ) else "NEQ",
                                "literal_sql": val_node.sql() if val_node is not None else None,
                            }
                        )

    return issues


# --- Rule ------------------------------------------------------------------


@register
class StandardConceptValueValidationRule(Rule):
    """Robust validation for standard_concept values."""

    rule_id = "concept_standardization.standard_concept_value_validation"
    name = "Standard Concept Value Validation"
    description = "Ensures standard_concept uses only valid values: 'S', 'C', or NULL."
    severity = Severity.ERROR
    suggested_fix = "REPLACE: `standard_concept = '<other>'` WITH one of: `= 'S'` (standard), `= 'C'` (classification), `IS NULL` (non-standard). Those are the only valid values in OMOP."
    long_description = (
        "The concept.standard_concept column has exactly three valid "
        "values: 'S' (standard), 'C' (classification), and NULL (neither, "
        "typically source vocabularies). Any other literal, like 'X', 'Y' "
        "or an empty string, matches no rows and is almost always a typo "
        "from a developer who remembered there was some letter value. The "
        "fix is usually 'S'; reach for 'C' only when you specifically "
        "want classification-level concepts such as MedDRA grouping."
    )
    example_bad = "SELECT concept_id\nFROM concept\nWHERE standard_concept = 'X';"
    example_good = "SELECT concept_id\nFROM concept\nWHERE standard_concept = 'S';"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            concept_present = has_table_reference(tree, CONCEPT_TABLE)

            issues = _find_invalid_values(tree, aliases, concept_present)

            for issue in issues:
                patch = None
                # Only the EQ-against-invalid-literal case has an unambiguous
                # mechanical fix: swap the bad literal for 'S' (the most-
                # common standard value). NEQ, IN, and mixed-type cases
                # require human judgement and stay FREEFORM.
                if issue["kind"] == "EQ" and issue.get("literal_sql"):
                    span = locate(sql, issue["literal_sql"])
                    if span is not None:
                        patch = patch_replace(span, "'S'")

                violations.append(
                    self.create_violation(
                        message=issue["message"],
                        suggested_fix_patch=patch,
                    )
                )

        return violations


__all__ = ["StandardConceptValueValidationRule"]
