"""Visit Detail-specific domain validation rules."""

from .visit_detail_has_no_preceding_visit_occurrence_id import VisitDetailHasNoPrecedingVisitOccurrenceIdRule

__all__ = [
    "VisitDetailHasNoPrecedingVisitOccurrenceIdRule",
]
