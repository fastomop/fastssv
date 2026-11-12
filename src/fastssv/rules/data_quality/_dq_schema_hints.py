"""Names the CDM v5.4 specification replaced or dropped, used to word the fix of a schema finding.

Sources:
    * "Changes from v5.3 to v5.4" (https://ohdsi.github.io/CommonDataModel/cdm54Changes.html): 8 fields renamed on
      VISIT_OCCURRENCE and VISIT_DETAIL, 1 table removed (ATTRIBUTE_DEFINITION).
    * OMOP CDM v5.4 field-level specification: which table holds a column that a neighbouring table lacks.
"""

from typing import Dict, Optional, Tuple

from fastssv.schemas import CDM_COLUMN_TYPES

_VISIT_RENAMES = {
    "admitting_source_concept_id": "admitted_from_concept_id",
    "admitting_source_value": "admitted_from_source_value",
    "discharge_to_concept_id": "discharged_to_concept_id",
    "discharge_to_source_value": "discharged_to_source_value",
}

# (table, v5.3 column) -> v5.4 column.
RENAMED_IN_V54: Dict[Tuple[str, str], str] = {
    **{("visit_occurrence", old): new for old, new in _VISIT_RENAMES.items()},
    **{("visit_detail", old): new for old, new in _VISIT_RENAMES.items()},
    ("visit_detail", "visit_detail_parent_id"): "parent_visit_detail_id",
}

# Tables of earlier CDM versions that v5.4 does not have.
REMOVED_IN_V54: Dict[str, str] = {
    "attribute_definition": "ATTRIBUTE_DEFINITION was removed from the CDM in v5.4",
}

# (table, column the table lacks) -> where the specification keeps that information.
LOCATION_HINTS: Dict[Tuple[str, str], str] = {
    ("episode_event", "person_id"): (
        "episode_event has no person_id; join episode on episode_id and read episode.person_id."
    ),
    ("visit_detail", "preceding_visit_occurrence_id"): (
        "visit_detail chains its rows through preceding_visit_detail_id; preceding_visit_occurrence_id belongs to "
        "visit_occurrence."
    ),
}

_REPLACEMENTS: Dict[Tuple[str, str], str] = {
    ("visit_detail", "preceding_visit_occurrence_id"): "preceding_visit_detail_id",
}


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, cut off above ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def near_miss_table(name: str) -> Optional[str]:
    """The single CDM table whose name is within two edits of ``name`` (one edit for names under seven letters,
    where two edits turn one word into another), else None."""
    hits = []
    for table in CDM_COLUMN_TYPES:
        limit = 2 if min(len(table), len(name)) >= 7 else 1
        if 0 < _edit_distance(name, table, limit) <= limit:
            hits.append(table)
    return hits[0] if len(hits) == 1 else None


def replacement_column(table: str, column: str) -> Optional[str]:
    """The v5.4 column a wrong name most plausibly stands for: a documented rename, the same name without the
    ``_start`` that only some event tables carry, or the single column within two edits."""
    columns = CDM_COLUMN_TYPES.get(table, {})
    for candidate in (
        RENAMED_IN_V54.get((table, column)),
        _REPLACEMENTS.get((table, column)),
        column.replace("_start_", "_"),
        column + "_id",
    ):
        if candidate and candidate != column and candidate in columns:
            return candidate
    hits = [c for c in columns if 0 < _edit_distance(column, c, 2) <= 2 and min(len(c), len(column)) >= 7]
    return hits[0] if len(hits) == 1 else None


def column_hint(table: str, column: str) -> str:
    """One sentence that names the v5.4 column or the table that holds it; empty when nothing is known."""
    if (table, column) in RENAMED_IN_V54:
        return f"CDM v5.4 renamed it to '{RENAMED_IN_V54[(table, column)]}'."
    if (table, column) in LOCATION_HINTS:
        return LOCATION_HINTS[(table, column)]
    candidate = replacement_column(table, column)
    if candidate:
        return f"Did you mean '{candidate}'?"
    owners = sorted(t for t, cols in CDM_COLUMN_TYPES.items() if column in cols)
    if 0 < len(owners) <= 4:
        return f"'{column}' is a column of {', '.join(owners)}."
    return ""


__all__ = ["LOCATION_HINTS", "REMOVED_IN_V54", "RENAMED_IN_V54", "column_hint", "near_miss_table", "replacement_column"]
