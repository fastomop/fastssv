"""Schema consistency tests.

Freezes the single-source-of-truth contract: ``CDM_COLUMN_TYPES`` is
canonical, and ``CDM_COLUMNS`` and ``STANDARD_CONCEPT_FIELDS`` must
reference only columns that exist there.
"""

from __future__ import annotations

import pytest

from fastssv.schemas import (
    CDM_COLUMN_TYPES,
    CDM_COLUMNS,
    STANDARD_CONCEPT_FIELDS,
)


def test_cdm_columns_is_derived_from_cdm_column_types():
    """``CDM_COLUMNS`` must be exactly the table → column-set view of
    ``CDM_COLUMN_TYPES``."""
    derived = {table: frozenset(cols.keys()) for table, cols in CDM_COLUMN_TYPES.items()}
    assert dict(CDM_COLUMNS) == derived, (
        "CDM_COLUMNS has drifted from CDM_COLUMN_TYPES. The expected "
        "invariant is CDM_COLUMNS = {t: frozenset(CDM_COLUMN_TYPES[t].keys())}."
    )


@pytest.mark.parametrize(
    "table, column",
    sorted(STANDARD_CONCEPT_FIELDS),
)
def test_standard_concept_field_exists(table: str, column: str) -> None:
    assert column in CDM_COLUMN_TYPES.get(table, {}), (
        f"STANDARD_CONCEPT_FIELDS declares ('{table}', '{column}') but that column is not in CDM_COLUMN_TYPES"
    )
