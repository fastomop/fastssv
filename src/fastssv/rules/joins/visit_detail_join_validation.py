"""Visit Detail Join Validation Rule.

OMOP semantic rule OMOP_034:
visit_detail records are nested within visit_occurrence. Queries using visit_detail
should join to visit_occurrence via visit_detail.visit_occurrence_id =
visit_occurrence.visit_occurrence_id, not via person_id alone.

Joining only on person_id creates a cartesian-like join where every visit_detail
for a person joins to EVERY visit_occurrence for that person, producing incorrect
results and performance issues.

Correct pattern:
    FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id

Incorrect pattern:
    FROM visit_detail vd
    JOIN visit_occurrence vo ON vd.person_id = vo.person_id
"""

from typing import Dict, List, Set

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


VD = "visit_detail"
VO = "visit_occurrence"
JOIN_KEY = "visit_occurrence_id"


# --- Helpers ---------------------------------------------------------------

def _is_vd(table: str) -> bool:
    return normalize_name(table) == VD


def _is_vo(table: str) -> bool:
    return normalize_name(table) == VO


def _is_correct_join(left, right, aliases) -> bool:
    """Check vd.visit_occurrence_id = vo.visit_occurrence_id"""
    if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
        return False

    lt, lc = resolve_table_col(left, aliases)
    rt, rc = resolve_table_col(right, aliases)

    if not (lt and lc and rt and rc):
        return False

    lt, lc = normalize_name(lt), normalize_name(lc)
    rt, rc = normalize_name(rt), normalize_name(rc)

    return (
        lc == JOIN_KEY and rc == JOIN_KEY and
        ((_is_vd(lt) and _is_vo(rt)) or (_is_vo(lt) and _is_vd(rt)))
    )


def _join_contains_correct_key(join: exp.Join, aliases: Dict[str, str]) -> bool:
    """Check if JOIN ON contains vd ↔ vo visit_occurrence_id equality"""
    on = join.args.get("on")
    if not on:
        return False

    for eq in on.find_all(exp.EQ):
        if _is_correct_join(eq.this, eq.expression, aliases):
            return True

    return False


def _join_uses_using_key(join: exp.Join) -> bool:
    """Check USING (visit_occurrence_id)"""
    using = join.args.get("using")
    if not using:
        return False

    cols = [normalize_name(c.name) for c in using]
    return JOIN_KEY in cols


def _is_vd_vo_join(join: exp.Join, aliases: Dict[str, str]) -> bool:
    """Check if join involves both visit_detail and visit_occurrence"""
    tables: Set[str] = set()

    # Right side table
    if join.this:
        alias = join.this.alias_or_name
        table = aliases.get(alias, alias)
        tables.add(normalize_name(table))

    # Tables referenced in ON clause
    on = join.args.get("on")
    if on:
        for col in on.find_all(exp.Column):
            if col.table:
                alias = str(col.table)
                table = aliases.get(alias, alias)
                tables.add(normalize_name(table))

    return VD in tables and VO in tables


# --- Core detection --------------------------------------------------------

def _find_invalid_joins(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues = []
    seen = set()

    for join in tree.find_all(exp.Join):
        if not _is_vd_vo_join(join, aliases):
            continue

        has_valid_join = (
            _join_contains_correct_key(join, aliases)
            or _join_uses_using_key(join)
        )

        if has_valid_join:
            continue

        key = join.sql()
        if key in seen:
            continue
        seen.add(key)

        issues.append(
            f"visit_detail joined to visit_occurrence without {JOIN_KEY}. "
            f"This may produce many-to-many joins and duplicate records. "
            f"Use: vd.{JOIN_KEY} = vo.{JOIN_KEY}"
        )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class VisitDetailJoinValidationRule(Rule):
    """Validates that visit_detail joins to visit_occurrence correctly."""

    rule_id = "joins.visit_detail_join_validation"
    name = "Visit Detail Join Validation"
    description = (
        "Ensures visit_detail joins to visit_occurrence using visit_occurrence_id. "
        "Joining only on person_id can produce incorrect results."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "Join using: vd.visit_occurrence_id = vo.visit_occurrence_id"
    )
    example_bad = (
        "SELECT * FROM visit_detail vd\n"
        "JOIN visit_occurrence vo ON vd.person_id = vo.person_id;"
    )
    example_good = (
        "SELECT * FROM visit_detail vd\n"
        "JOIN visit_occurrence vo ON vd.visit_occurrence_id = vo.visit_occurrence_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            issues = _find_invalid_joins(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["VisitDetailJoinValidationRule"]
