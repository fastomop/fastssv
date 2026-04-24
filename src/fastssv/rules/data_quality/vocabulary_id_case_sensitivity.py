from typing import Dict, List, Optional, Set, Tuple

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

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


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_for_matching(value: str) -> str:
    """Normalize for comparison: lowercase + remove separators."""
    return value.lower().replace("-", "").replace(" ", "").replace("_", "")


def _get_canonical_vocabulary(value: str) -> Optional[str]:
    return CANONICAL_VOCABULARIES.get(_normalize_for_matching(value))


def _extract_string_literal(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    return None


def _is_vocabulary_column(col: exp.Column, aliases: Dict[str, str]) -> bool:
    _, col_name = resolve_table_col(col, aliases)
    return _norm(col_name) == "vocabulary_id"


def _is_wrapped_in_function(node: exp.Column) -> bool:
    """Check if column is wrapped in a SQL function like UPPER(), LOWER(), COALESCE(), etc.

    Excludes logical/comparison operators (And, Or, EQ, etc.) which are also Func subclasses.
    """
    parent = node.parent
    while parent:
        # Check if it's a function, but exclude logical/comparison operators
        if isinstance(parent, exp.Func) and not isinstance(parent, (
            exp.And, exp.Or, exp.Not,
            exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
            exp.In, exp.Between, exp.Like, exp.ILike,
        )):
            return True
        parent = parent.parent
    return False


# --- Extraction ------------------------------------------------------------

def _extract_vocabulary_filters(
    tree: exp.Expression,
    aliases: Dict[str, str],
) -> List[Tuple[str, str]]:
    filters: List[Tuple[str, str]] = []

    for node in tree.walk():

        # --- EQ ---
        if isinstance(node, exp.EQ):
            pairs = [(node.this, node.expression), (node.expression, node.this)]

            for col_node, val_node in pairs:
                if isinstance(col_node, exp.Column) and not _is_wrapped_in_function(col_node):
                    if not _is_vocabulary_column(col_node, aliases):
                        continue

                    value = _extract_string_literal(val_node)
                    if value is not None:
                        filters.append((value, node.sql()))

        # --- IN ---
        elif isinstance(node, exp.In):
            if isinstance(node.this, exp.Column) and not _is_wrapped_in_function(node.this):
                if not _is_vocabulary_column(node.this, aliases):
                    continue

                for expr in node.expressions or []:
                    value = _extract_string_literal(expr)
                    if value is not None:
                        filters.append((value, node.sql()))

        # --- BETWEEN ---
        elif isinstance(node, exp.Between):
            if isinstance(node.this, exp.Column) and not _is_wrapped_in_function(node.this):
                if not _is_vocabulary_column(node.this, aliases):
                    continue

                for bound in [node.args.get("low"), node.args.get("high")]:
                    value = _extract_string_literal(bound)
                    if value is not None:
                        filters.append((value, node.sql()))

        # --- LIKE / ILIKE ---
        elif isinstance(node, (exp.Like, exp.ILike)):
            if isinstance(node.this, exp.Column) and not _is_wrapped_in_function(node.this):
                if not _is_vocabulary_column(node.this, aliases):
                    continue

                value = _extract_string_literal(node.expression)
                if value is not None:
                    filters.append((value, node.sql()))

    return filters


# --- Validation Logic ------------------------------------------------------

def _check_vocabulary(value: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Validate vocabulary_id value.

    Returns:
        None if valid
        dict with:
            provided
            expected
            issue: 'case_sensitivity' | 'hyphen' | 'both'
    """
    has_hyphen = "-" in value
    canonical = _get_canonical_vocabulary(value)

    # Unknown vocabulary
    if canonical is None:
        if has_hyphen:
            return {
                "provided": value,
                "expected": None,
                "issue": "hyphen",
            }
        return None

    # Fully correct
    if value == canonical:
        return None

    # Check if case differs AFTER removing hyphens
    value_no_hyphen = value.replace('-', '')
    case_differs = (value_no_hyphen.lower() == canonical.lower() and value_no_hyphen != canonical)

    if has_hyphen and case_differs:
        issue = "both"
    elif has_hyphen:
        issue = "hyphen"
    else:
        issue = "case_sensitivity"

    return {
        "provided": value,
        "expected": canonical,
        "issue": issue,
    }


# --- Rule ------------------------------------------------------------------

@register
class VocabularyIdCaseSensitivityRule(Rule):
    """Validate vocabulary_id casing and format."""

    rule_id = "data_quality.vocabulary_id_validation"
    name = "Vocabulary ID Validation"

    description = (
        "Ensures vocabulary_id values use correct canonical casing and are hyphen-free. "
        "Incorrect values may return zero results due to case sensitivity."
    )

    severity = Severity.ERROR  # default (overridden per violation)

    suggested_fix = "Use canonical OMOP vocabulary_id values."
    long_description = (
        "vocabulary_id values in OMOP follow a canonical casing — "
        "'SNOMED', 'LOINC', 'RxNorm', 'ICD10CM' — and are case-sensitive. "
        "A filter with the wrong casing ('snomed', 'SnoMed') quietly "
        "matches zero rows in most engines. Stick to the canonical form "
        "published in the OHDSI vocabulary."
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
        if "vocabulary_id" not in sql.lower():
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        violations: List[RuleViolation] = []

        for tree in trees:
            if not tree:
                continue

            aliases = extract_aliases(tree)
            filters = _extract_vocabulary_filters(tree, aliases)

            seen: Set[str] = set()

            for value, context in filters:
                error = _check_vocabulary(value)
                if not error:
                    continue

                key = f"{error['provided']}|{error['issue']}"
                if key in seen:
                    continue
                seen.add(key)

                issue = error["issue"]
                expected = error["expected"]

                # --- Messages ---
                if issue == "hyphen":
                    message = (
                        f"Invalid vocabulary_id '{value}': contains hyphens. "
                        f"OMOP vocabulary_id values are hyphen-free."
                    )
                    fix = (
                        f"Remove hyphens from '{value}'"
                        if expected is None
                        else f"Replace '{value}' with '{expected}'"
                    )
                    severity = Severity.WARNING

                elif issue == "case_sensitivity":
                    message = (
                        f"Incorrect vocabulary_id casing: '{value}'. "
                        f"Expected '{expected}'. Case-sensitive comparison may fail."
                    )
                    fix = f"Replace '{value}' with '{expected}'"
                    severity = Severity.ERROR

                else:  # both
                    message = (
                        f"Invalid vocabulary_id '{value}': incorrect casing and contains hyphens. "
                        f"Expected '{expected}'."
                    )
                    fix = f"Replace '{value}' with '{expected}'"
                    severity = Severity.ERROR

                violations.append(
                    self.create_violation(
                        message=message,
                        severity=severity,
                        suggested_fix=fix,
                        details={
                            "provided": value,
                            "expected": expected,
                            "issue": issue,
                            "context": context,
                        },
                    )
                )

        return violations


__all__ = ["VocabularyIdCaseSensitivityRule"]
