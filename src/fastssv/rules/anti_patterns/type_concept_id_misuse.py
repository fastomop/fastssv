"""Type Concept ID Misuse Rule.

``*_type_concept_id`` columns record the provenance of a record (EHR, claim, registry ...), and the CDM v5.4
field-level specification gives them the foreign-key domain 'Type Concept'. A comparison of such a column with a
concept id that is not a Type Concept therefore matches no record of a conformant CDM, typically because a
clinical concept was written into the provenance column instead of the primary ``*_concept_id`` column:

    SELECT * FROM condition_occurrence WHERE condition_type_concept_id = 201826   -- 201826 is a Condition concept

Severity:
- ERROR: every compared id is a concept of the vocabulary release outside the 'Type Concept' domain. Concept ids
  are persistent across releases, so the comparison can never hold.
- WARNING: an IN list that mixes Type Concepts with other ids (the others are dead, the rest still filters); an id
  the release does not contain (a newer or a local concept); a column whose domain the specification leaves open
  (``cost_type_concept_id``); and a Type Concept whose legacy class names another table's family
  (``condition_type_concept_id = 38000280``, an 'Observation Type'). The conventions say Type Concepts "have to be
  chosen correctly" for the target table but that nothing enforces it, so such rows are unlikely, not impossible.

The comparison is recognised with the column on either side, through casts and parentheses, through CTE and
derived-table renames, and in ``=``, ``IN``, ``= ANY (ARRAY[...])`` and ``BETWEEN``, in any position (WHERE, ON of
inner and outer joins, HAVING, CASE, FILTER). Negations (``<>``, ``NOT IN``) exclude nothing and are not reported;
comparisons with a column, a subquery or an expression cannot be judged statically.
"""

from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import DERIVED, extract_aliases, normalize_name, parse_sql, resolve_column_source
from fastssv.core.patch import locate, replace
from fastssv.core.references import BOOK_CDM, CDM_CONVENTIONS, section
from fastssv.core.registry import register
from fastssv.rules.anti_patterns._ap_cdm_fields import TYPE_CONCEPT_COLUMNS
from fastssv.rules.anti_patterns._ap_concept_ids import RELEASE, in_release
from fastssv.rules.anti_patterns._type_concept_ids import TYPE_CONCEPT_FAMILY, TYPE_CONCEPT_IDS

# The specification describes cost_type_concept_id as the provenance of the cost data but leaves its domain open.
_OPEN_DOMAIN_COLUMNS = {"cost_type_concept_id": "cost"}
_COLUMNS = {**TYPE_CONCEPT_COLUMNS, **_OPEN_DOMAIN_COLUMNS}

# Clinical column of the same table, for the suggested fix (CDM v5.4 field names).
_PRIMARY_FIELD = {
    "condition_type_concept_id": "condition_concept_id",
    "drug_type_concept_id": "drug_concept_id",
    "procedure_type_concept_id": "procedure_concept_id",
    "measurement_type_concept_id": "measurement_concept_id",
    "observation_type_concept_id": "observation_concept_id",
    "visit_type_concept_id": "visit_concept_id",
    "visit_detail_type_concept_id": "visit_detail_concept_id",
    "device_type_concept_id": "device_concept_id",
    "specimen_type_concept_id": "specimen_concept_id",
    "note_type_concept_id": "note_class_concept_id",
    "death_type_concept_id": "cause_concept_id",
    "episode_type_concept_id": "episode_concept_id",
}

# Legacy concept_class_id of each column's own Type Concept family (classes of the 'Type Concept' domain in the
# release named by _ap_concept_ids.RELEASE).
_OWN_FAMILY = {
    "condition_type_concept_id": "Condition Type",
    "drug_type_concept_id": "Drug Type",
    "procedure_type_concept_id": "Procedure Type",
    "measurement_type_concept_id": "Meas Type",
    "observation_type_concept_id": "Observation Type",
    "visit_type_concept_id": "Visit Type",
    "visit_detail_type_concept_id": "Visit Type",
    "device_type_concept_id": "Device Type",
    "specimen_type_concept_id": "Specimen Type",
    "note_type_concept_id": "Note Type",
    "death_type_concept_id": "Death Type",
    "episode_type_concept_id": "Episode Type",
    "period_type_concept_id": "Obs Period Type",
    "cost_type_concept_id": "Cost Type",
}

_UNWRAP = (exp.Cast, exp.TryCast, exp.Paren)


def _unwrap(node: Optional[exp.Expression]) -> Optional[exp.Expression]:
    while isinstance(node, _UNWRAP):
        node = node.this
    return node


def _int_value(node: Optional[exp.Expression]) -> Optional[int]:
    node = _unwrap(node)
    if isinstance(node, exp.Neg):
        return None
    if isinstance(node, exp.Literal):
        text = str(node.this).strip()
        if text.isdigit():
            return int(text)
    return None


def _type_column(node: Optional[exp.Expression], aliases: Dict[str, str]) -> Optional[Tuple[exp.Column, str]]:
    """(column node, base column name) when ``node`` reads a *_type_concept_id column of the CDM."""
    col = _unwrap(node)
    if not isinstance(col, exp.Column):
        return None
    table, name = resolve_column_source(col, aliases)
    name = normalize_name(name or "")
    if name not in _COLUMNS or table == DERIVED:
        return None
    if table and normalize_name(table) != _COLUMNS[name]:
        # Lineage reached a table, and it is not the one that owns this column (e.g. a non-CDM table).
        return None
    return col, name


def _comparison(node: exp.Expression, aliases: Dict[str, str]):
    """(column, base column, compared literal nodes) for an =, IN, = ANY(ARRAY) or BETWEEN on a type column."""
    if isinstance(node, exp.EQ):
        for col_side, other in ((node.this, node.expression), (node.expression, node.this)):
            found = _type_column(col_side, aliases)
            if found is None:
                continue
            other = _unwrap(other)
            if isinstance(other, exp.Any):
                arr = _unwrap(other.this)
                values = list(arr.expressions) if isinstance(arr, exp.Array) else None
                return (*found, values) if values else None
            return (*found, [other])
        return None
    if isinstance(node, exp.In):
        found = _type_column(node.this, aliases)
        if found is None or node.args.get("query") is not None or not node.expressions:
            return None
        return (*found, list(node.expressions))
    if isinstance(node, exp.Between):
        found = _type_column(node.this, aliases)
        if found is None:
            return None
        low, high = _int_value(node.args.get("low")), _int_value(node.args.get("high"))
        if low is None or high is None:
            return None
        if any(low <= t <= high for t in TYPE_CONCEPT_IDS):
            return None
        return (*found, [node.args.get("low")]) if low == high else (*found, None)
    return None


def _negated(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, exp.Not):
            return True
        parent = parent.parent
    return False


def _in_disjunction(node: exp.Expression) -> bool:
    """A dead branch of an OR leaves the other branches filtering, so the query still returns rows."""
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, exp.Or):
            return True
        parent = parent.parent
    return False


def _context(node: exp.Expression) -> str:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, exp.Join):
            side = (parent.side or "").upper()
            return f"{side} JOIN ON clause" if side else "JOIN ON clause"
        if isinstance(parent, exp.Having):
            return "HAVING clause"
        if isinstance(parent, (exp.Case, exp.If)):
            return "CASE expression"
        if isinstance(parent, exp.Filter):
            return "FILTER clause"
        if isinstance(parent, exp.Where):
            return "WHERE clause"
        parent = parent.parent
    return "expression"


def _classify(values: List[int]) -> Tuple[List[int], List[int], List[Tuple[int, str]], bool]:
    """(known non-Type ids, ids absent from the release, foreign-family Type Concepts, any usable id)."""
    known_other, unknown, foreign = [], [], []
    usable = False
    for v in values:
        if v == 0 or v in TYPE_CONCEPT_IDS:
            usable = True
            family = TYPE_CONCEPT_FAMILY.get(v)
            if v != 0 and family is not None:
                foreign.append((v, family))
        elif in_release(v):
            known_other.append(v)
        else:
            unknown.append(v)
    return known_other, unknown, foreign, usable


@register
class TypeConceptIdMisuseRule(Rule):
    """*_type_concept_id compared with concept ids that are not Type Concepts."""

    rule_id = "anti_patterns.type_concept_id_misuse"
    name = "Type Concept ID Not For Clinical Filtering"
    description = (
        "A *_type_concept_id column (record provenance: EHR, claim, registry) is compared with a concept id that is "
        "not a Type Concept, typically a clinical concept meant for the primary *_concept_id column; nothing matches."
    )
    severity = Severity.ERROR
    suggested_fix = (
        "REPLACE: `<table>.<x>_type_concept_id = <id>` WITH `<table>.<x>_concept_id = <id>`. *_type_concept_id "
        "encodes provenance (EHR, claim, registry), not clinical meaning."
    )
    long_description = (
        "`*_type_concept_id` columns carry provenance (EHR, claim, patient-reported, registry): where the record "
        "came from, not what it means clinically. The CDM v5.4 specification gives them the foreign-key domain "
        "'Type Concept', so a comparison with a concept id outside that domain matches no record of a conformant "
        "CDM. The rule is an ERROR when every compared id is a concept of the embedded vocabulary release outside "
        "the 'Type Concept' domain (concept ids are persistent across releases). It is a WARNING for an IN list "
        "that mixes Type Concepts with other ids, for an id the release does not contain (a newer or local "
        "concept), for cost_type_concept_id (whose domain the specification leaves open), and for a Type Concept "
        "of another table's legacy family. The comparison is found with the column on either side, through casts "
        "and CTE renames, in =, IN, = ANY(ARRAY[...]) and BETWEEN, in WHERE, join ON clauses (inner and outer), "
        "HAVING, CASE and FILTER."
    )
    example_bad = "SELECT person_id\nFROM condition_occurrence\nWHERE condition_type_concept_id = 201826;"
    example_good = "SELECT person_id\nFROM condition_occurrence\nWHERE condition_concept_id = 201820;"
    extra_examples = (
        RuleExample(
            sql="SELECT person_id\nFROM condition_occurrence\nWHERE condition_type_concept_id IN (32817, 201826);",
            severity=Severity.WARNING,
            note="IN list mixing a Type Concept with a clinical concept id: the clinical id never matches",
        ),
        RuleExample(
            sql=(
                "SELECT person_id\n"
                "FROM condition_occurrence\n"
                "WHERE condition_concept_id = 201826\n"
                "  AND condition_type_concept_id = 38000280;"
            ),
            severity=Severity.WARNING,
            note="Type Concept from another table's family (38000280 is an Observation Type)",
        ),
    )
    references = (
        section(CDM_CONVENTIONS, "type-concepts", "Type Concepts"),
        section(BOOK_CDM, "general-naming-conventions-of-fields", "General naming conventions of fields"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            aliases = extract_aliases(tree)
            for node in tree.find_all(exp.EQ, exp.In, exp.Between):
                if _negated(node):
                    continue
                found = _comparison(node, aliases)
                if found is None:
                    continue
                col_node, column, literal_nodes = found
                violation = self._judge(sql, node, col_node, column, literal_nodes)
                if violation is not None:
                    violations.append(violation)
        return violations

    def _judge(self, sql, node, col_node, column, literal_nodes) -> Optional[RuleViolation]:
        context = _context(node)
        if literal_nodes is None:
            low, high = _int_value(node.args.get("low")), _int_value(node.args.get("high"))
            return self.create_violation(
                message=(
                    f"'{column}' BETWEEN {low} AND {high} in {context} admits no Type Concept of vocabulary release "
                    f"{RELEASE}. The column holds record provenance, so the range likely matches nothing."
                ),
                severity=Severity.WARNING,
            )
        values = [_int_value(n) for n in literal_nodes]
        if any(v is None for v in values):
            return None
        known_other, unknown, foreign, usable = _classify(values)
        primary = _PRIMARY_FIELD.get(column, "the primary concept_id column")
        open_domain = column in _OPEN_DOMAIN_COLUMNS

        if known_other or unknown:
            dead = known_other + unknown
            ids = ", ".join(str(v) for v in dead[:6])
            every_known = not usable and not unknown and not open_domain and not _in_disjunction(node)
            if every_known:
                message = (
                    f"'{column}' is compared in {context} with concept id(s) {ids}, which are not Type Concepts "
                    f"(vocabulary release {RELEASE}). The column holds record provenance and its values are Type "
                    f"Concepts, so this comparison matches no record; a clinical concept belongs in '{primary}'."
                )
            elif usable:
                message = (
                    f"'{column}' list in {context} includes id(s) {ids}, which are not Type Concepts of vocabulary "
                    f"release {RELEASE} and match no record; a clinical concept belongs in '{primary}'."
                )
            elif unknown and not known_other:
                message = (
                    f"'{column}' is compared in {context} with id(s) {ids}, which vocabulary release {RELEASE} does "
                    "not contain. If they are not Type Concepts of a newer or local vocabulary, the comparison "
                    f"matches no record; a clinical concept belongs in '{primary}'."
                )
            else:
                message = (
                    f"'{column}' is compared in {context} with concept id(s) {ids}, which are not Type Concepts "
                    f"(vocabulary release {RELEASE}). The specification leaves this column's domain open, but it "
                    f"holds the provenance of the record, so the comparison likely matches nothing."
                )
            patch = None
            if every_known and column in _PRIMARY_FIELD and isinstance(node, (exp.EQ, exp.In)):
                span = locate(sql, node.sql())
                if span is not None:
                    rewritten = node.copy()
                    target = next(c for c in rewritten.find_all(exp.Column) if c.name == col_node.name)
                    target.set("this", exp.to_identifier(_PRIMARY_FIELD[column]))
                    if normalize_name(col_node.name) == column:
                        patch = replace(span, rewritten.sql())
            return self.create_violation(
                message=message,
                severity=Severity.ERROR if every_known else Severity.WARNING,
                suggested_fix_patch=patch,
            )

        own = _OWN_FAMILY.get(column)
        relevant = foreign
        if own is None or not relevant or len(relevant) != len(values) or any(f == own for _, f in relevant):
            return None
        families = " / ".join(sorted({f for _, f in relevant}))
        ids = ", ".join(str(v) for v, _ in relevant[:6])
        table = _COLUMNS[column]
        return self.create_violation(
            message=(
                f"'{column}' is compared in {context} with Type Concept(s) {ids} of the legacy {families} family. "
                f"The conventions ask for Type Concepts matching the target table ({table}: {own}, or the standard "
                "concepts of the 'Type Concept' vocabulary such as 32817 'EHR'), but nothing enforces it, so this "
                "filter likely returns few or no rows."
            ),
            severity=Severity.WARNING,
            suggested_fix=(
                f"REPLACE: the Type Concept id(s) WITH {own} or standard 'Type Concept' vocabulary ids "
                "(e.g. 32817 'EHR', 32810 'Claim'), OR remove the provenance filter if it was not requested."
            ),
        )


__all__ = ["TypeConceptIdMisuseRule"]
