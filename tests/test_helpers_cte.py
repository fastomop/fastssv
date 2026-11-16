"""CTE-shadowing semantics for `core.helpers`.

A user CTE named after an OMOP table (e.g. `WITH cohort AS (...)`) must not
be confused with the actual OMOP table. The helpers below are the central
chokepoint for OMOP-table-targeting rules; locking in the CTE-aware
behavior here prevents regressions across the ~50 rules that depend on
them.
"""

from __future__ import annotations

import sqlglot

from fastssv.core.helpers import (
    collect_cte_names,
    collect_locally_defined_tables,
    collect_locally_defined_unqualified_tables,
    extract_aliases,
    has_table_reference,
)


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


def test_has_table_reference_with_on_union_still_shadows() -> None:
    """sqlglot attaches the WITH of ``WITH x AS (…) SELECT … UNION …`` to
    the ``Union`` node, not either ``Select``. The shadow walk must find
    it there — an earlier Select-only walk missed this and treated the
    CTE reference in the second branch as the OMOP ``cohort`` table
    (common Atlas shape: top-level UNION ALL over a shared concept CTE).
    """
    tree = _parse("WITH cohort AS (SELECT 1 AS x) SELECT a FROM condition_occurrence UNION ALL SELECT x FROM cohort")
    assert has_table_reference(tree, "cohort") is False
    assert has_table_reference(tree, "condition_occurrence") is True


def test_has_table_reference_with_on_insert_still_shadows() -> None:
    """Same as the UNION case: ``WITH x AS (…) INSERT INTO t SELECT …``
    hangs the WITH off the ``Insert`` node."""
    tree = _parse("WITH cohort AS (SELECT 1 AS x) INSERT INTO results SELECT x FROM cohort")
    assert has_table_reference(tree, "cohort") is False


# ---- collect_locally_defined_tables ---------------------------------------


def test_collect_locally_defined_tables_ctas() -> None:
    """``CREATE TABLE … AS SELECT`` parses as ``Create(this=Table(...))``."""
    assert collect_locally_defined_tables("CREATE TABLE scratch.tmpach_0 AS SELECT 1") == frozenset({"tmpach_0"})


def test_collect_locally_defined_tables_column_def() -> None:
    """``CREATE TABLE foo (col TYPE)`` parses as ``Create(this=Schema(this=Table))``.
    The helper must unwrap the ``Schema`` layer.
    """
    assert collect_locally_defined_tables("CREATE TABLE scratch.tmpach_0 (id INT, val VARCHAR(255))") == frozenset(
        {"tmpach_0"}
    )


def test_collect_locally_defined_tables_modifiers() -> None:
    """``TEMP``, ``IF NOT EXISTS``, ``OR REPLACE`` and quoted mixed-case
    names all collapse to the same lowercased base name.
    """
    names = collect_locally_defined_tables(
        "CREATE TEMPORARY TABLE a (id INT);\n"
        "CREATE TABLE IF NOT EXISTS b (id INT);\n"
        "CREATE OR REPLACE TABLE c AS SELECT 1;\n"
        'CREATE TABLE "Mixed_Case" (id INT);'
    )
    assert names == frozenset({"a", "b", "c", "mixed_case"})


def test_collect_locally_defined_tables_views() -> None:
    """``CREATE VIEW`` and ``CREATE MATERIALIZED VIEW`` introduce a name
    downstream SELECTs can reference the same way a table would; the rule
    needs to skip them too.
    """
    names = collect_locally_defined_tables("CREATE VIEW v AS SELECT 1;\nCREATE MATERIALIZED VIEW mv AS SELECT 1;")
    assert names == frozenset({"v", "mv"})


def test_collect_locally_defined_tables_ignores_non_creates() -> None:
    """INSERT/DROP/SELECT do not *introduce* a new table — the helper must
    not capture their target names.
    """
    assert (
        collect_locally_defined_tables(
            "INSERT INTO scratch.foo SELECT 1;\nDROP TABLE scratch.bar;\nSELECT * FROM scratch.baz;"
        )
        == frozenset()
    )


def test_collect_locally_defined_tables_tolerates_bad_statements() -> None:
    """One unparseable statement (typical SqlRender ``@param`` leftover)
    must not poison the whole pool — neighbouring CREATE statements still
    contribute. This is why the helper parses statement-by-statement
    instead of whole-batch.
    """
    sql = (
        "CREATE TABLE scratch.tempResults_104 AS SELECT 1;\n"
        "CREATE TABLE results.achilles_@detailType AS SELECT 1;\n"  # unrendered
        "CREATE TABLE scratch.tempResults_105 AS SELECT 1;\n"
    )
    names = collect_locally_defined_tables(sql)
    assert "tempresults_104" in names
    assert "tempresults_105" in names


def test_collect_locally_defined_tables_empty_and_garbage() -> None:
    """Robust to empty / comment-only / non-SQL input."""
    for bad in ["", "   ", "-- comment only", "not sql at all", ";;;"]:
        assert collect_locally_defined_tables(bad) == frozenset()


# ---- collect_locally_defined_unqualified_tables ----------------------------


def test_collect_unqualified_excludes_schema_qualified_creates() -> None:
    """``CREATE TABLE backup.death`` defines ``backup.death`` — it must NOT
    contribute a shadow for the destructive-operations rule, because an
    unqualified ``DELETE FROM death`` still resolves to the clinical table
    on the search path. The broad collector keeps it (schema rule wants
    the stripped name); the unqualified collector drops it.
    """
    sql = "CREATE TABLE backup_schema.death AS SELECT * FROM cdm.death;"
    assert collect_locally_defined_tables(sql) == frozenset({"death"})
    assert collect_locally_defined_unqualified_tables(sql) == frozenset()


def test_collect_unqualified_keeps_bare_creates() -> None:
    """An unqualified CREATE genuinely shadows the name for the rest of
    the session (Achilles 2004: ``CREATE TEMP TABLE death AS …``)."""
    assert collect_locally_defined_unqualified_tables("CREATE TABLE death AS SELECT 1") == frozenset({"death"})


def test_collect_unqualified_keeps_temp_creates() -> None:
    """TEMP tables are session-local and shadow regardless of spelling."""
    assert collect_locally_defined_unqualified_tables("CREATE TEMPORARY TABLE death (person_id INT)") == frozenset(
        {"death"}
    )


def test_collect_unqualified_mixed_batch() -> None:
    """Mixed batch: only the unqualified / TEMP creates shadow."""
    sql = (
        "CREATE TABLE scratch.tempResults_104 AS SELECT 1;\n"
        "CREATE TEMPORARY TABLE death AS SELECT DISTINCT person_id FROM cdm.death;\n"
        "CREATE TABLE local_counts AS SELECT 1;\n"
    )
    assert collect_locally_defined_tables(sql) == frozenset({"tempresults_104", "death", "local_counts"})
    assert collect_locally_defined_unqualified_tables(sql) == frozenset({"death", "local_counts"})
