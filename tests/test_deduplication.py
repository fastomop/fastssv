"""Unit tests for `fastssv.core.deduplication`.

Covers every branch of `_normalize_issue` and the priority-selection logic
in `deduplicate_violations`.
"""

from __future__ import annotations

from fastssv.core.base import RuleViolation, Severity
from fastssv.core.deduplication import deduplicate_violations


def _v(
    rule_id: str,
    message: str,
    severity: Severity = Severity.ERROR,
    details: dict | None = None,
) -> RuleViolation:
    return RuleViolation(
        rule_id=rule_id,
        severity=severity,
        message=message,
        suggested_fix="",
        details=details or {},
    )


def test_empty_list_returns_empty() -> None:
    assert deduplicate_violations([]) == []


def test_single_violation_passes_through() -> None:
    v = _v("x.y", "something")
    assert deduplicate_violations([v]) == [v]


# Synthetic rule_id used to exercise the "longer rule_id wins" branch of
# deduplicate_violations. Real registered rule is data_quality.schema_validation;
# the second rule_id only needs to be a different (longer) string.
_LONGER_RULE_ID = "data_quality.schema_validation_longer_synthetic"


def test_invalid_column_dedup_by_table_and_column() -> None:
    a = _v(
        "data_quality.schema_validation",
        "Column 'foo' does not exist in table 'person'.",
        details={"type": "invalid_column", "table": "person", "column": "foo"},
    )
    b = _v(
        _LONGER_RULE_ID,
        "Column 'FOO' does not exist in 'Person'.",
        details={"type": "invalid_column", "table": "Person", "column": "FOO"},
    )
    out = deduplicate_violations([a, b])
    # Same underlying schema error → one survives
    assert len(out) == 1
    # Longer rule_id wins as "more specific"
    assert out[0].rule_id == _LONGER_RULE_ID


def test_invalid_column_regex_path_matches_message_only() -> None:
    """Second rule reports no details dict, only the prose message — still dedups."""
    a = _v(
        _LONGER_RULE_ID,
        "Column 'foo' does not exist in table 'person'.",
        details={"type": "invalid_column", "table": "person", "column": "foo"},
    )
    b = _v(
        "data_quality.schema_validation",
        "Column 'foo' does not exist in table 'person'.",
    )
    out = deduplicate_violations([a, b])
    assert len(out) == 1


def test_invalid_table_dedup() -> None:
    a = _v(
        "data_quality.schema_validation",
        "Table 'typo_table' does not exist.",
        details={"type": "invalid_table", "table": "typo_table"},
    )
    b = _v(
        _LONGER_RULE_ID,
        "Table 'Typo_Table' does not exist.",
        details={"type": "invalid_table", "table": "Typo_Table"},
    )
    assert len(deduplicate_violations([a, b])) == 1


def test_type_mismatch_dedup() -> None:
    a = _v(
        "data_quality.column_type_validation",
        "Type mismatch.",
        details={
            "type": "type_mismatch",
            "table": "person",
            "column": "year_of_birth",
            "column_type": "integer",
            "literal_type": "string",
        },
    )
    b = _v(
        "data_quality.column_type_validation",
        "Type mismatch (other phrasing).",
        details={
            "type": "type_mismatch",
            "table": "person",
            "column": "year_of_birth",
            "column_type": "integer",
            "literal_type": "string",
        },
    )
    assert len(deduplicate_violations([a, b])) == 1


def test_structural_dedup_by_layer_and_type() -> None:
    a = _v("x.a", "Parse failed", details={"layer": "structural", "type": "parse_error"})
    b = _v("y.b", "Parse failed again", details={"layer": "structural", "type": "parse_error"})
    assert len(deduplicate_violations([a, b])) == 1


def test_default_key_uses_rule_id_plus_message() -> None:
    a = _v("some.rule", "identical prose")
    b = _v("some.rule", "identical prose")
    different = _v("other.rule", "identical prose")
    out = deduplicate_violations([a, b, different])
    assert len(out) == 2  # a/b collapse, `different` stays


def test_error_beats_warning_at_same_key() -> None:
    warn = _v(
        "r",
        "Column 'foo' does not exist in table 'person'.",
        severity=Severity.WARNING,
        details={"type": "invalid_column", "table": "person", "column": "foo"},
    )
    err = _v(
        "r",
        "Column 'foo' does not exist in table 'person'.",
        severity=Severity.ERROR,
        details={"type": "invalid_column", "table": "person", "column": "foo"},
    )
    out = deduplicate_violations([warn, err])
    assert len(out) == 1
    assert out[0].severity == Severity.ERROR


def test_original_order_preserved() -> None:
    a = _v("rule.a", "A")
    b = _v("rule.b", "B")
    c = _v("rule.c", "C")
    out = deduplicate_violations([a, b, c])
    assert [v.rule_id for v in out] == ["rule.a", "rule.b", "rule.c"]
