"""Drug Exposure Lot Number Is Free Text Rule.

OMOP semantic rule OMOP_108:
drug_exposure.lot_number is a free-text VARCHAR field for the manufacturing lot.
It is not a concept_id and should not be joined to concept or used as an integer.

The Problem:
    The drug_exposure table has a lot_number column that stores free-text
    manufacturing lot identifiers:
    - lot_number: VARCHAR field (e.g., 'LOT2024A', 'BATCH-5678')

    Developers might mistakenly:
    1. Join lot_number to the concept table as if it were a concept_id
    2. Use numeric comparisons or arithmetic with lot_number (treating it like an integer)

    Both patterns are incorrect and will produce unexpected results or errors.

Violation patterns:
    -- WRONG: Joining lot_number to concept
    SELECT * FROM drug_exposure de
    JOIN concept c ON de.lot_number = c.concept_id;

    -- WRONG: Joining to concept_code
    SELECT * FROM drug_exposure de
    JOIN concept c ON de.lot_number = c.concept_code;

    -- WRONG: Numeric comparison
    SELECT * FROM drug_exposure
    WHERE lot_number = 12345;

Correct patterns:
    -- CORRECT: Using as free text
    SELECT * FROM drug_exposure
    WHERE lot_number = 'LOT2024A';

    -- CORRECT: Pattern matching
    SELECT * FROM drug_exposure
    WHERE lot_number LIKE 'BATCH-%';

    -- CORRECT: NULL check
    SELECT * FROM drug_exposure
    WHERE lot_number IS NOT NULL;
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

DRUG_EXPOSURE = "drug_exposure"
CONCEPT = "concept"
LOT_NUMBER = "lot_number"


# --- Normalized Constants --------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


NORM_DRUG_EXPOSURE = _norm(DRUG_EXPOSURE)
NORM_CONCEPT = _norm(CONCEPT)
NORM_LOT_NUMBER = _norm(LOT_NUMBER)


# --- Helpers ---------------------------------------------------------------

OP_MAP = {
    exp.EQ: "=",
    exp.NEQ: "!=",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
}


def _normalize_aliases(aliases: Dict[str, str]) -> Dict[str, str]:
    return {_norm(k): _norm(v) for k, v in aliases.items()}


def _get_table_aliases(
    aliases: Dict[str, str],
    table_name: str,
) -> Set[str]:
    return {k for k, v in aliases.items() if v == table_name}


def _resolve_lot_number_column(
    col: exp.Column,
    aliases: Dict[str, str],
    drug_aliases: Set[str],
) -> Optional[Tuple[str, str]]:
    table, col_name = resolve_table_col(col, aliases)

    if not col_name:
        return None

    col_norm = _norm(col_name)
    if col_norm != NORM_LOT_NUMBER:
        return None

    if table:
        table_norm = _norm(table)
        if table_norm in drug_aliases:
            return table_norm, col_norm
        return None

    if len(drug_aliases) == 1:
        return next(iter(drug_aliases)), col_norm

    return None


def _resolve_concept_column(
    col: exp.Column,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> Optional[Tuple[str, str]]:
    table, col_name = resolve_table_col(col, aliases)

    if not col_name:
        return None

    if table:
        table_norm = _norm(table)
        if table_norm in concept_aliases:
            return table_norm, _norm(col_name)
        return None

    if len(concept_aliases) == 1:
        return next(iter(concept_aliases)), _norm(col_name)

    return None


def _is_numeric_literal(node: exp.Expression) -> bool:
    if not isinstance(node, exp.Literal):
        return False
    try:
        float(node.this)
        return True
    except (ValueError, TypeError):
        return False


def _detect_lot_number_concept_comparisons(
    select: exp.Select,
    aliases: Dict[str, str],
    drug_aliases: Set[str],
    concept_aliases: Set[str],
) -> Set[str]:
    violations: Set[str] = set()

    if not drug_aliases or not concept_aliases:
        return violations

    for node in select.walk():
        if not isinstance(node, exp.EQ):
            continue

        if not is_in_where_or_join_clause(node):
            continue

        left = node.this
        right = node.expression

        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        left_lot = _resolve_lot_number_column(left, aliases, drug_aliases)
        right_lot = _resolve_lot_number_column(right, aliases, drug_aliases)

        left_con = _resolve_concept_column(left, aliases, concept_aliases)
        right_con = _resolve_concept_column(right, aliases, concept_aliases)

        if left_lot and right_con:
            violations.add(f"{left_lot[0]}.{left_lot[1]} = {right_con[0]}.{right_con[1]}")
        elif right_lot and left_con:
            violations.add(f"{right_lot[0]}.{right_lot[1]} = {left_con[0]}.{left_con[1]}")

    return violations


def _detect_lot_number_numeric_comparisons(
    select: exp.Select,
    aliases: Dict[str, str],
    drug_aliases: Set[str],
) -> Set[str]:
    violations: Set[str] = set()

    if not drug_aliases:
        return violations

    for node in select.walk():
        if type(node) not in OP_MAP:
            continue

        if not is_in_where_or_join_clause(node):
            continue

        op_symbol = OP_MAP[type(node)]

        left = node.this
        right = node.expression

        lot_side = None
        numeric_node = None

        if isinstance(left, exp.Column):
            left_lot = _resolve_lot_number_column(left, aliases, drug_aliases)
            if left_lot and _is_numeric_literal(right):
                lot_side = left_lot
                numeric_node = right

        if isinstance(right, exp.Column) and not lot_side:
            right_lot = _resolve_lot_number_column(right, aliases, drug_aliases)
            if right_lot and _is_numeric_literal(left):
                lot_side = right_lot
                numeric_node = left

        if lot_side and numeric_node:
            violations.add(f"{lot_side[0]}.{lot_side[1]} {op_symbol} {numeric_node.this}")

    return violations


# --- Rule ------------------------------------------------------------------

@register
class DrugExposureLotNumberIsFreeTextRule(Rule):
    """Detects incorrect usage of drug_exposure.lot_number."""

    rule_id = "data_quality.drug_exposure_lot_number_is_free_text"
    name = "Drug Exposure Lot Number Is Free Text"

    description = (
        "Ensures that drug_exposure.lot_number (free-text VARCHAR field) "
        "is not joined to the concept table or used in numeric comparisons."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "Remove joins between drug_exposure.lot_number and concept table. "
        "Avoid numeric comparisons with lot_number. Treat lot_number as a free-text field."
    )
    long_description = (
        "drug_exposure.lot_number is a free-text VARCHAR field that stores "
        "the manufacturer's lot identifier for the dispensed drug. It can "
        "contain letters, digits, hyphens, and other characters; it has "
        "no mapping into the concept table and no reliable numeric "
        "semantics. Joining it to concept or comparing it numerically "
        "returns zero or meaningless rows."
    )
    example_bad = (
        "SELECT de.person_id\n"
        "FROM drug_exposure de\n"
        "JOIN concept c ON de.lot_number = c.concept_name;"
    )
    example_good = (
        "SELECT de.person_id\n"
        "FROM drug_exposure de\n"
        "JOIN concept c ON de.drug_concept_id = c.concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if DRUG_EXPOSURE not in sql_lower:
            return []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        for tree in trees:
            if tree is None:
                continue

            if not has_table_reference(tree, DRUG_EXPOSURE):
                continue

            aliases = _normalize_aliases(extract_aliases(tree))

            drug_aliases = _get_table_aliases(aliases, NORM_DRUG_EXPOSURE)
            concept_aliases = _get_table_aliases(aliases, NORM_CONCEPT)

            seen_patterns: Set[str] = set()

            for select in tree.find_all(exp.Select):
                concept_violations = _detect_lot_number_concept_comparisons(
                    select, aliases, drug_aliases, concept_aliases
                )

                numeric_violations = _detect_lot_number_numeric_comparisons(
                    select, aliases, drug_aliases
                )

                all_detected = concept_violations | numeric_violations

                for pattern in all_detected:
                    if pattern in seen_patterns:
                        continue

                    seen_patterns.add(pattern)

                    message = (
                        f"Invalid usage detected: {pattern}. "
                        f"drug_exposure.lot_number is a free-text field and must not be "
                        f"joined to concept table or used in numeric comparisons."
                    )

                    violations.append(
                        self.create_violation(
                            message=message,
                            suggested_fix=self.suggested_fix,
                            details={
                                "pattern": pattern,
                                "recommendation": (
                                    "Use lot_number only for text filtering/display. "
                                    "Do not join to concept or compare numerically."
                                ),
                            },
                        )
                    )

        return violations


__all__ = ["DrugExposureLotNumberIsFreeTextRule"]
