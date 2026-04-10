"""Procedure Domain Rules.

Rules specific to procedure-related OMOP CDM tables and concepts.
"""

from .procedure_occurrence_quantity_semantics import ProcedureOccurrenceQuantitySemanticsRule

__all__ = [
    "ProcedureOccurrenceQuantitySemanticsRule",
]
