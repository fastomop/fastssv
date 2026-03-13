"""Concept Lookup Context Rule.

OMOP vocabulary rule:
String filtering on concept table columns (concept_name, concept_code, etc.)
is allowed ONLY when used inside a concept_id lookup context (subquery or CTE
that outputs concept_id).
"""

from typing import Dict, List

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register

# Pairs of (table_name, column_name) for concept table string columns
CONCEPT_STRING_COLUMNS = {
    ("concept", "concept_name"),
    ("concept", "concept_code"),
    ("concept", "vocabulary_id"),
    ("concept", "domain_id"),
    ("concept", "concept_class_id"),
    ("concept_synonym", "concept_synonym_name"),
    ("concept_ancestor", "min_levels_of_separation"),
    ("concept_ancestor", "max_levels_of_separation"),
    ("vocabulary", "vocabulary_name"),
    ("vocabulary", "vocabulary_reference"),
    ("vocabulary", "vocabulary_version"),
    ("domain", "domain_name"),
    ("concept_class", "concept_class_name"),
    ("relationship", "relationship_name"),
    ("relationship", "is_hierarchical"),
    ("relationship", "defines_ancestry"),
}

STRING_MATCH_EXP_TYPES = (exp.Like, exp.ILike, exp.RegexpLike)


def _is_inside_concept_id_lookup(col_node: exp.Column, aliases: Dict[str, str]) -> bool:
    """Check if this column is used inside a concept_id lookup context.

    Returns True if this column is used inside a SELECT that:
    1. Outputs concept_id (directly or via alias)
    2. Is selecting from a vocabulary table
    3. Joins concept table to clinical table via *_concept_id

    Example allowed contexts:
      SELECT concept_id FROM concept WHERE concept_code = 'E11'
      SELECT c.concept_id AS cid FROM concept c WHERE c.concept_name LIKE '%diabetes%'
      EXISTS (SELECT 1 FROM concept c WHERE c.concept_id = x.some_concept_id AND c.standard_concept='S')
      JOIN concept c ON table.x_concept_id = c.concept_id WHERE c.domain_id = 'Condition'
    """
    select = col_node.find_ancestor(exp.Select)
    if not select:
        return False

    vocab_tables = {"concept", "concept_synonym", "concept_ancestor",
                    "concept_relationship", "vocabulary", "domain",
                    "concept_class", "relationship"}

    from_clause = select.find(exp.From)
    is_from_vocab_table = False
    if from_clause:
        for table in from_clause.find_all(exp.Table):
            table_name = normalize_name(table.name)
            real_table = aliases.get(table_name, table_name)
            if real_table in vocab_tables:
                is_from_vocab_table = True
                break

    # Also check JOINs
    for join in select.find_all(exp.Join):
        join_table = join.find(exp.Table)
        if join_table:
            table_name = normalize_name(join_table.name)
            real_table = aliases.get(table_name, table_name)
            if real_table in vocab_tables:
                is_from_vocab_table = True
                break

    if not is_from_vocab_table:
        return False

    # Check if this is an EXISTS subquery that correlates to a concept_id column
    exists_ancestor = col_node.find_ancestor(exp.Exists)
    if exists_ancestor:
        where = select.find(exp.Where)
        if where:
            for eq in where.find_all(exp.EQ):
                left, right = eq.left, eq.right
                for side_a, side_b in [(left, right), (right, left)]:
                    if isinstance(side_a, exp.Column) and isinstance(side_b, exp.Column):
                        a_name = normalize_name(side_a.name)
                        b_name = normalize_name(side_b.name)
                        if a_name == "concept_id":
                            if b_name.endswith("_concept_id") or b_name == "concept_id":
                                return True

    # Check if concept table is joined to a clinical table via *_concept_id
    # This allows filtering concept attributes when joining to clinical tables
    # Example: JOIN concept c ON table.x_concept_id = c.concept_id WHERE c.domain_id = 'X'
    for join in select.find_all(exp.Join):
        join_on = join.args.get("on")
        if join_on:
            for eq in join_on.find_all(exp.EQ):
                left, right = eq.left, eq.right
                for side_a, side_b in [(left, right), (right, left)]:
                    if isinstance(side_a, exp.Column) and isinstance(side_b, exp.Column):
                        a_table, a_col = resolve_table_col(side_a, aliases)
                        b_table, b_col = resolve_table_col(side_b, aliases)

                        # Check if one side is concept.concept_id and other is a *_concept_id
                        if (a_table in vocab_tables and a_col == "concept_id" and
                            (b_col.endswith("_concept_id") or b_col == "concept_id")):
                            return True
                        if (b_table in vocab_tables and b_col == "concept_id" and
                            (a_col.endswith("_concept_id") or a_col == "concept_id")):
                            return True

    # Check if SELECT contains concept_id in its projection
    for proj in select.expressions or []:
        if isinstance(proj, exp.Star):
            return True

        if isinstance(proj, exp.Alias):
            alias_name = normalize_name(proj.alias) if proj.alias else ""
            if alias_name == "concept_id" or alias_name.endswith("_concept_id"):
                return True
            target = proj.this
        else:
            target = proj

        if isinstance(target, exp.Column):
            col_name = normalize_name(target.name)
            if col_name == "concept_id":
                return True
            if col_name in {"concept_id_1", "concept_id_2"}:
                return True
            if col_name in {"descendant_concept_id", "ancestor_concept_id"}:
                return True

    return False


def _check_string_match_violations(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[RuleViolation]:
    """Check for LIKE/ILIKE/REGEXP violations on concept table columns outside lookup context."""
    violations: List[RuleViolation] = []

    for node in tree.walk():
        is_not = False
        check_node = node

        if isinstance(node, exp.Not):
            inner = node.this
            if isinstance(inner, STRING_MATCH_EXP_TYPES):
                is_not = True
                check_node = inner
            else:
                continue
        elif not isinstance(node, STRING_MATCH_EXP_TYPES):
            continue

        left = check_node.this
        right = check_node.expression

        if not isinstance(left, exp.Column):
            continue

        table, col = resolve_table_col(left, aliases)
        key = (table, col)

        not_prefix = "NOT " if is_not else ""
        op_name = check_node.key.upper() if hasattr(check_node, 'key') else "LIKE"

        # Check if it's a concept table string column
        if key in CONCEPT_STRING_COLUMNS:
            if not _is_inside_concept_id_lookup(left, aliases):
                violations.append(RuleViolation(
                    rule_id="vocabulary.concept_lookup_context",
                    severity=Severity.ERROR,
                    message=f"String matching on concept table outside concept_id lookup: {left.sql()} {not_prefix}{op_name} {right.sql()}",
                    suggested_fix="Wrap in subquery: WHERE *_concept_id IN (SELECT concept_id FROM concept WHERE ...)",
                    details={"column": f"{table}.{col}", "operation": f"{not_prefix}{op_name}"},
                ))

    return violations


def _check_equality_violations(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[RuleViolation]:
    """Check for equality comparison violations on concept table columns outside lookup context."""
    violations: List[RuleViolation] = []

    for eq in tree.find_all(exp.EQ):
        left = eq.left
        right = eq.right

        if isinstance(right, exp.Column) and is_string_literal(left):
            left, right = right, left

        if not (isinstance(left, exp.Column) and is_string_literal(right)):
            continue

        table, col = resolve_table_col(left, aliases)
        key = (table, col)

        if key in CONCEPT_STRING_COLUMNS:
            if not _is_inside_concept_id_lookup(left, aliases):
                violations.append(RuleViolation(
                    rule_id="vocabulary.concept_lookup_context",
                    severity=Severity.ERROR,
                    message=f"Concept table string filter outside concept_id lookup: {left.sql()} = {right.sql()}",
                    suggested_fix="Wrap in subquery: WHERE *_concept_id IN (SELECT concept_id FROM concept WHERE ...)",
                    details={"column": f"{table}.{col}", "operation": "="},
                ))

    return violations


def _check_in_clause_violations(
    tree: exp.Expression,
    aliases: Dict[str, str]
) -> List[RuleViolation]:
    """Check for IN clause violations on concept table columns outside lookup context."""
    violations: List[RuleViolation] = []

    for in_expr in tree.find_all(exp.In):
        is_not = isinstance(in_expr.parent, exp.Not)

        col_expr = in_expr.this
        if not isinstance(col_expr, exp.Column):
            continue

        has_string_values = False
        string_values = []
        for val in in_expr.expressions or []:
            if is_string_literal(val):
                has_string_values = True
                string_values.append(val.sql())

        if not has_string_values:
            continue

        table, col = resolve_table_col(col_expr, aliases)
        key = (table, col)

        not_prefix = "NOT " if is_not else ""
        values_str = ", ".join(string_values[:3])
        if len(string_values) > 3:
            values_str += ", ..."

        if key in CONCEPT_STRING_COLUMNS:
            if not _is_inside_concept_id_lookup(col_expr, aliases):
                violations.append(RuleViolation(
                    rule_id="vocabulary.concept_lookup_context",
                    severity=Severity.ERROR,
                    message=f"Concept table string IN clause outside concept_id lookup: {col_expr.sql()} {not_prefix}IN ({values_str})",
                    suggested_fix="Wrap in subquery: WHERE *_concept_id IN (SELECT concept_id FROM concept WHERE ...)",
                    details={"column": f"{table}.{col}", "operation": f"{not_prefix}IN"},
                ))

    return violations


@register
class ConceptLookupContextRule(Rule):
    """Ensures concept table string filters are in concept_id lookup context."""

    rule_id = "vocabulary.concept_lookup_context"
    name = "Concept Lookup Context"
    description = (
        "Ensures string filtering on concept table columns is only done inside "
        "a concept_id lookup context (subquery or CTE that outputs concept_id)"
    )
    severity = Severity.ERROR
    suggested_fix = "Wrap in subquery: WHERE *_concept_id IN (SELECT concept_id FROM concept WHERE ...)"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        all_violations: List[RuleViolation] = []

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)

            all_violations.extend(_check_string_match_violations(tree, aliases))
            all_violations.extend(_check_equality_violations(tree, aliases))
            all_violations.extend(_check_in_clause_violations(tree, aliases))

        return all_violations


__all__ = ["ConceptLookupContextRule", "CONCEPT_STRING_COLUMNS"]
