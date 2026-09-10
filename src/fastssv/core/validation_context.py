"""Validation Context.

Provides context for rule execution including strict mode and other
configuration. Backed by a ``ContextVar`` so concurrent requests in the
FastAPI service don't stomp on each other's strict-mode setting —
``asyncio.to_thread`` copies the current ``contextvars.Context`` to the
worker thread, so rules running in the threadpool observe the context
set by the HTTP handler.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import FrozenSet, Iterator


@dataclass
class ValidationContext:
    """Context for rule validation execution."""

    strict_mode: bool = False
    """Strict mode: escalates best-practice warnings to errors."""

    dialect: str = "postgres"
    """SQL dialect being validated."""

    local_tables: FrozenSet[str] = field(default_factory=frozenset)
    """Names of tables introduced by ``CREATE TABLE``/``CREATE VIEW``
    elsewhere in the current batch. Populated by the CLI / API before
    per-statement validation when the input contains multiple
    statements so ``data_quality.schema_validation`` doesn't flag
    intra-batch scratch tables (OHDSI Achilles emits hundreds —
    ``tempResults_104``, ``measurementView_1815``, …) as unknown OMOP
    tables when the rule only sees one statement at a time. Names are
    stored lowercase to match ``CDM_COLUMN_TYPES``."""

    local_unqualified_tables: FrozenSet[str] = field(default_factory=frozenset)
    """The subset of ``local_tables`` created *without* a schema
    qualifier (or with TEMP/TEMPORARY). Only these can shadow a
    protected OMOP name for
    ``anti_patterns.destructive_operations_on_clinical_tables``: a
    schema-qualified ``CREATE TABLE backup.death AS …`` defines
    ``backup.death``, so an unqualified ``DELETE FROM death`` still hits
    the clinical table on the search path and must keep firing.
    Populated via ``collect_locally_defined_unqualified_tables``."""

    def should_escalate_rule(self, rule_id: str) -> bool:
        """Determine if a rule should be escalated to ERROR in strict mode."""
        if not self.strict_mode:
            return False

        # Rules that escalate in strict mode (best-practice rules that
        # default to WARNING but cohort-definition workflows want as ERROR).
        #
        # Note: ``concept_standardization.invalid_reason_enforcement`` is
        # NOT in this set even though its rule_id appears related. That
        # rule is *gated* behind strict mode (silent in default mode,
        # fires as WARNING when strict mode is on); it isn't escalated
        # to ERROR. Strict mode there means "enable the rule," not
        # "promote a warning."
        strict_escalation_rules = {
            "concept_standardization.standard_concept_enforcement",
            "concept_standardization.concept_domain_validation",
            "anti_patterns.concept_code_requires_vocabulary_id",
            "joins.concept_relationship_requires_relationship_id",
        }
        return rule_id in strict_escalation_rules


_current_context: ContextVar[ValidationContext] = ContextVar(
    "fastssv_validation_context",
    default=ValidationContext(),
)


def get_validation_context() -> ValidationContext:
    """Get the current validation context."""
    return _current_context.get()


def set_validation_context(context: ValidationContext) -> None:
    """Set the current validation context.

    Prefer ``with_strict_mode`` when the scope is a single call — it
    restores the prior context automatically.
    """
    _current_context.set(context)


@contextmanager
def with_strict_mode(enabled: bool = True) -> Iterator[ValidationContext]:
    """Temporarily enable/disable strict mode while keeping the current dialect."""
    current = _current_context.get()
    new_ctx = ValidationContext(
        strict_mode=enabled,
        dialect=current.dialect,
        local_tables=current.local_tables,
        local_unqualified_tables=current.local_unqualified_tables,
    )
    token = _current_context.set(new_ctx)
    try:
        yield new_ctx
    finally:
        _current_context.reset(token)


@contextmanager
def with_local_tables(
    names: FrozenSet[str],
    unqualified_names: "FrozenSet[str] | None" = None,
) -> Iterator[ValidationContext]:
    """Bind ``local_tables`` (and ``local_unqualified_tables``) on the
    context for the duration of a block.

    Used by the CLI / API before iterating per-statement validation
    over a multi-statement batch so cross-statement scratch / temp
    tables aren't reported as unknown OMOP tables. Nests cleanly
    inside ``with_strict_mode``.

    ``unqualified_names`` defaults to ``names`` when omitted — callers
    that don't distinguish how the tables were created get the previous
    behaviour. The CLI / API pass the precise subset from
    ``collect_locally_defined_unqualified_tables`` so schema-qualified
    creates don't loosen the destructive-operations guard.
    """
    current = _current_context.get()
    new_ctx = ValidationContext(
        strict_mode=current.strict_mode,
        dialect=current.dialect,
        local_tables=frozenset(names),
        local_unqualified_tables=frozenset(names if unqualified_names is None else unqualified_names),
    )
    token = _current_context.set(new_ctx)
    try:
        yield new_ctx
    finally:
        _current_context.reset(token)


__all__ = [
    "ValidationContext",
    "get_validation_context",
    "set_validation_context",
    "with_strict_mode",
    "with_local_tables",
]
