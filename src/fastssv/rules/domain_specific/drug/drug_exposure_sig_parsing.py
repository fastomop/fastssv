"""Drug Exposure Sig Parsing Rule.

OMOP semantic rule OMOP_072:
drug_exposure.sig contains free-text prescription instructions (e.g., 'Take 1 tablet
twice daily'). Do not parse sig for structured dose information; use drug_strength
table instead.

The Problem:
    The sig field contains unstructured free-text that varies widely across sites:
    - "1 tab bid"
    - "take one tablet twice daily"
    - "Take 1-2 tablets by mouth every 4-6 hours as needed"

    Parsing this for structured dosing data is unreliable and error-prone.
    The drug_strength table provides standardized, structured dosing information.

    Common mistakes:
    - SUBSTRING(sig, 1, CHARINDEX(' ', sig)) to extract dose
    - REGEXP_SUBSTR(sig, '[0-9]+') to extract numbers
    - PATINDEX('%[0-9]%', sig) to find numeric positions

Violation pattern:
    SELECT CAST(SUBSTRING(sig, 1, CHARINDEX(' ', sig)) AS INT) AS dose
    FROM drug_exposure
    -- Attempts to parse free-text for structured dose

Correct pattern:
    SELECT ds.amount_value, ds.amount_unit_concept_id
    FROM drug_exposure de
    JOIN drug_strength ds ON de.drug_concept_id = ds.drug_concept_id
    WHERE ds.invalid_reason IS NULL
    -- Uses standardized structured dosing data
"""

from typing import Dict, List, Optional, Set, Tuple
import re

from sqlglot import exp

from fastssv.core.base import Rule, RuleViolation, Severity
from fastssv.core.helpers import (
    extract_aliases,
    normalize_name,
    parse_sql,
    resolve_table_col,
    has_table_reference,
)
from fastssv.core.registry import register


# --- Constants -------------------------------------------------------------

DRUG_EXPOSURE = "drug_exposure"
SIG = "sig"

STRING_FUNCTIONS = {
    "substring",
    "substr",
    "charindex",
    "patindex",
    "position",
    "instr",
    "locate",
    "regexp_substr",
    "regexp_replace",
    "regexp_extract",
    "left",
    "right",
    "split_part",
}

PARSING_FUNCTIONS = {
    "substring",
    "substr",
    "regexp_substr",
    "regexp_extract",
    "split_part",
}

NUMERIC_TYPES = {"int", "integer", "decimal", "numeric", "float", "real", "number"}

NUMERIC_REGEX = re.compile(r"\d|\[0-9\]|\\d")


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _get_function_name(node: exp.Expression) -> Optional[str]:
    """Robust function name extraction across sqlglot nodes."""
    if isinstance(node, exp.Anonymous):
        return _norm(str(node.this))

    if hasattr(node, "sql_name"):
        try:
            return _norm(node.sql_name())
        except Exception:
            pass

    if hasattr(node, "name"):
        return _norm(node.name)

    return None


def _is_sig_column(col: exp.Expression, aliases: Dict[str, str]) -> bool:
    """Check if expression refers to drug_exposure.sig."""
    if isinstance(col, exp.Column):
        table, name = resolve_table_col(col, aliases)
        if _norm(name) != SIG:
            return False

        if _norm(table) == DRUG_EXPOSURE:
            return True

        if not table:
            return any(_norm(t) == DRUG_EXPOSURE for t in aliases.values())

    elif isinstance(col, exp.Var):
        if _norm(str(col.this)) == SIG:
            return any(_norm(t) == DRUG_EXPOSURE for t in aliases.values())

    return False


def _contains_sig(node: exp.Expression, aliases: Dict[str, str]) -> bool:
    for col in node.find_all(exp.Column):
        if _is_sig_column(col, aliases):
            return True

    for var in node.find_all(exp.Var):
        if _is_sig_column(var, aliases):
            return True

    return False


def _contains_numeric_cast(node: exp.Expression) -> bool:
    """Detect numeric casting including ::int syntax."""
    # Explicit CAST
    if isinstance(node, (exp.Cast, exp.TryCast)):
        to_type = str(node.to).lower()
        return any(t in to_type for t in NUMERIC_TYPES)

    # ::int style (Postgres)
    if isinstance(node, exp.DataType):
        return any(t in str(node).lower() for t in NUMERIC_TYPES)

    # Walk subtree
    for sub in node.walk():
        if isinstance(sub, (exp.Cast, exp.TryCast)):
            to_type = str(sub.to).lower()
            if any(t in to_type for t in NUMERIC_TYPES):
                return True

    return False


def _is_arithmetic(node: exp.Expression) -> bool:
    return isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div))


def _is_numeric_literal(lit: exp.Literal) -> bool:
    try:
        float(str(lit.this))
        return True
    except Exception:
        return False


def _has_numeric_comparison(node: exp.Expression) -> bool:
    if isinstance(node, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return any(_is_numeric_literal(l) for l in node.find_all(exp.Literal))
    return False


def _regex_indicates_numeric(func: exp.Expression) -> bool:
    """Check if regex patterns extract numbers."""
    for arg in func.expressions:
        if isinstance(arg, exp.Literal):
            pattern = str(arg.this)
            if NUMERIC_REGEX.search(pattern):
                return True
    return False


def _is_numeric_extraction_context(node: exp.Expression) -> bool:
    """Walk up the tree to detect numeric usage context."""
    current = node

    while current:
        if _contains_numeric_cast(current):
            return True

        if _is_arithmetic(current):
            return True

        if _has_numeric_comparison(current):
            return True

        current = getattr(current, "parent", None)

    return False


def _likely_extraction(func: exp.Expression, func_name: str) -> bool:
    """Heuristic to reduce false positives."""
    if func_name in {"regexp_extract", "regexp_substr"}:
        return _regex_indicates_numeric(func)

    if func_name in {"substring", "substr", "split_part"}:
        # Heuristic: positional slicing often indicates parsing
        return len(func.expressions) >= 2

    return False


# --- Detection -------------------------------------------------------------

def _find_violations(tree: exp.Expression, aliases: Dict[str, str]) -> List[str]:
    issues: List[str] = []
    seen: Set[Tuple[str, str]] = set()

    for node in tree.walk():
        func_name = _get_function_name(node)
        if not func_name or func_name not in STRING_FUNCTIONS:
            continue

        if not _contains_sig(node, aliases):
            continue

        is_numeric = _is_numeric_extraction_context(node)

        should_flag = is_numeric or (
            func_name in PARSING_FUNCTIONS and _likely_extraction(node, func_name)
        )

        if not should_flag:
            continue

        key = (func_name, node.sql(dialect=""))
        if key in seen:
            continue
        seen.add(key)

        if is_numeric:
            issues.append(
                f"Parsing drug_exposure.sig for numeric dose extraction detected "
                f"(using {func_name.upper()}()). The sig field is free-text and "
                f"varies across sites. Use drug_strength for standardized dosing."
            )
        else:
            issues.append(
                f"String parsing on drug_exposure.sig detected (using {func_name.upper()}()). "
                f"Avoid extracting structured data from sig; use drug_strength instead."
            )

    return issues


# --- Rule ------------------------------------------------------------------

@register
class DrugExposureSigParsingRule(Rule):
    """Detect parsing of drug_exposure.sig for structured dose extraction."""

    rule_id = "domain_specific.drug_exposure_sig_parsing"
    name = "Drug Exposure Sig Parsing"

    description = (
        "Detects string parsing of drug_exposure.sig to extract structured dose "
        "information. The sig field is free-text and not standardized."
    )

    severity = Severity.WARNING

    suggested_fix = (
        "Join drug_strength to obtain standardized dose fields "
        "(amount_value, numerator_value, denominator_value) "
        "instead of parsing sig."
    )

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        # Lightweight pre-filter
        sql_lower = sql.lower()
        if "sig" not in sql_lower:
            return []

        if not any(fn in sql_lower for fn in STRING_FUNCTIONS):
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            if not has_table_reference(tree, DRUG_EXPOSURE):
                continue

            aliases = extract_aliases(tree)
            issues = _find_violations(tree, aliases)

            for msg in issues:
                violations.append(self.create_violation(message=msg))

        return violations


__all__ = ["DrugExposureSigParsingRule"]
