"""OMOP CDM v5.4 — semantic concept-field declarations.

Defines which (table, column) pairs are *standard* or *source* concept-id
fields, and which vocabulary tables drive hierarchy lookups. Used by
``concept_standardization.standard_concept_enforcement`` (default vs.
strict-mode targeting), ``joins.maps_to_direction``, and
``joins.join_path_validation``.

Every entry in ``STANDARD_CONCEPT_FIELDS`` and ``SOURCE_CONCEPT_FIELDS``
must be a real column in
``fastssv.schemas.cdm_column_types.CDM_COLUMN_TYPES``;
``tests/test_schema_consistency.py`` asserts this in CI.

Targeting model (default vs. strict)
------------------------------------
The CDM v5.4 spec defines two distinct concept-id flavours per event:

* ``<event>_concept_id`` — the **standard** concept the ETL has already
  mapped the source code to. The ETL is responsible; downstream queries
  can trust it. Listed in ``STANDARD_CONCEPT_FIELDS``.
* ``<event>_source_concept_id`` — the **pre-mapping** source concept.
  Frequently non-standard (or unmapped, ``= 0``); analytical queries that
  use this directly are at risk of producing wrong cohorts. Listed in
  ``SOURCE_CONCEPT_FIELDS``.

The standard-concept-enforcement rule consumes these differently:

* Default mode (correctness-precision): fires on ``SOURCE_CONCEPT_FIELDS``
  references that aren't filtered or mapped via ``concept_relationship
  'Maps to'``, and on vocabulary-context queries (joins to
  ``VOCABULARY_TABLES``) that don't filter by ``standard_concept = 'S'``.
* Strict mode (ETL-validation / new-dataset distrust): adds the historic
  broad check against ``STANDARD_CONCEPT_FIELDS`` on top — every standard
  concept-id reference needs an explicit standardness filter even though
  the CDM spec already guarantees it.
"""

# OMOP fields that should hold STANDARD concept ids.
STANDARD_CONCEPT_FIELDS = {
    # person
    ("person", "gender_concept_id"),
    ("person", "race_concept_id"),
    ("person", "ethnicity_concept_id"),
    # condition_occurrence
    ("condition_occurrence", "condition_concept_id"),
    ("condition_occurrence", "condition_type_concept_id"),
    ("condition_occurrence", "condition_status_concept_id"),
    # drug_exposure
    ("drug_exposure", "drug_concept_id"),
    ("drug_exposure", "drug_type_concept_id"),
    ("drug_exposure", "route_concept_id"),
    # procedure_occurrence
    ("procedure_occurrence", "procedure_concept_id"),
    ("procedure_occurrence", "procedure_type_concept_id"),
    ("procedure_occurrence", "modifier_concept_id"),
    # measurement
    ("measurement", "measurement_concept_id"),
    ("measurement", "measurement_type_concept_id"),
    ("measurement", "unit_concept_id"),
    ("measurement", "operator_concept_id"),
    ("measurement", "value_as_concept_id"),
    # observation
    ("observation", "observation_concept_id"),
    ("observation", "observation_type_concept_id"),
    ("observation", "qualifier_concept_id"),
    ("observation", "unit_concept_id"),
    ("observation", "value_as_concept_id"),
    # device_exposure
    ("device_exposure", "device_concept_id"),
    ("device_exposure", "device_type_concept_id"),
    # visit tables
    ("visit_occurrence", "visit_concept_id"),
    ("visit_occurrence", "visit_type_concept_id"),
    ("visit_occurrence", "admitted_from_concept_id"),
    ("visit_occurrence", "discharged_to_concept_id"),
    ("visit_detail", "visit_detail_concept_id"),
    ("visit_detail", "visit_detail_type_concept_id"),
    ("visit_detail", "admitted_from_concept_id"),
    ("visit_detail", "discharged_to_concept_id"),
    # death
    ("death", "cause_concept_id"),
    ("death", "death_type_concept_id"),
    # specimen
    ("specimen", "specimen_concept_id"),
    ("specimen", "specimen_type_concept_id"),
    ("specimen", "unit_concept_id"),
    ("specimen", "anatomic_site_concept_id"),
    ("specimen", "disease_status_concept_id"),
    # episode
    ("episode", "episode_concept_id"),
    ("episode", "episode_type_concept_id"),
    ("episode", "episode_object_concept_id"),
    # episode_event
    ("episode_event", "episode_event_field_concept_id"),
    # note
    ("note", "note_type_concept_id"),
    ("note", "note_class_concept_id"),
    ("note", "encoding_concept_id"),
    ("note", "language_concept_id"),
    # note_nlp
    ("note_nlp", "note_nlp_concept_id"),
    ("note_nlp", "section_concept_id"),
    # cost
    ("cost", "cost_type_concept_id"),
    ("cost", "currency_concept_id"),
    ("cost", "revenue_code_concept_id"),
    ("cost", "drg_concept_id"),
    # payer_plan_period (only the standard fields — the four matching
    # *_source_concept_id columns live in SOURCE_CONCEPT_FIELDS instead;
    # earlier revisions of this set mis-included them as standard).
    ("payer_plan_period", "payer_concept_id"),
    ("payer_plan_period", "plan_concept_id"),
    ("payer_plan_period", "sponsor_concept_id"),
    ("payer_plan_period", "stop_reason_concept_id"),
    # observation_period
    ("observation_period", "period_type_concept_id"),
    # drug_era
    ("drug_era", "drug_concept_id"),
    # condition_era
    ("condition_era", "condition_concept_id"),
    # dose_era
    ("dose_era", "drug_concept_id"),
    ("dose_era", "unit_concept_id"),
}


# Pre-mapping source concept-id fields. Unlike STANDARD_CONCEPT_FIELDS,
# these are *not* guaranteed standard — they hold the original source
# concept the ETL received, which may be non-standard, deprecated, or
# unmapped. Analytical queries that filter / project / aggregate by a
# *_source_concept_id without first mapping to standard (via
# concept_relationship 'Maps to' or an explicit join + standard_concept
# filter) silently mix layers and produce incorrect cohorts.
#
# Enumerated from CDM_COLUMN_TYPES — every entry is a real column.
SOURCE_CONCEPT_FIELDS = {
    ("condition_occurrence", "condition_source_concept_id"),
    ("death", "cause_source_concept_id"),
    ("device_exposure", "device_source_concept_id"),
    ("device_exposure", "unit_source_concept_id"),
    ("drug_exposure", "drug_source_concept_id"),
    ("episode", "episode_source_concept_id"),
    ("measurement", "measurement_source_concept_id"),
    ("measurement", "unit_source_concept_id"),
    ("note_nlp", "note_nlp_source_concept_id"),
    ("observation", "observation_source_concept_id"),
    ("payer_plan_period", "payer_source_concept_id"),
    ("payer_plan_period", "plan_source_concept_id"),
    ("payer_plan_period", "sponsor_source_concept_id"),
    ("payer_plan_period", "stop_reason_source_concept_id"),
    ("person", "ethnicity_source_concept_id"),
    ("person", "gender_source_concept_id"),
    ("person", "race_source_concept_id"),
    ("procedure_occurrence", "procedure_source_concept_id"),
    ("provider", "gender_source_concept_id"),
    ("provider", "specialty_source_concept_id"),
    ("visit_detail", "visit_detail_source_concept_id"),
    ("visit_occurrence", "visit_source_concept_id"),
}


# Vocabulary tables. A join to any of these from clinical-event SQL means
# the query is doing hierarchy/lookup work where the layer (standard vs.
# classification vs. source) materially changes results — so the standard
# concept filter becomes meaningful even on otherwise-trusted
# ``<event>_concept_id`` columns.
VOCABULARY_TABLES = frozenset(
    {
        "concept",
        "concept_ancestor",
        "concept_relationship",
        "concept_synonym",
        "vocabulary",
    }
)


__all__ = [
    "STANDARD_CONCEPT_FIELDS",
    "SOURCE_CONCEPT_FIELDS",
    "VOCABULARY_TABLES",
]
