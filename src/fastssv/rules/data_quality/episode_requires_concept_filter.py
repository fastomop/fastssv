"""Episode Requires Concept Filter Rule.

OMOP semantic rules OMOP_240, OMOP_505.

CDM v5.4 ``episode`` holds every kind of episode in one table: disease episodes, treatment regimens, disease
extent, and so on, told apart by ``episode_concept_id``. A query that reads episode rows without restricting that
column, and without returning it, mixes all episode types in one result. Whether that is wrong depends on the data
and the question, so the finding is a WARNING: the CDM specification defines the column but does not require a
filter on it.

Flagged, per ``episode`` instance in a SELECT:
    * ``episode_concept_id`` is neither restricted nor shown.

Flagged, per ``episode_event`` instance:
    * ``episode_id`` is not used at all, so the linked events cannot be tied to an episode type.

``episode_concept_id`` counts as restricted by any non-negated WHERE / ON / HAVING predicate on it (``=``, ``IN``,
``BETWEEN``, a join to a concept-set table), by a filter on a ``concept`` row joined to it, or by a restriction
downstream on a projected copy. ``<>``, ``NOT IN`` and ``IS [NOT] NULL`` let every type through and do not count.
It counts as shown when the SELECT projects or groups by it, or projects a column of the joined ``concept`` row.
``SELECT *`` does not count: the column is there but nothing says the reader looks at it.

Not flagged: a lookup of known rows by primary key (``episode_id = 42``), a read that analyses nothing (episode as
the only source, no WHERE / GROUP BY, and a ``LIMIT`` peek or a whole-table ``COUNT(*)``), constant-false schema
probes (``WHERE 0 = 1``), and DML / DDL targets. Any other aggregate (episodes per person, persons with an episode)
mixes the types like a row-level read and is flagged.

Violation:
    SELECT e.person_id, e.episode_start_date
    FROM episode e
    WHERE e.episode_start_date >= DATE '2020-01-01'

Correct:
    ... AND e.episode_concept_id = 32533   -- Disease Episode
"""

from typing import List

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import cdm_table
from fastssv.core.helpers import parse_sql
from fastssv.core.registry import register

from ._type_column import instances_pinned_by_key, instances_using, unused_instances

EPISODE = "episode"
EPISODE_EVENT = "episode_event"
EPISODE_CONCEPT_ID = "episode_concept_id"
EPISODE_ID = "episode_id"


@register
class EpisodeRequiresConceptFilterRule(Rule):
    """Flag episode reads that mix every episode type."""

    rule_id = "data_quality.episode_requires_concept_filter"
    name = "Episode Requires Concept Filter"

    description = (
        "An episode read that neither restricts nor returns episode_concept_id mixes every episode type (disease "
        "episodes, treatment regimens, ...) in one result."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "ADD: `episode_concept_id = <id>` (or IN (...)) to select one episode type, or add episode_concept_id to "
        "the SELECT list / GROUP BY if every type is wanted."
    )
    long_description = (
        "The episode table stores disease episodes, treatment regimens and other abstractions side by side; "
        "episode_concept_id says which kind a row is. Reading episode without restricting or returning that "
        "column puts all kinds in one result, so a count of 'episodes per person' adds disease phases to "
        "treatment lines. The CDM specification does not require the filter, so this is a warning: restrict "
        "episode_concept_id, or project or group by it when the mix is intended. A lookup by episode_id, a LIMIT "
        "peek and a whole-table COUNT(*) are not flagged; an aggregate such as episodes per person is. "
        "episode_event is flagged when its episode_id is never used, because its "
        "events then cannot be tied to an episode type."
    )
    example_bad = (
        "SELECT e.person_id, e.episode_start_date\nFROM episode e\nWHERE e.episode_start_date >= DATE '2020-01-01';"
    )
    example_good = (
        "SELECT e.person_id, e.episode_start_date\n"
        "FROM episode e\n"
        "WHERE e.episode_concept_id = 32533\n"
        "  AND e.episode_start_date >= DATE '2020-01-01';"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT po.person_id, po.procedure_concept_id\n"
                "FROM episode_event ee\n"
                "JOIN procedure_occurrence po ON ee.event_id = po.procedure_occurrence_id\n"
                "WHERE ee.episode_event_field_concept_id = 1147082;"
            ),
            severity=Severity.WARNING,
            note="episode_event read without using episode_id: procedures linked to episodes of every type.",
        ),
    )
    references = (cdm_table("episode"), cdm_table("episode_event"))

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if EPISODE not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if not tree:
                continue

            typed = instances_using(tree, EPISODE, EPISODE_CONCEPT_ID) | instances_pinned_by_key(
                tree, EPISODE, EPISODE_ID
            )
            for _, alias in unused_instances(tree, EPISODE, typed, {EPISODE_CONCEPT_ID, "episode_type_concept_id"}):
                violations.append(
                    self.create_violation(
                        message=(
                            f"episode '{alias}' is read without restricting episode_concept_id, so rows of every "
                            "episode type (disease episodes, treatment regimens, ...) are mixed. Restrict "
                            "episode_concept_id, or select it if every type is wanted."
                        ),
                        details={"table": EPISODE, "alias": alias},
                    )
                )

            linked = instances_using(tree, EPISODE_EVENT, EPISODE_ID, restrict_only=False)
            for _, alias in unused_instances(
                tree, EPISODE_EVENT, linked, {EPISODE_ID, "episode_event_field_concept_id"}
            ):
                violations.append(
                    self.create_violation(
                        message=(
                            f"episode_event '{alias}' is read without using episode_id, so events linked to "
                            "episodes of every type are mixed. Join episode on episode_id and restrict "
                            "episode_concept_id."
                        ),
                        suggested_fix=(
                            "ADD: `JOIN episode e ON ee.episode_id = e.episode_id` and restrict `e.episode_concept_id`."
                        ),
                        details={"table": EPISODE_EVENT, "alias": alias},
                    )
                )

        return violations


__all__ = ["EpisodeRequiresConceptFilterRule"]
