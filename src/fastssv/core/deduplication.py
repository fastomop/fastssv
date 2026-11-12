"""Error Deduplication System.

Prevents duplicate reporting of the same underlying issue by multiple rules.
"""

from typing import List, Set

from sqlglot import exp

from fastssv.core.base import RuleViolation, Severity

_LIMIT_WITHOUT_ORDER_BY = "anti_patterns.limit_without_order_by"


def _normalized_sql(node: exp.Expression) -> str:
    while isinstance(node, exp.Paren):
        node = node.this
    return " ".join(node.sql().lower().split())


def predicate_key(node: exp.Expression) -> str:
    """Normalised SQL of a predicate, so findings from different rules on the same equality compare equal:
    operands of ``=`` in sorted order, lower-cased, whitespace collapsed."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.EQ):
        left, right = sorted((_normalized_sql(node.this), _normalized_sql(node.expression)))
        return f"{left} = {right}"
    return _normalized_sql(node)


def scope_key(select: exp.Expression) -> str:
    """Normalised SQL of a query scope (e.g. the SELECT a LIMIT belongs to)."""
    return _normalized_sql(select)


def _drop_consequences(violations: List[RuleViolation]) -> List[RuleViolation]:
    """Remove findings that only restate a defect another rule already reports."""
    named_by: dict = {}
    for v in violations:
        predicate = (v.details or {}).get("predicate")
        if predicate and v.severity == Severity.ERROR:
            named_by.setdefault(predicate, set()).add(v.rule_id)
    explained_limits: dict = {}
    for v in violations:
        scope = (v.details or {}).get("limit_scope")
        if scope and v.rule_id != _LIMIT_WITHOUT_ORDER_BY:
            explained_limits.setdefault(scope, set()).add(v.rule_id)

    kept = []
    for v in violations:
        details = v.details or {}
        # A WARNING that lists the equalities it judged (e.g. "these sources are not tied to one person") is a
        # consequence when every one of them is a wrong predicate that an ERROR of another rule already names.
        predicates: Set[str] = set(details.get("predicates") or ())
        if (
            v.severity == Severity.WARNING
            and predicates
            and all(named_by.get(p, set()) - {v.rule_id} for p in predicates)
        ):
            continue
        # A bare "LIMIT without ORDER BY" yields to another rule that explains why that LIMIT is wrong.
        if v.rule_id == _LIMIT_WITHOUT_ORDER_BY and details.get("limit_scope") in explained_limits:
            continue
        kept.append(v)
    return kept


def _normalize_issue(violation: RuleViolation) -> str:
    """Create a normalized key for deduplication.

    Groups violations that represent the same underlying issue:
    - Same table/column name errors
    - Same type mismatch errors
    - Same missing filter warnings
    """
    details = violation.details or {}

    # Schema violations: deduplicate by table+column (regardless of which rule reported it)
    if details.get("type") == "invalid_column":
        table = details.get("table", "").lower()
        column = details.get("column", "").lower()
        return f"schema:invalid_column:{table}:{column}"

    # Also check message for "does not exist" pattern (catches both schema rules)
    if "does not exist in table" in violation.message.lower():
        # Extract table and column from message
        import re

        match = re.search(r"column\s+'([^']+)'.*table\s+'([^']+)'", violation.message.lower())
        if match:
            column, table = match.groups()
            return f"schema:invalid_column:{table}:{column}"

    if details.get("type") == "invalid_table":
        table = details.get("table", "").lower()
        return f"schema:invalid_table:{table}"

    # Type mismatch: deduplicate by column+types
    if details.get("type") in ("type_mismatch", "conflicting_filters"):
        table = details.get("table", "").lower()
        column = details.get("column", "").lower()
        col_type = details.get("column_type", "").lower()
        lit_type = details.get("literal_type", "").lower()
        return f"type:mismatch:{table}:{column}:{col_type}:{lit_type}"

    # Structural errors: deduplicate by type
    if details.get("layer") == "structural":
        error_type = details.get("type", "unknown")
        return f"structural:{error_type}"

    # Default: deduplicate by rule_id + message pattern
    message_key = violation.message[:100].lower()
    return f"rule:{violation.rule_id}:{message_key}"


def deduplicate_violations(violations: List[RuleViolation]) -> List[RuleViolation]:
    """Remove duplicate violations representing the same underlying issue.

    Rules:
    1. Keep highest severity (ERROR > WARNING)
    2. Keep most specific rule (schema.comprehensive_validation > data_quality.schema_validation)
    3. Keep first occurrence if same severity/specificity

    Before grouping, consequence findings are dropped (see ``_drop_consequences``).

    Returns deduplicated list of violations.
    """
    if not violations:
        return []
    violations = _drop_consequences(violations)

    # Identity-keyed index map: lets us tiebreak/preserve original order
    # in O(1) without `list.index`, and avoids value-equality collisions
    # between distinct RuleViolation dataclasses with identical fields.
    # `setdefault` keeps the first index if the same instance appears
    # twice — matching `list.index(v)` semantics.
    original_index: dict[int, int] = {}
    for i, v in enumerate(violations):
        original_index.setdefault(id(v), i)

    # Group violations by normalized issue
    issue_groups: dict = {}

    for violation in violations:
        key = _normalize_issue(violation)
        if key not in issue_groups:
            issue_groups[key] = []
        issue_groups[key].append(violation)

    # Select best violation from each group
    deduplicated = []

    for key, group in issue_groups.items():
        if len(group) == 1:
            deduplicated.append(group[0])
            continue

        # Sort by priority:
        # 1. ERROR before WARNING
        # 2. More specific rules (longer rule_id = more specific)
        # 3. First occurrence
        best = sorted(
            group,
            key=lambda v: (
                0 if v.severity.value == "error" else 1,  # ERROR first
                -len(v.rule_id),  # Longer rule_id = more specific
                original_index[id(v)],  # Original order
            ),
        )[0]

        deduplicated.append(best)

    # Return in original order
    return sorted(deduplicated, key=lambda v: original_index[id(v)])


__all__ = ["deduplicate_violations", "predicate_key", "scope_key"]
