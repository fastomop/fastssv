"""Tests for the structured fix-patch infrastructure (`fastssv.core.patch`).

Covers the schema's four actions (REPLACE, ADD, REMOVE, FREEFORM), the
locate() helper that maps SQL fragments to byte spans, the apply_patch()
applier, and integration with three converted rules.
"""

from __future__ import annotations

import pytest

from fastssv.core.patch import (
    PatchError,
    add,
    apply_patch,
    freeform,
    has_unresolved_placeholders,
    locate,
    remove,
    replace,
)


# --- Schema construction ---------------------------------------------------


def test_replace_shape():
    assert replace((10, 20), "new") == {"action": "REPLACE", "span": [10, 20], "text": "new"}


def test_add_shape():
    assert add(15, " AND x = 1") == {"action": "ADD", "at": 15, "text": " AND x = 1"}


def test_remove_shape():
    assert remove((5, 10)) == {"action": "REMOVE", "span": [5, 10]}


def test_freeform_shape():
    assert freeform("do this and that") == {"action": "FREEFORM", "text": "do this and that"}


# --- Applier ---------------------------------------------------------------


def test_apply_replace_substitutes_span():
    sql = "SELECT * FROM t WHERE x = NULL"
    span = locate(sql, "x = NULL")
    assert span is not None
    out = apply_patch(sql, replace(span, "x IS NULL"))
    assert out == "SELECT * FROM t WHERE x IS NULL"


def test_apply_add_inserts_at_position():
    sql = "SELECT * FROM t WHERE c.concept_code = 'E11.9'"
    span = locate(sql, "c.concept_code = 'E11.9'")
    assert span is not None
    out = apply_patch(sql, add(span[1], " AND c.vocabulary_id = 'ICD10CM'"))
    assert out == "SELECT * FROM t WHERE c.concept_code = 'E11.9' AND c.vocabulary_id = 'ICD10CM'"


def test_apply_remove_deletes_span():
    sql = "SELECT DISTINCT person_id FROM person"
    span = locate(sql, "DISTINCT ")
    assert span is not None
    out = apply_patch(sql, remove(span))
    assert out == "SELECT person_id FROM person"


def test_apply_freeform_raises():
    with pytest.raises(PatchError):
        apply_patch("SELECT 1", freeform("do this manually"))


def test_apply_unknown_action_raises():
    with pytest.raises(PatchError):
        apply_patch("SELECT 1", {"action": "UNKNOWN"})


def test_apply_out_of_range_span_raises():
    with pytest.raises(PatchError):
        apply_patch("SELECT 1", {"action": "REPLACE", "span": [0, 999], "text": "x"})


def test_apply_negative_span_raises():
    with pytest.raises(PatchError):
        apply_patch("SELECT 1", {"action": "REPLACE", "span": [-1, 5], "text": "x"})


def test_apply_inverted_span_raises():
    with pytest.raises(PatchError):
        apply_patch("SELECT 1", {"action": "REPLACE", "span": [5, 2], "text": "x"})


def test_apply_add_invalid_offset_raises():
    with pytest.raises(PatchError):
        apply_patch("SELECT 1", {"action": "ADD", "at": -1, "text": "x"})


# --- locate() --------------------------------------------------------------


def test_locate_exact_match():
    sql = "SELECT * FROM t WHERE x = 1"
    assert locate(sql, "x = 1") == (22, 27)


def test_locate_returns_none_when_missing():
    assert locate("SELECT 1", "DROP TABLE") is None


def test_locate_returns_none_when_ambiguous():
    sql = "SELECT a, a FROM t"
    assert locate(sql, "a") is None


def test_locate_case_insensitive_fallback():
    sql = "SELECT * FROM T WHERE Concept_Id = 1"
    span = locate(sql, "concept_id = 1")
    assert span is not None
    assert sql[span[0] : span[1]].lower() == "concept_id = 1"


def test_locate_whitespace_normalised_fallback():
    sql = "SELECT *\n  FROM   t\n WHERE x = 1"
    # Fragment uses single spaces; source has runs of whitespace
    span = locate(sql, "FROM t WHERE x = 1")
    assert span is not None
    assert "FROM" in sql[span[0] : span[1]]
    assert "WHERE x = 1" in sql[span[0] : span[1]]


# --- Placeholders ----------------------------------------------------------


def test_has_unresolved_placeholders_detects():
    assert has_unresolved_placeholders({"action": "ADD", "at": 0, "text": " AND vocabulary_id = '<vocab>'"})


def test_has_unresolved_placeholders_clean_when_resolved():
    assert not has_unresolved_placeholders({"action": "ADD", "at": 0, "text": " AND vocabulary_id = 'ICD10CM'"})


# --- Rule integration ------------------------------------------------------


def _violations(sql: str, rule_id: str):
    """Run the named rule and return its violations on ``sql``."""
    from fastssv.core.registry import get_all_rules
    import fastssv.rules  # noqa: F401  (load registrations)

    rules = {cls.rule_id: cls for cls in get_all_rules()}
    return rules[rule_id]().validate(sql)


def test_null_comparison_emits_replace_patch():
    sql = "SELECT person_id FROM death WHERE death_date = NULL"
    violations = _violations(sql, "anti_patterns.null_comparison_operator")
    assert violations
    patch = violations[0].suggested_fix_patch
    assert patch is not None
    assert patch["action"] == "REPLACE"
    fixed = apply_patch(sql, patch)
    assert fixed == "SELECT person_id FROM death WHERE death_date IS NULL"


def test_null_comparison_neq_emits_is_not_null():
    sql = "SELECT person_id FROM death WHERE death_date <> NULL"
    violations = _violations(sql, "anti_patterns.null_comparison_operator")
    assert violations
    patch = violations[0].suggested_fix_patch
    assert patch["action"] == "REPLACE"
    fixed = apply_patch(sql, patch)
    assert "IS NOT NULL" in fixed


def test_concept_code_emits_add_patch_with_inferred_vocab():
    sql = "SELECT c.concept_id FROM concept c WHERE c.concept_code = 'E11.9'"
    violations = _violations(sql, "anti_patterns.concept_code_requires_vocabulary_id")
    assert violations
    patch = violations[0].suggested_fix_patch
    assert patch is not None
    assert patch["action"] == "ADD"
    # ICD10CM is unambiguously inferable from 'E11.9'
    assert "ICD10CM" in patch["text"]
    assert not has_unresolved_placeholders(patch)
    fixed = apply_patch(sql, patch)
    assert "vocabulary_id = 'ICD10CM'" in fixed


def test_concept_code_like_emits_placeholder_patch():
    sql = "SELECT c.concept_id FROM concept c WHERE c.concept_code LIKE 'E%'"
    violations = _violations(sql, "anti_patterns.concept_code_requires_vocabulary_id")
    assert violations
    patch = violations[0].suggested_fix_patch
    assert patch is not None
    if patch["action"] == "ADD":
        # No vocab can be inferred from a LIKE pattern → placeholder remains
        assert has_unresolved_placeholders(patch)


def test_no_distinct_on_pk_emits_remove_patch():
    sql = "SELECT DISTINCT person_id FROM person"
    violations = _violations(sql, "anti_patterns.no_distinct_on_primary_key_column")
    assert violations
    patch = violations[0].suggested_fix_patch
    assert patch["action"] == "REMOVE"
    assert apply_patch(sql, patch) == "SELECT person_id FROM person"


def test_concept_join_emits_replace_with_aliased_sql():
    """Detector resolves aliases (`c` → `concept`); patch must locate the
    aliased form in the source, not the resolved form."""
    sql = "SELECT * FROM condition_occurrence co JOIN concept c ON co.condition_concept_id = c.concept_name"
    violations = _violations(sql, "joins.concept_join_validation")
    assert violations
    patch = violations[0].suggested_fix_patch
    assert patch["action"] == "REPLACE"
    fixed = apply_patch(sql, patch)
    assert "co.condition_concept_id = c.concept_id" in fixed


def test_concept_class_join_emits_replace_with_aliased_sql():
    sql = "SELECT * FROM concept c JOIN concept_class cc ON c.vocabulary_id = cc.concept_class_id"
    violations = _violations(sql, "joins.concept_concept_class_join_validation")
    assert violations
    patch = violations[0].suggested_fix_patch
    assert patch["action"] == "REPLACE"
    fixed = apply_patch(sql, patch)
    assert "c.concept_class_id = cc.concept_class_id" in fixed


def test_clinical_visit_detail_join_emits_replace_with_aliased_sql():
    sql = "SELECT * FROM measurement m JOIN visit_detail vd ON m.visit_occurrence_id = vd.visit_detail_id"
    violations = _violations(sql, "joins.clinical_visit_detail_join_validation")
    assert violations
    patch = violations[0].suggested_fix_patch
    assert patch["action"] == "REPLACE"
    fixed = apply_patch(sql, patch)
    assert "m.visit_detail_id = vd.visit_detail_id" in fixed


def test_year_of_birth_emits_freeform():
    sql = (
        "SELECT co.person_id FROM person p "
        "JOIN condition_occurrence co ON p.person_id = co.person_id "
        "WHERE EXTRACT(YEAR FROM co.condition_start_date) - p.year_of_birth >= 65"
    )
    violations = _violations(sql, "domain_specific.year_of_birth_age_arithmetic")
    assert violations
    patch = violations[0].suggested_fix_patch
    assert patch is not None
    assert patch["action"] == "FREEFORM"
    # FREEFORM patches are not auto-appliable
    with pytest.raises(PatchError):
        apply_patch(sql, patch)


def test_violation_to_dict_emits_patch_dict_for_mechanical():
    """Mechanical (REPLACE/ADD/REMOVE) violations serialise `fix` as the
    structured patch dict — no separate prose field."""
    sql = "SELECT 1 FROM death WHERE death_date = NULL"
    violations = _violations(sql, "anti_patterns.null_comparison_operator")
    d = violations[0].to_dict()
    assert "suggested_fix" not in d
    assert "suggested_fix_patch" not in d
    assert isinstance(d["fix"], dict)
    assert d["fix"]["action"] == "REPLACE"


def test_violation_freeform_serialises_as_prose_string():
    """FREEFORM violations expose `fix` as the prose string — same shape
    the prior `suggested_fix` field used to carry."""
    from fastssv.core.base import RuleViolation, Severity

    v = RuleViolation(
        rule_id="x",
        severity=Severity.ERROR,
        message="m",
        suggested_fix="REPLACE: a WITH b.",
    )
    # Internal representation: still a structured FREEFORM patch.
    assert v.suggested_fix_patch == {"action": "FREEFORM", "text": "REPLACE: a WITH b."}
    # Serialised representation: a single `fix` field carrying the prose.
    d = v.to_dict()
    assert d["fix"] == "REPLACE: a WITH b."
    assert "suggested_fix" not in d
    assert "suggested_fix_patch" not in d


# --- Style-guide linter ----------------------------------------------------

# Canonical imperative verbs the LLM-friendly fix-message style guide
# allows at the start of a class-level ``suggested_fix``. Adding a new verb
# here is intentional — it's a small surface change that's easy to review.
CANONICAL_FIX_VERBS = (
    "REPLACE:",
    "ADD:",
    "REMOVE:",
    "JOIN:",
    "JOIN ",
    "CAST:",
    "REWRITE:",
    "FILTER:",
    "WRAP:",
    "GROUP BY:",
    "QUALIFY:",
    "RENAME:",
)


def test_class_level_suggested_fix_starts_with_canonical_verb():
    """Every rule's class-level ``suggested_fix`` must lead with one of
    the canonical caps verbs. This enforces the LLM-friendly style guide
    so the prose is parseable by small local models — they latch onto the
    leading verb token to decide what edit to perform.

    To add a new verb, append to ``CANONICAL_FIX_VERBS`` above; the
    one-line addition makes the change reviewable.
    """
    from fastssv.core.registry import get_all_rules
    import fastssv.rules  # noqa: F401  (load registrations)

    drifters = []
    for cls in get_all_rules():
        text = (cls.suggested_fix or "").strip()
        if not text:
            continue
        if not any(text.startswith(v) for v in CANONICAL_FIX_VERBS):
            drifters.append((cls.rule_id, text[:80]))

    assert not drifters, (
        f"\n{len(drifters)} rule(s) have suggested_fix that doesn't start with a "
        f"canonical caps verb. The LLM-friendly style guide requires one of "
        f"{CANONICAL_FIX_VERBS} as the leading token.\n"
        + "\n".join(f"  {rid}: {prefix}..." for rid, prefix in drifters)
    )


def test_violation_with_empty_suggested_fix_omits_fix_field():
    """Edge case: empty suggested_fix shouldn't synthesise a fix field."""
    from fastssv.core.base import RuleViolation, Severity

    v = RuleViolation(
        rule_id="x",
        severity=Severity.ERROR,
        message="m",
        suggested_fix="",
    )
    assert v.suggested_fix_patch is None
    assert "fix" not in v.to_dict()
