"""Standard Concept Enforcement Rule.

Two OMOP CDM v5.4 concerns under one rule_id:

* **Source concepts matched against the hierarchy** (every mode, WARNING). ``concept_ancestor`` holds only standard
  and classification concepts; non-standard source concepts (ICD10CM, NDC, Read, ...) do not participate in the
  hierarchy. Comparing a ``*_source_concept_id`` with ``concept_ancestor.ancestor_concept_id`` /
  ``descendant_concept_id`` (directly, through an IN-subquery, or through a CTE whose column comes from
  concept_ancestor) keeps only records whose source code happens to be standard and silently drops the rest.
  Using source concepts on their own (literal ids, a codeset join) is legitimate and not flagged: the field-level
  specification says the source field "should only be used when Standard Concepts do not adequately represent the
  source detail", which makes direct use a supported choice. A source id matched against the standard side of a
  'Maps to' link is reported by ``joins.maps_to_direction``.
* **Standard concepts** (``<event>_concept_id``), strict mode only, WARNING. The CDM conventions put only Standard
  concepts in these fields ("Non-standard Concepts can only appear in *_source_concept_id fields"), so default mode
  trusts them and a query that relies on that is correct on a conformant CDM. Strict mode (ETL validation /
  new-dataset workflows) asks the query to check it anyway: ``standard_concept = 'S'``, a ``'Maps to'`` mapping, or a
  concept_ancestor expansion. A literal concept id is not a check: it can name a non-standard concept.
"""

from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleExample, RuleViolation, Severity
from fastssv.core.references import BOOK_VOCABULARIES, CDM_CONVENTIONS, cdm_table, section
from fastssv.core.helpers import (
    DERIVED,
    _query_branches,
    column_origins,
    unwrap_cast,
    has_condition,
    extract_aliases,
    extract_join_conditions,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register
from fastssv.core.helpers import comparison_pairs

from ._cs_scope import instance_filters, links
from fastssv.schemas import (
    SOURCE_CONCEPT_FIELDS,
    STANDARD_CONCEPT_FIELDS,
)

# relationship_id values commonly used for standard mapping in OMOP
MAPS_TO_RELATIONSHIP = "Maps to"


def _extract_concept_references(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> List[Tuple[str, str, exp.Column]]:
    """Extract all resolved (table, column, node) references for concept fields.

    For unqualified columns (e.g. ``condition_concept_id`` rather than
    ``co.condition_concept_id``), attribute to the unique table in scope
    whose schema lists the column as a standard concept field. Without
    this fallback, single-table queries that omit aliases miss the rule
    entirely.
    """
    refs: List[Tuple[str, str]] = []
    tables_in_scope = {normalize_name(t) for t in aliases.values() if t}

    for col in tree.find_all(exp.Column):
        table, col_name = resolve_table_col(col, aliases)

        if not col_name:
            continue
        if col_name != "concept_id" and not col_name.endswith("_concept_id"):
            continue

        if not table:
            # Unqualified — try to attribute to a unique standard-field-owning
            # table in scope. Skip if zero or multiple candidates (ambiguous).
            col_norm = normalize_name(col_name)
            candidates = [t for t in tables_in_scope if (t, col_norm) in standard_fields]
            if len(candidates) != 1:
                continue
            table = candidates[0]

        refs.append((table, col_name, col))

    return refs


_ANCESTOR_COLUMNS: Set[Tuple[str, str]] = {
    ("concept_ancestor", "ancestor_concept_id"),
    ("concept_ancestor", "descendant_concept_id"),
}


def _operand_origins(side: exp.Expression) -> Optional[Set[Tuple[str, str]]]:
    """Base columns behind one comparison operand: a column, or every branch's projection of an IN/scalar subquery.
    None when any part is unknown; a literal or computed projection shows up as DERIVED."""
    side = unwrap_cast(side)
    if isinstance(side, exp.Column):
        return column_origins(side)
    query = side.this if isinstance(side, exp.Subquery) else side
    if not isinstance(query, (exp.Select, exp.SetOperation)):
        return None
    out: Set[Tuple[str, str]] = set()
    for branch in _query_branches(query):
        if not branch.expressions:
            return None
        first = branch.expressions[0]
        expr = unwrap_cast(first.this if isinstance(first, exp.Alias) else first)
        if not isinstance(expr, exp.Column):
            out.add((DERIVED, normalize_name(first.alias_or_name)))
            continue
        origins = column_origins(expr)
        if origins is None:
            return None
        out |= origins
    return out


def _consequence(node: exp.Expression) -> str:
    """What a never-matching comparison does to the non-standard-coded records, given where it sits."""
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        if isinstance(parent, exp.Not):
            return (
                "so this exclusion never removes them: they stay in the result although their mapped concept may "
                "lie in the excluded hierarchy."
            )
        if isinstance(parent, exp.Join):
            if parent.args.get("side"):
                return "so the outer join leaves them unmatched and they are treated as outside the hierarchy."
            return "so they are silently dropped."
        if isinstance(parent, (exp.Where, exp.Having)):
            return "so they are silently dropped."
        parent = parent.parent
    return "so this expression classifies them as outside the hierarchy."


def _source_matched_to_ancestor(
    tree: exp.Expression, source_fields: Set[Tuple[str, str]]
) -> List[Tuple[Tuple[str, str], Tuple[str, str], exp.Expression]]:
    """(source column, concept_ancestor column, predicate) for each comparison of a *_source_concept_id with a value
    set drawn only from concept_ancestor ids."""
    hits = []
    for node, left, right in comparison_pairs(tree):
        lo, ro = _operand_origins(left), _operand_origins(right)
        for a, b in ((lo, ro), (ro, lo)):
            if a and b and a <= source_fields and b <= _ANCESTOR_COLUMNS:
                hits.append((sorted(a)[0], sorted(b)[0], node))
    return hits


def _filters_via_concept_ancestor(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> bool:
    """True if the query restricts a STANDARD concept_id column via
    concept_ancestor's hierarchy.

    Pattern:
        ``<standard_concept_id_col> IN (SELECT descendant_concept_id
                                        FROM concept_ancestor [WHERE ...])``
        ``<standard_concept_id_col> IN (SELECT ancestor_concept_id
                                        FROM concept_ancestor [WHERE ...])``

    concept_ancestor holds only Standard and Classification concepts (CDM
    v5.4 conventions), and a Classification concept is never stored in an
    event column, so feeding rows from concept_ancestor into a *_concept_id
    slot matches only Standard concepts; an additional
    ``standard_concept = 'S'`` filter would be redundant.

    Scope-limited to the *direct* subquery form. CTE-indirected patterns
    (``WITH cte AS (SELECT descendant_concept_id FROM concept_ancestor ...)
    SELECT ... WHERE col IN (SELECT concept_id FROM cte)``) are not traced
    here — they're handled by existing rule behavior, where the literal-vs-
    standard distinction is harder to verify safely without inlining the CTE.
    """
    tables_in_scope = {normalize_name(t) for t in aliases.values() if t}

    for node in tree.find_all(exp.In):
        # Subquery form only — IN (1, 2, 3) has node.expressions populated.
        if node.expressions:
            continue

        if not isinstance(node.this, exp.Column):
            continue

        table_resolved, col_name = resolve_table_col(node.this, aliases)
        if not col_name:
            continue
        col_norm = normalize_name(col_name)

        if table_resolved:
            table_norm = normalize_name(table_resolved)
        else:
            candidates = [t for t in tables_in_scope if (t, col_norm) in standard_fields]
            if len(candidates) != 1:
                continue
            table_norm = candidates[0]

        if (table_norm, col_norm) not in standard_fields:
            continue

        if _in_subquery_selects_concept_ancestor_id(node):
            return True

    return False


def _concept_ancestor_derived_sources(tree: exp.Expression) -> Set[str]:
    """Names of CTEs / derived tables whose rows come from ``concept_ancestor`` (every SELECT branch projects
    ``descendant_concept_id`` / ``ancestor_concept_id`` — aliased or not — or is a literal-only SELECT such as
    ``UNION SELECT 19078106``). concept_ancestor holds only Standard and Classification concepts (CDM v5.4
    conventions: "Only valid and Standard Concepts participate in the CONCEPT_ANCESTOR table"), and a Classification
    concept is never stored in an event column, so such a codeset matches only Standard concepts there, exactly like
    the direct ``IN (SELECT descendant_concept_id FROM concept_ancestor ...)`` form."""
    out: Set[str] = set()
    named: List[Tuple[str, exp.Expression]] = []
    for cte in tree.find_all(exp.CTE):
        if cte.alias:
            named.append((normalize_name(cte.alias), cte.this))
    for sub in tree.find_all(exp.Subquery):
        if sub.alias:
            named.append((normalize_name(sub.alias), sub.this))
    for name, body in named:
        branches = [body.this, body.expression] if isinstance(body, exp.Union) else [body]
        # flatten nested unions
        stack, selects = list(branches), []
        while stack:
            b = stack.pop()
            if isinstance(b, exp.Union):
                stack.extend([b.this, b.expression])
            elif isinstance(b, exp.Select):
                selects.append(b)
        if not selects:
            continue
        ok = True
        from_ancestor = False
        for sel in selects:
            tables = {normalize_name(t.name) for t in sel.find_all(exp.Table)}
            if not tables:  # literal-only branch (SELECT 19078106)
                ok &= all(
                    isinstance(e.unalias() if isinstance(e, exp.Alias) else e, exp.Literal) for e in sel.expressions
                )
                continue
            if tables != {"concept_ancestor"}:
                ok = False
                break
            from_ancestor = True
            projected = [e.unalias() if isinstance(e, exp.Alias) else e for e in sel.expressions]
            ok &= all(
                isinstance(e, exp.Column) and normalize_name(e.name) in {"descendant_concept_id", "ancestor_concept_id"}
                for e in projected
            )
        if ok and from_ancestor:
            out.add(name)
    return out


def _uses_concept_ancestor_derived_source(
    tree: exp.Expression, aliases: Dict[str, str], standard_fields: Set[Tuple[str, str]]
) -> bool:
    """A standard *_concept_id column is joined (EQ) or filtered (IN subquery) against a concept_ancestor-derived
    codeset — transitively standard, so `standard_concept = 'S'` would be redundant."""
    sources = _concept_ancestor_derived_sources(tree)
    if not sources:
        return False
    aliases_to_source = {normalize_name(a): normalize_name(t) for a, t in aliases.items() if t}
    for a, t in list(aliases_to_source.items()):
        if t in sources:
            aliases_to_source[a] = t

    def is_source_col(col: exp.Column) -> bool:
        tbl = normalize_name(col.table) if col.table else None
        return bool(tbl) and (tbl in sources or aliases_to_source.get(tbl) in sources)

    tables_in_scope = {normalize_name(t) for t in aliases.values() if t}

    def is_standard_col(col: exp.Column) -> bool:
        t, c = resolve_table_col(col, aliases)
        if not c:
            return False
        if t:
            return (normalize_name(t), normalize_name(c)) in standard_fields
        # unqualified (e.g. `WHERE drug_concept_id IN (...)` inside a single-table CTE): accept when exactly one
        # table in scope owns that standard field
        return len([tb for tb in tables_in_scope if (tb, normalize_name(c)) in standard_fields]) == 1

    for eq in tree.find_all(exp.EQ):
        l, r = eq.this, eq.expression
        if isinstance(l, exp.Column) and isinstance(r, exp.Column):
            if (is_standard_col(l) and is_source_col(r)) or (is_standard_col(r) and is_source_col(l)):
                return True
    for node in tree.find_all(exp.In):
        if node.expressions or not isinstance(node.this, exp.Column) or not is_standard_col(node.this):
            continue
        sub = node.args.get("query")
        if sub is not None and {normalize_name(t.name) for t in sub.find_all(exp.Table)} & sources:
            return True
    return False


def _has_chained_join_to_concept_ancestor_via_concept(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> bool:
    """True if a clinical *_concept_id is constrained to standard concepts via
    a two-hop JOIN chain through `concept.concept_id` to
    `concept_ancestor.descendant_concept_id` (or `ancestor_concept_id`).

    Pattern (semantically identical to the direct-JOIN and IN-subquery forms
    handled elsewhere; users adopt this shape when they also want to project
    columns from the concept table, e.g. `concept_name`):

        FROM <clinical>
        JOIN concept c ON <clinical>.<concept_id_col> = c.concept_id
        JOIN concept_ancestor ca ON c.concept_id = ca.descendant_concept_id
        WHERE ca.ancestor_concept_id = <id>

    The chain transitively constrains `<clinical>.<concept_id_col>` to
    `concept_ancestor.descendant_concept_id`, which is Standard or
    Classification (CDM v5.4 conventions). The intermediate `concept` join is just a
    relay — it doesn't restrict standardness further or undo it. An
    additional `concept.standard_concept = 'S'` filter would be redundant.

    The two hops can appear in either order in the query, and either side
    of each EQ; this helper checks both orientations.
    """
    target_cols = {"descendant_concept_id", "ancestor_concept_id"}
    join_conditions = extract_join_conditions(tree, aliases)

    has_clinical_to_concept = False
    has_concept_to_concept_ancestor = False

    for lt, lc, rt, rc in join_conditions:
        for s1_t, s1_c, s2_t, s2_c in ((lt, lc, rt, rc), (rt, rc, lt, lc)):
            # Hop 1: clinical fact table . *_concept_id = concept.concept_id
            if normalize_name(s2_t) == "concept" and normalize_name(s2_c) == "concept_id":
                key = (normalize_name(s1_t), normalize_name(s1_c))
                if key in standard_fields:
                    has_clinical_to_concept = True
            # Hop 2: concept.concept_id = concept_ancestor.{ancestor,descendant}_concept_id
            if (
                normalize_name(s1_t) == "concept"
                and normalize_name(s1_c) == "concept_id"
                and normalize_name(s2_t) == "concept_ancestor"
                and normalize_name(s2_c) in target_cols
            ):
                has_concept_to_concept_ancestor = True

    return has_clinical_to_concept and has_concept_to_concept_ancestor


def _has_clinical_join_to_concept_ancestor(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> bool:
    """True if the query joins a clinical fact table directly to
    concept_ancestor on its descendant_concept_id or ancestor_concept_id,
    constraining the clinical *_concept_id slot to standard concepts.

    Pattern (semantically identical to the IN-subquery form handled by
    `_filters_via_concept_ancestor`):

        FROM drug_exposure de
        JOIN concept_ancestor ca ON de.drug_concept_id = ca.descendant_concept_id
        WHERE ca.ancestor_concept_id = <id>

    Every `de.drug_concept_id` that survives the join is by construction a
    `concept_ancestor.descendant_concept_id`, which is Standard or
    Classification (CDM v5.4 conventions: "Only valid and Standard Concepts
    participate in the CONCEPT_ANCESTOR table"). An additional
    `standard_concept = 'S'` filter would be redundant.
    """
    target_cols = {"descendant_concept_id", "ancestor_concept_id"}

    for lt, lc, rt, rc in extract_join_conditions(tree, aliases):
        # Check both join directions: clinical=ca, ca=clinical.
        for side1_table, side1_col, side2_table, side2_col in (
            (lt, lc, rt, rc),
            (rt, rc, lt, lc),
        ):
            if side2_table != "concept_ancestor":
                continue
            if normalize_name(side2_col) not in target_cols:
                continue
            key = (normalize_name(side1_table), normalize_name(side1_col))
            if key in standard_fields:
                return True
    return False


def _in_subquery_selects_concept_ancestor_id(in_node: exp.In) -> bool:
    """True if the IN's subquery selects descendant_concept_id or
    ancestor_concept_id directly from concept_ancestor."""
    selects = list(in_node.find_all(exp.Select))
    if not selects:
        return False

    select = selects[0]

    if not has_table_reference(select, "concept_ancestor"):
        return False

    target_cols = {"descendant_concept_id", "ancestor_concept_id"}
    for proj in select.expressions or []:
        underlying = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(underlying, exp.Column):
            if normalize_name(underlying.name) in target_cols:
                return True
    return False


def _targets_non_standard_on_purpose(
    tree: exp.Expression, aliases: Dict[str, str], standard_fields: Set[Tuple[str, str]]
) -> bool:
    """ETL-QA shapes that look for unmapped or non-standard rows: a standard field compared with concept 0
    (`= 0`, `IN (0)`, `<> 0`), or an explicit non-standard filter (`standard_concept IS NULL`, `<> 'S'`). Concept 0 has
    a NULL standard_concept, so requiring `standard_concept = 'S'` there would empty the result."""
    from fastssv.core.helpers import is_numeric_literal

    tables_in_scope = {normalize_name(t) for t in aliases.values() if t}

    def is_standard_field(col: exp.Expression) -> bool:
        if not isinstance(col, exp.Column):
            return False
        table, name = resolve_table_col(col, aliases)
        name = normalize_name(name)
        if table:
            return (normalize_name(table), name) in standard_fields
        return len([t for t in tables_in_scope if (t, name) in standard_fields]) == 1

    for node in tree.find_all(exp.EQ, exp.NEQ):
        for col, val in ((node.this, node.expression), (node.expression, node.this)):
            if is_standard_field(col) and is_numeric_literal(val, 0):
                return True
            if (
                isinstance(node, exp.NEQ)
                and isinstance(col, exp.Column)
                and normalize_name(col.name) == "standard_concept"
                and isinstance(val, exp.Literal)
                and val.is_string
                and val.this.upper() == "S"
            ):
                return True
    for node in tree.find_all(exp.In):
        if is_standard_field(node.this) and any(is_numeric_literal(v, 0) for v in node.expressions or []):
            return True
    for node in tree.find_all(exp.Is):
        col = node.this
        if (
            isinstance(col, exp.Column)
            and normalize_name(col.name) == "standard_concept"
            and isinstance(node.expression, exp.Null)
            and not isinstance(node.parent, exp.Not)
        ):
            return True
    return False


def _each_field_checked_as_standard(tree: exp.Expression, refs: List[Tuple[str, str]]) -> bool:
    """Every referenced standard field is matched to a concept row that is required to be Standard. A Standard filter
    on some other concept row (a drug's, say) checks nothing about a condition column."""
    if not has_table_reference(tree, "concept"):
        return False
    standard_rows = {
        key
        for key, flts in instance_filters(tree, "concept", "standard_concept").items()
        if any(f.required and f.op in ("eq", "in") and {v.upper() for v in f.values} == {"S"} for f in flts)
    }
    if not standard_rows:
        return False
    checked = {link.origin for link in links(tree, "concept", "concept_id") if link.instance in standard_rows}
    return all((normalize_name(t), normalize_name(c)) in checked for t, c in refs)


def _source_relayed_to_ancestor(tree: exp.Expression, source_fields: Set[Tuple[str, str]]):
    """(source field, concept_ancestor column, predicate) where a source field and a concept_ancestor id are both
    matched to the same concept row, so the source id meets the hierarchy through that row."""
    to_concept = [link for link in links(tree, "concept", "concept_id") if link.required]
    by_instance: Dict[Tuple[int, str], List] = {}
    for link in to_concept:
        by_instance.setdefault(link.instance, []).append(link)
    out = []
    for group in by_instance.values():
        sources = [link for link in group if link.origin in source_fields]
        ancestors = [link for link in group if link.origin in _ANCESTOR_COLUMNS]
        for src in sources:
            for anc in ancestors:
                out.append((src.origin, anc.origin, anc.node))
    return out


def _enforces_standard_concept(tree: exp.Expression) -> bool:
    """Detect if query enforces standard concepts via standard_concept = 'S'."""
    if not has_table_reference(tree, "concept"):
        return False

    return has_condition(tree, "standard_concept", {"s"}, require_where_clause=True)


def _uses_maps_to_relationship(tree: exp.Expression) -> bool:
    """Detect if query uses concept_relationship relationship_id = 'Maps to'."""
    if not has_table_reference(tree, "concept_relationship"):
        return False

    return has_condition(tree, "relationship_id", {normalize_name(MAPS_TO_RELATIONSHIP)}, require_where_clause=True)


@register
class StandardConceptEnforcementRule(Rule):
    """Source concepts matched against concept_ancestor; strict mode: standard concept fields not enforced."""

    rule_id = "concept_standardization.standard_concept_enforcement"
    name = "Standard Concept Enforcement"
    description = (
        "Warns when a `*_source_concept_id` is matched against concept_ancestor ids, which hold only standard and "
        "classification concepts, so non-standard source codes silently drop out. Strict mode additionally warns "
        "when `<event>_concept_id` is used without `standard_concept = 'S'`, a `Maps to` mapping or a "
        "concept_ancestor expansion."
    )
    severity = Severity.WARNING
    suggested_fix = (
        "REPLACE: the `*_source_concept_id` in the concept_ancestor comparison WITH the paired standard "
        "`*_concept_id` (e.g. condition_concept_id), which is what the hierarchy is built over."
    )
    long_description = (
        "concept_ancestor is built over standard and classification concepts only; non-standard source concepts "
        "(ICD10CM, NDC, Read, local codes) do not take part in the hierarchy. Matching *_source_concept_id against "
        "ancestor_concept_id or descendant_concept_id therefore keeps just the records whose source code is itself "
        "standard (for example SNOMED-coded sources) and silently loses every record coded in a source vocabulary. "
        "Expand the hierarchy over the standard *_concept_id instead. Filtering on source concepts by themselves "
        "(literal ids, a codeset) is legitimate and not flagged. In strict mode the rule also distrusts the ETL and "
        "warns when <event>_concept_id is used without a standardness check. The CDM requires the ETL to store "
        "Standard concepts there, so such a query is correct on a conformant CDM; the strict-mode finding is a "
        "WARNING."
    )
    example_bad = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept_ancestor ca ON ca.descendant_concept_id = co.condition_source_concept_id\n"
        "WHERE ca.ancestor_concept_id = 201826;"
    )
    example_good = (
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept_ancestor ca ON ca.descendant_concept_id = co.condition_concept_id\n"
        "WHERE ca.ancestor_concept_id = 201826;"
    )
    references = (
        section(CDM_CONVENTIONS, "concept-ancestor", "Concept ancestor"),
        cdm_table("concept_ancestor"),
        section(BOOK_VOCABULARIES, "standardConcepts", "Standard concepts"),
    )
    extra_examples = (
        RuleExample(
            sql=(
                "SELECT co.person_id\n"
                "FROM condition_occurrence co\n"
                "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
                "WHERE c.concept_name ILIKE '%diabetes%';"
            ),
            severity=Severity.WARNING,
            note="Strict mode: standard concept field used without a standardness check",
            strict=True,
        ),
    )

    # Fields that are already guaranteed to be standard by OMOP CDM design
    # These do NOT require explicit standard_concept = 'S' enforcement
    ALREADY_STANDARD_FIELDS = {
        # ERA tables - derived from occurrence tables, only contain standard concepts
        ("condition_era", "condition_concept_id"),
        ("drug_era", "drug_concept_id"),
        ("dose_era", "drug_concept_id"),
        # Person demographic attributes - always standard
        ("person", "gender_concept_id"),
        ("person", "race_concept_id"),
        ("person", "ethnicity_concept_id"),
    }

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        """Validate SQL and return list of violations."""
        violations = []

        trees, error = parse_sql(sql, dialect)
        if error:
            return []

        standard_fields: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in STANDARD_CONCEPT_FIELDS
        }
        source_fields: Set[Tuple[str, str]] = {(normalize_name(t), normalize_name(c)) for t, c in SOURCE_CONCEPT_FIELDS}
        already_standard: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in self.ALREADY_STANDARD_FIELDS
        }

        from fastssv.core.validation_context import get_validation_context

        # The strict-mode check is an extra layer the user opts into; it is not an escalation of the default finding.
        strict = get_validation_context().strict_mode

        for tree in trees:
            if tree is None:
                continue

            seen: Set[Tuple[str, str]] = set()
            hits = _source_matched_to_ancestor(tree, source_fields) + _source_relayed_to_ancestor(tree, source_fields)
            for (src_t, src_c), (anc_t, anc_c), node in hits:
                if (src_c, anc_c) in seen:
                    continue
                seen.add((src_c, anc_c))
                standard_col = src_c.replace("_source_concept_id", "_concept_id")
                violations.append(
                    self.create_violation(
                        message=(
                            f"`{src_t}.{src_c}` (a source concept id) is matched against {anc_t}.{anc_c}, which "
                            "holds only standard and classification concepts. Records whose source code is "
                            f"non-standard (ICD10CM, NDC, ...) can never match, {_consequence(node)}"
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=(
                            f"REPLACE: `{src_c}` WITH `{standard_col}` in this comparison; the hierarchy is "
                            "defined over standard concepts."
                        ),
                        details={
                            "issue": "source_concept_matched_to_concept_ancestor",
                            "source_column": f"{src_t}.{src_c}",
                            "ancestor_column": f"{anc_t}.{anc_c}",
                            "predicate": node.sql(),
                        },
                    )
                )

            if not strict:
                continue

            # Strict mode distrusts the ETL on <event>_concept_id and wants the query itself to enforce standardness.
            aliases = extract_aliases(tree)
            standard_refs = [
                (table, col)
                for table, col, _ in _extract_concept_references(tree, aliases, standard_fields)
                if (normalize_name(table), normalize_name(col)) in standard_fields
                and not normalize_name(col).endswith("_type_concept_id")
                and (normalize_name(table), normalize_name(col)) not in already_standard
            ]
            if not standard_refs:
                continue
            if (
                _each_field_checked_as_standard(tree, standard_refs)
                or _targets_non_standard_on_purpose(tree, aliases, standard_fields)
                or _uses_maps_to_relationship(tree)
                or _filters_via_concept_ancestor(tree, aliases, standard_fields)
                or _has_clinical_join_to_concept_ancestor(tree, aliases, standard_fields)
                or _has_chained_join_to_concept_ancestor_via_concept(tree, aliases, standard_fields)
                or _uses_concept_ancestor_derived_source(tree, aliases, standard_fields)
            ):
                continue

            message = (
                "Strict mode: the query uses STANDARD concept fields without checking that the concepts are "
                "Standard. The CDM requires the ETL to store Standard concepts there (or 0 for an unmapped record), "
                "so this matters only if the ETL may not conform."
            )
            # A CTE named ``concept`` would capture the bare ``JOIN concept c`` the fix recommends.
            top_with = tree.args.get("with_") or tree.args.get("with")
            top_cte_names: Set[str] = set()
            if top_with is not None:
                for top_cte in top_with.expressions or []:
                    if top_cte.alias:
                        top_cte_names.add(normalize_name(top_cte.alias))
            shadow = top_cte_names & {"concept", "concept_relationship"}
            if shadow:
                shadow_list = ", ".join(sorted(shadow))
                suggested_fix = (
                    "ADD: `JOIN omop.concept c ON c.concept_id = <table>.<concept_id_col>` "
                    "AND `WHERE (c.standard_concept = 'S' OR c.concept_id = 0)` to check standardness while "
                    "keeping unmapped records (concept 0 has no standard_concept). "
                    f"NOTE: this query has a CTE named `{shadow_list}` which shadows the OMOP "
                    "vocabulary table — the JOIN must be schema-qualified (`omop.concept`) "
                    "or the CTE renamed, otherwise the JOIN would bind to the CTE and the "
                    "`standard_concept` column would not exist."
                )
            else:
                suggested_fix = (
                    "ADD: `JOIN concept c ON c.concept_id = <table>.<concept_id_col>` "
                    "AND `WHERE (c.standard_concept = 'S' OR c.concept_id = 0)` to check standardness while "
                    "keeping unmapped records (concept 0 has no standard_concept)."
                )
            violations.append(
                self.create_violation(
                    message=message,
                    severity=Severity.WARNING,
                    suggested_fix=suggested_fix,
                    details={
                        "issue": "standard_concept_not_enforced",
                        "strict_mode": True,
                    },
                )
            )

        return violations


__all__ = ["StandardConceptEnforcementRule"]
