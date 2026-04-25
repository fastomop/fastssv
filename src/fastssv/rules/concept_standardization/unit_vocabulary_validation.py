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
from fastssv.core.patch import locate, replace as patch_replace
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

def _is_exploratory_vocabulary_analysis(
    tree: exp.Expression,
    concept_alias: str,
) -> bool:
    """Check if query is exploring vocabulary distribution vs filtering by it.

    Exploratory patterns:
    - SELECT c.vocabulary_id (displaying vocabularies)
    - GROUP BY c.vocabulary_id (analyzing distribution)
    - COUNT/aggregation with vocabulary_id

    Returns True if exploratory, False if production filtering.
    """
    concept_alias_norm = _norm(concept_alias)

    for select in tree.find_all(exp.Select):
        # Check if vocabulary_id is in SELECT list
        for expr in select.expressions or []:
            for col in expr.find_all(exp.Column):
                col_name = _norm(col.name)
                col_table = _norm(col.table) if col.table else None

                if col_name == "vocabulary_id":
                    if not col_table or col_table == concept_alias_norm:
                        return True

        # Check if vocabulary_id is in GROUP BY
        group_by = select.args.get("group")
        if group_by and isinstance(group_by, exp.Group):
            for group_expr in group_by.expressions:
                if isinstance(group_expr, exp.Column):
                    col_name = _norm(group_expr.name)
                    col_table = _norm(group_expr.table) if group_expr.table else None

                    if col_name == "vocabulary_id":
                        if not col_table or col_table == concept_alias_norm:
                            return True

    return False


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

    suggested_fix = "REPLACE: `c.vocabulary_id = '<other>'` WITH `c.vocabulary_id = 'UCUM'` for unit_concept_id joins. Standard OMOP unit concepts come from UCUM."
    long_description = (
        "OMOP unit concepts live in the UCUM vocabulary (Unified Code for "
        "Units of Measure). Filtering unit_concept_id joins with "
        "vocabulary_id = 'LOINC' (or any other non-UCUM vocabulary) "
        "returns zero rows because units are not stored there. Use UCUM "
        "for unit lookups, or drop the vocabulary filter if the join "
        "already constrains to unit_concept_id."
    )
    example_bad = (
        "SELECT m.person_id\n"
        "FROM measurement m\n"
        "JOIN concept c ON m.unit_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'LOINC';"
    )
    example_good = (
        "SELECT m.person_id\n"
        "FROM measurement m\n"
        "JOIN concept c ON m.unit_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'UCUM';"
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

                # Check if this is exploratory analysis
                is_exploratory = _is_exploratory_vocabulary_analysis(tree, concept_alias)

                for vocab_value, context in vocab_filters:
                    vocab_norm = _norm(vocab_value)

                    if vocab_norm == VALID_UNIT_VOCABULARY_NORM:
                        continue

                    key = f"{unit_col}|{vocab_value}|{context}"
                    if key in seen:
                        continue
                    seen.add(key)

                    if is_exploratory:
                        # Exploratory analysis - WARNING severity
                        message = (
                            f"Unit column '{unit_col}' uses vocabulary_id = '{vocab_value}', "
                            f"but the query appears to be exploring vocabulary distribution. "
                            f"Standard unit concepts use '{VALID_UNIT_VOCABULARY}'. "
                            f"This is valid for exploratory analysis but may indicate incorrect vocabulary usage "
                            f"if used in production queries."
                        )
                        severity = Severity.WARNING
                        suggested_fix = (
                            f"For production queries, use vocabulary_id = '{VALID_UNIT_VOCABULARY}'"
                        )
                    else:
                        # Production filtering - ERROR severity
                        message = (
                            f"Unit column '{unit_col}' filtered by vocabulary_id = '{vocab_value}'. "
                            f"Standard unit concepts use '{VALID_UNIT_VOCABULARY}', not '{vocab_value}'. "
                            f"This filter will return zero or incorrect results because unit concept IDs "
                            f"don't belong to {vocab_value} vocabulary."
                        )
                        severity = Severity.ERROR
                        suggested_fix = (
                            f"Replace with vocabulary_id = '{VALID_UNIT_VOCABULARY}' or remove the filter"
                        )

                    # Build a mechanical REPLACE patch swapping the bad
                    # vocab literal with 'UCUM' inside the offending
                    # predicate. Only attempt this for production
                    # filtering (not exploratory analyses) and only when
                    # the bad value appears inside a uniquely-locatable
                    # predicate. NULL filters (`vocabulary_id IS NULL`)
                    # are not handled by this REPLACE.
                    patch = None
                    if not is_exploratory and vocab_norm and vocab_norm != "null":
                        span = locate(sql, context)
                        if span is not None and f"'{vocab_value}'" in context:
                            new_text = context.replace(
                                f"'{vocab_value}'",
                                f"'{VALID_UNIT_VOCABULARY}'",
                            )
                            patch = patch_replace(span, new_text)

                    violations.append(
                        self.create_violation(
                            message=message,
                            severity=severity,
                            suggested_fix=suggested_fix,
                            suggested_fix_patch=patch,
                            details={
                                "unit_column": unit_col,
                                "incorrect_vocabulary": vocab_value,
                                "expected_vocabulary": VALID_UNIT_VOCABULARY,
                                "context": context,
                                "is_exploratory": is_exploratory,
                            },
                        )
                    )

        return violations


__all__ = ["UnitVocabularyValidationRule"]
