"""A required ``concept.vocabulary_id`` filter on the concept row a standard concept column is matched to.

Shared by domain_vocabulary_validation (clinical and other domain columns) and unit_vocabulary_validation (unit
columns), which differ only in which columns they own and what they say.
"""

import re
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

from sqlglot import exp

from ._cs_scope import ColumnFilter, Link, instance_filters, instance_is_null_extended, links

# Book of OHDSI: PostgreSQL compares strings case-sensitively, SQL Server does not. Checked on DuckDB and PostgreSQL;
# on other engines a case-only mismatch is reported as a WARNING.
CASE_VERIFIED_DIALECTS = {"postgres", "postgresql", "duckdb"}
CASE_INSENSITIVE_DIALECTS = {"tsql", "mysql"}

# Concept 0 ("No matching concept") is in vocabulary 'None'; the CDM stores it for unmapped records.
UNMAPPED_VOCABULARY = "None"


@dataclass
class VocabularyMismatch:
    link: Link
    flt: ColumnFilter
    impossible: List[str]
    only_case: bool
    dead_only: bool  # the filter also admits a vocabulary that matches: rows still come back


def _like_regex(pattern: str, case_insensitive: bool) -> "re.Pattern[str]":
    body = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pattern)
    return re.compile(f"^{body}$", re.IGNORECASE if case_insensitive else 0)


def _admitted(flt: ColumnFilter, known: FrozenSet[str], case_insensitive: bool) -> Optional[List[str]]:
    """The vocabulary_id values a required positive filter admits. A LIKE pattern is expanded to the known
    vocabularies it matches; it is left alone when it matches none (it may target a local vocabulary)."""
    if not (flt.required and not flt.outer_join_on and flt.complete):
        return None
    if flt.op in ("eq", "in"):
        return list(flt.values)
    if flt.op == "like":
        pattern = flt.values[0]
        if not any(ch in pattern for ch in "%_"):
            return [pattern]
        regex = _like_regex(pattern, case_insensitive or flt.case_folded or isinstance(flt.node, exp.ILike))
        matched = sorted(v for v in known if regex.match(v))
        return matched or None
    return None


def mismatches(
    tree: exp.Expression,
    allowed_for: Callable[[Tuple[str, str]], Optional[FrozenSet[str]]],
    known: FrozenSet[str],
    dialect: str,
) -> Iterator[VocabularyMismatch]:
    """Required vocabulary_id filters on concept rows matched to a column for which ``allowed_for`` gives the
    vocabularies that can occur. A value outside the release's known vocabularies is left alone: it may be a local
    vocabulary."""
    case_insensitive = dialect in CASE_INSENSITIVE_DIALECTS
    known_by_lower: Dict[str, str] = {v.lower(): v for v in known}
    filters = instance_filters(tree, "concept", "vocabulary_id")
    seen: Set[int] = set()
    for link in links(tree, "concept", "concept_id"):
        # A WHERE filter on the null-extended side of an outer join keeps only matched rows, so it binds the link.
        if not link.required and not instance_is_null_extended(link):
            continue
        allowed = allowed_for(link.origin)
        if allowed is None:
            continue
        allowed = allowed | {UNMAPPED_VOCABULARY}
        allowed_lower = {v.lower() for v in allowed}
        for flt in filters.get(link.instance, []):
            values = _admitted(flt, known, case_insensitive)
            if not values or id(flt.node) in seen:
                continue
            exact_ok = [
                v
                for v in values
                if v in allowed or ((case_insensitive or flt.case_folded) and v.lower() in allowed_lower)
            ]
            impossible: List[str] = []
            case_only: List[str] = []
            for v in values:
                if v in exact_ok:
                    continue
                if v.lower() in allowed_lower:
                    case_only.append(v)
                elif v.lower() in known_by_lower:
                    impossible.append(v)
            if not impossible and not case_only:
                continue
            if len(exact_ok) + len(impossible) + len(case_only) < len(values):
                # some value is unknown to the release (a local vocabulary): the filter may match local concepts
                continue
            seen.add(id(flt.node))
            yield VocabularyMismatch(
                link=link,
                flt=flt,
                impossible=impossible + case_only,
                only_case=not impossible,
                dead_only=bool(exact_ok),
            )


__all__ = [
    "CASE_INSENSITIVE_DIALECTS",
    "CASE_VERIFIED_DIALECTS",
    "UNMAPPED_VOCABULARY",
    "VocabularyMismatch",
    "mismatches",
]
