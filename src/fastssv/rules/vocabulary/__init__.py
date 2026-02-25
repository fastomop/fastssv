"""Vocabulary validation rules for OMOP SQL queries.

Rules:
- no_string_id: Prevents string matching on *_source_value columns
- concept_lookup: Ensures concept table string filters are in concept_id lookup context
- concept_code_vocab_id: Ensures concept_code filters are accompanied by vocabulary_id
- schema_validation: Validates column references against OMOP CDM schema
- concept_name_lookup: Warns against filtering by concept_name (anti-pattern)
"""

# Import all rule modules to trigger registration
from . import concept_code_vocab_id, concept_lookup, concept_name_lookup, no_string_id, schema_validation

__all__ = [
    "no_string_id",
    "concept_lookup",
    "concept_code_vocab_id",
    "schema_validation",
    "concept_name_lookup",
]
