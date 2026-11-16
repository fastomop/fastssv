"""Concept Name Lookup Anti-pattern Rule.

Flags queries that pick out concepts by their display name instead of by an
identifier: ``concept.concept_name`` or ``concept_synonym.concept_synonym_name``
compared against string literals with ``=``, ``<>``, ``IN``/``NOT IN``, or a
pattern match (``LIKE``, ``ILIKE``, ``SIMILAR TO``, ``~``, ``~*``). Blank literals
(``<> ''``) and match-everything patterns (``LIKE '%'``) name no concept and are
ignored.

Names are display text: the CDM keys concepts by ``concept_id`` (and by
``concept_code`` within a ``vocabulary_id``), nothing constrains a name to be
unique, and vocabulary releases may reword it. A name filter therefore
returns a different concept set across sites and releases, and a pattern can
pull in unrelated concepts. Exact comparison is also case-sensitive in
PostgreSQL.

The column side is recognised through case-folding and trimming wrappers
(``LOWER``, ``UPPER``, ``INITCAP``, ``TRIM``/``LTRIM``/``RTRIM``/``BTRIM``) and casts,
and unqualified or CTE-renamed columns are resolved through column lineage.
"""

from typing import Dict, List, Optional, Tuple

from sqlglot import exp

from fastssv.core.base import Reference, Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import BOOK_SQL, BOOK_VOCABULARIES, CDM_CONVENTIONS, section
from fastssv.core.helpers import (
    extract_aliases,
    is_string_literal,
    normalize_name,
    parse_sql,
    resolve_column_source,
)
from fastssv.core.registry import register

_NAME_COLUMNS = {
    ("concept", "concept_name"): "Concept names",
    ("concept_synonym", "concept_synonym_name"): "Synonym names",
}
_CASE_FOLDS = (exp.Lower, exp.Upper, exp.Initcap)
_TRIM_FUNCS = {"btrim", "ltrim", "rtrim"}
_PATTERN_TYPES = (exp.Like, exp.ILike, exp.SimilarTo, exp.RegexpLike, exp.RegexpILike)
_PATTERN_OPS = {
    exp.Like: "LIKE",
    exp.ILike: "ILIKE",
    exp.SimilarTo: "SIMILAR TO",
    exp.RegexpLike: "~",
    exp.RegexpILike: "~*",
}
_ADVICE = "Use concept_id, or concept_code + vocabulary_id."


def _peel(node: exp.Expression) -> Tuple[exp.Expression, bool]:
    """Strip casts, parentheses, case folding and trimming; report whether case was folded."""
    folded = False
    while True:
        if isinstance(node, (exp.Cast, exp.TryCast, exp.Paren)):
            node = node.this
        elif isinstance(node, _CASE_FOLDS):
            node, folded = node.this, True
        elif isinstance(node, exp.Trim):
            node = node.this
        elif isinstance(node, exp.Anonymous) and node.name.lower() in _TRIM_FUNCS and node.expressions:
            node = node.expressions[0]
        else:
            return node, folded


def _is_name_literal(node: exp.Expression) -> bool:
    """A string literal carrying a name; '' (a blank-name check) identifies no concept."""
    return is_string_literal(node) and bool(str(node.this).strip())


def _name_label(node: exp.Expression, aliases: Dict[str, str]) -> Optional[str]:
    """The name-column label when ``node`` reads concept.concept_name or concept_synonym.concept_synonym_name."""
    if not isinstance(node, exp.Column):
        return None
    table, column = resolve_column_source(node, aliases)
    return _NAME_COLUMNS.get((normalize_name(table or ""), normalize_name(column or "")))


def _pattern_literal(node: exp.Expression) -> Optional[exp.Literal]:
    """A string pattern written as a literal, a concatenation of literals (`'%' || 'x' || '%'`), or the first
    member of `ANY (ARRAY[...])`."""
    node, _ = _peel(node)
    if isinstance(node, exp.Any):
        inner, _ = _peel(node.this)
        if isinstance(inner, exp.Array) and inner.expressions:
            members = [_peel(e)[0] for e in inner.expressions]
            names = [m for m in members if _is_name_literal(m) and str(m.this).strip("%").strip()]
            return names[0] if names else None
        return None
    if isinstance(node, (exp.DPipe, exp.Concat)):
        parts = [p for p in node.find_all(exp.Literal)]
        if parts and all(p.is_string for p in parts) and not list(node.find_all(exp.Column)):
            return exp.Literal.string("".join(str(p.this) for p in parts))
        return None
    return node if isinstance(node, exp.Literal) else None


def _has_wildcards(match: exp.Expression, pattern: str) -> bool:
    """A regex always matches substrings; LIKE / ILIKE / SIMILAR TO without wildcards is a plain name comparison."""
    if isinstance(match, (exp.RegexpLike, exp.RegexpILike)):
        return True
    wildcards = "%_" if isinstance(match, (exp.Like, exp.ILike)) else "%_|*+?()[]{}"
    return any(ch in pattern for ch in wildcards)


def _tail(label: str, exact_case_sensitive: bool) -> str:
    text = f" {label} are display text, not identifiers: not unique, and can be reworded between vocabulary releases."
    if exact_case_sensitive:
        text += " Exact comparison is also case-sensitive in PostgreSQL."
    return f"{text} {_ADVICE}"


@register
class ConceptNameLookupRule(Rule):
    """Warns when concepts are identified by concept_name / concept_synonym_name instead of an identifier."""

    rule_id = "anti_patterns.concept_name_lookup"
    name = "Concept Name Lookup Anti-pattern"
    description = (
        "Warns when queries identify concepts by concept_name or concept_synonym_name (equality, IN, or pattern "
        "matching) instead of concept_id or concept_code + vocabulary_id. Names are not unique and can change "
        "across vocabulary releases."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: `WHERE c.concept_name = '<name>'` WITH `WHERE c.concept_code = '<code>' AND c.vocabulary_id = "
        "'<vocab>'`, OR with `WHERE c.concept_id = <id>` if the concept_id is known."
    )
    long_description = (
        "concept_name is display text, not a key: the CDM identifies a concept by concept_id, or by concept_code "
        "within a vocabulary_id, and nothing makes a name unique. Vocabulary releases can reword names, so a "
        "name filter can return a different concept set across "
        "sites and releases, and a LIKE pattern pulls in every concept whose name happens to contain the text. "
        "Exact comparison is case-sensitive in PostgreSQL (gender concept 8507 is named 'MALE', not 'Male'). The "
        "same applies to concept_synonym.concept_synonym_name. Filter by concept_id, or by concept_code + "
        "vocabulary_id."
    )
    example_bad = "SELECT c.concept_id\nFROM concept c\nWHERE c.concept_name = 'Type 2 diabetes mellitus';"
    example_good = (
        "SELECT c.concept_id\nFROM concept c\nWHERE c.concept_code = 'E11'\n  AND c.vocabulary_id = 'ICD10CM';"
    )
    extra_examples = (
        RuleExample(
            sql="SELECT c.concept_id\nFROM concept c\nWHERE LOWER(c.concept_name) LIKE '%diabetes%';",
            severity=Severity.WARNING,
            note="Pattern match on concept_name: pulls in every concept whose name contains the text",
        ),
    )
    references = (
        section(CDM_CONVENTIONS, "concepts", "Concepts"),
        section(BOOK_VOCABULARIES, "concept-names", "Concept names"),
        section(BOOK_SQL, "case-sensitivity-in-string-comparisons", "Case sensitivity in string comparisons"),
        Reference(
            "OHDSI Vocabulary release notes, v20260227: concept name changes (ATC, CPT4)",
            "https://github.com/OHDSI/Vocabulary-v5.0/releases/tag/v20260227_1772176757.000000",
        ),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        lowered = sql.lower()
        if "concept_name" not in lowered and "concept_synonym_name" not in lowered:
            # A CTE can rename concept_name, but the base column is still spelled out somewhere.
            return []

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            aliases = extract_aliases(tree)
            violations.extend(self._comparisons(tree, aliases))
            violations.extend(self._in_lists(tree, aliases))
            violations.extend(self._patterns(tree, aliases))
            violations.extend(self._substring_functions(tree, aliases))
        return violations

    def _comparisons(self, tree: exp.Expression, aliases: Dict[str, str]) -> List[RuleViolation]:
        out: List[RuleViolation] = []
        for cmp in tree.find_all(exp.EQ, exp.NEQ):
            for col_side, lit_side in ((cmp.left, cmp.right), (cmp.right, cmp.left)):
                col, folded = _peel(col_side)
                lit, _ = _peel(lit_side)
                label = _name_label(col, aliases)
                if label is None or not _is_name_literal(lit):
                    continue
                op = "=" if isinstance(cmp, exp.EQ) else "<>"
                verb = "identifies" if op == "=" else "excludes"
                out.append(
                    self.create_violation(
                        message=(
                            f"Query {verb} concepts by name: {col_side.sql()} {op} {lit_side.sql()}."
                            + _tail(label, exact_case_sensitive=not folded)
                        ),
                        details={"column": col.sql(), "operator": op, "value": lit.this},
                    )
                )
                break
        return out

    def _in_lists(self, tree: exp.Expression, aliases: Dict[str, str]) -> List[RuleViolation]:
        out: List[RuleViolation] = []
        for in_expr in tree.find_all(exp.In):
            col, folded = _peel(in_expr.this)
            label = _name_label(col, aliases)
            if label is None:
                continue
            values = [v.this for v in (_peel(e)[0] for e in in_expr.expressions or []) if _is_name_literal(v)]
            if not values:
                continue
            shown = ", ".join(f"'{v}'" for v in values[:3]) + (", ..." if len(values) > 3 else "")
            negated = isinstance(in_expr.parent, exp.Not)
            op = "NOT IN" if negated else "IN"
            verb = "excludes" if negated else "identifies"
            out.append(
                self.create_violation(
                    message=(
                        f"Query {verb} concepts by name: {in_expr.this.sql()} {op} ({shown})."
                        + _tail(label, exact_case_sensitive=not folded)
                    ),
                    details={"column": col.sql(), "operator": op, "values": values},
                )
            )
        return out

    def _patterns(self, tree: exp.Expression, aliases: Dict[str, str]) -> List[RuleViolation]:
        out: List[RuleViolation] = []
        for match in tree.find_all(*_PATTERN_TYPES):
            col, folded = _peel(match.this)
            label = _name_label(col, aliases)
            if label is None:
                continue
            pattern = _pattern_literal(match.expression)
            if pattern is None or not _is_name_literal(pattern) or not str(pattern.this).strip("%").strip():
                continue
            negated = isinstance(match.parent, exp.Not)
            op = ("NOT " if negated else "") + _PATTERN_OPS[type(match)]
            verb = "excludes" if negated else "identifies"
            predicate = f"{match.this.sql()} {op} {match.expression.sql()}"
            if _has_wildcards(match, str(pattern.this)):
                message = (
                    f"Query {verb} concepts by pattern-matching names: {predicate}. A name pattern can match "
                    f"unrelated concepts and miss differently named ones." + _tail(label, exact_case_sensitive=False)
                )
            else:
                case_sensitive = not folded and not isinstance(match, (exp.ILike, exp.RegexpILike))
                message = f"Query {verb} concepts by name: {predicate}." + _tail(label, case_sensitive)
            out.append(
                self.create_violation(
                    message=message,
                    details={"column": col.sql(), "operator": op, "pattern": pattern.this},
                )
            )
        return out

    def _substring_functions(self, tree: exp.Expression, aliases: Dict[str, str]) -> List[RuleViolation]:
        """POSITION / STRPOS / CHARINDEX, STARTS_WITH and CONTAINS over a name with a literal: substring matches."""
        out: List[RuleViolation] = []
        for func in tree.find_all(exp.StrPosition, exp.StartsWith, exp.Contains):
            col, _ = _peel(func.this)
            label = _name_label(col, aliases)
            if label is None:
                continue
            literal = func.args.get("substr") if isinstance(func, exp.StrPosition) else func.expression
            literal, _ = _peel(literal)
            if not _is_name_literal(literal):
                continue
            negated = isinstance(func.parent, exp.Not)
            verb = "excludes" if negated else "identifies"
            out.append(
                self.create_violation(
                    message=(
                        f"Query {verb} concepts by pattern-matching names: {func.sql()}. A name pattern can match "
                        "unrelated concepts and miss differently named ones." + _tail(label, exact_case_sensitive=False)
                    ),
                    details={"column": col.sql(), "operator": func.key.upper(), "pattern": literal.this},
                )
            )
        return out


__all__ = ["ConceptNameLookupRule"]
