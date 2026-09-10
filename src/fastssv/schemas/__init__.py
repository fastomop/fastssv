"""FastSSV OMOP CDM v5.4 schema views.

Two files only:

- ``cdm_column_types`` — canonical per-table ``{column: type}`` map plus
  the derived column-name view (``CDM_COLUMNS``) and helper accessors.
- ``semantic_schema`` — concept-field declarations: standard concept-id
  fields (CDM-guaranteed standard), source concept-id fields (pre-mapping
  layer, may be non-standard), and vocabulary tables (joins to these mean
  the query is doing hierarchy/lookup work).

Earlier revisions also shipped ``cdm_columns``, ``cdm_schema`` and
``concept_class_id_canonical``; they were retired because no rule
consumed them. ``SOURCE_CONCEPT_FIELDS`` and ``VOCABULARY_TABLES`` were
re-introduced in [Unreleased] for the redesigned standard-concept
enforcement rule (see CHANGELOG for the OMOP-spec rationale).
"""

from .cdm_column_types import (
    CDM_COLUMN_TYPES,
    CDM_COLUMNS,
    DATE,
    DATETIME,
    FLOAT,
    INTEGER,
    SOURCE_VALUE_COLUMNS,
    TIMESTAMP,
    VARCHAR,
    are_types_compatible,
    get_column_type,
    get_table_columns,
)
from .semantic_schema import (
    SOURCE_CONCEPT_FIELDS,
    STANDARD_CONCEPT_FIELDS,
    VOCABULARY_TABLES,
)

__all__ = [
    "CDM_COLUMN_TYPES",
    "CDM_COLUMNS",
    "get_table_columns",
    "get_column_type",
    "are_types_compatible",
    "SOURCE_VALUE_COLUMNS",
    "INTEGER",
    "VARCHAR",
    "DATE",
    "DATETIME",
    "TIMESTAMP",
    "FLOAT",
    "STANDARD_CONCEPT_FIELDS",
    "SOURCE_CONCEPT_FIELDS",
    "VOCABULARY_TABLES",
]
