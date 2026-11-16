"""Concept Code Requires Vocabulary ID Rule.

CDM v5.4, CONCEPT.concept_code: "Note that concept codes are not unique across vocabularies." The Book of OHDSI
(5.2.9): "the same code can exist in different vocabularies with completely different meanings". A predicate on
``concept.concept_code`` (against a literal, a pattern, a range, or another column) without a ``vocabulary_id``
constraint on the same concept row therefore matches every vocabulary that uses the code: 'E11.9' is a code of
ICD10, ICD10CM and OPCS4.

The code column is followed through CTEs, derived tables and renaming aliases to the concept table it reads, and
may be wrapped in casts, LOWER / UPPER / TRIM or LEFT / SUBSTRING. A ``JOIN concept USING (concept_code)`` counts
as a code join. The predicate may sit in WHERE, any ON clause or HAVING.

A constraint is any AND-connected predicate, in any SELECT along the code column's lineage, on ``vocabulary_id``
of the same concept row: ``=`` (literal or column), ``IN``, or a pattern match, through casts, case folding and
COALESCE. An equality on the row's ``concept_id`` (a primary key) pins the row and counts too. A predicate under
OR or NOT constrains nothing.

The finding is a WARNING: whether other vocabularies hold the code depends on the vocabulary data.
"""

from typing import List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.helpers import (
    _explicit_projection,
    _pick_source,
    _query_branches,
    _select_sources,
    _source_query,
    _star_qualifiers,
    is_string_literal,
    normalize_name,
    parse_sql,
)
from fastssv.core.patch import add as patch_add, freeform, locate
from fastssv.core.references import BOOK_VOCABULARIES, cdm_table, section
from fastssv.core.registry import register
from fastssv.rules.anti_patterns._ap_code_shapes import CODE_SHAPES, RELEASE

RULE_ID = "anti_patterns.concept_code_requires_vocabulary_id"
_PATTERNS = (exp.Like, exp.ILike, exp.RegexpLike, exp.RegexpILike, exp.SimilarTo)
_WRAPPERS = (exp.Cast, exp.TryCast, exp.Paren, exp.Lower, exp.Upper, exp.Trim, exp.Left, exp.Right, exp.Substring)
_VOCAB_WRAPPERS = (exp.Cast, exp.TryCast, exp.Paren, exp.Lower, exp.Upper, exp.Trim, exp.Coalesce)

# A concept table read: (id of the SELECT that reads it, id of the Table node).
Instance = Tuple[int, int]


def _infer_vocabulary(codes: List[str]) -> Optional[str]:
    """The one vocabulary every code's shape belongs to in the embedded release, or None."""
    found: Set[str] = set()
    for code in codes:
        vocabs = [v for shape, v in CODE_SHAPES.items() if shape.fullmatch(code.strip())]
        if len(vocabs) != 1 or len(vocabs[0]) != 1:
            return None
        found.add(vocabs[0][0])
    return found.pop() if len(found) == 1 else None


def _candidates(codes: List[str]) -> List[str]:
    """Vocabularies whose codes share the shape of every code (for the message), empty if unknown."""
    common: Optional[Set[str]] = None
    for code in codes:
        vocabs = [set(v) for shape, v in CODE_SHAPES.items() if shape.fullmatch(code.strip())]
        if len(vocabs) != 1:
            return []
        common = vocabs[0] if common is None else common & vocabs[0]
    return sorted(common or [])


def _peel(node: Optional[exp.Expression], wrappers=_WRAPPERS) -> Optional[exp.Expression]:
    while isinstance(node, wrappers):
        node = node.this
    return node


def _resolve(select: exp.Select, qualifier: Optional[str], name: str, depth: int = 0) -> Optional[Instance]:
    """The concept table read that column ``name`` of ``select`` (qualified by ``qualifier``) comes from."""
    if depth > 12:
        return None
    sources = _select_sources(select)
    if qualifier:
        matches = [s for s in sources if normalize_name(s.alias_or_name) == qualifier]
        src = matches[0] if len(matches) == 1 else None
    else:
        src = _pick_source(select, sources, name, 0)
    if src is None:
        return None
    query = _source_query(select, src)
    if query is None:
        if isinstance(src, exp.Table) and normalize_name(src.name) == "concept":
            return id(select), id(src)
        return None
    branches = _query_branches(query)
    if len(branches) != 1:
        return None
    branch = branches[0]
    proj = _explicit_projection(branch, name)
    if proj is not None:
        inner = _peel(proj.this if isinstance(proj, exp.Alias) else proj)
        if not isinstance(inner, exp.Column):
            return None
        return _resolve(
            branch, normalize_name(inner.table) if inner.table else None, normalize_name(inner.name), depth + 1
        )
    stars = _star_qualifiers(branch)
    if not stars:
        return None
    star = stars[0] if len(stars) == 1 else None
    return _resolve(branch, star, name, depth + 1)


def _column_instance(col: exp.Column) -> Optional[Instance]:
    select = col.find_ancestor(exp.Select)
    if select is None:
        return None
    return _resolve(select, normalize_name(col.table) if col.table else None, normalize_name(col.name))


def _scope_alias(col: exp.Column) -> str:
    """The name the column's own SELECT uses for the source an unqualified ``col`` reads."""
    select = col.find_ancestor(exp.Select)
    src = _pick_source(select, _select_sources(select), normalize_name(col.name), 0) if select is not None else None
    return src.alias_or_name if src is not None else "concept"


def _conjunct(node: exp.Expression) -> bool:
    """Whether ``node`` is an AND-connected predicate of its SELECT (no OR / NOT above it)."""
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, (exp.Or, exp.Not)):
            return False
        if isinstance(parent, (exp.Where, exp.Join, exp.Having)):
            return True
        if not isinstance(parent, (exp.And, exp.Paren)):
            return False
        parent = parent.parent
    return False


def _constraint_instances(node: exp.Expression) -> Set[Instance]:
    """Concept reads whose vocabulary_id ``node`` constrains, or whose concept_id it pins to a literal."""
    found: Set[Instance] = set()
    if not isinstance(node, (exp.EQ, exp.In, *_PATTERNS)):
        return found
    sides = [node.this, node.expression] if isinstance(node, exp.EQ) else [node.this]
    for side in sides:
        col = _peel(side, _VOCAB_WRAPPERS)
        if not isinstance(col, exp.Column):
            continue
        name = normalize_name(col.name)
        other = node.expression if side is node.this else node.this
        if name == "vocabulary_id":
            if isinstance(node, exp.EQ) and isinstance(_peel(other), exp.Null):
                continue
        elif name == "concept_id" and isinstance(node, exp.EQ):
            other = _peel(other)
            if not (isinstance(other, exp.Literal) and not other.is_string):
                continue
        else:
            continue
        instance = _column_instance(col)
        if instance is not None:
            found.add(instance)
    return found


def _constrained_instances(tree: exp.Expression) -> Set[Instance]:
    """Concept reads constrained by a predicate that is AND-connected at the level of its whole SELECT."""
    found: Set[Instance] = set()
    for node in tree.find_all(exp.EQ, exp.In, *_PATTERNS):
        if _conjunct(node):
            found |= _constraint_instances(node)
    return found


def _and_leaves(node: exp.Expression) -> List[exp.Expression]:
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        return _and_leaves(node.left) + _and_leaves(node.right)
    return [node]


def _locally_constrained(predicate: exp.Expression, instance: Instance) -> bool:
    """Whether a predicate AND-connected to ``predicate`` constrains the same concept read, e.g. inside one branch of
    `(concept_code = 'a' AND vocabulary_id = 'X') OR (concept_code = 'b' AND vocabulary_id = 'Y')`."""
    node = predicate
    while node.parent is not None and not isinstance(node.parent, (exp.Select, exp.Not)):
        parent = node.parent
        if isinstance(parent, exp.And):
            sibling = parent.right if node is parent.left else parent.left
            if any(instance in _constraint_instances(leaf) for leaf in _and_leaves(sibling)):
                return True
        elif not isinstance(parent, (exp.Paren, exp.Or)):
            break
        node = parent
    return False


def _code_predicates(tree: exp.Expression):
    """(code column, predicate node, literal codes, kind) for each predicate on concept_code."""
    out = []
    for node in tree.find_all(exp.EQ, exp.In, exp.Between, *_PATTERNS):
        if node.find_ancestor(exp.Where, exp.Join, exp.Having) is None:
            continue
        if isinstance(node, exp.EQ):
            pairs = ((node.this, node.expression), (node.expression, node.this))
        else:
            pairs = ((node.this, None),)
        for side, other in pairs:
            col = _peel(side)
            if not isinstance(col, exp.Column) or normalize_name(col.name) != "concept_code":
                continue
            codes: List[str] = []
            if isinstance(node, exp.In):
                values = [_peel(v) for v in node.expressions or []]
                if not values or not all(is_string_literal(v) for v in values):
                    continue
                codes = [str(v.this) for v in values]
                kind = "IN clause"
            elif isinstance(node, exp.EQ):
                peeled = _peel(other)
                if is_string_literal(peeled):
                    codes, kind = [str(peeled.this)], "filtered"
                elif isinstance(peeled, exp.Column):
                    kind = "joined"
                else:
                    continue
            elif isinstance(node, exp.Between):
                kind = "BETWEEN range"
            else:
                kind = node.key.upper()
            out.append((col, node, codes, kind))
            break
    for join in tree.find_all(exp.Join):
        for ident in join.args.get("using") or []:
            if normalize_name(ident.name) == "concept_code" and isinstance(join.this, exp.Table):
                if normalize_name(join.this.name) == "concept":
                    select = join.find_ancestor(exp.Select)
                    out.append(((select, join.this), join, [], "joined with USING"))
    return out


@register
class ConceptCodeRequiresVocabularyIdRule(Rule):
    """concept_code predicates (literal filters, ranges, patterns and code joins) without vocabulary_id."""

    rule_id = RULE_ID
    name = "Concept Code Requires Vocabulary ID"
    description = (
        "Concept codes are not unique across vocabularies. A filter or join on concept.concept_code should constrain "
        "vocabulary_id on the same concept row, or it matches every vocabulary that uses the code."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "ADD: `AND <alias>.vocabulary_id = '<vocab>'` (or a join on vocabulary_id) alongside the concept_code "
        "predicate. Concept codes are not unique across vocabularies; without the constraint the same code in "
        "several vocabularies matches all of them."
    )
    long_description = (
        "The CDM specification notes that concept codes are not unique across vocabularies: the same code string "
        "can exist in several vocabularies with different meanings ('E11' and 'E11.9' are codes of ICD10, ICD10CM "
        "and OPCS4). A filter, range, pattern or join on concept_code without a vocabulary_id constraint on the "
        "same concept row matches all of them. The code column is followed through CTEs, derived tables and "
        "renames; the constraint may sit in any SELECT along that lineage and must be AND-connected. An equality "
        "on the same row's concept_id pins the row and counts too. A vocabulary is suggested only when the "
        "code's shape occurs in exactly one vocabulary of the embedded release."
    )
    example_bad = "SELECT c.concept_id\nFROM concept c\nWHERE c.concept_code = 'E11.9';"
    example_good = (
        "SELECT c.concept_id\nFROM concept c\nWHERE c.concept_code = 'E11.9'\n  AND c.vocabulary_id = 'ICD10CM';"
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT co.condition_occurrence_id, c.concept_id AS source_concept_id\n"
                "FROM condition_occurrence co\n"
                "JOIN concept c ON co.condition_source_value = c.concept_code;"
            ),
            severity=Severity.WARNING,
            note="Join on concept_code: source codes matched to every vocabulary that uses the same code",
        ),
    )
    references = (
        cdm_table("concept"),
        section(BOOK_VOCABULARIES, "concept-codes", "Concept codes"),
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        from fastssv.core.validation_context import get_validation_context

        if "concept_code" not in sql.lower():
            return []
        # Strict mode escalates this best-practice warning to ERROR.
        severity = Severity.ERROR if get_validation_context().should_escalate_rule(self.rule_id) else Severity.WARNING

        trees, parse_error = parse_sql(sql, dialect)
        if parse_error:
            return []

        violations: List[RuleViolation] = []
        for tree in trees:
            if tree is None:
                continue
            constrained = _constrained_instances(tree)
            seen: Set[Instance] = set()
            for col, node, codes, kind in _code_predicates(tree):
                if isinstance(col, tuple):
                    select, table = col
                    instance = (id(select), id(table)) if select is not None else None
                    qualifier = table.alias_or_name
                    shown = node.sql()
                else:
                    instance = _column_instance(col)
                    qualifier = None
                    shown = node.sql()
                if instance is None or instance in constrained or instance in seen:
                    continue
                if not isinstance(col, tuple) and _locally_constrained(node, instance):
                    continue
                seen.add(instance)
                if qualifier is None:
                    qualifier = col.table or _scope_alias(col)
                violations.append(self._violation(sql, node, codes, kind, shown, qualifier, severity))
        return violations

    def _violation(self, sql, node, codes, kind, shown, qualifier, severity) -> RuleViolation:
        inferred = _infer_vocabulary(codes) if codes else None
        candidates = _candidates(codes) if codes and not inferred else []
        if inferred:
            vocab_text = f"'{inferred}' (the only vocabulary with this code shape in release {RELEASE})"
            value = f"'{inferred}'"
        elif candidates:
            vocab_text = "one of " + ", ".join(f"'{v}'" for v in candidates) + " (vocabularies with this code shape)"
            value = "'<vocab>'"
        else:
            vocab_text = "'<vocab>'"
            value = "'<vocab>'"
        suggested_fix = (
            f"ADD: `AND {qualifier}.vocabulary_id = {value}` (or a join on vocabulary_id) in the same SELECT as the "
            f"concept_code predicate; the vocabulary is {vocab_text}."
        )
        span = locate(sql, node.sql()) if isinstance(node, (exp.EQ, exp.In) + _PATTERNS) else None
        patch = patch_add(span[1], f" AND {qualifier}.vocabulary_id = {value}") if span else freeform(suggested_fix)
        return RuleViolation(
            rule_id=RULE_ID,
            severity=severity,
            message=(
                f"concept_code {kind} without vocabulary_id: {shown}. Concept codes are not unique across "
                "vocabularies, so this matches the code in every vocabulary that uses it."
            ),
            suggested_fix=suggested_fix,
            details={"column": "concept.concept_code", "inferred_vocabulary": inferred, "candidates": candidates},
            suggested_fix_patch=patch,
        )


__all__ = ["ConceptCodeRequiresVocabularyIdRule"]
