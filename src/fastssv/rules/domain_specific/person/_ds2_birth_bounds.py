"""Plausible values of the person birth fields: plausibleValueLow / plausibleValueHigh of the OHDSI Data Quality
Dashboard field-level checks for CDM v5.4 (inst/csv/OMOP_CDMv5.4_Field_Level.csv, main branch, fetched 2026-11-11).
An upper bound that DQD computes from the current date is left out (None)."""

# field -> (lowest plausible value, highest plausible value or None)
BIRTH_FIELD_BOUNDS = {
    "year_of_birth": (1850, None),  # DQD: 1850 .. YEAR(GETDATE())+1
    "month_of_birth": (1, 12),  # DQD: 1 .. 12
    "day_of_birth": (1, 31),  # DQD: 1 .. 31
}
