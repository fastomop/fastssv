"""Cost Event ID Polymorphic Resolution Rule.

``cost.cost_event_id`` is "a foreign key identifier to the event (e.g. Measurement, Procedure, Visit, Drug Exposure,
etc) record for which cost data are recorded", and ``cost.cost_domain_id`` is the domain "from which the
corresponding table can be inferred" (CDM v5.4, COST). The same integer can therefore denote a drug exposure in one
cost row and a visit in another; only ``cost_domain_id`` says which.

For every comparison that matches ``cost_event_id`` with a column, a subquery or a literal (in WHERE, JOIN ON, HAVING
or USING), the rule collects the restrictions of ``cost_domain_id`` that hold for the same cost row at that point.
Restrictions are traced through CTEs, derived tables and casts; one under an OR counts only inside its own operand,
one in an outer join's ON counts only for that join, and one on another read of ``cost`` does not count.

- ERROR: the key is matched with an event table's primary key and the restriction rules out that table's domain
  (``cost_domain_id = 'Procedure'`` joined to ``drug_exposure``, ``NOT (cost_domain_id = 'Drug')``). On any CDM every
  row such a join returns pairs a cost with an unrelated event that happens to share its id.
- WARNING: no restriction. The result is wrong only when the cost table holds costs of several domains.
- WARNING: the restriction admits the right domain and other listed domains (``IN ('Drug', 'Procedure')``).

A restriction whose value the SQL does not show (parameter, subquery, a column filtered elsewhere) is accepted, and
so is a number (``cost_domain_id = 13``), which shows no domain and is a type error that
data_quality.column_type_validation reports. A literal that differs from the domain_id only in case (``'drug'``)
counts as naming that domain; data_quality.canonical_string_value_validation reports the casing. NULL tests on
``cost_event_id`` do not interpret the key and are not reported.
"""

from dataclasses import replace
from typing import List, Optional, Tuple


from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import parse_sql
from fastssv.core.references import BOOK_VOCABULARIES, cdm_table, section
from fastssv.core.registry import register

from .._ds1_cdm_facts import COST_EVENT_TABLES
from .._ds1_polymorphic_keys import EXCLUDED, OPAQUE, PATTERN, VALUES, KeyUse, Restriction, key_uses, like_matches

COST = "cost"
COST_EVENT_ID = "cost_event_id"
COST_DOMAIN_ID = "cost_domain_id"


def _folded(value: str, fold: Optional[str]) -> str:
    return value.lower() if fold == "lower" else value.upper() if fold == "upper" else value


def _admits(r: Restriction, expected: str, ignore_case: bool) -> bool:
    """Whether restriction ``r`` lets a row with ``cost_domain_id = expected`` through."""
    if r.kind == VALUES:
        for text, is_string in r.literals:
            if not is_string:
                continue
            if r.fold in ("lower", "upper") and text != _folded(text, r.fold):
                continue  # LOWER(x) = 'Drug' holds for no value of x
            if ignore_case or r.fold == "ilike":
                if text.lower() == expected.lower():
                    return True
            elif text == _folded(expected, r.fold):
                return True
        return False
    if r.kind == PATTERN:
        pattern = r.literals[0][0]
        return like_matches(pattern, _folded(expected, r.fold), ignore_case or r.fold == "ilike")
    return True


def _shown(r: Restriction) -> str:
    return ", ".join(f"'{t}'" if s else t for t, s in r.literals)


def _event_target(use: KeyUse) -> Optional[Tuple[str, str, str]]:
    for table, column in sorted(use.targets):
        info = COST_EVENT_TABLES.get(table)
        if info and info[0] == column:
            return table, column, info[1]
    return None


def _readable(r: Restriction) -> Restriction:
    """``r`` without number literals, which are a type error on varchar cost_domain_id
    (data_quality.column_type_validation); with none left it shows no domain and is treated as opaque."""
    if r.kind not in (VALUES, EXCLUDED) or all(is_string for _, is_string in r.literals):
        return r
    strings = tuple(lit for lit in r.literals if lit[1])
    return replace(r, kind=r.kind if strings else OPAQUE, literals=strings)


def _judge(use: KeyUse) -> Optional[Tuple[str, Severity]]:
    restrictions = [_readable(r) for r in use.restrictions]
    if any(r.kind == OPAQUE for r in restrictions):
        return None
    positive = [r for r in restrictions if r.kind in (VALUES, PATTERN)]
    excluded = [r for r in restrictions if r.kind == EXCLUDED]
    target = _event_target(use)
    if target is None:
        if positive:
            return None
        return (
            f"`{use.text}` is used without restricting {COST_DOMAIN_ID}. {COST_EVENT_ID} holds the id of a row in "
            f"whichever table {COST_DOMAIN_ID} names, so where the cost table holds costs of several domains the same "
            f"id can denote unrelated events.",
            Severity.WARNING,
        )
    table, column, expected = target
    pair = f"`{use.text}` is matched with {table}.{column}"
    wrong = (
        f"Costs of {table} rows carry {COST_DOMAIN_ID} = '{expected}', so every row this returns pairs the cost of "
        f"another kind of event with an unrelated {table} row that shares its id."
    )
    for r in excluded:
        if _admits(Restriction(r.node, r.paths, VALUES, r.literals, r.fold), expected, ignore_case=False):
            return f"{pair}, but {COST_DOMAIN_ID} is restricted to exclude {_shown(r)}. {wrong}", Severity.ERROR
    if not positive:
        return (
            f"{pair} without restricting {COST_DOMAIN_ID} to '{expected}'. {COST_EVENT_ID} holds the id of a row in "
            f"whichever table {COST_DOMAIN_ID} names; where the cost table holds costs of several domains, costs of "
            f"other events whose id equals a {table} id are joined too.",
            Severity.WARNING,
        )
    for r in positive:
        if r.kind == VALUES and r.fold in ("lower", "upper") and not _admits(r, expected, ignore_case=True):
            function = "LOWER" if r.fold == "lower" else "UPPER"
            return (
                f"{function}({COST_DOMAIN_ID}) is compared with {_shown(r)}, which no {r.fold}-cased value equals, "
                f"so the condition holds for no row and the query on {use.text} returns nothing.",
                Severity.ERROR,
            )
        if not _admits(r, expected, ignore_case=True):
            return f"{pair}, but {COST_DOMAIN_ID} is restricted to {_shown(r)}. {wrong}", Severity.ERROR
    # A literal that differs from the domain_id only in case names the right domain; its casing is
    # data_quality.canonical_string_value_validation's finding.
    pinned = any(
        r.kind == PATTERN
        or all(_admits(Restriction(r.node, r.paths, VALUES, (lit,), r.fold), expected, True) for lit in r.literals)
        for r in positive
    )
    if not pinned:
        listed = next(r for r in positive if r.kind == VALUES)
        return (
            f"{COST_DOMAIN_ID} is restricted to {_shown(listed)} while {pair}; costs of the other listed domains "
            f"whose event id equals a {table} id are joined too. Restrict it to '{expected}' alone.",
            Severity.WARNING,
        )
    return None


@register
class CostEventIdPolymorphicResolutionRule(Rule):
    """cost_event_id matched without, or against, the cost_domain_id of the table it is matched with."""

    rule_id = "domain_specific.cost_event_id_polymorphic_resolution"
    name = "Cost Event ID Polymorphic Resolution"

    description = (
        "cost.cost_event_id holds the id of a row in whichever table cost.cost_domain_id names. Matching it with an "
        "event table while cost_domain_id is restricted to another domain pairs costs with unrelated events (ERROR); "
        "matching it without a cost_domain_id restriction is wrong wherever the cost table holds several domains "
        "(WARNING)."
    )

    severity = Severity.ERROR

    suggested_fix = (
        "ADD: a restriction on the same cost row (or correct the existing one): `c.cost_domain_id = '<Domain>'` with the case-exact "
        "domain_id of the table matched with cost_event_id ('Drug' for drug_exposure, 'Procedure' for "
        "procedure_occurrence, 'Visit' for visit_occurrence and visit_detail, 'Device' for device_exposure, …)."
    )
    long_description = (
        "cost.cost_event_id has no single foreign-key target: the CDM says the table it points into is inferred "
        "from cost.cost_domain_id. When the query matches cost_event_id with drug_exposure_id but keeps only "
        "'Procedure' costs, every returned row is a procedure cost paired with whichever drug exposure happens to "
        "have the same id, on any CDM. Without any cost_domain_id restriction the join is right only while the cost "
        "table holds a single domain, so that case is a warning. A wrong-case domain id ('drug') is left to "
        "data_quality.canonical_string_value_validation, and a number compared with cost_domain_id to "
        "data_quality.column_type_validation."
    )

    example_bad = (
        "SELECT c.cost_id, de.drug_concept_id\n"
        "FROM cost c\n"
        "JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id\n"
        "WHERE c.cost_domain_id = 'Procedure';"
    )
    example_good = (
        "SELECT c.cost_id, de.drug_concept_id\n"
        "FROM cost c\n"
        "JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id\n"
        "WHERE c.cost_domain_id = 'Drug';"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT c.cost_id, de.drug_concept_id\n"
                "FROM cost c\n"
                "JOIN drug_exposure de ON c.cost_event_id = de.drug_exposure_id;"
            ),
            severity=Severity.WARNING,
            note="no cost_domain_id restriction: wrong wherever the cost table holds costs of several domains",
        ),
    )
    references = (
        cdm_table("cost"),
        section(BOOK_VOCABULARIES, "conceptDomains", "Domains"),
        Reference(
            "PostgreSQL documentation: Nondeterministic Collations",
            "https://www.postgresql.org/docs/current/collation.html#COLLATION-NONDETERMINISTIC",
        ),
        Reference(
            "DuckDB documentation: Collations",
            "https://duckdb.org/docs/stable/sql/expressions/collations",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = (sql or "").lower()
        if COST not in lowered or COST_EVENT_ID not in lowered:
            return []
        trees, err = parse_sql(sql, dialect)
        if err:
            return []
        violations: List[RuleViolation] = []
        seen = set()
        for tree in trees:
            if tree is None:
                continue
            for use in key_uses(tree, COST, COST_EVENT_ID, COST_DOMAIN_ID):
                verdict = _judge(use)
                if verdict is None or verdict[0] in seen:
                    continue
                seen.add(verdict[0])
                message, severity = verdict
                violations.append(
                    self.create_violation(
                        message=message, severity=severity, details={"table": COST, "column": COST_EVENT_ID}
                    )
                )
        return violations


__all__ = ["CostEventIdPolymorphicResolutionRule"]
