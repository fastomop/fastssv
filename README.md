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

This query runs cleanly and returns rows, but every row is analytically suspect:

```sql
SELECT *
FROM drug_exposure de
JOIN concept c ON de.drug_concept_id = c.concept_id
WHERE c.concept_name LIKE '%aspirin%';
```

`fastssv query.sql` writes `output/validation_report.json`:

```json
{
  "query": "SELECT * FROM drug_exposure de JOIN concept c ON de.drug_concept_id = c.concept_id WHERE c.concept_name LIKE '%aspirin%';",
  "is_valid": true,
  "error_count": 0,
  "warning_count": 3,
  "warnings": [
    {
      "rule_id": "anti_patterns.concept_name_lookup",
      "severity": "warning",
      "issue": "Query filters by concept_name with pattern matching ('%aspirin%'). This is highly unreliable as concept names can vary. Use concept_code + vocabulary_id or concept_id instead.",
      "fix": "REPLACE: `WHERE c.concept_name = '<name>'` WITH `WHERE c.concept_code = '<code>' AND c.vocabulary_id = '<vocab>'`, OR with `WHERE c.concept_id = <id>` if the concept_id is known."
    },
    {
      "rule_id": "concept_standardization.standard_concept_enforcement",
      "severity": "warning",
      "issue": "Query uses STANDARD concept fields without ensuring concepts are standard.",
      "fix": "ADD: `JOIN concept c ON c.concept_id = <table>.<concept_id_col>` AND `WHERE c.standard_concept = 'S'` to filter to standard concepts."
    },
    {
      "rule_id": "concept_standardization.concept_domain_validation",
      "severity": "warning",
      "issue": "drug_exposure.drug_concept_id joined to concept 'c' without domain_id filter. Expected domain 'Drug'.",
      "fix": "ADD: `AND c.domain_id = 'Drug'` to the WHERE/JOIN-ON predicates."
    }
  ]
}
```

`is_valid` is `true` because every violation here is a `warning` — under normal mode, only `error`-severity violations gate the exit code. Run `fastssv query.sql --strict` to escalate best-practice warnings to errors. See the [Semantic rules guide](docs/semantic_rules_guide.md) for the reasoning behind each category and the [Rules reference](docs/rules_reference.md) for the full catalog.

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

The service ships body-size limits, parse-timeout, rate limiting, strict CORS, security headers, structured JSON logging, and a Docker image under `deploy/`. See the [HTTP API guide](docs/api.md) for endpoints, configuration, and deployment.

## Documentation

| Topic | Page |
|---|---|
| Architecture overview | [docs/architecture.md](docs/architecture.md) |
| Plugin system / writing a rule | [docs/plugin_architecture.md](docs/plugin_architecture.md) |
| Reasoning behind each rule category | [docs/semantic_rules_guide.md](docs/semantic_rules_guide.md) |
| Per-rule catalog (all 154) | [docs/rules_reference.md](docs/rules_reference.md) |
| HTTP API | [docs/api.md](docs/api.md) |
| JSON report format | [docs/json_output.md](docs/json_output.md) |
| Logging | [docs/logging.md](docs/logging.md) |

For contributing, see [AGENTS.md](AGENTS.md). Release notes live in [CHANGELOG.md](CHANGELOG.md).

## Stability

Pre-1.0 (`0.x.y`). The Python API (`validate_sql_structured`, `validate_sql`, `RuleViolation`, `Severity`, the registry helpers) and the `rule_id` format `<category>.<rule_name>` are stable. The exact rule set, violation wording, and individual severities may change between minor versions as rules are calibrated against real OHDSI corpora. Pin to a minor version (`fastssv>=0.2,<0.3`) and review [CHANGELOG.md](CHANGELOG.md) before upgrading.

## License

Apache 2.0. See [LICENSE](LICENSE).
