<p align="center">
  <img src="docs/logo.png" alt="FastSSV" width="300">
</p>

# FastSSV — Fast Semantic Static Validator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![OMOP CDM](https://img.shields.io/badge/OMOP-CDM%20v5.4-5C4EE5)
![Rules](https://img.shields.io/badge/rules-154-orange)
![Version](https://img.shields.io/badge/version-0.2.0-blue)
![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Tests](https://github.com/fastomop/fastSSV/actions/workflows/tests.yml/badge.svg)

**OMOP SQL that runs without errors can still be analytically wrong.**

A query that silently drops 30% of patients because it misses concept descendants, filters on a deprecated concept, or applies a temporal constraint outside a patient's observation window will execute cleanly, return plausible numbers, and produce a flawed study. FastSSV catches these violations before they reach results — 154 rules, no database connection, deterministic.

📖 **Full documentation: <https://fastomop.github.io/fastSSV/>**

---

## Install

```bash
pip install fastssv
```

## Use it

```bash
fastssv path/to/query.sql                       # writes output/validation_report.json
fastssv path/to/query.sql --strict              # cohort-grade enforcement
fastssv path/to/query.sql --dialect bigquery    # auto, postgres, tsql, oracle, redshift, bigquery, snowflake, databricks, duckdb
```

```python
from fastssv import validate_sql_structured

for v in validate_sql_structured(sql):
    print(f"[{v.severity.value.upper()}] {v.rule_id}: {v.message}")
```

## What it catches

This query runs cleanly and returns rows, but is analytically wrong:

```sql
SELECT person_id
FROM condition_occurrence
WHERE condition_concept_id IN (201826, 443238);
```

```json
{
  "is_valid": false,
  "violations": [
    {
      "rule_id": "concept_standardization.standard_concept_enforcement",
      "severity": "warning",
      "issue": "Query filters condition_concept_id without constraining standard_concept = 'S'.",
      "suggested_fix": "Join concept and add WHERE concept.standard_concept = 'S', or resolve through 'Maps to'."
    },
    {
      "rule_id": "data_quality.unmapped_concept_handling",
      "severity": "warning",
      "issue": "Query filters condition_concept_id without acknowledging concept_id = 0 (unmapped).",
      "suggested_fix": "Add: condition_concept_id > 0"
    }
  ]
}
```

See the [Semantic rules guide](docs/SEMANTIC_RULES_GUIDE.md) for the reasoning behind each category and the [Rules reference](docs/RULES_REFERENCE.md) for the full catalog.

## Why FastSSV

Existing OHDSI tools validate data quality, characterise cohorts, and measure phenotype performance. None of them validate whether the SQL logic itself follows OMOP CDM rules. FastSSV fills that gap.

**It targets silent failures.** The violations FastSSV catches are not syntax errors or missing columns — they are cases where the SQL is valid, the query returns results, and those results are wrong. Missing hierarchy expansion, reversed concept relationship direction, temporal filters outside observation windows: all of these pass any SQL linter and fail any replication attempt.

**It is static and deterministic.** FastSSV parses SQL into an abstract syntax tree and checks structural patterns against the OMOP CDM v5.4 schema. The same query produces the same result every time, on any machine, without connecting to a database.

**It is AI-agnostic.** SQL produced by humans, ATLAS, scripts, or AI agents is validated identically. FastSSV treats any SQL generator as a black box whose output needs checking.

**It is rule-based and extensible.** Every check is a discrete, documented rule with a unique ID, a severity level, a violation message, and a suggested fix. New rules can be added without touching existing ones.

## Position in the OHDSI ecosystem

FastSSV validates what other OHDSI tools assume to be correct — the SQL logic itself.

| Layer | Tool |
|---|---|
| Data correctness | DataQualityDashboard |
| Data characterisation | Achilles |
| Cohort inspection | CohortDiagnostics |
| Phenotype validity | PheValuator |
| Model performance | PatientLevelPrediction |
| **Analysis logic validity** | **FastSSV** |

## HTTP API (optional)

```bash
pip install "fastssv[api]"
fastssv serve              # http://localhost:8000 — JSON API + HTMX web UI
```

The service ships body-size limits, parse-timeout, rate limiting, strict CORS, security headers, structured JSON logging, and a Docker image under `deploy/`. See the [HTTP API guide](docs/API.md) for endpoints, configuration, and deployment.

## Documentation

| Topic | Page |
|---|---|
| Architecture overview | [docs/architecture.md](docs/architecture.md) |
| Plugin system / writing a rule | [docs/PLUGIN_ARCHITECTURE.md](docs/PLUGIN_ARCHITECTURE.md) |
| Reasoning behind each rule category | [docs/SEMANTIC_RULES_GUIDE.md](docs/SEMANTIC_RULES_GUIDE.md) |
| Per-rule catalog (all 154) | [docs/RULES_REFERENCE.md](docs/RULES_REFERENCE.md) |
| HTTP API | [docs/API.md](docs/API.md) |
| JSON report format | [docs/JSON_OUTPUT.md](docs/JSON_OUTPUT.md) |
| Logging | [docs/LOGGING.md](docs/LOGGING.md) |

For contributing, see [AGENTS.md](AGENTS.md). Release notes live in [CHANGELOG.md](CHANGELOG.md).

## Stability

Pre-1.0 (`0.x.y`). The Python API (`validate_sql_structured`, `validate_sql`, `RuleViolation`, `Severity`, the registry helpers) and the `rule_id` format `<category>.<rule_name>` are stable. The exact rule set, violation wording, and individual severities may change between minor versions as rules are calibrated against real OHDSI corpora. Pin to a minor version (`fastssv>=0.2,<0.3`) and review [CHANGELOG.md](CHANGELOG.md) before upgrading.

## License

Apache 2.0. See [LICENSE](LICENSE).
