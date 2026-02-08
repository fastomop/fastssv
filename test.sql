WITH
risk_cond_concepts AS (
  SELECT descendant_concept_id AS concept_id
  FROM omop.concept_ancestor
  WHERE ancestor_concept_id IN (320128,40481919,3654996,201820,436940,433736)
),
anthracycline_drug_concepts AS (
  SELECT descendant_concept_id AS concept_id
  FROM omop.concept_ancestor
  WHERE ancestor_concept_id = 1338512
),
radiation_cond_concepts AS (
  SELECT descendant_concept_id AS concept_id
  FROM omop.concept_ancestor
  WHERE ancestor_concept_id = 4326962
),
structural_cond_concepts AS (
  SELECT descendant_concept_id AS concept_id
  FROM omop.concept_ancestor
  WHERE ancestor_concept_id IN (319835,3023670,3023680,312912,313217,4145279)
),
biomarker_measure_concepts AS (
  SELECT UNNEST(ARRAY[3022246,3022275,3016762]) AS concept_id
),
risk_patients AS (
  SELECT DISTINCT person_id FROM omop.condition_occurrence co
  WHERE co.condition_concept_id IN (SELECT concept_id FROM risk_cond_concepts)
  UNION
  SELECT DISTINCT person_id FROM omop.drug_exposure de
  WHERE de.drug_concept_id IN (SELECT concept_id FROM anthracycline_drug_concepts)
  UNION
  SELECT DISTINCT person_id FROM omop.condition_occurrence co2
  WHERE co2.condition_concept_id IN (SELECT concept_id FROM radiation_cond_concepts)
),
exclude_structural AS (
  SELECT DISTINCT person_id FROM omop.condition_occurrence co
  WHERE co.condition_concept_id IN (SELECT concept_id FROM structural_cond_concepts)
),
exclude_biomarker AS (
  SELECT DISTINCT person_id FROM omop.measurement m
  WHERE m.measurement_concept_id IN (SELECT concept_id FROM biomarker_measure_concepts)
)
SELECT COUNT(DISTINCT rp.person_id) AS patient_count
FROM risk_patients rp
LEFT JOIN exclude_structural es ON rp.person_id = es.person_id
LEFT JOIN exclude_biomarker eb ON rp.person_id = eb.person_id
WHERE es.person_id IS NULL AND eb.person_id IS NULL;
