"""Canonical Vocabulary String Value Validation Rule.

Validates that filters on canonical OMOP vocabulary string columns
(``concept_class_id``, ``domain_id``, ``vocabulary_id``) use the canonical
casing — and, for ``vocabulary_id``, are hyphen-free. Wrong casing or
hyphens silently match zero rows under case-sensitive comparison.

Replaces three previously separate rules:

- data_quality.concept_class_id_case_sensitivity (VOCAB_007)
- data_quality.domain_id_case_sensitivity        (VOCAB_006)
- data_quality.vocabulary_id_validation          (VOCAB_004 / VOCAB_005)

Each had identical detection shape (find EQ/IN/LIKE filters on the column,
look up the value in a canonical map, flag if mismatched) and differed only
in (a) which canonical map applied, and (b) whether hyphens are also flagged
(``vocabulary_id`` only).
"""

from typing import Callable, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.patch import locate, replace as patch_replace
from fastssv.core.registry import register


# --- Per-column canonical maps -------------------------------------------

CANONICAL_CONCEPT_CLASSES: Dict[str, str] = {
    # --- RxNorm ---
    "ingredient": "Ingredient",
    "clinicaldrug": "Clinical Drug",
    "brandeddrug": "Branded Drug",
    "clinicaldrugform": "Clinical Drug Form",
    "clinicaldrugcomp": "Clinical Drug Comp",
    "brandeddrugcomp": "Branded Drug Comp",
    "brandeddrugform": "Branded Drug Form",
    "clinicalpack": "Clinical Pack",
    "brandedpack": "Branded Pack",
    "brandname": "Brand Name",
    "doseform": "Dose Form",
    "doseformgroup": "Dose Form Group",
    # --- SNOMED ---
    "clinicalfinding": "Clinical Finding",
    "procedure": "Procedure",
    "bodystructure": "Body Structure",
    "observableentity": "Observable Entity",
    "qualifiervalue": "Qualifier Value",
    "contextdependent": "Context-dependent",
    "morphologicabnormality": "Morphologic Abnormality",
    "event": "Event",
    "situation": "Situation",
    "regimetherapy": "Regime/Therapy",
    "stagingscale": "Staging Scale",
    "assessmentscale": "Assessment Scale",
    "tumorfinding": "Tumor Finding",
    "organism": "Organism",
    "substance": "Substance",
    "physicalobject": "Physical Object",
    "specimen": "Specimen",
    # --- LOINC ---
    "labtest": "Lab Test",
    "clinicalobservation": "Clinical Observation",
    "survey": "Survey",
    "loinccomponent": "LOINC Component",
    # --- Type Concepts ---
    "typeconcept": "Type Concept",
    "conditiontype": "Condition Type",
    "drugtype": "Drug Type",
    "proceduretype": "Procedure Type",
    "visittype": "Visit Type",
    "observationtype": "Observation Type",
    "measurementtype": "Measurement Type",
    "devicetype": "Device Type",
    "specimentype": "Specimen Type",
    "notetype": "Note Type",
    "episodetype": "Episode Type",
    # --- Admin / generic ---
    "domain": "Domain",
    "vocabulary": "Vocabulary",
    "unit": "Unit",
    "currency": "Currency",
    "relationship": "Relationship",
    # --- Demographics ---
    "race": "Race",
    "ethnicity": "Ethnicity",
    "gender": "Gender",
    # --- Common ---
    "condition": "Condition",
    "measurement": "Measurement",
    "drug": "Drug",
    "device": "Device",
    "visit": "Visit",
    # --- Misc ---
    "undefined": "Undefined",
    "metadata": "Metadata",
    "modelcomponent": "Model Component",
    "administrativeconcept": "Administrative Concept",
}


def _norm_for_match_concept_class(value: str) -> str:
    return value.lower().replace(" ", "").replace("-", "").replace("_", "")


def _get_concept_class(value: str) -> Optional[str]:
    return CANONICAL_CONCEPT_CLASSES.get(_norm_for_match_concept_class(value))


CANONICAL_DOMAINS: Dict[str, str] = {
    "condition": "Condition",
    "drug": "Drug",
    "procedure": "Procedure",
    "measurement": "Measurement",
    "observation": "Observation",
    "device": "Device",
    "specanatomicsite": "Spec Anatomic Site",
    "measvalue": "Meas Value",
    "route": "Route",
    "unit": "Unit",
    "visit": "Visit",
    "typeconcept": "Type Concept",
    "race": "Race",
    "ethnicity": "Ethnicity",
    "gender": "Gender",
}

CANONICAL_VOCABULARIES: Dict[str, str] = {
    "snomed": "SNOMED",
    "rxnorm": "RxNorm",
    "rxnormextension": "RxNorm Extension",
    "icd10cm": "ICD10CM",
    "icd9cm": "ICD9CM",
    "icd10pcs": "ICD10PCS",
    "icd9proc": "ICD9Proc",
    "loinc": "LOINC",
    "cpt4": "CPT4",
    "hcpcs": "HCPCS",
    "ndc": "NDC",
    "atc": "ATC",
    "read": "Read",
    "opcs4": "OPCS4",
    "meddra": "MedDRA",
    "mesh": "MeSH",
    "multum": "Multum",
    "gemscript": "GemScript",
    "hesspecialty": "HES Specialty",
    "ucum": "UCUM",
    "race": "Race",
    "ethnicity": "Ethnicity",
    "gender": "Gender",
    "conditiontype": "Condition Type",
    "drugtype": "Drug Type",
    "proceduretype": "Procedure Type",
    "visittype": "Visit Type",
    "observationtype": "Observation Type",
    "measurementtype": "Measurement Type",
    "specimentype": "Specimen Type",
    "notetype": "Note Type",
    "devicetype": "Device Type",
    "currency": "Currency",
    "unit": "Unit",
    "relationshiptype": "Relationship",
    "indication": "Indication",
    "contraindication": "Contraindication",
}


def _norm_for_match_simple(value: str) -> str:
    return value.lower().replace(" ", "")


def _norm_for_match_with_hyphens(value: str) -> str:
    return value.lower().replace("-", "").replace(" ", "").replace("_", "")


def _get_domain(value: str) -> Optional[str]:
    return CANONICAL_DOMAINS.get(_norm_for_match_simple(value))


def _get_vocabulary(value: str) -> Optional[str]:
    return CANONICAL_VOCABULARIES.get(_norm_for_match_with_hyphens(value))


# Per-column behaviour. Each entry is keyed by the normalized column name.
TARGETS: Dict[str, Dict[str, object]] = {
    "concept_class_id": {
        "label": "concept_class_id",
        "canonical_lookup": _get_concept_class,
        # vocab strips hyphens in the matching key; the others don't.
        "flag_hyphens_in_unknown": False,
        # vocab also skips columns wrapped in UPPER/LOWER/COALESCE/etc.
        "skip_if_wrapped_in_function": False,
    },
    "domain_id": {
        "label": "domain_id",
        "canonical_lookup": _get_domain,
        "flag_hyphens_in_unknown": False,
        "skip_if_wrapped_in_function": False,
    },
    "vocabulary_id": {
        "label": "vocabulary_id",
        "canonical_lookup": _get_vocabulary,
        "flag_hyphens_in_unknown": True,
        "skip_if_wrapped_in_function": True,
    },
}


# --- Helpers --------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


def _is_target_column(
    col: exp.Column, aliases: Dict[str, str]
) -> Optional[str]:
    """If ``col`` resolves to a registered target column, return its
    normalized name. Otherwise None.
    """
    _, col_name = resolve_table_col(col, aliases)
    norm = _norm(col_name)
    if norm in TARGETS:
        return norm
    return None


def _is_wrapped_in_function(node: exp.Column) -> bool:
    """Check if column is wrapped in a SQL function (UPPER, LOWER, COALESCE,
    etc.). Excludes logical/comparison operators which are also exp.Func
    subclasses in sqlglot.
    """
    parent = node.parent
    while parent:
        if isinstance(parent, exp.Func) and not isinstance(
            parent,
            (
                exp.And, exp.Or, exp.Not,
                exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
                exp.In, exp.Between, exp.Like, exp.ILike,
            ),
        ):
            return True
        parent = parent.parent
    return False


# --- Extraction ----------------------------------------------------------

def _extract_target_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str, str]]:
    """Return list of (column_name, value, context_sql) tuples for every
    string-literal filter against a target column.
    """
    filters: List[Tuple[str, str, str]] = []

    def maybe_record(col_node: exp.Column, val_node: exp.Expression, context: str) -> None:
        col = _is_target_column(col_node, aliases)
        if not col:
            return
        cfg = TARGETS[col]
        if cfg["skip_if_wrapped_in_function"] and _is_wrapped_in_function(col_node):
            return
        value = _extract_string_literal(val_node)
        if value is None:
            return
        filters.append((col, value, context))

    for node in tree.walk():
        if isinstance(node, exp.EQ):
            for col_side, val_side in (
                (node.this, node.expression),
                (node.expression, node.this),
            ):
                if isinstance(col_side, exp.Column):
                    maybe_record(col_side, val_side, node.sql())

        elif isinstance(node, exp.In):
            if isinstance(node.this, exp.Column):
                for expr in node.expressions or []:
                    maybe_record(node.this, expr, node.sql())

        elif isinstance(node, exp.Between):
            if isinstance(node.this, exp.Column):
                for bound in (node.args.get("low"), node.args.get("high")):
                    if bound is not None:
                        maybe_record(node.this, bound, node.sql())

        elif isinstance(node, (exp.Like, exp.ILike)):
            if isinstance(node.this, exp.Column):
                maybe_record(node.this, node.expression, node.sql())

    return filters


# --- Validation ----------------------------------------------------------

def _evaluate(
    column: str,
    value: str,
) -> Optional[Tuple[Optional[str], str, Severity]]:
    """Validate a single (column, value) filter.

    Returns ``None`` if the value is acceptable. Otherwise a tuple of
    ``(expected_canonical_or_None, issue, severity)`` where ``issue`` is
    one of ``"case_sensitivity"`` / ``"hyphen"`` / ``"both"``.
    """
    cfg = TARGETS[column]
    canonical_lookup: Callable[[str], Optional[str]] = cfg["canonical_lookup"]  # type: ignore[assignment]
    flag_hyphens = bool(cfg["flag_hyphens_in_unknown"])

    has_hyphen = "-" in value
    canonical = canonical_lookup(value)

    # Unknown value
    if canonical is None:
        if flag_hyphens and has_hyphen:
            return (None, "hyphen", Severity.WARNING)
        return None

    if value == canonical:
        return None

    # Known but mismatched. For columns that strip hyphens in matching,
    # split the diagnosis into "case", "hyphen", or "both".
    if flag_hyphens:
        value_no_hyphen = value.replace("-", "")
        case_differs = (
            value_no_hyphen.lower() == canonical.lower()
            and value_no_hyphen != canonical
        )
        if has_hyphen and case_differs:
            issue = "both"
        elif has_hyphen:
            issue = "hyphen"
        else:
            issue = "case_sensitivity"
        # 'hyphen' alone is a warning; 'case_sensitivity' / 'both' are errors.
        sev = Severity.WARNING if issue == "hyphen" else Severity.ERROR
        return (canonical, issue, sev)

    return (canonical, "case_sensitivity", Severity.ERROR)


# --- Rule ----------------------------------------------------------------

@register
class CanonicalStringValueValidationRule(Rule):
    """Validate canonical casing on concept_class_id, domain_id, vocabulary_id."""

    rule_id = "data_quality.canonical_string_value_validation"
    name = "Canonical Vocabulary String Value Validation"

    description = (
        "Filters on concept_class_id, domain_id, and vocabulary_id must use "
        "canonical OMOP casing (e.g. 'Ingredient', 'Condition', 'SNOMED'). "
        "Case-sensitive comparison silently returns zero rows when the casing "
        "is wrong; vocabulary_id values must also be hyphen-free."
    )

    severity = Severity.ERROR  # default; overridden per violation

    suggested_fix = "REPLACE: the lowercased / hyphenated string literal WITH the canonical OMOP value. Examples: 'snomed' → 'SNOMED', 'icd10cm' → 'ICD10CM', 'ICD-10-CM' → 'ICD10CM', 'condition' → 'Condition', 'ingredient' → 'Ingredient'."
    long_description = (
        "Three OMOP vocabulary string columns are case-sensitive and follow a "
        "fixed canonical casing: concept_class_id ('Ingredient', 'Clinical "
        "Drug', …), domain_id ('Condition', 'Drug', …), and vocabulary_id "
        "('SNOMED', 'RxNorm', 'ICD10CM', …). Filtering with the wrong casing "
        "('ingredient', 'condition', 'snomed') silently returns zero rows. "
        "vocabulary_id values are additionally hyphen-free in OMOP — "
        "'ICD-10-CM' is invalid; the canonical form is 'ICD10CM'. Match the "
        "canonical form exactly."
    )

    example_bad = (
        "SELECT concept_id\n"
        "FROM concept\n"
        "WHERE vocabulary_id = 'snomed';"
    )
    example_good = (
        "SELECT concept_id\n"
        "FROM concept\n"
        "WHERE vocabulary_id = 'SNOMED';"
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        sql_lower = sql.lower()
        if not any(col in sql_lower for col in TARGETS):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            filters = _extract_target_filters(tree, aliases)

            seen: Set[Tuple[str, str, str]] = set()

            for column, value, context in filters:
                outcome = _evaluate(column, value)
                if outcome is None:
                    continue

                expected, issue, sev = outcome
                key = (column, value, issue)
                if key in seen:
                    continue
                seen.add(key)

                # --- Build message + fix ---
                if issue == "hyphen" and expected is None:
                    message = (
                        f"Invalid {column} '{value}': contains hyphens. "
                        f"OMOP {column} values are hyphen-free."
                    )
                    fix = f"REPLACE: `'{value}'` WITH the canonical OMOP form — remove hyphens (e.g. 'ICD-10-CM' -> 'ICD10CM')."

                elif issue == "hyphen":
                    message = (
                        f"Invalid {column} '{value}': contains hyphens. "
                        f"OMOP {column} values are hyphen-free."
                    )
                    fix = f"REPLACE: `'{value}'` WITH `'{expected}'` (canonical OMOP {column})."

                elif issue == "both":
                    message = (
                        f"Invalid {column} '{value}': incorrect casing and contains hyphens. "
                        f"Expected '{expected}'."
                    )
                    fix = f"REPLACE: `'{value}'` WITH `'{expected}'` (canonical OMOP {column})."

                else:  # case_sensitivity
                    message = (
                        f"Incorrect {column} casing: '{value}'. "
                        f"Expected '{expected}'. Case-sensitive comparison may fail."
                    )
                    fix = f"REPLACE: `'{value}'` WITH `'{expected}'` (canonical OMOP {column} casing)."

                # Build a structured REPLACE patch for the literal token when
                # the canonical value is known. The literal in source may use
                # single or double quotes; try both. If the literal isn't
                # uniquely locatable, fall back to FREEFORM auto-default.
                patch = None
                if expected is not None:
                    for q in ("'", '"'):
                        bad_lit = f"{q}{value}{q}"
                        good_lit = f"{q}{expected}{q}"
                        span = locate(sql, bad_lit)
                        if span is not None:
                            patch = patch_replace(span, good_lit)
                            break

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=sev,
                        suggested_fix=fix,
                        suggested_fix_patch=patch,
                        details={
                            "column": column,
                            "provided": value,
                            "expected": expected,
                            "issue": issue,
                            "context": context,
                        },
                    )
                )

        return violations


__all__ = ["CanonicalStringValueValidationRule"]
