"""Domain Vocabulary Validation Rule.

OMOP semantic rules VOCAB_022, VOCAB_023, VOCAB_024, VOCAB_025:
Standard *_concept_id columns in clinical tables reference standard concepts from
specific vocabularies. Filtering by source vocabulary_id values returns zero results.

The Problem:
    Each OMOP clinical domain has specific standard vocabularies:

    - Conditions: SNOMED (not ICD10CM, ICD9CM, ICD10, ICD9)
    - Drugs: RxNorm, RxNorm Extension (not NDC, ATC, GCN_SEQNO, SPL)
    - Procedures: SNOMED, CPT4, HCPCS (not ICD10PCS, ICD9Proc, OPCS4)
    - Measurements: LOINC, SNOMED (not CPT4 when standard_concept = 'S')

    Standard *_concept_id columns (condition_concept_id, drug_concept_id, etc.)
    ALWAYS reference standard concepts. If you filter by source vocabulary_id
    values, you'll get zero results because standard concept IDs don't belong
    to those vocabularies.

    Common mistakes:
    1. Joining condition_concept_id to concept and filtering vocabulary_id = 'ICD10CM'
    2. Joining drug_concept_id to concept and filtering vocabulary_id = 'NDC'
    3. Joining procedure_concept_id to concept and filtering vocabulary_id = 'ICD10PCS'
    4. Joining measurement_concept_id to concept and filtering vocabulary_id = 'CPT4'

Violation patterns:
    -- WRONG: ICD10CM filter on standard condition_concept_id (VOCAB_022)
    SELECT *
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'ICD10CM'
    -- Returns nothing! condition_concept_id uses SNOMED, not ICD10CM

    -- WRONG: NDC filter on standard drug_concept_id (VOCAB_023)
    SELECT *
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'NDC'
    -- Returns nothing! drug_concept_id uses RxNorm, not NDC

    -- WRONG: ICD10PCS filter on standard procedure_concept_id (VOCAB_024)
    SELECT *
    FROM procedure_occurrence po
    JOIN concept c ON po.procedure_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'ICD10PCS'
    -- Returns nothing! procedure_concept_id uses SNOMED/CPT4/HCPCS

    -- WRONG: CPT4 filter on standard measurement_concept_id (VOCAB_025)
    SELECT *
    FROM measurement m
    JOIN concept c ON m.measurement_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'CPT4' AND c.standard_concept = 'S'
    -- CPT4 is not a standard measurement vocabulary

Correct patterns:
    -- CORRECT: Use SNOMED for conditions
    SELECT *
    FROM condition_occurrence co
    JOIN concept c ON co.condition_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'SNOMED'

    -- CORRECT: Use RxNorm for drugs
    SELECT *
    FROM drug_exposure de
    JOIN concept c ON de.drug_concept_id = c.concept_id
    WHERE c.vocabulary_id IN ('RxNorm', 'RxNorm Extension')

    -- CORRECT: Use SNOMED/CPT4/HCPCS for procedures
    SELECT *
    FROM procedure_occurrence po
    JOIN concept c ON po.procedure_concept_id = c.concept_id
    WHERE c.vocabulary_id IN ('SNOMED', 'CPT4', 'HCPCS')

    -- CORRECT: Use LOINC/SNOMED for measurements
    SELECT *
    FROM measurement m
    JOIN concept c ON m.measurement_concept_id = c.concept_id
    WHERE c.vocabulary_id IN ('LOINC', 'SNOMED')

    -- CORRECT: Use source vocabulary with *_source_concept_id
    SELECT *
    FROM condition_occurrence co
    JOIN concept c ON co.condition_source_concept_id = c.concept_id
    WHERE c.vocabulary_id = 'ICD10CM'
    -- This is correct! source_concept_id can have any vocabulary
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
VOCABULARY_ID = "vocabulary_id"
STANDARD_CONCEPT = "standard_concept"

DOMAIN_VOCABULARIES = {
    "condition_concept_id": {
        "domain_name": "Condition",
        "standard_vocabularies": {"snomed"},
        "invalid_vocabularies": {"icd10cm", "icd9cm", "icd10", "icd9"},
        "rule_id": "VOCAB_022",
    },
    "drug_concept_id": {
        "domain_name": "Drug",
        "standard_vocabularies": {"rxnorm", "rxnorm extension"},
        "invalid_vocabularies": {"ndc", "atc", "gcn_seqno", "spl"},
        "rule_id": "VOCAB_023",
    },
    "procedure_concept_id": {
        "domain_name": "Procedure",
        "standard_vocabularies": {"snomed", "cpt4", "hcpcs"},
        "invalid_vocabularies": {"icd10pcs", "icd9proc", "opcs4"},
        "rule_id": "VOCAB_024",
    },
    "measurement_concept_id": {
        "domain_name": "Measurement",
        "standard_vocabularies": {"loinc", "snomed"},
        "invalid_vocabularies": {"cpt4"},
        "rule_id": "VOCAB_025",
        "require_standard_filter": True,
    },
}


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _get_concept_aliases(aliases: Dict[str, str]) -> Set[str]:
    return {
        alias for alias, table in aliases.items()
        if _norm(table) == CONCEPT_TABLE
    }


def _is_column(
    col: exp.Column,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
    target_col: str,
) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != target_col:
        return False

    if table:
        return _norm(table) == CONCEPT_TABLE

    return len(concept_aliases) == 1


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and isinstance(node.this, str):
        return _norm(node.this)
    return None


# --- Filter Detection ------------------------------------------------------

def _extract_vocab_filters(
    node: exp.Expression,
    aliases: Dict[str, str],
    concept_alias: str,
    concept_aliases: Set[str],
) -> Set[str]:
    """Extract all vocabulary_id values used in filters."""
    values: Set[str] = set()

    # EQ / NEQ
    for comp in node.find_all((exp.EQ, exp.NEQ)):
        pairs = [(comp.this, comp.expression), (comp.expression, comp.this)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_column(col_node, aliases, concept_aliases, VOCABULARY_ID):
                continue

            col_table = _norm(col_node.table) if col_node.table else None

            if col_table:
                if col_table != concept_alias:
                    continue
            else:
                if len(concept_aliases) != 1:
                    continue

            val = _extract_string_literal(val_node)
            if val:
                values.add(val)

    # IN / NOT IN
    for in_node in node.find_all(exp.In):
        if not isinstance(in_node.this, exp.Column):
            continue

        if not _is_column(in_node.this, aliases, concept_aliases, VOCABULARY_ID):
            continue

        col_table = _norm(in_node.this.table) if in_node.this.table else None

        if col_table:
            if col_table != concept_alias:
                continue
        else:
            if len(concept_aliases) != 1:
                continue

        exprs = in_node.args.get("expressions") or []
        for e in exprs:
            val = _extract_string_literal(e)
            if val:
                values.add(val)

    return values


def _has_standard_filter(
    node: exp.Expression,
    aliases: Dict[str, str],
    concept_alias: str,
    concept_aliases: Set[str],
) -> bool:
    """Check for standard_concept = 'S'."""
    for eq in node.find_all(exp.EQ):
        pairs = [(eq.this, eq.expression), (eq.expression, eq.this)]

        for col_node, val_node in pairs:
            if not isinstance(col_node, exp.Column):
                continue

            if not _is_column(col_node, aliases, concept_aliases, STANDARD_CONCEPT):
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

    return False


# --- Detection -------------------------------------------------------------

def _is_exploratory_vocabulary_analysis(
    select: exp.Select,
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

    # Check if vocabulary_id is in SELECT list
    for expr in select.expressions or []:
        for col in expr.find_all(exp.Column):
            col_name = _norm(col.name)
            col_table = _norm(col.table) if col.table else None

            if col_name == VOCABULARY_ID:
                if not col_table or col_table == concept_alias_norm:
                    return True

    # Check if vocabulary_id is in GROUP BY
    group_by = select.args.get("group")
    if group_by and isinstance(group_by, exp.Group):
        for group_expr in group_by.expressions:
            if isinstance(group_expr, exp.Column):
                col_name = _norm(group_expr.name)
                col_table = _norm(group_expr.table) if group_expr.table else None

                if col_name == VOCABULARY_ID:
                    if not col_table or col_table == concept_alias_norm:
                        return True

    return False


def _detect(tree: exp.Expression) -> List[Dict[str, object]]:
    violations = []
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

            # Detect domain column
            found_domain = None
            domain_col = None

            for eq in on_clause.find_all(exp.EQ):
                pairs = [(eq.this, eq.expression), (eq.expression, eq.this)]

                for left, right in pairs:
                    if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                        continue

                    if _is_column(right, aliases, concept_aliases, CONCEPT_ID):
                        right_alias = _norm(right.table) if right.table else None

                        if right_alias == table_alias or (
                            not right_alias and len(concept_aliases) == 1
                        ):
                            _, col_name = resolve_table_col(left, aliases)
                            col_norm = _norm(col_name)

                            if col_norm in DOMAIN_VOCABULARIES:
                                found_domain = col_norm
                                domain_col = col_name
                                break

                    elif _is_column(left, aliases, concept_aliases, CONCEPT_ID):
                        left_alias = _norm(left.table) if left.table else None

                        if left_alias == table_alias or (
                            not left_alias and len(concept_aliases) == 1
                        ):
                            _, col_name = resolve_table_col(right, aliases)
                            col_norm = _norm(col_name)

                            if col_norm in DOMAIN_VOCABULARIES:
                                found_domain = col_norm
                                domain_col = col_name
                                break

                if found_domain:
                    break

            if not found_domain:
                continue

            config = DOMAIN_VOCABULARIES[found_domain]

            vocab_values = set()

            vocab_values |= _extract_vocab_filters(on_clause, aliases, table_alias, concept_aliases)

            where = select.args.get("where")
            if where:
                vocab_values |= _extract_vocab_filters(where, aliases, table_alias, concept_aliases)

            having = select.args.get("having")
            if having:
                vocab_values |= _extract_vocab_filters(having, aliases, table_alias, concept_aliases)

            invalid_found = vocab_values & config["invalid_vocabularies"]

            if not invalid_found:
                continue

            if config.get("require_standard_filter"):
                if not (
                    _has_standard_filter(on_clause, aliases, table_alias, concept_aliases)
                    or (where and _has_standard_filter(where, aliases, table_alias, concept_aliases))
                    or (having and _has_standard_filter(having, aliases, table_alias, concept_aliases))
                ):
                    continue

            key = f"{found_domain}_{table_alias}_{sorted(invalid_found)}_{id(select)}"
            if key in seen:
                continue
            seen.add(key)

            # Check if this is exploratory analysis
            is_exploratory = _is_exploratory_vocabulary_analysis(select, table_alias)

            violations.append({
                "domain": found_domain,
                "domain_name": config["domain_name"],
                "column": domain_col,
                "invalid_vocabularies": sorted(invalid_found),
                "expected_vocabularies": sorted(config["standard_vocabularies"]),
                "alias": table_alias,
                "rule_id": config["rule_id"],
                "context": join.sql(),
                "is_exploratory": is_exploratory,
            })

    return violations


# --- Rule ------------------------------------------------------------------

@register
class DomainVocabularyValidationRule(Rule):
    """Detect invalid vocabulary filters for standard concept_id columns."""

    rule_id = "concept_standardization.domain_vocabulary_validation"
    name = "Domain Vocabulary Validation (VOCAB_022-025)"

    description = (
        "Standard *_concept_id columns should align with domain-specific "
        "standard vocabularies. Filtering by source vocabularies is likely incorrect."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `c.vocabulary_id = '<source_vocab>'` WITH the domain's standard vocabulary (Condition→'SNOMED', Drug→'RxNorm', Measurement→'LOINC', Procedure→'SNOMED'/'CPT4', Observation→'SNOMED'). Use *_source_concept_id columns for source-vocabulary filtering."
    long_description = (
        "Each OMOP domain has a canonical standard vocabulary: Condition → "
        "SNOMED, Drug → RxNorm/RxNorm Extension, Procedure → SNOMED / CPT4 "
        "/ HCPCS depending on site, Measurement → LOINC, Unit → UCUM. "
        "Filtering a standard *_concept_id column by a *source* vocabulary "
        "like ICD10CM, ICD9CM, or NDC returns zero rows because those "
        "codes live on the source side. Use the domain's standard "
        "vocabulary, or switch to the *_source_concept_id column if you "
        "genuinely want to filter on the originating code system."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'ICD10CM';"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'SNOMED';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if CONCEPT_TABLE not in sql.lower():
            return []

        if not any(col in sql.lower() for col in DOMAIN_VOCABULARIES):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations = []

        for tree in trees:
            if not tree or not has_table_reference(tree, CONCEPT_TABLE):
                continue

            detected = _detect(tree)

            for v in detected:
                invalid = ", ".join(f"'{x}'" for x in v["invalid_vocabularies"])
                expected = ", ".join(f"'{x}'" for x in v["expected_vocabularies"])

                is_exploratory = v.get("is_exploratory", False)

                if is_exploratory:
                    # Exploratory analysis - WARNING severity
                    message = (
                        f"{v['column']} joined to concept (alias: {v['alias']}) "
                        f"with vocabulary_id {invalid}, but the query appears to be exploring "
                        f"vocabulary distribution. {v['domain_name']} standard concepts typically use {expected}. "
                        f"This is valid for exploratory analysis but may indicate incorrect vocabulary usage "
                        f"if used in production queries."
                    )
                    severity = Severity.WARNING
                else:
                    # Production filtering - ERROR severity
                    message = (
                        f"{v['column']} joined to concept (alias: {v['alias']}) "
                        f"with vocabulary_id {invalid}. "
                        f"{v['domain_name']} standard concepts use {expected}, not {invalid}. "
                        f"This filter will return zero or incorrect results because standard concept IDs "
                        f"don't belong to source vocabularies."
                    )
                    severity = Severity.ERROR

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=severity,
                        suggested_fix=self.suggested_fix,
                        details=v,
                    )
                )

        return violations


__all__ = ["DomainVocabularyValidationRule"]
