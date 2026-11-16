"""Concept to Vocabulary / Domain / Concept Class Join Validation Rule.

CONCEPT references the three metadata tables through foreign keys (CDM v5.4): ``concept.vocabulary_id`` ->
VOCABULARY, ``concept.domain_id`` -> DOMAIN, ``concept.concept_class_id`` -> CONCEPT_CLASS. Each metadata table also
points back at the concept that represents its row (``vocabulary_concept_id``, ``domain_concept_id``,
``concept_class_concept_id`` -> CONCEPT). Other tables reference them too (``source_to_concept_map.target_vocabulary_id``,
``cost.cost_domain_id``).

* WARNING: a foreign key compared with the name column of the table it references (``concept.domain_id =
  domain.domain_name``). The join returns correct rows wherever the name equals the id: in Athena v5.0 27-AUG-25
  that holds for 2 of 113 vocabularies, 30 of 50 domains (every main clinical domain) and 208 of 433 concept
  classes, and the other rows are dropped. Whether that matters depends on which rows the query needs.
* ERROR: a foreign key compared with another non-key column of that table (``concept.vocabulary_id =
  vocabulary.vocabulary_version``). No value of such a column is an id, so the join never pairs the right rows.
* WARNING: any other column pair between the two tables (``concept.vocabulary_id = concept_class.concept_class_id``),
  unless the same two sources are already linked by a foreign key in that SELECT. Such a pair can match by design
  (vocabularies without classes use the vocabulary_id as the class), so it is not an error.

Operands are resolved by lineage through casts, LOWER / UPPER / TRIM, CTEs, derived tables, set operations and
IN-subqueries, in every SELECT.

Violation patterns:
    SELECT * FROM concept c JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_version      -- ERROR
    SELECT * FROM concept c JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_name         -- WARNING
    SELECT * FROM concept c JOIN domain d ON c.domain_id = d.domain_name                     -- WARNING

Correct patterns:
    SELECT * FROM concept c JOIN vocabulary v ON c.vocabulary_id = v.vocabulary_id
    SELECT v.vocabulary_id, mc.concept_name FROM vocabulary v JOIN concept mc ON mc.concept_id = v.vocabulary_concept_id
"""

from typing import List, Set, Tuple

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.patch import build_join_replace_patch
from fastssv.core.references import CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from ._j1_reference_joins import Finding, expected_link, reference_findings
from ._join_scope import hoist_joins
from ._person_keys import FOREIGN_KEYS

METADATA_TABLES = {"vocabulary", "domain", "concept_class"}

# Rows of each reference table whose name equals its id, out of all rows (Athena v5.0 27-AUG-25). A key compared
# with the name column matches exactly those rows, so the join is right for them and drops the rest; the other
# non-key columns (vocabulary_version, vocabulary_reference, is_hierarchical, defines_ancestry) never equal an id.
_NAME_EQUALS_ID = {"vocabulary": (2, 113), "domain": (30, 50), "concept_class": (208, 433), "relationship": (72, 722)}
_NAME_NOTE = {
    "domain": " (every main clinical domain: Condition, Drug, Procedure, Measurement, Observation, Device, Visit, "
    "Specimen, Note)"
}


def _name_side(finding: Finding):
    """The operand that is its table's name column, when the key misuse compares a key with a name."""
    for side in (finding.reference, finding.partner):
        if side.table in _NAME_EQUALS_ID and side.name == f"{side.table}_name":
            return side
    return None


def misuse_patch(sql: str, finding: Finding, fallback: str):
    """REPLACE the non-key operand of a key misuse with the key its partner references."""
    a, b = finding.partner, finding.reference
    for fk_side, other in ((a, b), (b, a)):
        target = FOREIGN_KEYS.get(fk_side.origin)
        if target is None or target[0] != other.table:
            continue
        if fk_side.column is None or other.column is None or not fk_side.column.table or not other.column.table:
            return None
        return build_join_replace_patch(
            sql,
            fk_side.column.table,
            fk_side.column.name,
            other.column.table,
            other.column.name,
            fk_side.column.name,
            target[1],
            fallback,
        )
    return None


def report(rule: Rule, sql: str, dialect: str, references: Set[str]) -> List[RuleViolation]:
    trees, err = parse_sql(sql, dialect)
    if err:
        return []
    violations: List[RuleViolation] = []
    seen: Set[Tuple[str, bool]] = set()
    for tree in trees:
        if not tree:
            continue
        tree = hoist_joins(tree)
        for finding in reference_findings(tree, references):
            text = finding.eq.sql()
            if (text, finding.error) in seen:
                continue
            seen.add((text, finding.error))
            p, r = finding.partner, finding.reference
            link = expected_link(p, r) or expected_link(r, p) or "the foreign-key pair"
            fix = f"REPLACE: `{text}` WITH `{link}`."
            name_side = _name_side(finding) if finding.error else None
            if name_side is not None:
                matched, total = _NAME_EQUALS_ID[name_side.table]
                violations.append(
                    rule.create_violation(
                        message=(
                            f"Key compared with a name: {text}. {name_side.table}.{name_side.name} is the display "
                            f"name, not the key; expected {link}. The join returns correct rows wherever a name "
                            f"equals its id, which in Athena v5.0 27-AUG-25 holds for {matched} of {total} "
                            f"{name_side.table} rows{_NAME_NOTE.get(name_side.table, '')}; rows of the others are "
                            "dropped."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=fix,
                        suggested_fix_patch=misuse_patch(sql, finding, fix),
                        details={"partner": f"{p.table}.{p.name}", "reference": f"{r.table}.{r.name}"},
                    )
                )
            elif finding.error:
                violations.append(
                    rule.create_violation(
                        message=(
                            f"Invalid join: {text}. {p.table}.{p.name} is compared with {r.table}.{r.name}, which is "
                            f"not the key it references and never holds an id, so the join never pairs the right "
                            f"rows; expected {link}."
                        ),
                        suggested_fix=fix,
                        suggested_fix_patch=misuse_patch(sql, finding, fix),
                        details={"partner": f"{p.table}.{p.name}", "reference": f"{r.table}.{r.name}"},
                    )
                )
            else:
                violations.append(
                    rule.create_violation(
                        message=(
                            f"Suspicious join: {text}. {p.table}.{p.name} and {r.table}.{r.name} hold different kinds "
                            f"of values and are not a foreign-key pair; the tables are linked by {link}. Keep it only "
                            f"if the match is deliberate."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=fix,
                        details={"partner": f"{p.table}.{p.name}", "reference": f"{r.table}.{r.name}"},
                    )
                )
    return violations


@register
class ConceptMetadataJoinValidationRule(Rule):
    """Joins to vocabulary, domain and concept_class must use their foreign keys."""

    rule_id = "joins.concept_metadata_join_validation"
    name = "Concept to Vocabulary, Domain and Concept Class Join Validation"

    description = (
        "Joins to vocabulary, domain and concept_class must use their foreign keys (concept.vocabulary_id = "
        "vocabulary.vocabulary_id, ..., or <table>_concept_id = concept.concept_id). A foreign key compared with a "
        "non-key column that never holds an id (vocabulary_version) is an ERROR; compared with the name column, "
        "which equals the id for some rows, and other column pairs are WARNINGs."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "REPLACE: the ON clause WITH the foreign-key pair: `concept.vocabulary_id = vocabulary.vocabulary_id`, "
        "`concept.domain_id = domain.domain_id` or `concept.concept_class_id = concept_class.concept_class_id`."
    )

    example_bad = "SELECT c.concept_id FROM concept c\nJOIN vocabulary v ON c.vocabulary_id = v.vocabulary_version;"
    example_good = "SELECT c.concept_id FROM concept c\nJOIN vocabulary v ON c.vocabulary_id = v.vocabulary_id;"
    extra_examples = (
        RuleExample(
            sql="SELECT c.concept_id FROM concept c\nJOIN concept_class cc ON c.concept_class_id = cc.concept_class_name;",
            severity=Severity.WARNING,
            note="The concept_class key compared with the class name: right only where the name equals the id.",
        ),
        RuleExample(
            sql="SELECT c.concept_id FROM concept c\nJOIN domain d ON c.domain_id = d.domain_name;",
            severity=Severity.WARNING,
            note="The domain key compared with the domain name: right for every main clinical domain, not for all.",
        ),
        RuleExample(
            sql="SELECT c.concept_id FROM concept c\nJOIN vocabulary v ON c.domain_id = v.vocabulary_id;",
            severity=Severity.WARNING,
            note="Another concept column (domain_id) matched against vocabulary_id.",
        ),
    )
    references = (
        cdm_table("concept"),
        cdm_table("vocabulary"),
        cdm_table("domain"),
        cdm_table("concept_class"),
        section(CDM_CONVENTIONS, "vocabularies", "Vocabularies"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if not any(t in lowered for t in METADATA_TABLES):
            return []
        return report(self, sql, dialect, METADATA_TABLES)


__all__ = ["ConceptMetadataJoinValidationRule", "misuse_patch", "report"]
