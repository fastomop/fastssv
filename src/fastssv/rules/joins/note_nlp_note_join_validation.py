"""Note NLP to Note Join Validation Rule.

OMOP semantic rule JOIN_019:
note_nlp joins to note via note_nlp.note_id = note.note_id. Joining on
any other columns is incorrect.

The Problem:
    The note_nlp table contains NLP-extracted entities from clinical notes.
    It's a vocabulary-like extension table that has NO direct patient context
    columns (no person_id, visit_occurrence_id, provider_id).

    The ONLY valid join is:
    note_nlp.note_id = note.note_id

    Common mistakes:
    1. Joining note_nlp_id (PK) to note_id (FK)
       - Semantically backwards (like joining person_id to drug_concept_id)
    2. Trying to join note_nlp directly to person/visit
       - Must go through note table first
    3. Using wrong column pairs

Violation pattern:
    SELECT *
    FROM note_nlp nn
    JOIN note n ON nn.note_nlp_id = n.note_id
    -- WRONG: Using PK instead of FK!

Correct pattern:
    SELECT
      nn.note_nlp_id,
      nn.lexical_variant,
      n.note_text,
      n.person_id
    FROM note_nlp nn
    JOIN note n ON nn.note_id = n.note_id
    WHERE nn.note_nlp_concept_id = 4329847
"""

from typing import Dict, List, Optional, Set, Tuple

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

NOTE_NLP = "note_nlp"
NOTE = "note"
NOTE_ID = "note_id"


# --- Helpers ---------------------------------------------------------------

def _norm(x: Optional[str]) -> Optional[str]:
    return normalize_name(x) if x else None


def _normalize_table(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return _norm(name.split(".")[-1])


def _is_note_nlp(table: Optional[str]) -> bool:
    return _normalize_table(table) == NOTE_NLP


def _is_note(table: Optional[str]) -> bool:
    return _normalize_table(table) == NOTE


def _is_col(col: Optional[str], name: str) -> bool:
    return _norm(col) == name


def _extract_eq_conditions(tree: exp.Expression) -> List[exp.EQ]:
    """Extract column-to-column equality conditions."""
    eqs = []
    for eq in tree.find_all(exp.EQ):
        if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
            eqs.append(eq)
    return eqs


# --- Detection -------------------------------------------------------------

def _detect(
    tree: exp.Expression,
    aliases: Dict[str, str],
):
    errors = []
    seen: Set[Tuple[str, str, str, str]] = set()

    found_any_relation = False
    found_valid_fk = False

    # --- 1. USING clause support -------------------------------------------
    for join in tree.find_all(exp.Join):
        using = join.args.get("using")
        if using:
            for col in using:
                if _norm(col.name) == NOTE_ID:
                    found_valid_fk = True
                    found_any_relation = True

    # --- 2. Equality joins --------------------------------------------------
    for eq in _extract_eq_conditions(tree):
        left, right = eq.this, eq.expression

        lt, lc = resolve_table_col(left, aliases)
        rt, rc = resolve_table_col(right, aliases)

        if not (lt and lc and rt and rc):
            continue

        lt_norm = _normalize_table(lt)
        rt_norm = _normalize_table(rt)

        if not ((_is_note_nlp(lt_norm) and _is_note(rt_norm)) or
                (_is_note_nlp(rt_norm) and _is_note(lt_norm))):
            continue

        found_any_relation = True

        # normalize direction
        if _is_note_nlp(lt_norm):
            nn_col, note_col = lc, rc
        else:
            nn_col, note_col = rc, lc

        nn_col_norm = _norm(nn_col)
        note_col_norm = _norm(note_col)

        # correct FK
        if nn_col_norm == NOTE_ID and note_col_norm == NOTE_ID:
            found_valid_fk = True
            continue

        # incorrect join
        key = (NOTE_NLP, nn_col_norm, NOTE, note_col_norm)
        if key not in seen:
            errors.append(key)
            seen.add(key)

    # --- 3. Missing join detection -----------------------------------------
    if has_table_reference(tree, NOTE_NLP) and has_table_reference(tree, NOTE):
        if found_any_relation and not found_valid_fk and not errors:
            # Generic error only when we detected a join but couldn't identify columns
            key = (NOTE_NLP, "INVALID", NOTE, "INVALID")
            if key not in seen:
                errors.append(key)
                seen.add(key)

        elif not found_any_relation:
            key = (NOTE_NLP, "NONE", NOTE, "NONE")
            if key not in seen:
                errors.append(key)
                seen.add(key)

    return errors


# --- Rule ------------------------------------------------------------------

@register
class NoteNlpNoteJoinValidationRule(Rule):
    """Validate note_nlp ↔ note joins via note_id."""

    rule_id = "joins.note_nlp_note_join_validation"
    name = "Note NLP to Note Join Validation"

    description = (
        "Ensures note_nlp joins to note using note_id. "
        "Flags missing or invalid joins."
    )

    severity = Severity.ERROR

    suggested_fix = "Use: note_nlp.note_id = note.note_id"

    def validate(self, sql: str, dialect: str = "postgres") -> List[RuleViolation]:
        violations: List[RuleViolation] = []

        sql_lower = sql.lower()
        if "note_nlp" not in sql_lower or "note" not in sql_lower:
            return []

        trees, err = parse_sql(sql, dialect)
        if err:
            return []

        for tree in trees:
            if not tree:
                continue

            if not (has_table_reference(tree, NOTE_NLP) and has_table_reference(tree, NOTE)):
                continue

            aliases = extract_aliases(tree)
            errors = _detect(tree, aliases)

            for nn, nn_col, note, note_col in errors:
                if nn_col == "NONE":
                    msg = (
                        "note_nlp and note are used but not joined. "
                        "Missing join condition."
                    )
                elif nn_col == "INVALID":
                    msg = (
                        "Invalid join between note_nlp and note. "
                        "Expected note_id = note_id."
                    )
                else:
                    msg = (
                        f"Invalid FK join between note_nlp and note: "
                        f"{nn}.{nn_col} = {note}.{note_col}. "
                        f"Expected note_id = note_id."
                    )

                violations.append(
                    self.create_violation(
                        message=msg,
                        suggested_fix=self.suggested_fix,
                        details={
                            "type": "invalid_fk_join",
                            "note_nlp_column": nn_col,
                            "note_column": note_col,
                        },
                    )
                )

        return violations


__all__ = ["NoteNlpNoteJoinValidationRule"]
