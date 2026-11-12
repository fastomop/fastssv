"""Code shapes and the vocabularies whose concept_code values have each shape, Athena release v5.0 27-AUG-25.

A shape suggests a vocabulary only when exactly one vocabulary uses it. Regenerate, do not edit by hand."""

import re

RELEASE = "v5.0 27-AUG-25"

# fmt: off
CODE_SHAPES = {
    re.compile(r"[A-Z]\d{2}\.[0-9A-Z]{1,4}"): ('ICD10', 'ICD10CM', 'ICD9CM', 'ICDO3', 'OPCS4'),  # ICD10CM-like
    re.compile(r"\d{3}\.\d{1,2}"): ('ICD9CM',),  # ICD9CM-like
    re.compile(r"[A-V]\d{4}"): ('CDISC', 'CPT4', 'HCPCS', 'ICD10PCS', 'OXMIS', 'Read'),  # HCPCS-like
    re.compile(r"\d{1,5}-\d"): ('LOINC', 'NCIt', 'UK Biobank'),  # LOINC-like
}
# fmt: on
