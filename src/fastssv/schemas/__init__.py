"""FastSSV OMOP CDM v5.4 schema views.

Two files only:

- ``cdm_column_types`` — canonical per-table ``{column: type}`` map plus
  the derived column-name view (``CDM_COLUMNS``) and helper accessors.
- ``semantic_schema`` — the set of (table, column) pairs that must hold
  standard concept ids.

Earlier revisions also shipped ``cdm_columns``, ``cdm_schema``,
``concept_class_id_canonical``, ``SOURCE_CONCEPT_FIELDS``, and
``SOURCE_VOCABS``. They were retired in [Unreleased] because no rule
consumed them; see CHANGELOG for details.
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
from .semantic_schema import STANDARD_CONCEPT_FIELDS

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
]
