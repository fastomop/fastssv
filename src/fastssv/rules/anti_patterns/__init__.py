"""Anti-Pattern Rules.

Rules catching common mistakes and anti-patterns to avoid.
"""

from .no_string_identification import NoStringIdentificationRule
from .concept_code_requires_vocabulary_id import ConceptCodeRequiresVocabularyIdRule
from .concept_name_lookup import ConceptNameLookupRule
from .type_concept_id_misuse import TypeConceptIdMisuseRule
from .concept_relationship_transitive_misuse import ConceptRelationshipTransitiveMisuseRule
from .destructive_operations_on_clinical_tables import DestructiveOperationsOnClinicalTablesRule
from .ambiguous_column_reference import AmbiguousColumnReferenceRule
from .join_key_validation import JoinKeyValidationRule
from .no_distinct_on_primary_key_column import NoDistinctOnPrimaryKeyColumnRule
from .singleton_metadata_clinical_join import SingletonMetadataClinicalJoinRule
from .cte_shadows_omop_table import CteShadowsOmopTableRule

__all__ = [
    "NoStringIdentificationRule",
    "ConceptCodeRequiresVocabularyIdRule",
    "ConceptNameLookupRule",
    "TypeConceptIdMisuseRule",
    "ConceptRelationshipTransitiveMisuseRule",
    "DestructiveOperationsOnClinicalTablesRule",
    "AmbiguousColumnReferenceRule",
    "JoinKeyValidationRule",
    "NoDistinctOnPrimaryKeyColumnRule",
    "SingletonMetadataClinicalJoinRule",
    "CteShadowsOmopTableRule",
]
