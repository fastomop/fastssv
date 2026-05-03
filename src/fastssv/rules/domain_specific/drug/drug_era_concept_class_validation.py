"""Drug Era Concept Class Validation Rule.

OMOP semantic rule OMOP_044:
drug_era contains only Ingredient-level concepts (concept_class_id = 'Ingredient').
Filtering for Clinical Drug, Branded Drug, or Clinical Drug Form will always return 0 rows.

The Problem:
    drug_era is a derived table that aggregates drug exposures at the Ingredient level.
    It only contains concepts where concept_class_id = 'Ingredient'.

    Filtering for other RxNorm concept classes will return no data:
    - 'Clinical Drug Form' (e.g., "Acetaminophen 500 MG Oral Tablet")
    - 'Clinical Drug' (e.g., "Acetaminophen 500 MG")
    - 'Branded Drug' (e.g., "Tylenol 500 MG")

Violation pattern:
    SELECT de.*
    FROM drug_era de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Clinical Drug'
    -- Returns 0 rows!

Correct pattern:
    SELECT de.*
    FROM drug_era de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.concept_class_id = 'Ingredient'
    -- Returns all Ingredient-level drug eras
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    is_in_where_or_join_clause,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

DRUG_ERA = "drug_era"
CONCEPT = "concept"

DRUG_CONCEPT_ID = "drug_concept_id"
CONCEPT_ID = "concept_id"
CONCEPT_CLASS_ID = "concept_class_id"

VALID_CLASS = "ingredient"


# --- Helpers ---------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _is_concept(table: Optional[str]) -> bool:
    return _norm(table) == CONCEPT


def _is_concept_class(col: Optional[str]) -> bool:
    return _norm(col) == CONCEPT_CLASS_ID


def _extract_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal):
        return _norm(str(node.this).strip("'").strip('"'))
    return None


# --- Join Detection --------------------------------------------------------


def _find_valid_concept_aliases(tree: exp.Expression, aliases: Dict[str, str]) -> Set[str]:
    """
    Find concept aliases that are correctly joined to drug_era via drug_concept_id.
    """
    valid_aliases: Set[str] = set()

    for eq in tree.find_all(exp.EQ):
        if not is_in_where_or_join_clause(eq):
            continue

        left, right = eq.this, eq.expression

        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        lt, lc = _norm(lt), _norm(lc)
        rt, rc = _norm(rt), _norm(rc)

        # drug_era -> concept
        if lt == DRUG_ERA and rt == CONCEPT:
            if lc == DRUG_CONCEPT_ID and rc == CONCEPT_ID:
                valid_aliases.add(_norm(str(right.table)))

        # concept -> drug_era
        elif rt == DRUG_ERA and lt == CONCEPT:
            if rc == DRUG_CONCEPT_ID and lc == CONCEPT_ID:
                valid_aliases.add(_norm(str(left.table)))

    return valid_aliases


def _is_valid_concept_class_column(
    col: exp.Column,
    aliases: Dict[str, str],
    valid_aliases: Set[str],
) -> bool:
    table, column = resolve_table_col(col, aliases)

    return _is_concept(table) and _is_concept_class(column) and (_norm(str(col.table)) in valid_aliases)


# --- Core Detection --------------------------------------------------------


def _find_violations(
    tree: exp.Expression,
    aliases: Dict[str, str],
    valid_aliases: Set[str],
) -> List[Tuple[str, Optional[exp.Expression]]]:
    """Return list of (message, replaceable_value_node).

    ``replaceable_value_node`` is the literal node that should be
    REPLACEd with ``'Ingredient'`` (only the EQ-with-wrong-value case
    has a deterministic mechanical fix; NEQ and IN are restructure-y).
    """
    issues: List[Tuple[str, Optional[exp.Expression]]] = []
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

            if not _is_valid_concept_class_column(col_node, aliases, valid_aliases):
                continue

            # --- Equality / inequality ---
            if isinstance(node, (exp.EQ, exp.NEQ)):
                value = _extract_literal(val_node)

                if not value:
                    continue

                key = f"{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                if isinstance(node, exp.EQ) and value != VALID_CLASS:
                    issues.append(
                        (
                            f"Invalid filter: concept_class_id = '{value.title()}'. "
                            f"drug_era only contains 'Ingredient' concepts. Query will return 0 rows.",
                            val_node,
                        )
                    )

                elif isinstance(node, exp.NEQ) and value == VALID_CLASS:
                    # NEQ to 'Ingredient' means "exclude all" — the right
                    # fix depends on user intent (drop the predicate or
                    # invert it). Leave as FREEFORM.
                    issues.append(
                        (
                            "Invalid filter: concept_class_id != 'Ingredient'. "
                            "drug_era only contains 'Ingredient' concepts. Query will return 0 rows.",
                            None,
                        )
                    )

            # --- IN clause ---
            elif isinstance(node, exp.In):
                values = {_extract_literal(v) for v in node.expressions or [] if _extract_literal(v)}

                if not values:
                    continue

                key = f"{node.sql()}"
                if key in seen:
                    continue
                seen.add(key)

                # Mixed or invalid
                invalid = {v for v in values if v != VALID_CLASS}

                if invalid:
                    # Display values in title case for readability
                    display_values = [v.title() for v in sorted(values)]
                    # IN-list rewrites are multi-value; drop to FREEFORM.
                    issues.append(
                        (
                            f"Invalid IN filter on concept_class_id: {display_values}. "
                            f"drug_era only contains 'Ingredient' concepts.",
                            None,
                        )
                    )

    return issues


# --- Rule ------------------------------------------------------------------


@register
class DrugEraConceptClassValidationRule(Rule):
    """Robust validation for drug_era concept_class_id usage."""

    rule_id = "domain_specific.drug_era_concept_class_validation"
    name = "Drug Era Concept Class Validation"
    description = "Ensures drug_era is filtered only on Ingredient-level concepts."
    severity = Severity.ERROR
    suggested_fix = "ADD: `AND c.concept_class_id = 'Ingredient'` when joining drug_era to concept. drug_era rolls up to ingredient-level; product-class filters never match."
    example_bad = (
        "SELECT de.person_id FROM drug_era de\n"
        "JOIN concept c ON de.drug_concept_id = c.concept_id\n"
        "WHERE c.concept_class_id = 'Branded Drug';"
    )
    example_good = (
        "SELECT de.person_id FROM drug_era de\n"
        "JOIN concept c ON de.drug_concept_id = c.concept_id\n"
        "WHERE c.concept_class_id = 'Ingredient';"
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

            valid_aliases = _find_valid_concept_aliases(tree, aliases)
            if not valid_aliases:
                continue

            issues = _find_violations(tree, aliases, valid_aliases)

            for msg, val_node in issues:
                # Build a REPLACE patch only for the EQ-with-wrong-literal
                # case where val_node is a string literal we can swap for
                # ``'Ingredient'``. Try both quote styles since the user may
                # have written the value with single or double quotes.
                patch = None
                if isinstance(val_node, exp.Literal) and val_node.is_string:
                    raw = str(val_node.this)
                    for fragment in (f"'{raw}'", f'"{raw}"'):
                        span = locate(sql, fragment)
                        if span is not None:
                            patch = patch_replace(span, "'Ingredient'")
                            break

                violations.append(self.create_violation(message=msg, suggested_fix_patch=patch))

        return violations


__all__ = ["DrugEraConceptClassValidationRule"]
