"""Registry-wide checks on rule metadata: every rule documents a triggering example, a corrected example and the
published sources its claim is derived from, and the docs page carries the same."""

import re
from pathlib import Path

import pytest

from fastssv.core.base import Reference, RuleExample
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import get_all_rules
from fastssv.core.validation_context import with_strict_mode

RULES = sorted((cls() for cls in get_all_rules()), key=lambda r: r.rule_id)
IDS = [r.rule_id for r in RULES]

# Rules that are silent outside strict mode; their examples are checked with strict mode on.
STRICT_ONLY = {"concept_standardization.invalid_reason_enforcement"}

DOCS = Path(__file__).resolve().parent.parent / "docs" / "rules_reference.md"


def _findings(rule, sql, strict=False):
    if strict or rule.rule_id in STRICT_ONLY:
        with with_strict_mode(True):
            return rule.validate(sql, "postgres")
    return rule.validate(sql, "postgres")


def _docs_sections():
    text = DOCS.read_text(encoding="utf-8")
    sections = {}
    for chunk in re.split(r"\n(?=### \d+\. )", text):
        m = re.match(r"### \d+\. .*?\{ #([\w-]+) \}", chunk)
        if m:
            sections[m.group(1)] = chunk
    return sections


def _anchor(rule_id):
    return rule_id.replace(".", "-").replace("_", "-")


@pytest.mark.parametrize("rule", RULES, ids=IDS)
def test_rule_has_examples(rule):
    assert rule.example_bad and rule.example_bad.strip(), "example_bad is missing"
    assert rule.example_good and rule.example_good.strip(), "example_good is missing"


@pytest.mark.parametrize("rule", RULES, ids=IDS)
def test_example_bad_triggers_rule(rule):
    assert _findings(rule, rule.example_bad), "example_bad does not trigger the rule"


@pytest.mark.parametrize("rule", RULES, ids=IDS)
def test_example_good_is_clean(rule):
    assert not _findings(rule, rule.example_good), "example_good triggers the rule"


@pytest.mark.parametrize("rule", RULES, ids=IDS)
def test_extra_examples_trigger_declared_severity(rule):
    for example in rule.extra_examples:
        assert isinstance(example, RuleExample)
        assert example.note.strip()
        severities = {v.severity for v in _findings(rule, example.sql, example.strict)}
        assert example.severity in severities, f"{example.note!r} does not emit {example.severity.name}"


@pytest.mark.parametrize("rule", RULES, ids=IDS)
def test_rule_cites_sources(rule):
    assert rule.references, "references is empty"
    urls = [ref.url for ref in rule.references]
    assert len(urls) == len(set(urls)), "duplicate reference URL"
    for ref in rule.references:
        assert isinstance(ref, Reference)
        assert ref.title.strip()
        assert ref.url.startswith("https://"), ref.url


@pytest.mark.parametrize("rule", RULES, ids=IDS)
def test_docs_section_has_examples_and_sources(rule):
    section = _docs_sections().get(_anchor(rule.rule_id))
    assert section is not None, f"docs/rules_reference.md has no section #{_anchor(rule.rule_id)}"
    assert "#### Examples" in section
    assert "#### Sources" in section
    for ref in rule.references:
        assert ref.url in section, f"docs section does not cite {ref.url}"


_PARITY_DIALECTS = ("postgres", "duckdb", "tsql")


@pytest.mark.parametrize("rule", RULES, ids=IDS)
def test_examples_give_identical_findings_on_every_dialect(rule):
    """The dialect is only used to parse: every example yields the same findings wherever it parses."""
    examples = [(rule.example_bad, False), (rule.example_good, False)]
    examples += [(e.sql, e.strict) for e in rule.extra_examples]
    for sql, strict in examples:
        by_dialect = {}
        for dialect in _PARITY_DIALECTS:
            if parse_sql(sql, dialect)[1]:
                continue
            with with_strict_mode(strict or rule.rule_id in STRICT_ONLY):
                found = rule.validate(sql, dialect)
            by_dialect[dialect] = sorted((v.rule_id, v.severity.name, v.message) for v in found)
        assert len({tuple(f) for f in by_dialect.values()}) <= 1, f"{sql!r}: {by_dialect}"
