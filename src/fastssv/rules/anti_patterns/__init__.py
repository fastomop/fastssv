"""Anti-Pattern Rules.

Rules catching common mistakes and anti-patterns to avoid.
"""

from .no_string_identification import NoStringIdentificationRule
from .concept_code_requires_vocabulary_id import ConceptCodeRequiresVocabularyIdRule
from .concept_lookup_context import ConceptLookupContextRule
from .concept_name_lookup import ConceptNameLookupRule
from .type_concept_id_misuse import TypeConceptIdMisuseRule
from .type_concept_id_domain_filter import TypeConceptIdDomainFilterRule
from .standard_concept_or_with_classification import StandardConceptOrWithClassificationRule
from .concept_relationship_missing_relationship_filter import ConceptRelationshipMissingRelationshipFilterRule
from .concept_relationship_transitive_misuse import ConceptRelationshipTransitiveMisuseRule
from .concept_ancestor_mixed_with_concept_relationship_redundantly import ConceptAncestorMixedWithConceptRelationshipRedundantlyRule

__all__ = [
    "NoStringIdentificationRule",
    "ConceptCodeRequiresVocabularyIdRule",
    "ConceptLookupContextRule",
    "ConceptNameLookupRule",
    "TypeConceptIdMisuseRule",
    "TypeConceptIdDomainFilterRule",
    "StandardConceptOrWithClassificationRule",
    "ConceptRelationshipMissingRelationshipFilterRule",
    "ConceptRelationshipTransitiveMisuseRule",
    "ConceptAncestorMixedWithConceptRelationshipRedundantlyRule",
]
