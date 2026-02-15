# Output Directory

This directory contains both reference datasets (tracked in git) and generated validation reports (gitignored).

## Reference Datasets (Tracked in Git)

These files are committed for reproducibility:
- `run_omop_query_dataset.sql` - Sample OMOP CDM queries (14MB)
- `run_omop_query_dataset.json` - Same dataset in JSON format (17MB)

## Generated Files (Gitignored)

These files are automatically generated and excluded from version control:
- `validation_report.json` - Validation reports from `main.py` CLI
- `raw_observations.json` - Cached raw observations from Langfuse (`scripts/dataset_fetch.py`)

## Usage

- The reference datasets provide reproducible test data for validation
- Generated reports are created automatically and ignored by git
- Run `python main.py <query.sql>` to generate new validation reports
- Run `python scripts/dataset_fetch.py` to regenerate datasets from Langfuse
