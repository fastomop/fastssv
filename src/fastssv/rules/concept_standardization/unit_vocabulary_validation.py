"""Unit Vocabulary Validation Rule.

OMOP semantic rule VOCAB_028:
Standard unit concepts use vocabulary_id = 'UCUM'. Filtering unit_concept_id
lookups with other vocabulary IDs returns non-standard units or zero results.

The Problem:
    In OMOP CDM, all standard unit concepts use the UCUM (Unified Code for Units
    of Measure) vocabulary. Unit concept columns (*_unit_concept_id) reference
    standard concepts from the UCUM vocabulary.

    When queries join unit_concept_id to the concept table and filter by
    vocabulary_id != 'UCUM', they may:
    - Return non-standard units
    - Return zero results
    - Miss the intended unit concepts

    Affected columns:
    - measurement.unit_concept_id
    - observation.unit_concept_id
    - drug_strength.amount_unit_concept_id
    - drug_strength.numerator_unit_concept_id
    - drug_strength.denominator_unit_concept_id
    - specimen.unit_concept_id
    - dose_era.unit_concept_id

Violation patterns:
    -- WRONG: Using SNOMED vocabulary for units
    SELECT *
    FROM measurement m
    JOIN concept c ON m.unit_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'SNOMED'
    -- Returns non-standard units or nothing!

    -- WRONG: Using non-UCUM vocabulary in JOIN condition
    SELECT *
    FROM observation o
    JOIN concept c
      ON o.unit_concept_id = c.concept_id
      AND c.vocabulary_id = 'LOINC'
    -- Wrong vocabulary for units!

Correct patterns:
    -- CORRECT: Using UCUM vocabulary for units
    SELECT *
    FROM measurement m
    JOIN concept c ON m.unit_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'UCUM'

    -- CORRECT: Using domain_id filter (also valid)
    SELECT *
    FROM measurement m
    JOIN concept c ON m.unit_concept_id = c.concept_id
    WHERE c.domain_id = 'Unit'

    -- CORRECT: No vocabulary filter (includes all units)
    SELECT *
    FROM measurement m
    JOIN concept c ON m.unit_concept_id = c.concept_id
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

UNIT_CONCEPT_COLUMNS = {
    "unit_concept_id",
    "amount_unit_concept_id",
    "numerator_unit_concept_id",
    "denominator_unit_concept_id",
    "dose_unit_concept_id",
}

VALID_UNIT_VOCABULARY = "UCUM"

INVALID_UNIT_VOCABULARIES = {
    "SNOMED",
    "LOINC",
    "RxNorm",
    "ICD10CM",
    "ICD9CM",
    "CPT4",
    "HCPCS",
}

UNIT_CONCEPT_COLUMNS_NORM = {normalize_name(c) for c in UNIT_CONCEPT_COLUMNS}
VALID_UNIT_VOCABULARY_NORM = normalize_name(VALID_UNIT_VOCABULARY)


# --- Helpers ---------------------------------------------------------------

def _norm(val: Optional[str]) -> Optional[str]:
    return normalize_name(val) if val else None


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if is_string_literal(node):
        # safer extraction
        if hasattr(node, "name"):
            return node.name
        return str(node.this)
    return None


def _get_table_alias(table: exp.Table) -> str:
    alias_expr = table.args.get("alias")
    return alias_expr.name if alias_expr else table.name


def _is_unit_concept_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    _, col_name = resolve_table_col(col, aliases)
    return _norm(col_name) in UNIT_CONCEPT_COLUMNS_NORM


def _is_vocabulary_id_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != "vocabulary_id":
        return False

    if table and _norm(table) != "concept":
        return False

    return True


# --- Core detection --------------------------------------------------------

def _find_unit_concept_joins(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    joins: List[Tuple[str, str, str]] = []

    for join in tree.find_all(exp.Join):
        if not isinstance(join.this, exp.Table):
            continue

        table_name = _norm(join.this.name)
        if table_name != "concept":
            continue

        concept_alias = _get_table_alias(join.this)
        concept_alias_norm = _norm(concept_alias)

        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left, right = eq.this, eq.expression

            for unit_col, concept_col in [(left, right), (right, left)]:
                if not isinstance(unit_col, exp.Column):
                    continue
                if not isinstance(concept_col, exp.Column):
                    continue

                if not _is_unit_concept_column(unit_col, aliases):
                    continue

                concept_table, concept_col_name = resolve_table_col(concept_col, aliases)

                if _norm(concept_col_name) != "concept_id":
                    continue

                # concept_table is the resolved table name (e.g., 'concept')
                # concept_alias could be an alias (e.g., 'c') or the table name itself
                # Check if concept_table matches either the alias or what the alias resolves to
                concept_table_name = aliases.get(concept_alias_norm, concept_alias_norm)
                if concept_table and _norm(concept_table) not in {concept_alias_norm, _norm(concept_table_name)}:
                    continue

                _, unit_col_name = resolve_table_col(unit_col, aliases)

                joins.append((unit_col_name, concept_alias, join.sql()))

    return joins


def _find_vocabulary_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
    concept_alias: str,
) -> List[Tuple[str, str]]:
    filters: List[Tuple[str, str]] = []
    concept_alias_norm = _norm(concept_alias)

    target_nodes = (
        list(tree.find_all(exp.EQ)) +
        list(tree.find_all(exp.NEQ)) +
        list(tree.find_all(exp.In)) +
        list(tree.find_all(exp.Is))
    )

    for node in target_nodes:
        if not is_in_where_or_join_clause(node):
            continue

        # --- EQ / NEQ ---
        if isinstance(node, (exp.EQ, exp.NEQ)):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if not isinstance(col_node, exp.Column):
                    continue

                if not _is_vocabulary_id_column(col_node, aliases):
                    continue

                col_table = _norm(col_node.table)

                if col_table and col_table != concept_alias_norm:
                    continue

                value = _extract_string_literal(val_node)
                if value:
                    filters.append((value, node.sql()))

        # --- IN ---
        elif isinstance(node, exp.In):
            if not isinstance(node.this, exp.Column):
                continue

            if not _is_vocabulary_id_column(node.this, aliases):
                continue

            col_table = _norm(node.this.table)
            if col_table and col_table != concept_alias_norm:
                continue

            for expr in node.expressions or []:
                value = _extract_string_literal(expr)
                if value:
                    filters.append((value, node.sql()))

        # --- IS (NULL cases) ---
        elif isinstance(node, exp.Is):
            if not isinstance(node.this, exp.Column):
                continue

            if not _is_vocabulary_id_column(node.this, aliases):
                continue

            col_table = _norm(node.this.table)
            if col_table and col_table != concept_alias_norm:
                continue

            filters.append(("NULL", node.sql()))

    return filters


# --- Rule ------------------------------------------------------------------

@register
class UnitVocabularyValidationRule(Rule):
    rule_id = "concept_standardization.unit_vocabulary_validation"
    name = "Unit Vocabulary Validation"

    description = (
        "Standard unit concepts use vocabulary_id = 'UCUM'. "
        "Filtering unit_concept_id with other vocabulary IDs returns "
        "non-standard units or zero results."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Use vocabulary_id = 'UCUM' for unit concept lookups, or remove "
        "the vocabulary_id filter entirely."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if "unit_concept_id" not in sql_lower or "vocabulary_id" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            unit_joins = _find_unit_concept_joins(tree, aliases)

            for unit_col, concept_alias, _ in unit_joins:
                vocab_filters = _find_vocabulary_filters(tree, aliases, concept_alias)

                for vocab_value, context in vocab_filters:
                    vocab_norm = _norm(vocab_value)

                    if vocab_norm == VALID_UNIT_VOCABULARY_NORM:
                        continue

                    key = f"{unit_col}|{vocab_value}|{context}"
                    if key in seen:
                        continue
                    seen.add(key)

                    violations.append(
                        self.create_violation(
                            message=(
                                f"Unit column '{unit_col}' uses vocabulary_id = '{vocab_value}'. "
                                f"Expected '{VALID_UNIT_VOCABULARY}'."
                            ),
                            severity=Severity.WARNING,
                            suggested_fix=(
                                f"Replace with vocabulary_id = '{VALID_UNIT_VOCABULARY}'"
                            ),
                            details={
                                "unit_column": unit_col,
                                "incorrect_vocabulary": vocab_value,
                                "expected_vocabulary": VALID_UNIT_VOCABULARY,
                                "context": context,
                            },
                        )
                    )

        return violations


__all__ = ["UnitVocabularyValidationRule"]