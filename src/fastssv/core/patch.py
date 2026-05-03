"""Structured fix patches for `RuleViolation.suggested_fix_patch`.

A patch is a small JSON-serializable record describing an edit to apply to
the offending SQL. Designed for two consumers:

1. An outer correction loop that applies trivial patches *without* invoking
   an LLM (the ~70% of rules where the fix is a deterministic substring
   rewrite).
2. A small local LLM that fills `<placeholder>` slots and produces the
   final SQL when the patch carries unresolved values.

The schema is tiny and uses byte offsets rather than text anchors, so the
same applier works regardless of whether the SQL contains repeated
fragments. Local LLMs do not need to *emit* this format — they consume it
as a hint and produce SQL. The producer (fastssv) is the only side that
constructs patches.

Schema (one of these shapes per patch):

    {"action": "REPLACE", "span": [start, end], "text": "..."}
    {"action": "ADD",     "at":   pos,          "text": "..."}
    {"action": "REMOVE",  "span": [start, end]}
    {"action": "FREEFORM","text": "..."}                    # escape hatch

`text` may contain `<name>` placeholders; the outer loop either resolves
them deterministically (e.g. vocabulary_id lookup) or routes to the LLM
with the rest of the SQL as context.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


# --- Construction ----------------------------------------------------------


def replace(span: Tuple[int, int], text: str) -> dict:
    return {"action": "REPLACE", "span": list(span), "text": text}


def add(at: int, text: str) -> dict:
    return {"action": "ADD", "at": int(at), "text": text}


def remove(span: Tuple[int, int]) -> dict:
    return {"action": "REMOVE", "span": list(span)}


def freeform(text: str) -> dict:
    return {"action": "FREEFORM", "text": text}


# --- Locating fragments in the source --------------------------------------

_WS_RE = re.compile(r"\s+")


def locate(sql: str, fragment: str) -> Optional[Tuple[int, int]]:
    """Find ``fragment`` in ``sql`` and return its ``[start, end)`` byte span,
    or ``None`` if the match is missing or ambiguous.

    Tries (in order) exact, case-insensitive, and whitespace-normalised
    matches. Returns ``None`` if more than one match exists at any tier
    (callers can fall back to FREEFORM rather than guess).
    """
    # Exact
    matches = _all_matches(sql, fragment)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None

    # Case-insensitive
    matches = _all_matches(sql.lower(), fragment.lower())
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None

    # Whitespace-normalised: collapse runs of whitespace in both
    norm_sql = _WS_RE.sub(" ", sql.lower())
    norm_frag = _WS_RE.sub(" ", fragment.lower()).strip()
    if not norm_frag:
        return None
    norm_matches = _all_matches(norm_sql, norm_frag)
    if len(norm_matches) != 1:
        return None
    # Map normalised offset back to original — walk the original counting
    # non-collapsed characters until we hit the normalised start.
    return _denormalise_span(sql, norm_matches[0])


def _all_matches(haystack: str, needle: str) -> list[Tuple[int, int]]:
    if not needle:
        return []
    out: list[Tuple[int, int]] = []
    start = 0
    n = len(needle)
    while True:
        idx = haystack.find(needle, start)
        if idx < 0:
            break
        out.append((idx, idx + n))
        start = idx + 1
    return out


def _denormalise_span(original: str, norm_span: Tuple[int, int]) -> Optional[Tuple[int, int]]:
    """Map a span from the whitespace-normalised lower-cased view back to
    the original string. Returns the smallest original span whose
    normalised form covers ``norm_span``."""
    norm_start, norm_end = norm_span
    norm_pos = 0
    orig_start: Optional[int] = None
    orig_end: Optional[int] = None
    in_ws_run = False
    i = 0
    while i < len(original):
        ch = original[i]
        if ch.isspace():
            if not in_ws_run:
                # Single space in normalised form
                if norm_pos == norm_start and orig_start is None:
                    orig_start = i
                norm_pos += 1
                if norm_pos == norm_end:
                    orig_end = i + 1
                    break
                in_ws_run = True
            i += 1
            continue
        in_ws_run = False
        if norm_pos == norm_start and orig_start is None:
            orig_start = i
        norm_pos += 1
        if norm_pos == norm_end:
            orig_end = i + 1
            break
        i += 1
    if orig_start is None or orig_end is None:
        return None
    return (orig_start, orig_end)


# --- Applier ---------------------------------------------------------------


class PatchError(ValueError):
    """Raised when a patch cannot be applied to the given SQL."""


def apply_patch(sql: str, patch: dict) -> str:
    """Apply ``patch`` to ``sql`` and return the new string.

    Raises ``PatchError`` for malformed patches, out-of-range spans, or
    FREEFORM patches (which require LLM resolution and have no
    deterministic apply).
    """
    action = patch.get("action")
    if action == "REPLACE":
        s, e = _span(patch, len(sql))
        return sql[:s] + patch["text"] + sql[e:]
    if action == "ADD":
        at = patch.get("at")
        if not isinstance(at, int) or not 0 <= at <= len(sql):
            raise PatchError(f"ADD: invalid 'at' offset {at!r}")
        return sql[:at] + patch["text"] + sql[at:]
    if action == "REMOVE":
        s, e = _span(patch, len(sql))
        return sql[:s] + sql[e:]
    if action == "FREEFORM":
        raise PatchError("FREEFORM patches must be resolved by an LLM, not applied directly")
    raise PatchError(f"unknown action {action!r}")


def _span(patch: dict, sql_len: int) -> Tuple[int, int]:
    span = patch.get("span")
    if not (isinstance(span, (list, tuple)) and len(span) == 2):
        raise PatchError(f"missing or malformed 'span': {span!r}")
    s, e = int(span[0]), int(span[1])
    if not (0 <= s <= e <= sql_len):
        raise PatchError(f"span out of range: {s}..{e} (sql length {sql_len})")
    return s, e


def build_join_replace_patch(
    sql: str,
    left_table: str,
    left_col: str,
    right_table: str,
    right_col: str,
    correct_left_col: str,
    correct_right_col: str,
    fallback_text: str,
    aliases: Optional[dict] = None,
) -> dict:
    """Build a REPLACE patch for a wrong-column join.

    Tries both orderings of the equality (`a.x = b.y` vs `b.y = a.x`)
    and, when ``aliases`` is supplied (an ``alias_or_table → table`` map
    from ``extract_aliases``), also tries forms where the qualifiers
    are aliases rather than full table names. Falls back to FREEFORM
    when no form is uniquely locatable.

    Used by the concept ↔ {concept_class, domain, vocabulary, concept}
    join rules and by clinical ↔ visit_detail join rules to emit a clean
    edit instruction without per-rule patch-construction boilerplate.
    """
    # Build the list of qualifiers to try for each side: the resolved
    # table name first, then any aliases pointing at that table.
    left_quals = _qualifiers_for_table(left_table, aliases)
    right_quals = _qualifiers_for_table(right_table, aliases)

    for lq in left_quals:
        for rq in right_quals:
            bad = f"{lq}.{left_col} = {rq}.{right_col}"
            good = f"{lq}.{correct_left_col} = {rq}.{correct_right_col}"
            span = locate(sql, bad)
            if span is not None:
                return replace(span, good)

            bad_swapped = f"{rq}.{right_col} = {lq}.{left_col}"
            good_swapped = f"{rq}.{correct_right_col} = {lq}.{correct_left_col}"
            span = locate(sql, bad_swapped)
            if span is not None:
                return replace(span, good_swapped)

    return freeform(fallback_text)


def _qualifiers_for_table(table: str, aliases: Optional[dict]) -> list:
    """Return the table name plus any aliases that resolve to it."""
    quals = [table]
    if aliases:
        for alias, resolved in aliases.items():
            if resolved == table and alias != table and alias not in quals:
                quals.append(alias)
    return quals


def has_unresolved_placeholders(patch: dict) -> bool:
    """True if the patch contains any ``<name>`` placeholders that need
    site-specific resolution before it can be applied automatically."""
    text = patch.get("text", "")
    return "<" in text and ">" in text


__all__ = [
    "PatchError",
    "replace",
    "add",
    "remove",
    "freeform",
    "locate",
    "apply_patch",
    "build_join_replace_patch",
    "has_unresolved_placeholders",
]
