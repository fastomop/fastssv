"""CDM v5.4 free-text columns: varchar, not a *_source_value, neither a primary nor a foreign key.

Generated from the OMOP CDM v5.4 field-level specification (OMOP_CDMv5.4_Field_Level.csv, tables of the cdm
schema). note_nlp.snippet and note_nlp.lexical_variant are left to domain_specific.note_nlp_snippet_misuse."""

# fmt: off
FREE_TEXT_COLUMNS = frozenset(
    (
        ('care_site', 'care_site_name'),
        ('cdm_source', 'cdm_etl_reference'),
        ('cdm_source', 'cdm_holder'),
        ('cdm_source', 'cdm_source_abbreviation'),
        ('cdm_source', 'cdm_source_name'),
        ('cdm_source', 'cdm_version'),
        ('cdm_source', 'source_description'),
        ('cdm_source', 'source_documentation_reference'),
        ('cdm_source', 'vocabulary_version'),
        ('condition_occurrence', 'stop_reason'),
        ('device_exposure', 'production_id'),
        ('device_exposure', 'unique_device_id'),
        ('drug_exposure', 'lot_number'),
        ('drug_exposure', 'sig'),
        ('drug_exposure', 'stop_reason'),
        ('location', 'address_1'),
        ('location', 'address_2'),
        ('location', 'city'),
        ('location', 'county'),
        ('location', 'state'),
        ('location', 'zip'),
        ('measurement', 'measurement_time'),
        ('metadata', 'name'),
        ('metadata', 'value_as_string'),
        ('note', 'note_text'),
        ('note', 'note_title'),
        ('note_nlp', 'nlp_system'),
        ('note_nlp', 'offset'),
        ('note_nlp', 'term_exists'),
        ('note_nlp', 'term_modifiers'),
        ('note_nlp', 'term_temporal'),
        ('observation', 'value_as_string'),
        ('provider', 'dea'),
        ('provider', 'npi'),
        ('provider', 'provider_name'),
        ('specimen', 'specimen_source_id'),
    )
)
# fmt: on
