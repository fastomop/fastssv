"""Invalid Reason Enforcement Rule (strict mode only).

Concepts have a life cycle (Book of OHDSI 5.2.10): VALID_START_DATE, VALID_END_DATE and INVALID_REASON, which is 'D'
(deleted), 'U' (replaced with an update) or NULL while the concept is valid (CDM v5.4 field-level specification).
When a query builds a concept set from ``concept`` by its attributes (vocabulary_id, domain_id, concept_code,
concept_name, ...), retired concepts come back unless it filters on validity.

No cited source makes that filter mandatory, and several queries are correct without it, so the rule runs only in
strict mode and reports a WARNING:

* Standard and Classification concepts are always valid (in Athena v5.0 27-AUG-25 all 4,438,707 of them have
  invalid_reason NULL), so a ``standard_concept = 'S'`` filter on the same concept row already implies validity.
* concept_relationship is not checked: "only active records with INVALID_REASON = NULL are available through ATHENA"
  (Book of OHDSI 5.2.10).
* A concept row whose id feeds the source side (concept_id_1) of a 'Maps to' relationship is a source-code lookup.
  Retired source codes keep their 'Maps to' row (1,344,991 retired concepts in the release), and historical records
  carry them; filtering them out loses data.
* A concept row matched to a clinical record's concept column decodes stored records, which were valid when recorded.
* concept_ancestor is not checked: "Only valid and Standard Concepts participate in the CONCEPT_ANCESTOR table".

drug_strength and source_to_concept_map carry their own validity columns and are reported when read without a
predicate on them; concept_synonym has none and is reported when it is used as a concept source without a validity
filter on a joined concept. A query whose WHERE is constant false (``WHERE FALSE``, ``WHERE 0 = 1``) returns no rows
and is never reported. Each check is made per table instance, so a validity filter on one concept row does not cover
another.
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    DERIVED,
    _select_sources,
    column_origins,
    normalize_name,
    parse_sql,
    relationship_filters,
)
from fastssv.core.references import BOOK_VOCABULARIES, CDM_CONVENTIONS, cdm_table, section
from fastssv.core.registry import register

from ._cs_scope import InstanceKey, instance_filters, instance_is_null_extended, links, operand_reads

ROW_VALIDITY_TABLES = {"drug_strength", "source_to_concept_map"}
VALIDITY_COLUMNS = {"invalid_reason", "valid_start_date", "valid_end_date"}

# Attributes that select concepts when filtered: the concept table is being used as a concept *source*.
CONCEPT_SOURCE_FILTER_COLUMNS = (
    "vocabulary_id",
    "domain_id",
    "concept_class_id",
    "standard_concept",
    "concept_name",
    "concept_code",
)

_VOCABULARY_TABLES = {
    "concept",
    "concept_relationship",
    "concept_ancestor",
    "concept_synonym",
    "drug_strength",
    "source_to_concept_map",
    "vocabulary",
    "domain",
    "concept_class",
    "relationship",
}
_NEGATED = {"neq", "not_in", "is_not_null"}


def _in_predicate(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, (exp.Where, exp.Join, exp.Having)):
            return True
        parent = parent.parent
    return False


def _constant_false(select: exp.Select) -> bool:
    where = select.args.get("where")
    cond = where.this if where is not None else None
    while isinstance(cond, exp.Paren):
        cond = cond.this
    if isinstance(cond, exp.Boolean):
        return not cond.this
    if isinstance(cond, exp.EQ) and isinstance(cond.this, exp.Literal) and isinstance(cond.expression, exp.Literal):
        return cond.this.this != cond.expression.this
    return False


def _validated_instances(tree: exp.Expression, table: str) -> Set[InstanceKey]:
    """Instances of ``table`` whose own invalid_reason / valid_start_date / valid_end_date appears in a predicate
    (``IS NULL``, ``= 'D'``, ``BETWEEN valid_start_date AND valid_end_date``, ...)."""
    out: Set[InstanceKey] = set()
    for col in tree.find_all(exp.Column):
        name = normalize_name(col.name)
        if name not in VALIDITY_COLUMNS or not _in_predicate(col):
            continue
        origins = column_origins(col)
        if origins is not None and (table, name) not in origins:
            continue
        for select, alias, read_table, _ in operand_reads(col):
            if read_table == table:
                out.add((id(select), alias))
    return out


def _instances_read(tree: exp.Expression, table: str) -> Dict[InstanceKey, exp.Select]:
    """Every base-table read of ``table`` in a SELECT that can return rows."""
    out: Dict[InstanceKey, exp.Select] = {}
    for select in tree.find_all(exp.Select):
        if _constant_false(select):
            continue
        for src in _select_sources(select):
            if isinstance(src, exp.Table) and normalize_name(src.name) == table:
                out[(id(select), normalize_name(src.alias_or_name))] = select
    return out


def _maps_to_source_instances(tree: exp.Expression) -> Set[InstanceKey]:
    """concept instances whose id is concept_id_1 of a concept_relationship restricted to 'Maps to'."""
    restricted = {k for k, v in relationship_filters(tree).items() if "maps to" in v}
    out: Set[InstanceKey] = set()
    for link in links(tree, "concept_relationship", "concept_id_1"):
        if link.instance in restricted and link.origin == ("concept", "concept_id"):
            for other in links(tree, "concept", "concept_id"):
                if other.node is link.node:
                    out.add(other.instance)
    return out


def _decoding_instances(tree: exp.Expression) -> Set[InstanceKey]:
    """concept instances matched to a concept column of a non-vocabulary table: they decode stored records."""
    return {
        link.instance
        for link in links(tree, "concept", "concept_id")
        if (link.required or instance_is_null_extended(link))
        and link.origin[0] not in _VOCABULARY_TABLES
        and link.origin[0] != DERIVED
        # joined in its own SELECT: the concept attributes describe the stored records
        and link.node.find_ancestor(exp.Select) is link.instance_select
    }


def _source_instances(tree: exp.Expression) -> Dict[InstanceKey, str]:
    """concept instances filtered on a concept-selecting attribute -> the first such predicate."""
    out: Dict[InstanceKey, str] = {}
    for column in CONCEPT_SOURCE_FILTER_COLUMNS:
        for key, flts in instance_filters(tree, "concept", column).items():
            for flt in flts:
                if flt.op not in _NEGATED and _in_predicate(flt.node):
                    out.setdefault(key, flt.sql)
    return out


def _standard_instances(tree: exp.Expression) -> Set[InstanceKey]:
    return {
        key
        for key, flts in instance_filters(tree, "concept", "standard_concept").items()
        if any(
            (f.required or f.outer_join_on) and f.op in ("eq", "in") and {v.upper() for v in f.values} <= {"S", "C"}
            for f in flts
        )
    }


@register
class InvalidReasonEnforcementRule(Rule):
    """Strict-mode-only rule: concept sets built from vocabulary tables should exclude retired rows."""

    rule_id = "concept_standardization.invalid_reason_enforcement"
    name = "Invalid Reason Enforcement (strict mode)"
    description = (
        "Strict-mode-only. Warns when a concept set is built from concept by its attributes, or drug_strength / "
        "source_to_concept_map / concept_synonym is read, without a validity filter, so retired or upgraded rows "
        "can be included. Silent in default mode."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: `AND <alias>.invalid_reason IS NULL` on that table, OR `AND CURRENT_DATE BETWEEN valid_start_date AND "
        "valid_end_date`. Strict-mode-only."
    )
    long_description = (
        "Vocabulary rows carry a life cycle: invalid_reason is 'D' (deleted), 'U' (upgraded) or NULL while valid, "
        "with valid_start_date / valid_end_date. A concept set built from concept by vocabulary, domain, code or name "
        "includes retired concepts unless the query filters on validity; drug_strength and source_to_concept_map have "
        "their own validity columns, and concept_synonym has none, so it needs a joined concept to filter on.\n\n"
        "**Gated behind strict mode.** No source makes the filter mandatory, and many queries are correct without "
        "it: Standard and Classification concepts are always valid, Athena ships only valid concept_relationship "
        "rows, retired source codes keep their 'Maps to' mappings (filtering them out of a source-code lookup loses "
        "historical records), and a concept joined to a clinical record decodes a concept that was valid when "
        "recorded. None of those is reported. Enable with ``--strict`` (CLI) or ``strict=True`` (API)."
    )
    example_bad = "SELECT concept_id, concept_name\nFROM concept\nWHERE vocabulary_id = 'SNOMED';"
    example_good = (
        "SELECT concept_id, concept_name\nFROM concept\nWHERE vocabulary_id = 'SNOMED'\n  AND invalid_reason IS NULL;"
    )
    references = (
        section(BOOK_VOCABULARIES, "conceptLifeCycle", "Life-cycle"),
        cdm_table("concept"),
        section(CDM_CONVENTIONS, "source-to-concept-map", "Source to concept map"),
        section(CDM_CONVENTIONS, "concept-ancestor", "Concept ancestor"),
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT target_concept_id\n"
                "FROM source_to_concept_map\n"
                "WHERE source_vocabulary_id = 'HOSP_LAB'\n"
                "  AND source_code = 'GLU';"
            ),
            severity=Severity.WARNING,
            note="source_to_concept_map (or drug_strength) read without a validity filter on its own rows",
            strict=True,
        ),
        RuleExample(
            sql=(
                "SELECT concept_id, concept_synonym_name\n"
                "FROM concept_synonym\n"
                "WHERE concept_synonym_name ILIKE '%myocardial infarction%';"
            ),
            severity=Severity.WARNING,
            note="concept_synonym used as a concept source without joining concept to filter invalid_reason",
            strict=True,
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Silent unless ``ValidationContext.strict_mode`` is on."""
        from fastssv.core.validation_context import get_validation_context

        if not get_validation_context().strict_mode:
            return []
        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            violations.extend(self._concept_sources(tree))
            violations.extend(self._row_validity_tables(tree))
            violations.extend(self._synonym_sources(tree))
        return violations

    def _concept_sources(self, tree: exp.Expression) -> List[RuleViolation]:
        read = _instances_read(tree, "concept")
        sources = {k: v for k, v in _source_instances(tree).items() if k in read}
        if not sources:
            return []
        exempt = (
            _validated_instances(tree, "concept")
            | _standard_instances(tree)
            | _maps_to_source_instances(tree)
            | _decoding_instances(tree)
        )
        unvalidated = [k for k in sources if k not in exempt]
        if not unvalidated:
            return []
        predicates = ", ".join(sorted({f"`{sources[k]}`" for k in unvalidated})[:3])
        return [
            self.create_violation(
                severity=Severity.WARNING,
                message=(
                    f"Query uses vocabulary table(s) [concept] to select concepts by {predicates} without filtering "
                    "by invalid_reason. "
                    "Retired ('D') and upgraded ('U') concepts are included unless invalid_reason IS NULL or a "
                    "validity-date predicate is added."
                ),
                suggested_fix=(
                    "ADD: `AND invalid_reason IS NULL` on that concept row, unless retired codes are wanted (for "
                    "example to map historical source codes)."
                ),
                details={"vocabulary_tables": ["concept"]},
            )
        ]

    def _row_validity_tables(self, tree: exp.Expression) -> List[RuleViolation]:
        out: List[RuleViolation] = []
        for table in sorted(ROW_VALIDITY_TABLES):
            read = _instances_read(tree, table)
            if not read or set(read) <= _validated_instances(tree, table):
                continue
            out.append(
                self.create_violation(
                    severity=Severity.WARNING,
                    message=(
                        f"Query reads {table} without a validity filter on its own rows. {table} carries "
                        "valid_start_date, valid_end_date and invalid_reason, so superseded rows are returned alongside "
                        "current ones."
                    ),
                    suggested_fix=(
                        f"ADD: `AND <{table} alias>.invalid_reason IS NULL`, OR `AND CURRENT_DATE BETWEEN "
                        "valid_start_date AND valid_end_date` on that table."
                    ),
                    details={"vocabulary_tables": [table]},
                )
            )
        return out

    def _synonym_sources(self, tree: exp.Expression) -> List[RuleViolation]:
        synonym_reads: List[Tuple[InstanceKey, exp.Select]] = []
        for key, select in _instances_read(tree, "concept_synonym").items():
            from_node = select.args.get("from_") or select.args.get("from")
            if from_node is not None and normalize_name(getattr(from_node.this, "name", "") or "") == "concept_synonym":
                synonym_reads.append((key, select))
        if not synonym_reads:
            return []
        validated = _validated_instances(tree, "concept") | _standard_instances(tree)
        concept_reads = _instances_read(tree, "concept")
        if any(k in validated for k in concept_reads):
            return []
        return [
            self.create_violation(
                severity=Severity.WARNING,
                message=(
                    "Query uses derived vocabulary table(s) [concept_synonym] which do not have an invalid_reason "
                    "column. To ensure only valid concepts are used, JOIN to the concept table and add "
                    "'concept.invalid_reason IS NULL' to filter out deprecated concepts."
                ),
                suggested_fix=(
                    "ADD: `JOIN concept c ON c.concept_id = <table>.<concept_id_col>` AND `WHERE c.invalid_reason IS "
                    "NULL` to exclude deprecated concepts."
                ),
                details={"derived_tables": ["concept_synonym"]},
            )
        ]


__all__ = ["InvalidReasonEnforcementRule"]
