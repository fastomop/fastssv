"""Concepts that some source concept maps to with both 'Maps to' and 'Maps to value' (valid relationships): a
conformant ETL stores them as the event concept and as value_as_concept_id of the same row. Athena v5.0 27-AUG-25."""

SAME_QUESTION_AND_ANSWER_CONCEPTS = frozenset(
    {
        903657,  # Cigarette smoker
        4310912,  # Care regimes management
        44814680,  # Residential facility
        44814701,  # Residential facility
    }
)
