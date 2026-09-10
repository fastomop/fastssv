"""Standard Concept Enforcement Rule.

Targets two distinct OMOP CDM v5.4 concerns under one rule_id:

* **Source concepts** (``*_source_concept_id``). Pre-mapping layer; may be
  non-standard, deprecated, or unmapped. Always warn when used
  analytically without explicit mapping via ``concept_relationship
  'Maps to'`` or a specific literal filter.
* **Standard concepts** (``<event>_concept_id``). The CDM spec already
  guarantees these are standard — the ETL is responsible. Default mode
  trusts the ETL and only warns when the query is doing
  *vocabulary-context* work (joins ``concept``, ``concept_ancestor``,
  or ``concept_relationship``) without filtering by ``standard_concept
  = 'S'``. Strict mode preserves the historical broad check —
  belt-and-suspenders for ETL validation / new-dataset distrust.

This rewrite (`/loop` post-mortem of OHDSI Achilles batch): the prior
rule fired on every ``<event>_concept_id`` reference without
enforcement, producing 119 warnings on a single Achilles run where
nearly all are noise — Achilles trusts the ETL, OHDSI tooling does
not re-check ``standard_concept = 'S'`` on already-standard fields.
"""

from typing import Dict, List, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    has_condition,
    extract_aliases,
    extract_join_conditions,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register
from fastssv.schemas import (
    SOURCE_CONCEPT_FIELDS,
    STANDARD_CONCEPT_FIELDS,
    VOCABULARY_TABLES,
)

# relationship_id values commonly used for standard mapping in OMOP
MAPS_TO_RELATIONSHIP = "Maps to"


def _extract_concept_references(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """Extract all resolved (table, column) references for concept fields.

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

        refs.append((table, col_name))

    return refs


def _has_specific_concept_id_filter(
    tree: exp.Expression,
    aliases: Dict[str, str],
    standard_fields: Set[Tuple[str, str]],
) -> bool:
    """Check if query filters specific STANDARD concept fields with literal IDs.

    Only literal filters on columns that are actually in ``standard_fields``
    (e.g. ``condition_occurrence.condition_concept_id``) count as "user already
    chose specific standard concepts" intent. Literal filters on vocabulary
    table columns such as ``concept_ancestor.ancestor_concept_id`` don't —
    those are hierarchy-rollup inputs, not standard-concept enforcement.
    """
    from fastssv.core.helpers import is_numeric_literal

    tables_in_scope = {normalize_name(t) for t in aliases.values() if t}

    for node in tree.find_all((exp.EQ, exp.In)):
        if not isinstance(node.this, exp.Column):
            continue

        table_resolved, col_name = resolve_table_col(node.this, aliases)
        if not col_name:
            continue

        # Only literals on actual standard-concept fields count as intent.
        col_norm = normalize_name(col_name)
        if table_resolved:
            table_norm = normalize_name(table_resolved)
        else:
            # Unqualified — attribute to the unique standard-field-owning
            # table in scope, mirroring _extract_concept_references.
            candidates = [t for t in tables_in_scope if (t, col_norm) in standard_fields]
            if len(candidates) != 1:
                continue
            table_norm = candidates[0]
        if (table_norm, col_norm) not in standard_fields:
            continue

        # Check for EQ with numeric literal
        if isinstance(node, exp.EQ):
            right = node.expression
            if is_numeric_literal(right) and not is_numeric_literal(right, 0):
                return True

        # Check for IN with numeric literals
        if isinstance(node, exp.In):
            for val in node.expressions or []:
                if is_numeric_literal(val) and not is_numeric_literal(val, 0):
                    return True

    return False


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

    By OMOP CDM definition, concept_ancestor is a hierarchy over Standard
    Concepts only — both ancestor_concept_id and descendant_concept_id are
    guaranteed-standard. Feeding rows from concept_ancestor into a
    *_concept_id slot transitively guarantees the standard-concept property,
    so an additional ``standard_concept = 'S'`` filter would be redundant.

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
    ``UNION SELECT 19078106``). Such a codeset is standard by CDM definition, exactly like the direct
    ``IN (SELECT descendant_concept_id FROM concept_ancestor ...)`` form. This shape — ``WITH drug1 AS (SELECT
    descendant_concept_id AS concept_id FROM concept_ancestor WHERE ancestor_concept_id = N) ... JOIN drug1`` —
    was the single most frequent warning on LLM-written OMOP SQL."""
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
    `concept_ancestor.descendant_concept_id`, which is guaranteed-standard
    by OMOP CDM definition. The intermediate `concept` join is just a
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
    `concept_ancestor.descendant_concept_id`, which is guaranteed-standard
    by OMOP CDM definition (concept_ancestor is a hierarchy over Standard
    Concepts only). An additional `standard_concept = 'S'` filter would be
    redundant. The JOIN form is the more common idiom in OHDSI cohort SQL
    because it avoids a correlated subquery.
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
    """Ensures queries using STANDARD concept fields enforce standard concepts."""

    rule_id = "concept_standardization.standard_concept_enforcement"
    name = "Standard Concept Enforcement"
    description = (
        "Warns on `*_source_concept_id` analytical use without mapping to "
        "standard, and on vocabulary-context queries that join `concept` / "
        "`concept_ancestor` / `concept_relationship` without filtering by "
        "`standard_concept = 'S'`. Bare `<event>_concept_id` references in "
        "default mode are trusted (CDM-guaranteed standard); strict mode "
        "preserves the broad ETL-validation check."
    )
    severity = Severity.WARNING
    suggested_fix = "ADD: `AND c.standard_concept = 'S'` to vocabulary joins, OR for source concepts: `JOIN concept_relationship cr ON cr.concept_id_1 = <table>.<source_concept_id_col> AND cr.relationship_id = 'Maps to'` and use `cr.concept_id_2` downstream."
    long_description = (
        "Two failure modes, one rule. (1) Source concepts: the CDM defines "
        "``*_source_concept_id`` as the *pre-mapping* layer; values may be "
        "non-standard, deprecated, or zero (unmapped). Using a source "
        "column directly in cohort logic mixes vocabulary layers and "
        "produces non-reproducible counts. (2) Standard concepts in "
        "vocabulary context: when a query joins to ``concept`` / "
        "``concept_ancestor`` / ``concept_relationship``, the result rows "
        "span every standardness layer of the OMOP hierarchy unless the "
        "join filters by ``standard_concept = 'S'``. Default-mode rule "
        "trusts the ETL on bare ``<event>_concept_id`` references — "
        "every OHDSI analytical tool (Achilles, Atlas, HADES) writes SQL "
        "this way and re-checking is redundant. Strict mode escalates to "
        "ERROR and applies the broad check for new-dataset / ETL-"
        "validation use cases."
    )
    example_bad = (
        "-- Source concept without mapping:\n"
        "SELECT co.person_id, co.condition_source_concept_id\n"
        "FROM condition_occurrence co\n"
        "WHERE co.condition_source_concept_id = 4112343;\n"
        "\n"
        "-- Vocabulary context without standard filter:\n"
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'SNOMED';"
    )
    example_good = (
        "-- Source concept mapped via concept_relationship 'Maps to':\n"
        "SELECT co.person_id, cr.concept_id_2 AS standard_concept_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept_relationship cr\n"
        "  ON cr.concept_id_1 = co.condition_source_concept_id\n"
        " AND cr.relationship_id = 'Maps to';\n"
        "\n"
        "-- Vocabulary join with explicit standardness filter:\n"
        "SELECT co.person_id\n"
        "FROM condition_occurrence co\n"
        "JOIN concept c ON co.condition_concept_id = c.concept_id\n"
        "WHERE c.vocabulary_id = 'SNOMED'\n"
        "  AND c.standard_concept = 'S';"
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
            # Parse errors handled elsewhere
            return []

        # Normalised lookup sets.
        standard_fields: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in STANDARD_CONCEPT_FIELDS
        }
        source_fields: Set[Tuple[str, str]] = {(normalize_name(t), normalize_name(c)) for t, c in SOURCE_CONCEPT_FIELDS}
        already_standard: Set[Tuple[str, str]] = {
            (normalize_name(t), normalize_name(c)) for t, c in self.ALREADY_STANDARD_FIELDS
        }
        all_concept_fields = standard_fields | source_fields

        from fastssv.core.validation_context import get_validation_context

        ctx = get_validation_context()
        strict = ctx.should_escalate_rule(self.rule_id)

        for tree in trees:
            if tree is None:
                continue

            aliases = extract_aliases(tree)
            all_refs = _extract_concept_references(tree, aliases, all_concept_fields)

            # Classify references. ``_type_concept_id`` columns hold
            # data-provenance tokens (EHR / Claim / Registry) from the
            # Type Concept vocabulary — standard within their own
            # vocabulary by construction. Filtering them by
            # ``standard_concept = 'S'`` is a category error.
            source_refs: List[Tuple[str, str]] = []
            standard_refs: List[Tuple[str, str]] = []
            for table, col in all_refs:
                key = (normalize_name(table), normalize_name(col))
                if key in source_fields:
                    source_refs.append((table, col))
                elif (
                    key in standard_fields
                    and not normalize_name(col).endswith("_type_concept_id")
                    and key not in already_standard
                ):
                    standard_refs.append((table, col))

            if not source_refs and not standard_refs:
                continue

            # Enforcement signals (shared across both branches).
            has_standard_enforcement = _enforces_standard_concept(tree)
            has_maps_to = _uses_maps_to_relationship(tree)
            # Evaluated separately per field class: a literal filter on a
            # *standard* concept column must not suppress the source-concept
            # warning (and vice versa) — the "user already chose specific
            # concepts" intent only extends to the field class it filters.
            has_specific_source_filter = _has_specific_concept_id_filter(tree, aliases, source_fields)
            has_specific_standard_filter = _has_specific_concept_id_filter(tree, aliases, standard_fields)
            has_concept_ancestor_filter = _filters_via_concept_ancestor(tree, aliases, standard_fields)
            has_concept_ancestor_join = _has_clinical_join_to_concept_ancestor(tree, aliases, standard_fields)
            has_concept_ancestor_chain = _has_chained_join_to_concept_ancestor_via_concept(
                tree, aliases, standard_fields
            )
            has_concept_ancestor_source = _uses_concept_ancestor_derived_source(tree, aliases, standard_fields)
            any_concept_ancestor = (
                has_concept_ancestor_filter
                or has_concept_ancestor_join
                or has_concept_ancestor_chain
                or has_concept_ancestor_source
            )

            # --- Branch 1: source-concept fire (always-on, default + strict) ---
            # Source concepts are CDM-defined as the pre-mapping layer.
            # Analytical use without (a) ``Maps to`` mapping, (b) a specific
            # literal filter, or (c) a concept_ancestor pattern feeding into
            # it, mixes vocabulary layers silently.
            if source_refs and not (has_maps_to or has_specific_source_filter or any_concept_ancestor):
                first_t, first_c = source_refs[0]
                violations.append(
                    self.create_violation(
                        message=(
                            f"Query uses `{first_t}.{first_c}` (a source concept-id field) without "
                            "mapping to a standard concept. Source concepts are pre-mapping — they "
                            "may be non-standard, deprecated, or unmapped — and downstream "
                            "analytics will silently mix vocabulary layers."
                        ),
                        severity=Severity.WARNING,
                        suggested_fix=(
                            f"MAP: `JOIN concept_relationship cr ON cr.concept_id_1 = {first_t}.{first_c} "
                            "AND cr.relationship_id = 'Maps to'` and use `cr.concept_id_2` downstream. "
                            "Alternatively filter by literal source concept IDs to signal explicit "
                            "source-value intent."
                        ),
                        details={
                            "issue": "source_concept_not_mapped",
                            "references": [list(r) for r in source_refs],
                        },
                    )
                )

            # --- Branch 2: standard-concept fire (mode-tiered) ---
            # Default mode trusts the ETL on bare ``<event>_concept_id``
            # references. The only default-mode trigger is *vocabulary
            # context*: the query already joins ``concept`` /
            # ``concept_ancestor`` / ``concept_relationship`` so the
            # standardness layer matters for downstream rows.
            # Strict mode applies the broad historical check.
            if not standard_refs:
                continue

            vocab_in_scope = bool({normalize_name(t) for t in aliases.values() if t} & VOCABULARY_TABLES)

            if strict:
                fires_standard = not (
                    has_standard_enforcement or has_maps_to or has_specific_standard_filter or any_concept_ancestor
                )
            else:
                # An explicit literal concept-id filter on the standard field (`c.concept_id IN (974166, …)`) is a
                # deliberate choice of concepts; asking for `standard_concept = 'S'` on top adds nothing.
                fires_standard = vocab_in_scope and not (
                    has_standard_enforcement or any_concept_ancestor or has_specific_standard_filter
                )

            if not fires_standard:
                continue

            severity = Severity.ERROR if strict else Severity.WARNING
            if strict:
                message = (
                    "Query uses STANDARD concept fields without ensuring concepts are "
                    "standard. (Strict mode: cohort definitions must use standard concepts)"
                )
            else:
                message = (
                    "Query joins vocabulary tables (concept / concept_ancestor / "
                    "concept_relationship) and uses STANDARD concept fields without "
                    "filtering by `standard_concept = 'S'`. The join result will mix "
                    "standard and non-standard concepts across the hierarchy."
                )

            # CTE-shadow-aware suggested fix (preserved from previous rule).
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
                    "AND `WHERE c.standard_concept = 'S'` to filter to standard concepts. "
                    f"NOTE: this query has a CTE named `{shadow_list}` which shadows the OMOP "
                    "vocabulary table — the JOIN must be schema-qualified (`omop.concept`) "
                    "or the CTE renamed, otherwise the JOIN would bind to the CTE and the "
                    "`standard_concept` column would not exist."
                )
            else:
                suggested_fix = (
                    "ADD: `JOIN concept c ON c.concept_id = <table>.<concept_id_col>` "
                    "AND `WHERE c.standard_concept = 'S'` to filter to standard concepts."
                )

            violations.append(
                self.create_violation(
                    message=message,
                    severity=severity,
                    suggested_fix=suggested_fix,
                    details={
                        "issue": "standard_concept_not_enforced",
                        "strict_mode_escalated": severity == Severity.ERROR,
                        "vocabulary_context": vocab_in_scope,
                    },
                )
            )

        return violations


__all__ = ["StandardConceptEnforcementRule"]
