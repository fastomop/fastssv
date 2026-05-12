"""Anti-Pattern Rules.

Rules catching common mistakes and anti-patterns to avoid.
"""

from .no_string_identification import NoStringIdentificationRule
from .concept_code_requires_vocabulary_id import ConceptCodeRequiresVocabularyIdRule
from .concept_name_lookup import ConceptNameLookupRule
from .type_concept_id_misuse import TypeConceptIdMisuseRule
from .type_concept_id_domain_filter import TypeConceptIdDomainFilterRule
from .standard_concept_or_with_classification import StandardConceptOrWithClassificationRule
from .concept_relationship_transitive_misuse import ConceptRelationshipTransitiveMisuseRule
from .concept_ancestor_mixed_with_concept_relationship_redundantly import (
    ConceptAncestorMixedWithConceptRelationshipRedundantlyRule,
)
from .destructive_operations_on_clinical_tables import DestructiveOperationsOnClinicalTablesRule
from .comma_separated_cross_join import CommaSeparatedCrossJoinRule
from .ambiguous_column_reference import AmbiguousColumnReferenceRule
from .join_key_validation import JoinKeyValidationRule
from .attribute_definition_invalid_join import AttributeDefinitionInvalidJoinRule
from .no_distinct_on_primary_key_column import NoDistinctOnPrimaryKeyColumnRule
from .singleton_metadata_clinical_join import SingletonMetadataClinicalJoinRule
from .having_without_group_by import HavingWithoutGroupByRule
from .duplicate_column_alias import DuplicateColumnAliasRule
from .top_as_synthetic_data import TopAsSyntheticDataRule
from .null_comparison_operator import NullComparisonOperatorRule
from .limit_without_order_by import LimitWithoutOrderByRule
from .cte_shadows_omop_table import CteShadowsOmopTableRule

__all__ = [
    "NoStringIdentificationRule",
    "ConceptCodeRequiresVocabularyIdRule",
    "ConceptNameLookupRule",
    "TypeConceptIdMisuseRule",
    "TypeConceptIdDomainFilterRule",
    "StandardConceptOrWithClassificationRule",
    "ConceptRelationshipTransitiveMisuseRule",
    "ConceptAncestorMixedWithConceptRelationshipRedundantlyRule",
    "DestructiveOperationsOnClinicalTablesRule",
    "CommaSeparatedCrossJoinRule",
    "AmbiguousColumnReferenceRule",
    "JoinKeyValidationRule",
    "AttributeDefinitionInvalidJoinRule",
    "NoDistinctOnPrimaryKeyColumnRule",
    "SingletonMetadataClinicalJoinRule",
    "HavingWithoutGroupByRule",
    "DuplicateColumnAliasRule",
    "TopAsSyntheticDataRule",
    "NullComparisonOperatorRule",
    "LimitWithoutOrderByRule",
    "CteShadowsOmopTableRule",
]
