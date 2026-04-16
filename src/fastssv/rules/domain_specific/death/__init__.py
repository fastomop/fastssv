"""Death-specific domain validation rules."""

from .death_join_to_person_not_to_clinical_event import DeathJoinToPersonNotToClinicalEventRule

__all__ = [
    "DeathJoinToPersonNotToClinicalEventRule",
]
