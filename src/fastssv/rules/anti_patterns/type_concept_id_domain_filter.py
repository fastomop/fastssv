"""Type Concept ID Domain Filter Rule.

OMOP semantic rule VOCAB_035:
When joining *_type_concept_id columns to the concept table to look up type concept
names, do NOT filter by clinical domains (domain_id = 'Condition', 'Drug', etc.).
Type concepts belong to the 'Type Concept' domain, not clinical domains.

The Problem:
    Type concept columns (*_type_concept_id) reference concepts that describe the
    provenance or type of a clinical record:
    - condition_type_concept_id: EHR record, Insurance claim, etc.
    - drug_type_concept_id: Prescription written, Dispensed in pharmacy, etc.

    These type concepts have domain_id = 'Type Concept', not clinical domains like
    'Condition' or 'Drug'. When queries join type_concept_id to concept and filter
    by clinical domains, they return zero results.

Violation pattern:
    -- WRONG: Filtering type concepts by clinical domain
    SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c ON co.condition_type_concept_id = c.concept_id
    WHERE c.domain_id = 'Condition'
    -- Returns nothing! Type concepts have domain_id = 'Type Concept'

    -- WRONG: Drug type concepts with Drug domain
    SELECT c.concept_name
    FROM drug_exposure de
    JOIN concept c ON de.drug_type_concept_id = c.concept_id
    WHERE c.domain_id = 'Drug'
    -- Returns nothing! Should be 'Type Concept'

Correct patterns:
    -- CORRECT: Filter by 'Type Concept' domain
    SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c ON co.condition_type_concept_id = c.concept_id
    WHERE c.domain_id = 'Type Concept'

    -- CORRECT: No domain filter (type concepts are already correct)
    SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c ON co.condition_type_concept_id = c.concept_id

    -- CORRECT: Filter by standard_concept (acceptable)
    SELECT c.concept_name
    FROM condition_occurrence co
    JOIN concept c ON co.condition_type_concept_id = c.concept_id
    WHERE c.standard_concept = 'S'
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.patch import locate, replace
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

CLINICAL_DOMAINS = {
    "Condition",
    "Drug",
    "Procedure",
    "Measurement",
    "Observation",
    "Device",
    "Visit",
    "Spec Anatomic Site",
    "Specimen",
    "Unit",
    "Provider",
    "Place of Service",
    "Care Site",
    "Revenue Code",
}

CORRECT_TYPE_DOMAIN = "Type Concept"

TYPE_CONCEPT_COLUMNS = {
    "condition_type_concept_id",
    "drug_type_concept_id",
    "procedure_type_concept_id",
    "measurement_type_concept_id",
    "observation_type_concept_id",
    "visit_type_concept_id",
    "visit_detail_type_concept_id",
    "device_type_concept_id",
    "specimen_type_concept_id",
    "note_type_concept_id",
    "episode_type_concept_id",
}

TYPE_CONCEPT_COLUMNS_NORM = {normalize_name(c) for c in TYPE_CONCEPT_COLUMNS}
CLINICAL_DOMAINS_NORM = {normalize_name(d) for d in CLINICAL_DOMAINS}
CORRECT_TYPE_DOMAIN_NORM = normalize_name(CORRECT_TYPE_DOMAIN)


# --- Helpers ---------------------------------------------------------------


def _norm(val: Optional[str]) -> Optional[str]:
    return normalize_name(val) if val else None


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if not is_string_literal(node):
        return None
    if hasattr(node, "name"):
        return node.name
    if hasattr(node, "this"):
        return str(node.this)
    return None


def _get_table_alias(table: exp.Table) -> str:
    alias_expr = table.args.get("alias")
    return alias_expr.name if alias_expr else table.name


def _is_type_concept_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    _, col_name = resolve_table_col(col, aliases)
    return _norm(col_name) in TYPE_CONCEPT_COLUMNS_NORM


def _is_domain_id_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    table, col_name = resolve_table_col(col, aliases)

    if _norm(col_name) != "domain_id":
        return False

    # Should be from concept table if table is specified
    if table and _norm(table) != "concept":
        return False

    return True


# --- Core detection --------------------------------------------------------


def _find_type_concept_joins(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    joins: List[Tuple[str, str, str]] = []

    for join in tree.find_all(exp.Join):
        if not isinstance(join.this, exp.Table):
            continue

        if _norm(join.this.name) != "concept":
            continue

        concept_alias = _get_table_alias(join.this)
        concept_alias_norm = _norm(concept_alias)

        on_clause = join.args.get("on")
        if not on_clause:
            continue

        for eq in on_clause.find_all(exp.EQ):
            left, right = eq.this, eq.expression

            for type_col, concept_col in [(left, right), (right, left)]:
                if not isinstance(type_col, exp.Column):
                    continue
                if not isinstance(concept_col, exp.Column):
                    continue

                if not _is_type_concept_column(type_col, aliases):
                    continue

                concept_table, concept_col_name = resolve_table_col(concept_col, aliases)

                if _norm(concept_col_name) != "concept_id":
                    continue

                # Check if concept_table matches either the alias or what the alias resolves to
                concept_table_name = aliases.get(concept_alias_norm, concept_alias_norm)
                if concept_table and _norm(concept_table) not in {concept_alias_norm, _norm(concept_table_name)}:
                    continue

                _, type_col_name = resolve_table_col(type_col, aliases)
                joins.append((type_col_name, concept_alias, join.sql()))

    return joins


def _find_domain_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
    concept_alias: str,
) -> List[Tuple[str, str, exp.Expression]]:
    """Return list of (value, context_sql, node).

    ``node`` is the offending predicate node (EQ / NEQ / IN / IS) so callers
    can construct a structured patch when the shape is mechanical.
    """
    filters: List[Tuple[str, str, exp.Expression]] = []
    concept_alias_norm = _norm(concept_alias)

    nodes = (
        list(tree.find_all(exp.EQ))
        + list(tree.find_all(exp.NEQ))
        + list(tree.find_all(exp.In))
        + list(tree.find_all(exp.Is))
    )

    for node in nodes:
        if not is_in_where_or_join_clause(node):
            continue

        # --- EQ / NEQ ---
        if isinstance(node, (exp.EQ, exp.NEQ)):
            for col_node, val_node in [(node.this, node.expression), (node.expression, node.this)]:
                if not isinstance(col_node, exp.Column):
                    continue

                if not _is_domain_id_column(col_node, aliases):
                    continue

                # Check if it's from our concept alias (allow unqualified columns)
                col_table = _norm(col_node.table)
                if col_table and col_table != concept_alias_norm:
                    continue

                value = _extract_string_literal(val_node)
                if value:
                    filters.append((value, node.sql(), node))

        # --- IN ---
        elif isinstance(node, exp.In):
            if not isinstance(node.this, exp.Column):
                continue

            if not _is_domain_id_column(node.this, aliases):
                continue

            col_table = _norm(node.this.table)
            if col_table and col_table != concept_alias_norm:
                continue

            for expr in node.expressions or []:
                value = _extract_string_literal(expr)
                if value:
                    filters.append((value, node.sql(), node))

        # --- IS (NULL) ---
        elif isinstance(node, exp.Is):
            if not isinstance(node.this, exp.Column):
                continue

            if not _is_domain_id_column(node.this, aliases):
                continue

            col_table = _norm(node.this.table)
            if col_table and col_table != concept_alias_norm:
                continue

            filters.append(("NULL", node.sql(), node))

    return filters


# --- Rule ------------------------------------------------------------------


@register
class TypeConceptIdDomainFilterRule(Rule):
    """Detect incorrect domain_id filters on type_concept_id lookups."""

    rule_id = "anti_patterns.type_concept_id_domain_filter"
    name = "Type Concept ID Domain Filter"

    description = (
        "Type concept columns (*_type_concept_id) reference concepts with "
        "domain_id = 'Type Concept'. Filtering by clinical domains returns no results."
    )

    severity = Severity.WARNING

    suggested_fix = "REPLACE: `WHERE c.domain_id = '<clinical_domain>'` WITH `WHERE c.domain_id = 'Type Concept'` when joining to a *_type_concept_id, OR remove the domain_id filter."
    long_description = (
        "The `*_type_concept_id` columns resolve to concepts whose "
        "domain_id is 'Type Concept' — they describe record provenance "
        "(EHR, claim, patient-reported), not clinical entities. Joining "
        "`*_type_concept_id` to concept and then filtering "
        "domain_id = 'Condition' (or 'Drug', etc.) always returns zero "
        "rows because type concepts don't live in clinical domains. Use "
        "domain_id = 'Type Concept' if you need the filter, or drop the "
        "domain_id predicate entirely."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_type_concept_id = c.concept_id\n"
        "WHERE c.domain_id = 'Condition';"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_type_concept_id = c.concept_id\n"
        "WHERE c.domain_id = 'Type Concept';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if "type_concept_id" not in sql.lower() or "domain_id" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []
        seen: Set[str] = set()

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            joins = _find_type_concept_joins(tree, aliases)

            for type_col, concept_alias, _ in joins:
                filters = _find_domain_filters(tree, aliases, concept_alias)

                for domain_value, context, node in filters:
                    domain_norm = _norm(domain_value)

                    if domain_norm == CORRECT_TYPE_DOMAIN_NORM:
                        continue

                    if domain_norm in CLINICAL_DOMAINS_NORM:
                        key = f"{type_col}|{domain_value}|{context}"
                        if key in seen:
                            continue
                        seen.add(key)

                        # Mechanical REPLACE only for the simple EQ case:
                        # `<col> = '<clinical_domain>'` →
                        # `<col> = 'Type Concept'`. IN/NEQ/IS shapes vary
                        # too much for a single canonical rewrite — leave
                        # them on FREEFORM auto-default.
                        patch = None
                        if isinstance(node, exp.EQ):
                            col_part = node.this if isinstance(node.this, exp.Column) else node.expression
                            if isinstance(col_part, exp.Column):
                                span = locate(sql, node.sql())
                                if span is not None:
                                    patch = replace(
                                        span,
                                        f"{col_part.sql()} = '{CORRECT_TYPE_DOMAIN}'",
                                    )

                        violations.append(
                            self.create_violation(
                                message=(
                                    f"Type column '{type_col}' filters domain_id = '{domain_value}'. "
                                    f"Expected '{CORRECT_TYPE_DOMAIN}'."
                                ),
                                severity=Severity.WARNING,
                                suggested_fix=(f"Use domain_id = '{CORRECT_TYPE_DOMAIN}' or remove the filter"),
                                suggested_fix_patch=patch,
                                details={
                                    "type_column": type_col,
                                    "incorrect_domain": domain_value,
                                    "expected_domain": CORRECT_TYPE_DOMAIN,
                                    "context": context,
                                },
                            )
                        )

        return violations


__all__ = ["TypeConceptIdDomainFilterRule"]
