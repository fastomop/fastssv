"""Canonical Vocabulary String Value Validation Rule.

vocabulary_id, domain_id and concept_class_id are "short case-sensitive unique alphanumeric" IDs (Book of OHDSI,
ch. 5). A literal that names one of them with the wrong case, or with dashes the ID omits ('ICD-10-CM' for
'ICD10CM'), matches no row on an engine that compares strings case-sensitively, and still matches no row on any
engine when the spelling differs.

The known IDs are those of the Athena release named in ``_dq_vocab_ids.py`` (113 vocabularies, 50 domains, 433
concept classes); the columns that hold them are the keys of VOCABULARY, DOMAIN and CONCEPT_CLASS and every CDM v5.4
column with a foreign key to one of them (concept.vocabulary_id, source_to_concept_map.target_vocabulary_id,
cost.cost_domain_id, ...). A column is checked only when its lineage is that CDM column, followed through CTEs,
derived tables and casts; a value recomputed on the way (``LOWER(domain_id) AS domain_id``) is not.

Flagged, wherever the comparison appears (``=``, ``<>``, ``IN``, ``NOT IN``, ``LIKE`` without wildcards, BETWEEN):
    * ERROR on PostgreSQL and DuckDB, which compare case-sensitively, when the literal differs from a known ID only
      in letter case ('snomed' for 'SNOMED');
    * WARNING on other dialects for the same: SQL Server and MySQL compare case-insensitively under their default
      collations, so the query works there but not on case-sensitive engines;
    * WARNING when the literal is a known ID written with dashes, spaces or underscores the ID does not have.
    The message follows the operator: ``=`` / IN with such a value match no row, while ``<>`` / NOT IN exclude
    nothing and keep every row.
Not flagged: ILIKE and comparisons on LOWER / UPPER of the column (case-insensitive by construction), and values
that are not a variant of any known ID (sites may load local vocabularies).
"""

from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import DERIVED, column_origins, parse_sql
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.references import BOOK_SQL, BOOK_VOCABULARIES, section
from fastssv.core.registry import register

from ._dq_vocab_ids import (
    CONCEPT_CLASS_ID_COLUMNS,
    CONCEPT_CLASS_IDS,
    DOMAIN_ID_COLUMNS,
    DOMAIN_IDS,
    VOCABULARY_ID_COLUMNS,
    VOCABULARY_IDS,
)

_CASE_SENSITIVE = {"postgres", "postgresql", "duckdb"}


def _key(value: str) -> str:
    return value.lower().replace(" ", "").replace("-", "").replace("_", "")


def _index(ids: FrozenSet[str]) -> Dict[str, str]:
    return {_key(i): i for i in ids}


_KINDS: Dict[Tuple[str, str], Tuple[str, Dict[str, str], FrozenSet[str]]] = {}
for _cols, _label, _ids in (
    (VOCABULARY_ID_COLUMNS, "vocabulary_id", VOCABULARY_IDS),
    (DOMAIN_ID_COLUMNS, "domain_id", DOMAIN_IDS),
    (CONCEPT_CLASS_ID_COLUMNS, "concept_class_id", CONCEPT_CLASS_IDS),
):
    for _col in _cols:
        _KINDS[_col] = (_label, _index(_ids), _ids)

_TARGET_NAMES = {c for _, c in _KINDS}
_CASE_FOLDING = (exp.Lower, exp.Upper)


def _target(node: exp.Expression) -> Optional[Tuple[str, Dict[str, str], FrozenSet[str]]]:
    """(label, normalised index, ids) when ``node`` reads, unchanged, a CDM column that holds vocabulary IDs."""
    while isinstance(node, (exp.Paren, exp.Cast)) and not isinstance(node, exp.TryCast):
        node = node.this
    if not isinstance(node, exp.Column):
        return None
    origins = column_origins(node)
    if not origins or any(t == DERIVED for t, _ in origins):
        return None
    kinds = [_KINDS.get(o) for o in origins]
    if any(k is None for k in kinds) or len({k[0] for k in kinds}) != 1:
        return None
    return kinds[0]


def _string(node: Optional[exp.Expression]) -> Optional[str]:
    while isinstance(node, (exp.Paren, exp.Cast)):
        node = node.this
    return str(node.this) if isinstance(node, exp.Literal) and node.is_string else None


def _negated(node: exp.Expression) -> bool:
    parent = node.parent
    while isinstance(parent, exp.Paren):
        parent = parent.parent
    return isinstance(parent, exp.Not)


def _comparisons(tree: exp.Expression) -> List[Tuple[exp.Expression, List[exp.Expression], bool, bool]]:
    """(column side, compared values, is LIKE, excludes) for every comparison in the statement. ``excludes`` marks
    ``<>``, NOT IN and a negated ``=`` / LIKE: a value no row holds makes such a filter keep every row."""
    out: List[Tuple[exp.Expression, List[exp.Expression], bool, bool]] = []
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.In, exp.Between, exp.Like):
        excludes = isinstance(node, exp.NEQ) != _negated(node)
        if isinstance(node, exp.In):
            out.append((node.this, list(node.expressions or []), False, excludes))
        elif isinstance(node, exp.Between):
            bounds = [b for b in (node.args.get("low"), node.args.get("high")) if b is not None]
            out.append((node.this, bounds, False, False))
        elif isinstance(node, exp.Like):
            out.append((node.this, [node.expression], True, excludes))
        else:
            out.append((node.this, [node.expression], False, excludes))
            out.append((node.expression, [node.this], False, excludes))
    return out


@register
class CanonicalStringValueValidationRule(Rule):
    """Literal vocabulary_id / domain_id / concept_class_id values with the wrong case or spelling."""

    rule_id = "data_quality.canonical_string_value_validation"
    name = "Canonical Vocabulary String Value Validation"

    description = (
        "Filters on vocabulary_id, domain_id and concept_class_id must spell the ID exactly ('SNOMED', 'Condition', "
        "'Ingredient'): the IDs are case-sensitive, so a wrong case matches no row on case-sensitive engines "
        "(PostgreSQL, DuckDB), and an ID written with dashes it does not have ('ICD-10-CM') matches no row anywhere."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "REPLACE: the string literal WITH the vocabulary ID as the vocabulary spells it, e.g. 'snomed' -> 'SNOMED', "
        "'icd10cm' -> 'ICD10CM', 'ICD-10-CM' -> 'ICD10CM', 'condition' -> 'Condition', 'ingredient' -> 'Ingredient'."
    )
    long_description = (
        "vocabulary_id, domain_id and concept_class_id are short case-sensitive IDs. On PostgreSQL and DuckDB, "
        "which compare strings case-sensitively, vocabulary_id = 'snomed' matches no row: an ERROR. SQL Server "
        "and MySQL compare case-insensitively under their default collations, so on those dialects the same filter "
        "works but is not portable: a WARNING. Vocabulary IDs generally omit the dashes of the vocabulary's name "
        "('ICD-9-CM' is 'ICD9CM'), so a dashed form of a known ID is reported as a WARNING. The known IDs come from "
        "an Athena release; values that are not a variant of any of them are not judged, since sites may load "
        "local vocabularies. ILIKE and comparisons on LOWER() or UPPER() of the column are case-insensitive and "
        "are not flagged."
    )

    example_bad = "SELECT concept_id\nFROM concept\nWHERE vocabulary_id = 'snomed';"
    example_good = "SELECT concept_id\nFROM concept\nWHERE vocabulary_id = 'SNOMED';"
    extra_examples = (
        RuleExample(
            sql="SELECT concept_id\nFROM concept\nWHERE vocabulary_id = 'ICD-10-CM';",
            severity=Severity.WARNING,
            note="A known vocabulary ID written with dashes it does not have; the ID is 'ICD10CM'.",
        ),
    )
    references = (
        section(BOOK_VOCABULARIES, "vocabularies", "Vocabularies"),
        section(BOOK_VOCABULARIES, "conceptDomains", "Domains"),
        section(BOOK_VOCABULARIES, "concept-classes", "Concept classes"),
        section(BOOK_SQL, "case-sensitivity-in-string-comparisons", "Case sensitivity in string comparisons"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if not any(name in lowered for name in _TARGET_NAMES):
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue
            seen: Set[Tuple[str, str]] = set()
            for column_side, values, is_like, excludes in _comparisons(tree):
                if isinstance(column_side, _CASE_FOLDING):
                    continue
                target = _target(column_side)
                if target is None:
                    continue
                label, index, ids = target
                for node in values:
                    value = _string(node)
                    if value is None or value in ids or (label, value) in seen:
                        continue
                    if is_like and ("%" in value or "_" in value):
                        continue
                    expected = index.get(_key(value))
                    if expected is None:
                        continue
                    seen.add((label, value))
                    violations.append(self._violation(sql, label, value, expected, dialect, excludes))
        return violations

    def _violation(
        self, sql: str, label: str, value: str, expected: str, dialect: str, excludes: bool = False
    ) -> RuleViolation:
        # A negated filter on a value no row holds excludes nothing, so it keeps every row instead of matching none.
        outcome = (
            f"the filter excludes nothing: it keeps every row, including the '{expected}' rows"
            if excludes
            else "the filter matches no row"
        )
        if value.lower() == expected.lower():
            issue = "case"
            if dialect in _CASE_SENSITIVE:
                severity = Severity.ERROR
                message = (
                    f"Incorrect {label} casing: '{value}'. The ID is '{expected}', and this dialect compares "
                    f"strings case-sensitively, so {outcome}."
                )
            else:
                severity = Severity.WARNING
                elsewhere = "excludes nothing" if excludes else "matches no row"
                message = (
                    f"Incorrect {label} casing: '{value}'. The ID is '{expected}'. This dialect's default collation "
                    f"compares case-insensitively, so the filter works here but {elsewhere} on case-sensitive "
                    f"engines (PostgreSQL, DuckDB)."
                )
        else:
            issue = "spelling"
            severity = Severity.WARNING
            message = (
                f"{label} '{value}' is not an ID in the vocabulary; the ID is '{expected}'. Vocabulary IDs generally "
                f"omit the hyphens and spaces of the vocabulary's name, and the case must match. Unless the database "
                f"has a local ID spelled '{value}', {outcome}."
            )
        patch = None
        for q in ("'", '"'):
            span = locate(sql, f"{q}{value}{q}")
            if span is not None:
                patch = patch_replace(span, f"{q}{expected}{q}")
                break
        return self.create_violation(
            message=message,
            severity=severity,
            suggested_fix=f"REPLACE: `'{value}'` WITH `'{expected}'`.",
            suggested_fix_patch=patch,
            details={"column": label, "provided": value, "expected": expected, "issue": issue},
        )


__all__ = ["CanonicalStringValueValidationRule"]
