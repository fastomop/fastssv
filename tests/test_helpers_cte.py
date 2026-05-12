"""CTE-shadowing semantics for `core.helpers`.

A user CTE named after an OMOP table (e.g. `WITH cohort AS (...)`) must not
be confused with the actual OMOP table. The helpers below are the central
chokepoint for OMOP-table-targeting rules; locking in the CTE-aware
behavior here prevents regressions across the ~50 rules that depend on
them.
"""

from __future__ import annotations

import sqlglot

from fastssv.core.helpers import collect_cte_names, extract_aliases, has_table_reference


def _parse(sql: str):
    return sqlglot.parse_one(sql, dialect="postgres")


def test_collect_cte_names_basic() -> None:
    tree = _parse("WITH cohort AS (SELECT 1) SELECT * FROM cohort")
    assert collect_cte_names(tree) == {"cohort"}


def test_collect_cte_names_multiple() -> None:
    tree = _parse("WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a JOIN b ON 1=1")
    assert collect_cte_names(tree) == {"a", "b"}


def test_collect_cte_names_no_cte() -> None:
    tree = _parse("SELECT * FROM cohort")
    assert collect_cte_names(tree) == set()


def test_has_table_reference_skips_cte_shadow() -> None:
    """An unqualified `FROM cohort` that resolves to a CTE in scope is not
    a reference to the OMOP `cohort` table."""
    tree = _parse("WITH cohort AS (SELECT 1) SELECT * FROM cohort c")
    assert has_table_reference(tree, "cohort") is False


def test_has_table_reference_keeps_real_table() -> None:
    tree = _parse("SELECT * FROM cohort c")
    assert has_table_reference(tree, "cohort") is True


def test_has_table_reference_schema_qualified_bypasses_shadow() -> None:
    """`mydb.cohort` references the OMOP table even when a CTE named
    `cohort` is in scope (standard SQL scoping)."""
    tree = _parse("WITH cohort AS (SELECT 1) SELECT * FROM mydb.cohort c")
    assert has_table_reference(tree, "cohort") is True


def test_extract_aliases_no_cte_unchanged() -> None:
    """Without any CTE, behavior is unchanged."""
    tree = _parse("SELECT * FROM cohort c JOIN condition_occurrence co ON c.subject_id = co.person_id")
    aliases = extract_aliases(tree)
    assert aliases["c"] == "cohort"
    assert aliases["cohort"] == "cohort"
    assert aliases["co"] == "condition_occurrence"


def test_has_table_reference_nested_cte_does_not_shadow_outer_ref() -> None:
    """A CTE defined inside a nested subquery is not visible to the outer
    query per standard SQL scoping. The previous tree-global ``collect_cte_names``
    intersection over-approximated visibility — an inner ``WITH cohort AS …``
    would suppress an outer ``FROM cohort``, silently producing a false
    negative on every OMOP-table-targeting rule that gates via this helper.
    """
    tree = _parse("SELECT * FROM cohort WHERE id IN (WITH cohort AS (SELECT 1 AS id) SELECT id FROM cohort)")
    assert has_table_reference(tree, "cohort") is True


def test_has_table_reference_self_reference_inside_cte_body_resolves_to_omop() -> None:
    """Inside the CTE's own body, an unqualified reference of the same name
    refers to the OMOP table, not the CTE itself (standard non-recursive
    SQL scoping). Confirms the self-reference carve-out in the shadow check."""
    tree = _parse("WITH cohort AS (SELECT * FROM cohort) SELECT person_id FROM cohort")
    # The OMOP `cohort` table IS referenced (inside the CTE body).
    assert has_table_reference(tree, "cohort") is True


def test_has_table_reference_outer_with_still_shadows() -> None:
    """Sanity: the outer-WITH case (the original cohort-shadow scenario
    that motivated this whole fix) still suppresses correctly."""
    tree = _parse(
        "WITH cohort AS (SELECT person_id FROM condition_occurrence) "
        "SELECT * FROM cohort c JOIN visit_occurrence vo ON c.person_id = vo.person_id"
    )
    # Only reference to `cohort` is the outer FROM cohort (shadowed by the WITH).
    # The CTE body uses condition_occurrence, not cohort.
    assert has_table_reference(tree, "cohort") is False
    assert has_table_reference(tree, "condition_occurrence") is True
