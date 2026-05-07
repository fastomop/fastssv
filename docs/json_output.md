# JSON output format

`uv run fastssv <path>` writes a structured JSON report to disk and prints a one-line summary to the terminal. The report is the canonical machine-readable contract for CI integrations, dashboards, and any downstream tool that needs to consume validation results.

## Quick reference

```bash
uv run fastssv query.sql                       # writes output/validation_report.json
uv run fastssv query.sql --output report.json  # custom path
uv run fastssv query.sql --combined            # multi-statement input → single combined report
```

**Exit codes**

| Code | Meaning |
|---|---|
| `0` | No `error`-severity violations (`is_valid: true` for the whole submission) |
| `1` | One or more `error`-severity violations (`is_valid: false`) |

`warning`-severity violations never affect the exit code under normal mode. Pass `--strict` to escalate best-practice warnings to errors — see the [Semantic rules guide](semantic_rules_guide.md) for which rules participate in strict mode.

---

## Single-query report

For input containing a single SQL statement (or multi-statement input with `--combined`), the report is a flat object:

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

| Field | Type | Notes |
|---|---|---|
| `query` | string | SQL with comments stripped and whitespace collapsed |
| `is_valid` | boolean | `true` iff `error_count == 0` |
| `error_count` | number | Count of `error`-severity violations |
| `warning_count` | number | Count of `warning`-severity violations |
| `errors` | array | Present only when `error_count > 0` |
| `warnings` | array | Present only when `warning_count > 0` |

Errors and warnings are **split into two arrays**, not flattened into a single `violations` array. If a query has only warnings, the `errors` key is omitted entirely (and vice versa).

---

## Multi-query report

When the input file contains multiple `;`-separated statements, FastSSV validates each independently and wraps the per-statement results in an outer object:

```json
{
  "total_queries": 2,
  "valid_queries": 2,
  "invalid_queries": 0,
  "results": [
    {
      "query": "SELECT * FROM person WHERE person_id = 1;",
      "is_valid": true,
      "error_count": 0,
      "warning_count": 0
    },
    {
      "query": "SELECT * FROM drug_exposure de JOIN concept c ON de.drug_concept_id = c.concept_id WHERE c.concept_name LIKE '%aspirin%';",
      "is_valid": true,
      "error_count": 0,
      "warning_count": 3,
      "warnings": [ ... ]
    }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `total_queries` | number | Statements parsed out of the input |
| `valid_queries` | number | Statements where `is_valid == true` |
| `invalid_queries` | number | Statements where `is_valid == false` |
| `results` | array | One single-query report per statement, in source order |

Each entry in `results` has the same shape as a [single-query report](#single-query-report). Position in the array is the statement index (0-based in JSON, 1-based in log lines).

To collapse a multi-statement file back to one combined report (legacy behaviour), pass `--combined` — the multiple statements are joined and reported as a single query.

---

## Violation object

Every entry in `errors[]` or `warnings[]` has the same shape:

```json
{
  "rule_id": "anti_patterns.concept_name_lookup",
  "severity": "warning",
  "issue": "Query filters by concept_name with pattern matching ('%aspirin%'). ...",
  "fix": "REPLACE: `WHERE c.concept_name = '<name>'` WITH `WHERE c.concept_code = '<code>' AND c.vocabulary_id = '<vocab>'`, ..."
}
```

| Field | Type | Notes |
|---|---|---|
| `rule_id` | string | Stable rule identifier, e.g. `joins.join_path_validation`. See the [Rules reference](rules_reference.md). |
| `severity` | `"error"` \| `"warning"` | Lowercase. Mirrored as `severity` field on the violation. |
| `issue` | string | Human-readable description of what's wrong and why it matters. |
| `fix` | string \| object | See below — heterogeneous: prose for free-form fixes, patch dict for mechanical ones. |
| `location` | string | **Optional.** Set when the rule can pin the issue to a specific table.column or SQL fragment. Omitted otherwise. |

### `fix` is heterogeneous

For free-form fixes (the default for ~60% of rules), `fix` is a prose string starting with an imperative verb (`REPLACE:`, `ADD:`, `JOIN:`, `FILTER:`, `GROUP BY:`, `CAST:`):

```json
"fix": "ADD: `AND c.domain_id = 'Drug'` to the WHERE/JOIN-ON predicates."
```

For mechanical fixes (~40% of rules — REPLACE / ADD / REMOVE patches that an outer correction loop can apply directly), `fix` is a patch object:

```json
"fix": {
  "action": "REPLACE",
  "span": [42, 55],
  "text": "concept_id IS NOT NULL"
}
```

Switch on the type:

```python
if isinstance(violation["fix"], str):
    print("Prose suggestion:", violation["fix"])
else:
    apply_patch(sql, violation["fix"])  # action / span / text
```

There is **no `suggested_fix` or `details` field** on the wire. Earlier versions exposed both; they were unified into the single `fix` field (and `details` was retired from the JSON because the keys varied too much across rules to be programmatically useful — see `core/base.py:RuleViolation.to_dict`).

---

## Programmatic usage

### Python — direct API (recommended)

Skip the subprocess + JSON round-trip. Call the library:

```python
from fastssv import validate_sql_structured
from fastssv.core.base import Severity

violations = validate_sql_structured(sql, dialect="postgres")
errors   = [v for v in violations if v.severity == Severity.ERROR]
warnings = [v for v in violations if v.severity == Severity.WARNING]

for v in violations:
    print(f"[{v.severity.value.upper()}] {v.rule_id}")
    print(f"  {v.message}")
    print(f"  Fix: {v.suggested_fix}")  # prose; .suggested_fix_patch holds the structured form
```

The Python `RuleViolation` dataclass keeps `suggested_fix` (prose) and `suggested_fix_patch` (structured) as separate attributes, plus an in-process `details` dict. The single `fix` field on the JSON wire is derived from these via `to_dict()`.

Filter at validation time instead of post-filtering:

```python
violations = validate_sql_structured(sql, categories=["concept_standardization"])
violations = validate_sql_structured(sql, rule_ids=["joins.join_path_validation"])
violations = validate_sql_structured(sql, dialect="bigquery")
```

### Python — subprocess

```python
import json
import subprocess
from pathlib import Path

subprocess.run(["fastssv", "query.sql", "--output", "report.json"], check=False)
report = json.loads(Path("report.json").read_text())

for v in report.get("errors", []) + report.get("warnings", []):
    print(f"[{v['rule_id']}] {v['issue']}")
```

### Shell + jq

```bash
uv run fastssv query.sql --output report.json

# All violations across both severities
jq '(.errors // []) + (.warnings // []) | .[] | "[\(.rule_id)] \(.issue)"' report.json

# Errors only
jq '.errors[]?' report.json

# Group violations by category
jq '[(.errors // []) + (.warnings // []) | .[] | .rule_id | split(".")[0]] | group_by(.) | map({cat: .[0], n: length})' report.json
```

### Multi-query result handling

```python
report = json.loads(Path("report.json").read_text())

if "results" in report:
    for idx, result in enumerate(report["results"], start=1):
        status = "✓" if result["is_valid"] else "✗"
        print(f"{status} Query {idx}: {result['error_count']} errors, {result['warning_count']} warnings")
else:
    status = "✓" if report["is_valid"] else "✗"
    print(f"{status} {report['error_count']} errors, {report['warning_count']} warnings")
```

---

## `validate_sql()` helper

`validate_sql()` is a separate convenience entry point that returns a grouped dict (rather than the CLI's flat report). It's useful when you want errors grouped by rule category without filtering yourself.

```typescript
{
  violations: RuleViolation[],   // All RuleViolation objects (Python objects, not dicts)
  category_errors: {
    anti_patterns: string[],
    concept_standardization: string[],
    data_quality: string[],
    domain_specific: string[],
    joins: string[],
    temporal: string[]
  },
  all_errors: string[],          // Flattened list of every violation message
  parse_error: string | null,    // Set when SQL couldn't be parsed; null otherwise
  dialect: string                // Dialect actually used (after auto-detection)
}
```

Use it when you want the category grouping pre-built:

```python
from fastssv import validate_sql

results = validate_sql(sql, categories=["concept_standardization", "temporal"])

for category, messages in results["category_errors"].items():
    if messages:
        print(f"{category}: {len(messages)} violations")
```

For everything else, prefer `validate_sql_structured()` (returns the typed dataclass list) or read the CLI report.

---

## Defensive parsing

The shape is stable enough to consume directly, but defensive code is cheap insurance:

```python
report = json.loads(output)

# Distinguish single vs multi-query
if "results" in report:
    entries = report["results"]
else:
    entries = [report]

for entry in entries:
    for v in entry.get("errors", []) + entry.get("warnings", []):
        rule_id  = v["rule_id"]
        severity = v["severity"]
        issue    = v["issue"]
        fix      = v.get("fix")          # may be string OR dict OR absent
        location = v.get("location")     # often absent
```

Optional fields (`errors`, `warnings`, `fix`, `location`) are omitted when not applicable rather than emitted as `null` — always use `.get()` or check `in`.
