"""Source Concept ID with Standard Filter Rule.

OMOP semantic rule VOCAB_020:
When resolving *_source_concept_id columns (e.g., condition_source_concept_id),
the join to concept should NOT filter standard_concept = 'S'. Source concepts are
intentionally non-standard - they represent the original source vocabulary codes.

The Problem:
    In OMOP CDM, clinical domain tables have two types of concept ID columns:

    1. Standard concept IDs (e.g., condition_concept_id):
       - Reference standard concepts for analytics
       - Should have standard_concept = 'S'

    2. Source concept IDs (e.g., condition_source_concept_id):
       - Reference the original source vocabulary codes (ICD-10, CPT, etc.)
       - Are intentionally NON-standard (standard_concept IS NULL or 'C')
       - Preserve the original coding system used in the source data

    Filtering standard_concept = 'S' when joining on *_source_concept_id is
    semantically wrong and will typically return zero results.

    Common mistakes:
    1. Joining source_concept_id to concept with standard_concept = 'S' filter
    2. Misunderstanding the dual concept ID design
    3. Applying standard concept filters to source concept joins

Violation patterns:
    -- WRONG: standard_concept = 'S' on source concept join
    SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c
      ON co.condition_source_concept_id = c.concept_id
    WHERE c.standard_concept = 'S'
    -- Returns nothing! Source concepts are non-standard by design

    -- WRONG: Filter in JOIN ON clause
    SELECT c.concept_name
    FROM drug_exposure de
    JOIN concept c
      ON de.drug_source_concept_id = c.concept_id
      AND c.standard_concept = 'S'

    -- WRONG: Same issue with procedure
    SELECT c.concept_name
    FROM procedure_occurrence po
    JOIN concept c
      ON po.procedure_source_concept_id = c.concept_id
    WHERE c.standard_concept = 'S'

Correct patterns:
    -- CORRECT: No standard_concept filter on source concept join
    SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c
      ON co.condition_source_concept_id = c.concept_id

    -- CORRECT: Join standard concept ID if you want standard concepts
    SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c
      ON co.condition_concept_id = c.concept_id
    WHERE c.standard_concept = 'S'

    -- CORRECT: Explicitly query non-standard source concepts
    SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c
      ON co.condition_source_concept_id = c.concept_id
    WHERE c.standard_concept IS NULL

    -- CORRECT: Explore source vocabulary
    SELECT c.vocabulary_id, c.concept_code
    FROM condition_occurrence co
    JOIN concept c
      ON co.condition_source_concept_id = c.concept_id
"""

from typing import Dict, List, Optional, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CONCEPT_TABLE = "concept"
CONCEPT_ID = "concept_id"
STANDARD_CONCEPT = "standard_concept"

SOURCE_CONCEPT_ID_COLUMNS: Set[str] = {
    "condition_source_concept_id",
    "drug_source_concept_id",
    "procedure_source_concept_id",
    "measurement_source_concept_id",
    "observation_source_concept_id",
    "device_source_concept_id",
    "visit_source_concept_id",
    "specimen_source_concept_id",
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _get_concept_aliases(aliases: Dict[str, str]) -> Set[str]:
    return {
        alias for alias, table in aliases.items()
        if _norm(table) == CONCEPT_TABLE
    }


def _is_source_concept_id_column(
    col: exp.Column,
    aliases: Dict[str, str],
) -> bool:
    _, col_name = resolve_table_col(col, aliases)
    return _norm(col_name) in SOURCE_CONCEPT_ID_COLUMNS


def _is_concept_id_column(
    col: exp.Column,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != CONCEPT_ID:
        return False

    if table:
        return _norm(table) == CONCEPT_TABLE

    return len(concept_aliases) == 1


def _is_standard_concept_column(
    col: exp.Column,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != STANDARD_CONCEPT:
        return False

    if table:
        return _norm(table) == CONCEPT_TABLE

    return len(concept_aliases) == 1


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and isinstance(node.this, str):
        return _norm(node.this)
    return None


# --- Filter Detection ------------------------------------------------------

def _has_standard_filter(
    node: exp.Expression,
    aliases: Dict[str, str],
    concept_alias: str,
    concept_aliases: Set[str],
) -> bool:
    """Detect standard_concept = 'S' or IN ('S') filters for a given concept alias."""

    # EQ
    for eq in node.find_all(exp.EQ):
        pairs = [(eq.this, eq.expression), (eq.expression, eq.this)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_standard_concept_column(col_node, aliases, concept_aliases):
                continue

            col_table = _norm(col_node.table) if col_node.table else None

            if col_table:
                if col_table != concept_alias:
                    continue
            else:
                if len(concept_aliases) != 1:
                    continue

            val = _extract_string_literal(val_node)
            if val == "s":
                return True

    # IN
    for in_node in node.find_all(exp.In):
        if not isinstance(in_node.this, exp.Column):
            continue

        if not _is_standard_concept_column(in_node.this, aliases, concept_aliases):
            continue

        col_table = _norm(in_node.this.table) if in_node.this.table else None

        if col_table:
            if col_table != concept_alias:
                continue
        else:
            if len(concept_aliases) != 1:
                continue

        exprs = in_node.args.get("expressions") or []
        values = {_extract_string_literal(e) for e in exprs}
        values.discard(None)

        if "s" in values:
            return True

    return False


def _has_having_filter(
    select: exp.Select,
    aliases: Dict[str, str],
    concept_alias: str,
    concept_aliases: Set[str],
) -> bool:
    having = select.args.get("having")
    if not having:
        return False

    return _has_standard_filter(having, aliases, concept_alias, concept_aliases)


# --- Detection -------------------------------------------------------------

def _detect_source_concept_joins(tree: exp.Expression) -> List[Dict[str, object]]:
    violations: List[Dict[str, object]] = []
    seen: Set[str] = set()

    for select in tree.find_all(exp.Select):
        aliases = extract_aliases(select)
        concept_aliases = _get_concept_aliases(aliases)

        if not concept_aliases:
            continue

        for join in select.find_all(exp.Join):
            table = join.this

            if not isinstance(table, exp.Table):
                continue

            table_name = _norm(table.name)
            if table_name != CONCEPT_TABLE:
                continue

            table_alias = _norm(str(table.alias)) if table.alias else table_name

            if table_alias not in concept_aliases:
                continue

            on_clause = join.args.get("on")
            if not on_clause:
                continue

            found_source_join = False
            source_col_name = None

            for eq in on_clause.find_all(exp.EQ):
                pairs = [(eq.this, eq.expression), (eq.expression, eq.this)]

                for left, right in pairs:
                    if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                        continue

                    if _is_source_concept_id_column(left, aliases):
                        if _is_concept_id_column(right, aliases, concept_aliases):
                            right_alias = _norm(right.table) if right.table else None

                            if right_alias == table_alias or (
                                not right_alias and len(concept_aliases) == 1
                            ):
                                found_source_join = True
                                _, source_col_name = resolve_table_col(left, aliases)
                                break

                    elif _is_source_concept_id_column(right, aliases):
                        if _is_concept_id_column(left, aliases, concept_aliases):
                            left_alias = _norm(left.table) if left.table else None

                            if left_alias == table_alias or (
                                not left_alias and len(concept_aliases) == 1
                            ):
                                found_source_join = True
                                _, source_col_name = resolve_table_col(right, aliases)
                                break

                if found_source_join:
                    break

            if not found_source_join:
                continue

            has_filter = False

            if _has_standard_filter(on_clause, aliases, table_alias, concept_aliases):
                has_filter = True

            where = select.args.get("where")
            if where and _has_standard_filter(where, aliases, table_alias, concept_aliases):
                has_filter = True

            if _has_having_filter(select, aliases, table_alias, concept_aliases):
                has_filter = True

            if not has_filter:
                continue

            key = f"{table_alias}_{source_col_name}_{id(select)}"
            if key in seen:
                continue
            seen.add(key)

            violations.append({
                "type": "source_concept_standard_filter",
                "alias": table_alias,
                "source_column": source_col_name,
                "context": join.sql(),
            })

    return violations


# --- Rule ------------------------------------------------------------------

@register
class SourceConceptIdStandardFilterRule(Rule):
    """Detect JOINs on *_source_concept_id with standard_concept = 'S' filter."""

    rule_id = "concept_standardization.source_concept_id_standard_filter"
    name = "Source Concept ID Should Not Filter Standard Concepts"

    description = (
        "Joining *_source_concept_id to concept with standard_concept = 'S' "
        "is semantically incorrect. Source concepts are non-standard."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Remove standard_concept = 'S' filter when joining source concept IDs, "
        "or use the standard *_concept_id column instead."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "source_concept_id" not in sql.lower():
            return []

        if CONCEPT_TABLE not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree or not has_table_reference(tree, CONCEPT_TABLE):
                continue

            detected = _detect_source_concept_joins(tree)

            for v in detected:
                violations.append(
                    self.create_violation(
                        message=(
                            f"JOIN on {v['source_column']} to concept (alias: {v['alias']}) "
                            f"with standard_concept = 'S'. Source concepts are non-standard "
                            f"and typically do not satisfy this filter."
                        ),
                        severity=self.severity,
                        suggested_fix=self.suggested_fix,
                        details=v,
                    )
                )

        return violations


__all__ = ["SourceConceptIdStandardFilterRule"]