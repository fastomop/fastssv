"""Free-Text Column Misuse Rule.

Detects when a free-text VARCHAR column on a clinical/reference table is
incorrectly joined to the concept table or compared to a numeric literal.

Replaces four previously separate rules that all encoded the same anti-pattern
shape with different (table, column) coordinates:

- data_quality.condition_occurrence_stop_reason_is_free_text  (OMOP_107)
- data_quality.drug_exposure_lot_number_is_free_text
- data_quality.note_nlp_term_modifiers_is_free_text
- data_quality.location_state_zip_not_joined_to_concept

Detection patterns flagged:
    SELECT * FROM <T> t JOIN concept c ON t.<col> = c.<any concept column>;
    SELECT * FROM <T> WHERE <col> = <numeric literal>;

Correct usage:
    Treat the column as free text — text equality, LIKE, NULL checks. Do not
    join to the concept table; for concept lookup, use the corresponding
    *_concept_id column on the same table.
"""

from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    is_in_where_or_join_clause,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Configuration --------------------------------------------------------

# (table, column) pairs to flag, mapped to a short human description used
# in the violation message and the recommended replacement (when relevant).
FREE_TEXT_FIELDS: Dict[Tuple[str, str], Dict[str, object]] = {
    ("condition_occurrence", "stop_reason"): {
        "description": ("free-text explanation for why a condition ended (e.g. 'Patient Improved')"),
        "replacement": "condition_concept_id",
        "flag_any_join": False,
    },
    ("drug_exposure", "lot_number"): {
        "description": "free-text manufacturer lot number",
        "replacement": "drug_concept_id",
        "flag_any_join": False,
    },
    ("note_nlp", "term_modifiers"): {
        # term_modifiers is unparsed key=value text. Any JOIN on it is
        # almost certainly a mistake, so flag any join condition involving
        # it, not only joins to the concept table.
        "description": "free-text NLP modifier annotations",
        "replacement": "note_nlp_concept_id",
        "flag_any_join": True,
    },
    ("location", "state"): {
        "description": "free-text US state abbreviation or name",
        "replacement": "country_concept_id",
        "flag_any_join": False,
    },
    ("location", "zip"): {
        "description": "free-text postal code",
        "replacement": "country_concept_id",
        "flag_any_join": False,
    },
}

CONCEPT_TABLE = "concept"

# CAST(<col> AS <type>) targets that imply numeric semantics — flagged on
# every registered free-text column.
NUMERIC_CAST_TYPES = {
    "INT",
    "INTEGER",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "NUMERIC",
    "DECIMAL",
    "FLOAT",
    "DOUBLE",
    "REAL",
}


# --- Helpers --------------------------------------------------------------


def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


_NORM_CONCEPT = _norm(CONCEPT_TABLE)
_NORM_FREE_TEXT_FIELDS: Dict[Tuple[str, str], Dict[str, str]] = {
    (_norm(t), _norm(c)): info for (t, c), info in FREE_TEXT_FIELDS.items()
}
_NORM_TARGET_TABLES: FrozenSet[str] = frozenset(t for t, _ in _NORM_FREE_TEXT_FIELDS)


def _normalize_aliases(aliases: Dict[str, str]) -> Dict[str, str]:
    return {_norm(k): _norm(v) for k, v in aliases.items()}


def _aliases_for_table(aliases: Dict[str, str], table_norm: str) -> Set[str]:
    return {alias for alias, real in aliases.items() if real == table_norm}


def _resolve_free_text_column(
    col: exp.Column,
    aliases: Dict[str, str],
) -> Optional[Tuple[str, str, str]]:
    """If ``col`` resolves to a registered free-text field, return
    (table_alias, table_norm, column_norm). Otherwise None.
    """
    table, col_name = resolve_table_col(col, aliases)
    col_norm = _norm(col_name)
    if not col_norm:
        return None

    if table:
        table_alias = _norm(table)
        table_norm = aliases.get(table_alias)
        if not table_norm:
            return None
        if (table_norm, col_norm) in _NORM_FREE_TEXT_FIELDS:
            return table_alias, table_norm, col_norm
        return None

    # Unqualified column: only resolve if exactly one target table is in scope
    candidate_tables = {real for real in aliases.values() if real in _NORM_TARGET_TABLES}
    if len(candidate_tables) != 1:
        return None
    table_norm = next(iter(candidate_tables))
    if (table_norm, col_norm) not in _NORM_FREE_TEXT_FIELDS:
        return None
    table_aliases = _aliases_for_table(aliases, table_norm)
    if len(table_aliases) != 1:
        return None
    return next(iter(table_aliases)), table_norm, col_norm


def _resolve_concept_column(
    col: exp.Column,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> Optional[Tuple[str, str]]:
    table, col_name = resolve_table_col(col, aliases)
    col_norm = _norm(col_name)
    if not col_norm:
        return None
    if table:
        table_alias = _norm(table)
        if table_alias in concept_aliases:
            return table_alias, col_norm
        return None
    if len(concept_aliases) == 1:
        return next(iter(concept_aliases)), col_norm
    return None


def _is_numeric_literal(node: exp.Expression) -> bool:
    if not isinstance(node, exp.Literal):
        return False
    try:
        float(node.this)
        return True
    except (ValueError, TypeError):
        return False


# --- Detection ------------------------------------------------------------


def _detect_concept_joins(
    select: exp.Select,
    aliases: Dict[str, str],
    concept_aliases: Set[str],
) -> List[Tuple[str, str, str, str]]:
    """Find ``free_text_col = concept_col`` equalities in WHERE/JOIN clauses.

    Returns list of (table_norm, col_norm, concept_alias, concept_col_norm).
    """
    found: List[Tuple[str, str, str, str]] = []

    for node in select.walk():
        if not isinstance(node, exp.EQ):
            continue
        if not is_in_where_or_join_clause(node):
            continue

        left, right = node.this, node.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue

        left_ft = _resolve_free_text_column(left, aliases)
        right_ft = _resolve_free_text_column(right, aliases)
        left_con = _resolve_concept_column(left, aliases, concept_aliases)
        right_con = _resolve_concept_column(right, aliases, concept_aliases)

        if left_ft and right_con:
            _, table_norm, col_norm = left_ft
            con_alias, con_col = right_con
            found.append((table_norm, col_norm, con_alias, con_col))
        elif right_ft and left_con:
            _, table_norm, col_norm = right_ft
            con_alias, con_col = left_con
            found.append((table_norm, col_norm, con_alias, con_col))

    return found


def _detect_any_join_use(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str]]:
    """Find free-text columns appearing in any JOIN ON clause (for fields
    flagged with ``flag_any_join``). Returns (table_norm, col_norm) pairs.
    """
    found: List[Tuple[str, str]] = []

    for join in tree.find_all(exp.Join):
        on_clause = join.args.get("on")
        if not on_clause:
            continue
        for col in on_clause.find_all(exp.Column):
            ft = _resolve_free_text_column(col, aliases)
            if not ft:
                continue
            _, table_norm, col_norm = ft
            info = _NORM_FREE_TEXT_FIELDS[(table_norm, col_norm)]
            if not info.get("flag_any_join"):
                continue
            found.append((table_norm, col_norm))

    return found


def _detect_numeric_casts(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    """Find ``CAST(<free_text_col> AS <numeric_type>)`` expressions.

    Returns list of (table_norm, col_norm, target_type).
    """
    found: List[Tuple[str, str, str]] = []

    for cast in tree.find_all(exp.Cast):
        inner = cast.this
        if not isinstance(inner, exp.Column):
            continue
        ft = _resolve_free_text_column(inner, aliases)
        if not ft:
            continue

        target_type = cast.args.get("to")
        if target_type is None:
            continue
        type_name = (
            target_type.this.name
            if hasattr(target_type, "this") and hasattr(target_type.this, "name")
            else str(target_type)
        ).upper()
        # exp.DataType.Type members (e.g. "INT", "BIGINT")
        if type_name not in NUMERIC_CAST_TYPES:
            continue

        _, table_norm, col_norm = ft
        found.append((table_norm, col_norm, type_name))

    return found


def _detect_numeric_comparisons(
    select: exp.Select,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    """Find numeric-literal comparisons against free-text columns.

    Returns list of (table_norm, col_norm, literal_str).
    """
    found: List[Tuple[str, str, str]] = []
    NUMERIC_OPS = (exp.EQ, exp.NEQ, exp.LT, exp.LTE, exp.GT, exp.GTE)

    for node in select.walk():
        if not isinstance(node, NUMERIC_OPS):
            continue
        if not is_in_where_or_join_clause(node):
            continue

        left, right = node.this, node.expression

        col_side: Optional[Tuple[str, str, str]] = None
        lit_node: Optional[exp.Literal] = None

        if isinstance(left, exp.Column):
            ft = _resolve_free_text_column(left, aliases)
            if ft and _is_numeric_literal(right):
                col_side = ft
                lit_node = right  # type: ignore[assignment]

        if not col_side and isinstance(right, exp.Column):
            ft = _resolve_free_text_column(right, aliases)
            if ft and _is_numeric_literal(left):
                col_side = ft
                lit_node = left  # type: ignore[assignment]

        if col_side and lit_node is not None:
            _, table_norm, col_norm = col_side
            found.append((table_norm, col_norm, str(lit_node.this)))

    return found


# --- Rule -----------------------------------------------------------------


@register
class FreeTextColumnMisuseRule(Rule):
    """Free-text VARCHAR fields must not be joined to concept or compared numerically."""

    rule_id = "data_quality.free_text_column_misuse"
    name = "Free-Text Column Misuse"

    description = (
        "Detects when a free-text VARCHAR column (e.g. condition_occurrence.stop_reason, "
        "drug_exposure.lot_number, note_nlp.term_modifiers, location.state, location.zip) "
        "is joined to the concept table or compared to a numeric literal. These columns "
        "have no concept mapping and no numeric semantics."
    )

    severity = Severity.ERROR

    suggested_fix = "REPLACE: the JOIN to concept on the free-text column WITH a JOIN on the row's structural concept_id (condition_concept_id / drug_concept_id / note_nlp_concept_id / country_concept_id). Treat free-text columns as strings (LIKE / IS NULL / equality), never CAST or numeric-compare."
    long_description = (
        "Several OMOP CDM tables expose VARCHAR columns that store free-text "
        "metadata: condition_occurrence.stop_reason, drug_exposure.lot_number, "
        "note_nlp.term_modifiers, location.state, location.zip. These have no "
        "mapping into the concept table and no numeric semantics. Joining them "
        "to concept (on concept_name, concept_code, or any concept column) "
        "yields zero or coincidental matches; comparing them numerically forces "
        "an implicit cast that quietly returns no rows. Use these columns only "
        "for text filtering/display, and use the row's standard *_concept_id "
        "column for concept lookups."
    )

    example_bad = (
        "SELECT co.person_id\nFROM condition_occurrence co\nJOIN concept c ON co.stop_reason = c.concept_name;"
    )
    example_good = (
        "SELECT co.person_id\nFROM condition_occurrence co\nJOIN concept c ON co.condition_concept_id = c.concept_id;"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        if not sql:
            return []

        sql_lower = sql.lower()
        # Fast pre-filter: skip if no relevant table is mentioned
        if not any(t in sql_lower for t in _NORM_TARGET_TABLES):
            return []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if tree is None:
                continue

            # Skip outright if no target table is referenced in this tree
            if not any(has_table_reference(tree, t) for t in _NORM_TARGET_TABLES):
                continue

            aliases = _normalize_aliases(extract_aliases(tree))
            concept_aliases = _aliases_for_table(aliases, _NORM_CONCEPT)

            seen: Set[Tuple[str, ...]] = set()
            # Tracks (table_norm, col_norm) pairs that already produced a
            # join-to-concept violation in this tree, so we don't double-fire
            # the broader "any JOIN" detection on the same pair.
            concept_join_seen: Set[Tuple[str, str]] = set()

            for select in tree.find_all(exp.Select):
                # 1) Free-text column joined to concept
                if concept_aliases:
                    for table_norm, col_norm, con_alias, con_col in _detect_concept_joins(
                        select, aliases, concept_aliases
                    ):
                        key = ("join", table_norm, col_norm, con_alias, con_col)
                        if key in seen:
                            continue
                        seen.add(key)
                        concept_join_seen.add((table_norm, col_norm))

                        info = _NORM_FREE_TEXT_FIELDS[(table_norm, col_norm)]
                        replacement = info["replacement"]

                        violations.append(
                            self.create_violation(
                                message=(
                                    f"{table_norm}.{col_norm} ({info['description']}) "
                                    f"is joined to concept ({con_alias}.{con_col}). "
                                    f"This column has no concept mapping. "
                                    f"Use {table_norm}.{replacement} for concept lookups instead."
                                ),
                                suggested_fix=(
                                    f"REPLACE: `{table_norm}.{col_norm}` in the JOIN ON "
                                    f"clause WITH `{table_norm}.{replacement}`, OR REMOVE "
                                    f"the JOIN entirely and treat `{col_norm}` as opaque text."
                                ),
                                details={
                                    "type": "join_to_concept",
                                    "table": table_norm,
                                    "column": col_norm,
                                    "replacement": replacement,
                                },
                            )
                        )

                # 2) Numeric comparison against free-text column
                for table_norm, col_norm, literal in _detect_numeric_comparisons(select, aliases):
                    key = ("numeric", table_norm, col_norm, literal)
                    if key in seen:
                        continue
                    seen.add(key)

                    info = _NORM_FREE_TEXT_FIELDS[(table_norm, col_norm)]

                    violations.append(
                        self.create_violation(
                            message=(
                                f"{table_norm}.{col_norm} ({info['description']}) "
                                f"compared to numeric literal {literal}. "
                                f"This column is VARCHAR; numeric comparison forces "
                                f"an implicit cast and may silently return no rows."
                            ),
                            suggested_fix=(
                                f"REPLACE: the numeric comparison WITH a string-literal "
                                f"equality, `LIKE`, or `IS NULL` — `{table_norm}.{col_norm}` "
                                f"is VARCHAR free text."
                            ),
                            details={
                                "type": "numeric_comparison",
                                "table": table_norm,
                                "column": col_norm,
                                "literal": literal,
                            },
                        )
                    )

            # 3) CAST to numeric type — tree-wide.
            for table_norm, col_norm, type_name in _detect_numeric_casts(tree, aliases):
                key = ("cast", table_norm, col_norm, type_name)
                if key in seen:
                    continue
                seen.add(key)

                info = _NORM_FREE_TEXT_FIELDS[(table_norm, col_norm)]
                violations.append(
                    self.create_violation(
                        message=(
                            f"{table_norm}.{col_norm} ({info['description']}) "
                            f"is CAST to numeric type {type_name}. "
                            f"This column is VARCHAR free text; numeric coercion "
                            f"will silently fail or truncate."
                        ),
                        suggested_fix=(
                            f"REMOVE: the numeric `CAST({col_norm} AS ...)`. "
                            f"Use `{col_norm}` as text (`LIKE`, `IS NULL`, or string equality)."
                        ),
                        details={
                            "type": "cast_to_numeric",
                            "table": table_norm,
                            "column": col_norm,
                            "cast_target": type_name,
                        },
                    )
                )

            # 4) Any JOIN involving the free-text column (only for fields
            #    where flag_any_join=True). Skipped if a more specific
            #    join-to-concept violation already fired on the same pair.
            for table_norm, col_norm in _detect_any_join_use(tree, aliases):
                if (table_norm, col_norm) in concept_join_seen:
                    continue
                key = ("any_join", table_norm, col_norm)
                if key in seen:
                    continue
                seen.add(key)

                info = _NORM_FREE_TEXT_FIELDS[(table_norm, col_norm)]
                violations.append(
                    self.create_violation(
                        message=(
                            f"{table_norm}.{col_norm} ({info['description']}) "
                            f"used in a JOIN ON clause. This column has no "
                            f"structured semantics and should not appear in joins."
                        ),
                        suggested_fix=(
                            f"REPLACE: `{table_norm}.{col_norm}` in the JOIN ON clause "
                            f"WITH `{table_norm}.{info['replacement']}` (or another "
                            f"structural FK column)."
                        ),
                        details={
                            "type": "join_on_free_text",
                            "table": table_norm,
                            "column": col_norm,
                        },
                    )
                )

        return violations


__all__ = ["FreeTextColumnMisuseRule"]
