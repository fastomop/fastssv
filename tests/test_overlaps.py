"""One owner per defect: queries that two rules of different groups used to report together.

Each case asserts the exact set of (rule_id, severity) findings the whole validator returns.
"""

from __future__ import annotations

import pytest

import fastssv
from fastssv.core.base import RuleViolation, Severity
from fastssv.core.deduplication import deduplicate_violations

LINKAGE = ("joins.clinical_person_id_linkage_validation", "WARNING")
CROSS_JOIN = ("anti_patterns.comma_separated_cross_join", "WARNING")
LITERAL_HIERARCHY = ("concept_standardization.concept_literal_without_hierarchy", "WARNING")
COLUMN_TYPE_E = ("data_quality.column_type_validation", "ERROR")
COLUMN_TYPE_W = ("data_quality.column_type_validation", "WARNING")
BOTH = ("postgres", "duckdb")

_CO_OP = (
    "SELECT co.person_id FROM condition_occurrence co JOIN observation_period op ON co.person_id = op.person_id WHERE "
)
_COST = "SELECT c.cost_id, de.drug_concept_id FROM cost c JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id WHERE "


def _found(sql: str, dialect: str) -> set:
    return {(v.rule_id, v.severity.name) for v in fastssv.validate_sql_structured(sql, dialect=dialect)}


# (case id, sql, {dialect: expected findings})
CASES = [
    (
        "D1 defines_ancestry",
        "SELECT relationship_id FROM relationship WHERE defines_ancestry = 1",
        {"postgres": {("domain_specific.vocabulary_relationship_boolean_comparison", "ERROR")}, "duckdb": set()},
    ),
    (
        "D1 is_hierarchical",
        "SELECT relationship_id FROM relationship WHERE is_hierarchical = 1",
        {"postgres": {("domain_specific.vocabulary_relationship_boolean_comparison", "ERROR")}, "duckdb": set()},
    ),
    (
        "D2 IN with a number",
        "SELECT concept_id FROM concept WHERE standard_concept IN ('S', 1)",
        {"postgres": {COLUMN_TYPE_E}, "duckdb": {COLUMN_TYPE_W}},
    ),
    (
        "D2 = number",
        "SELECT concept_id FROM concept WHERE standard_concept = 1",
        {"postgres": {COLUMN_TYPE_E}, "duckdb": {COLUMN_TYPE_W}},
    ),
    (
        "D2 = TRUE",
        "SELECT concept_id FROM concept WHERE standard_concept = TRUE",
        {"postgres": {COLUMN_TYPE_E}, "duckdb": {COLUMN_TYPE_W}},
    ),
    (
        "D3 person_id = person_source_value",
        "SELECT co.condition_concept_id FROM condition_occurrence co "
        "JOIN person p ON co.person_id = p.person_source_value",
        # Only an ERROR on the predicate absorbs the linkage warning; DuckDB's type finding is a WARNING.
        {"postgres": {COLUMN_TYPE_E}, "duckdb": {COLUMN_TYPE_W, LINKAGE}},
    ),
    (
        "D4 BETWEEN with swapped bounds",
        _CO_OP + "co.condition_start_date BETWEEN op.observation_period_end_date AND op.observation_period_start_date",
        {d: {("temporal.observation_period_date_range_logic", "ERROR")} for d in BOTH},
    ),
    (
        "D4 >= end AND <= start",
        _CO_OP + "co.condition_start_date >= op.observation_period_end_date "
        "AND co.condition_start_date <= op.observation_period_start_date",
        {d: {("temporal.observation_period_date_range_logic", "ERROR")} for d in BOTH},
    ),
    (
        "D4 period column on the left",
        _CO_OP + "op.observation_period_end_date <= co.condition_start_date "
        "AND op.observation_period_start_date >= co.condition_start_date",
        {d: {("temporal.observation_period_date_range_logic", "ERROR")} for d in BOTH},
    ),
    (
        "D5 visit_occurrence_id = visit_detail_id",
        "SELECT * FROM condition_occurrence co JOIN visit_detail vd ON co.visit_occurrence_id = vd.visit_detail_id",
        {d: {("joins.visit_occurrence_id_join_validation", "ERROR")} for d in BOTH},
    ),
    (
        "D6 LIMIT 1 on a Maps to lookup",
        "SELECT cr.concept_id_2 FROM concept c JOIN concept_relationship cr ON cr.concept_id_1 = c.concept_id "
        "WHERE c.vocabulary_id = 'ICD9CM' AND c.concept_code = '070.43' AND cr.relationship_id = 'Maps to' LIMIT 1",
        {d: {("concept_standardization.multiple_maps_to_targets", "WARNING")} for d in BOTH},
    ),
    (
        "D7 comma join",
        "SELECT co.person_id, de.drug_concept_id FROM condition_occurrence co, drug_exposure de "
        "WHERE co.condition_concept_id = 201820",
        {d: {LINKAGE, LITERAL_HIERARCHY} for d in BOTH},
    ),
    (
        "D7 CROSS JOIN",
        "SELECT co.person_id, de.drug_concept_id FROM condition_occurrence co CROSS JOIN drug_exposure de "
        "WHERE co.condition_concept_id = 201820",
        {d: {LINKAGE, LITERAL_HIERARCHY} for d in BOTH},
    ),
    (
        "D7 JOIN ON TRUE",
        "SELECT co.person_id, de.drug_concept_id FROM condition_occurrence co JOIN drug_exposure de ON TRUE "
        "WHERE co.condition_concept_id = 201820",
        {d: {LINKAGE, LITERAL_HIERARCHY} for d in BOTH},
    ),
    (
        "D7 clinical x vocabulary keeps the cross-join warning",
        "SELECT co.person_id, c.concept_name FROM condition_occurrence co, concept c "
        "WHERE co.condition_concept_id = 201820",
        {d: {CROSS_JOIN, LITERAL_HIERARCHY} for d in BOTH},
    ),
    (
        "D8 care_site_id = concept_id",
        "SELECT * FROM visit_occurrence v JOIN concept c ON v.care_site_id = c.concept_id",
        {d: {("anti_patterns.join_key_validation", "ERROR")} for d in BOTH},
    ),
    (
        "D8 condition_occurrence_id = concept_id",
        "SELECT * FROM condition_occurrence co JOIN concept c ON co.condition_occurrence_id = c.concept_id",
        {d: {("anti_patterns.join_key_validation", "ERROR")} for d in BOTH},
    ),
    (
        "D8 visit_detail_id = concept_id",
        "SELECT * FROM visit_detail vd JOIN concept c ON vd.visit_detail_id = c.concept_id",
        {d: {("anti_patterns.join_key_validation", "ERROR")} for d in BOTH},
    ),
    (
        "D9 metadata_id = person_id",
        "SELECT * FROM metadata m JOIN person p ON m.metadata_id = p.person_id",
        {d: {("joins.person_id_join_validation", "ERROR")} for d in BOTH},
    ),
    (
        "D9 key no joins rule judges keeps the metadata warning",
        "SELECT * FROM metadata m JOIN observation o ON m.metadata_id = o.observation_event_id",
        {
            d: {
                ("anti_patterns.singleton_metadata_clinical_join", "WARNING"),
                ("domain_specific.event_field_polymorphic_resolution", "WARNING"),
            }
            for d in BOTH
        },
    ),
    (
        "D10 cohort.subject_id = visit_occurrence_id",
        "SELECT * FROM cohort c JOIN condition_occurrence co ON c.subject_id = co.visit_occurrence_id",
        {d: {("joins.cohort_clinical_join_validation", "WARNING")} for d in BOTH},
    ),
    (
        "D11 wrong-case cost_domain_id",
        _COST + "c.cost_domain_id = 'drug'",
        {d: {("data_quality.canonical_string_value_validation", "ERROR")} for d in BOTH},
    ),
    (
        "D11 another domain is still judged",
        _COST + "c.cost_domain_id = 'Procedure'",
        {d: {("domain_specific.cost_event_id_polymorphic_resolution", "ERROR")} for d in BOTH},
    ),
    (
        "D12 number on cost_domain_id",
        _COST + "c.cost_domain_id = 13",
        {"postgres": {COLUMN_TYPE_E}, "duckdb": {COLUMN_TYPE_W}},
    ),
    (
        "D13 term_modifiers = concept_id",
        "SELECT * FROM note_nlp nn JOIN concept c ON nn.term_modifiers = c.concept_id",
        {"postgres": {COLUMN_TYPE_E}, "duckdb": {COLUMN_TYPE_W}},
    ),
    (
        "D13 term_modifiers = another text column keeps the free-text warning",
        "SELECT * FROM note_nlp nn1 JOIN note_nlp nn2 ON nn1.term_modifiers = nn2.term_modifiers",
        {d: {("data_quality.free_text_column_misuse", "WARNING")} for d in BOTH},
    ),
    (
        "linkage kept: no equality",
        "SELECT co.person_id FROM condition_occurrence co JOIN drug_exposure de ON co.condition_concept_id > 0",
        {d: {LINKAGE} for d in BOTH},
    ),
    (
        "linkage kept: equality on two dates",
        "SELECT co.person_id FROM condition_occurrence co "
        "JOIN drug_exposure de ON co.condition_start_date = de.drug_exposure_start_date",
        {d: {LINKAGE} for d in BOTH},
    ),
    (
        "linkage kept: wrong partner no ERROR rule reports",
        "SELECT co.person_id FROM condition_occurrence co JOIN drug_exposure de ON co.person_id = de.quantity",
        {d: {LINKAGE} for d in BOTH},
    ),
]


@pytest.mark.parametrize(
    "sql,dialect,expected",
    [(sql, d, exp) for _, sql, by_dialect in CASES for d, exp in by_dialect.items()],
    ids=[f"{case}-{d}" for case, _, by_dialect in CASES for d in by_dialect],
)
def test_one_owner_per_defect(sql: str, dialect: str, expected: set) -> None:
    assert _found(sql, dialect) == expected


def _v(rule_id: str, severity: Severity, details: dict) -> RuleViolation:
    return RuleViolation(
        rule_id=rule_id, severity=severity, message=f"{severity.name} {details}", suggested_fix="", details=details
    )


def test_consequence_warning_needs_every_predicate_named_by_an_error() -> None:
    warning = _v(LINKAGE[0], Severity.WARNING, {"predicates": ["a.x = b.y", "a.z = b.w"]})
    error = _v("joins.person_id_join_validation", Severity.ERROR, {"predicate": "a.x = b.y"})
    assert deduplicate_violations([warning, error]) == [warning, error]
    other = _v("data_quality.column_type_validation", Severity.ERROR, {"predicate": "a.z = b.w"})
    assert deduplicate_violations([warning, error, other]) == [error, other]


def test_consequence_warning_not_absorbed_by_a_warning_or_its_own_rule() -> None:
    warning = _v(LINKAGE[0], Severity.WARNING, {"predicates": ["a.x = b.y"]})
    type_warning = _v("data_quality.column_type_validation", Severity.WARNING, {"predicate": "a.x = b.y"})
    assert deduplicate_violations([warning, type_warning]) == [warning, type_warning]
    own = _v(LINKAGE[0], Severity.ERROR, {"predicate": "a.x = b.y"})
    assert warning in deduplicate_violations([warning, own])


def test_limit_finding_yields_to_a_rule_that_explains_the_same_limit() -> None:
    limit = _v("anti_patterns.limit_without_order_by", Severity.WARNING, {"limit_scope": "select 1 limit 1"})
    maps_to = _v(
        "concept_standardization.multiple_maps_to_targets", Severity.WARNING, {"limit_scope": "select 1 limit 1"}
    )
    elsewhere = _v(
        "concept_standardization.multiple_maps_to_targets", Severity.WARNING, {"limit_scope": "select 2 limit 1"}
    )
    assert deduplicate_violations([limit, maps_to]) == [maps_to]
    assert deduplicate_violations([limit, elsewhere]) == [limit, elsewhere]
