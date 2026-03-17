"""Anti-Pattern Rules.

Rules catching common mistakes and anti-patterns to avoid.
"""

from .no_string_identification import NoStringIdentificationRule
from .concept_code_requires_vocabulary_id import ConceptCodeRequiresVocabularyIdRule
from .concept_lookup_context import ConceptLookupContextRule
from .concept_name_lookup import ConceptNameLookupRule
from .type_concept_id_misuse import TypeConceptIdMisuseRule

__all__ = [
    "NoStringIdentificationRule",
    "ConceptCodeRequiresVocabularyIdRule",
    "ConceptLookupContextRule",
    "ConceptNameLookupRule",
    "TypeConceptIdMisuseRule",
]
