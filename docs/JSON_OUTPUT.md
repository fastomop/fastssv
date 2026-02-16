# JSON Output Format for FastSSV Validation

## Quick Reference

### Command Line Usage
```bash
# Default output to output/validation_report.json
python main.py query.sql

# Custom output path
python main.py query.sql --output my_report.json

# With other options
python main.py query.sql --dialect mysql --categories semantic
```

### Exit Codes
- **0** = No ERROR-level violations (`is_valid: true`)
- **1** = One or more ERROR-level violations (`is_valid: false`)

Note: WARNING-level violations do not affect exit code.

### Key Response Fields

**Single Query Response:**
| Field | Type | Description |
|-------|------|-------------|
| `query` | string | Normalized SQL query (whitespace collapsed) |
| `dialect` | string | SQL dialect (postgres, mysql, duckdb, etc.) |
| `is_valid` | boolean | true if no ERROR-level violations |
| `error_count` | number | Count of ERROR-level violations |
| `warning_count` | number | Count of WARNING-level violations |
| `violations` | array | Violation objects (omitted if empty) |

**Violation Object:**
| Field | Type | Description |
|-------|------|-------------|
| `rule_id` | string | Unique rule identifier (e.g., "semantic.join_path_validation") |
| `severity` | string | "error" or "warning" (lowercase) |
| `issue` | string | Human-readable violation message |
| `suggested_fix` | string | Recommendation for fixing the violation |
| `location` | string | Optional location info |
| `details` | object | Optional structured metadata |

**Multiple Query Response:**
| Field | Type | Description |
|-------|------|-------------|
| `total_queries` | number | Total number of queries validated |
| `valid_queries` | number | Count of queries with no ERROR violations |
| `invalid_queries` | number | Count of queries with ERROR violations |
| `results` | array | Query results with `query_index` field |

---

## Overview

FastSSV outputs validation results in JSON format for structured, machine-readable results. This enables integration with automated systems, APIs, and CI/CD pipelines.

## CLI Behavior

FastSSV writes JSON validation reports to a file and prints a summary to the terminal:

```bash
$ python main.py query.sql
Validation INVALID
  Errors: 2
  Warnings: 1
  Report saved to: /path/to/output/validation_report.json
```

### Output File Location

**Default:** `output/validation_report.json`

**Custom path:** Use `--output` or `-o` flag:
```bash
python main.py query.sql --output my_custom_report.json
```

The output directory is automatically created if it doesn't exist.

## JSON Structure

### Single Query Output

For a single query, the output structure is:

```json
{
  "query": "SELECT p.person_id FROM person p WHERE p.gender_concept_id = 8507",
  "dialect": "postgres",
  "is_valid": true,
  "error_count": 0,
  "warning_count": 0
}
```

When validation fails, a `violations` array is included:

```json
{
  "query": "SELECT c.person_id, x.concept_name FROM condition_occurrence c JOIN concept x ON x.concept_id = c.condition_concept_id WHERE x.concept_name = 'Hypertension'",
  "dialect": "postgres",
  "is_valid": false,
  "error_count": 2,
  "warning_count": 1,
  "violations": [
    {
      "rule_id": "semantic.standard_concept_enforcement",
      "severity": "error",
      "issue": "Query uses STANDARD concept field 'condition_concept_id' without enforcing standard_concept = 'S'. This may include non-standard concepts in results.",
      "suggested_fix": "Add WHERE clause: concept.standard_concept = 'S' OR use concept_relationship with relationship_id = 'Maps to'",
      "details": {
        "field": "condition_concept_id",
        "table": "condition_occurrence"
      }
    },
    {
      "rule_id": "vocabulary.no_string_identification",
      "severity": "warning",
      "issue": "Concept table string filter outside concept_id lookup: x.concept_name = 'Hypertension'",
      "suggested_fix": "Use concept_id-based filtering instead of string matching on concept_name"
    }
  ]
}
```

### Multiple Queries Output

When the SQL file contains multiple queries (separated by semicolons), the output structure is:

```json
{
  "total_queries": 3,
  "valid_queries": 2,
  "invalid_queries": 1,
  "results": [
    {
      "query_index": 1,
      "query": "SELECT * FROM person LIMIT 10",
      "dialect": "postgres",
      "is_valid": true,
      "error_count": 0,
      "warning_count": 0
    },
    {
      "query_index": 2,
      "query": "SELECT ...",
      "dialect": "postgres",
      "is_valid": false,
      "error_count": 1,
      "warning_count": 0,
      "violations": [...]
    },
    {
      "query_index": 3,
      "query": "SELECT ...",
      "dialect": "postgres",
      "is_valid": true,
      "error_count": 0,
      "warning_count": 0
    }
  ]
}
```

### Violation Object Structure

Each violation has the following fields:

```json
{
  "rule_id": "semantic.hierarchy_expansion_required",
  "severity": "error",
  "issue": "Query filters on drug_concept_id without using concept_ancestor for hierarchy expansion",
  "suggested_fix": "JOIN to concept_ancestor to capture all descendant concepts",
  "location": "drug_exposure.drug_concept_id",
  "details": {
    "field": "drug_concept_id",
    "table": "drug_exposure",
    "filtered_values": [1234, 5678]
  }
}
```

**Required fields:**
- `rule_id` - Unique identifier for the validation rule
- `severity` - Either `"error"` (blocks validation) or `"warning"` (informational)
- `issue` - Human-readable description of the violation
- `suggested_fix` - Specific recommendation for fixing the issue

**Optional fields:**
- `location` - Where the issue occurs (table.column, line number, etc.)
- `details` - Structured metadata with additional context

## Programmatic Usage

### Python - Subprocess

```python
import json
import subprocess
from pathlib import Path

# Run FastSSV
result = subprocess.run(
    ["python", "main.py", "query.sql", "--output", "report.json"],
    capture_output=True,
    text=True
)

# Read JSON report
report = json.loads(Path("report.json").read_text())

# Check if valid
if report["is_valid"]:
    print("✓ Query is valid")
else:
    print(f"✗ {report['error_count']} errors, {report['warning_count']} warnings")
    for violation in report.get("violations", []):
        print(f"  [{violation['rule_id']}] {violation['issue']}")
        print(f"  → {violation['suggested_fix']}")

# Check exit code
if result.returncode != 0:
    print("Validation failed (errors found)")
```

### Python - Direct API (Recommended)

```python
from fastssv import validate_sql_structured

# Get structured violations
violations = validate_sql_structured(sql, dialect="postgres")

# Check results
if not violations:
    print("✓ Valid")
else:
    for v in violations:
        print(f"{v.severity.value}: [{v.rule_id}] {v.message}")
        print(f"  Fix: {v.suggested_fix}")

# Filter by category
violations = validate_sql_structured(sql, categories=["semantic"])

# Filter by specific rules
violations = validate_sql_structured(
    sql,
    rule_ids=["semantic.standard_concept_enforcement"]
)

# Convert to dict for JSON serialization
violations_dict = [v.to_dict() for v in violations]
```

### JavaScript/Node.js

```javascript
const fs = require('fs');
const { execSync } = require('child_process');

// Run FastSSV
execSync('python main.py query.sql --output report.json');

// Read JSON report
const report = JSON.parse(fs.readFileSync('report.json', 'utf8'));

// Process results
if (report.is_valid) {
  console.log('✓ Query is valid');
} else {
  console.log(`✗ ${report.error_count} errors found`);

  report.violations?.forEach(v => {
    console.log(`  [${v.rule_id}] ${v.issue}`);
    console.log(`  → ${v.suggested_fix}`);
  });
}
```

### Shell Script

```bash
#!/bin/bash

python main.py query.sql --output result.json

if [ $? -eq 0 ]; then
  echo "✓ Valid"
else
  echo "✗ Invalid"
  jq '.violations[] | "[\(.rule_id)] \(.issue)"' result.json
fi
```

## Integration Examples

### REST API Endpoint

```python
from flask import Flask, request, jsonify
from fastssv import validate_sql_structured

app = Flask(__name__)

@app.route("/validate", methods=["POST"])
def validate():
    sql = request.json.get("query")
    dialect = request.json.get("dialect", "postgres")

    violations = validate_sql_structured(sql, dialect=dialect)

    errors = [v for v in violations if v.severity.value == "error"]
    warnings = [v for v in violations if v.severity.value == "warning"]

    response = {
        "is_valid": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "violations": [v.to_dict() for v in violations]
    }

    status_code = 200 if response["is_valid"] else 400
    return jsonify(response), status_code
```

### GitHub Actions Workflow

```yaml
name: Validate OMOP SQL Queries

on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          pip install -e .

      - name: Validate SQL queries
        run: |
          for sql_file in queries/*.sql; do
            echo "Validating $sql_file..."
            python main.py "$sql_file" --output "output/$(basename $sql_file .sql).json"

            if [ $? -ne 0 ]; then
              echo "❌ Failed: $sql_file"
              cat "output/$(basename $sql_file .sql).json" | jq '.violations'
              exit 1
            fi
          done

          echo "✅ All queries validated successfully"
```

### Batch Validation Script

```python
import json
import sys
import subprocess
from pathlib import Path

def validate_all_queries(query_dir: Path, output_dir: Path):
    """Validate all SQL queries in a directory."""
    output_dir.mkdir(parents=True, exist_ok=True)

    failed = []

    for sql_file in query_dir.glob("*.sql"):
        output_file = output_dir / f"{sql_file.stem}.json"

        result = subprocess.run(
            ["python", "main.py", str(sql_file), "--output", str(output_file)],
            capture_output=True,
            text=True
        )

        report = json.loads(output_file.read_text())

        if not report["is_valid"]:
            failed.append({
                "file": sql_file.name,
                "errors": report["error_count"],
                "violations": report.get("violations", [])
            })

    # Print summary
    if failed:
        print(f"\n❌ {len(failed)} queries failed validation:\n")
        for fail in failed:
            print(f"  {fail['file']}: {fail['errors']} errors")
            for v in fail['violations']:
                print(f"    - [{v['rule_id']}] {v['issue']}")
        sys.exit(1)
    else:
        print(f"\n✅ All queries validated successfully")
        sys.exit(0)

if __name__ == "__main__":
    validate_all_queries(Path("queries"), Path("output/validation"))
```

## Common Parsing Patterns

### Check Overall Status
```python
report = json.loads(report_file.read_text())

if report["is_valid"]:
    print("✓ Valid")
else:
    print(f"✗ {report['error_count']} errors, {report['warning_count']} warnings")
```

### Filter by Severity
```python
errors = [v for v in report.get("violations", []) if v["severity"] == "error"]
warnings = [v for v in report.get("violations", []) if v["severity"] == "warning"]

print(f"Errors: {len(errors)}, Warnings: {len(warnings)}")
```

### Group by Rule Category
```python
from collections import defaultdict

by_category = defaultdict(list)
for v in report.get("violations", []):
    category = v["rule_id"].split(".")[0]  # "semantic" or "vocabulary"
    by_category[category].append(v)

for category, violations in by_category.items():
    print(f"{category}: {len(violations)} violations")
```

### Handle Multiple Queries
```python
report = json.loads(report_file.read_text())

if "results" in report:
    # Multiple queries
    print(f"Validated {report['total_queries']} queries")
    print(f"  Valid: {report['valid_queries']}")
    print(f"  Invalid: {report['invalid_queries']}")

    for result in report["results"]:
        status = "✓" if result["is_valid"] else "✗"
        print(f"{status} Query {result['query_index']}: {result['error_count']} errors")
else:
    # Single query
    status = "✓" if report["is_valid"] else "✗"
    print(f"{status} {report['error_count']} errors, {report['warning_count']} warnings")
```

## Schema Reference

### Complete Single Query Schema

```typescript
{
  query: string,              // Normalized SQL query (comments removed, whitespace collapsed)
  dialect: string,            // SQL dialect used for parsing
  is_valid: boolean,          // true if no ERROR-level violations
  error_count: number,        // Count of ERROR violations
  warning_count: number,      // Count of WARNING violations
  violations?: [              // Array (omitted if empty)
    {
      rule_id: string,        // e.g., "semantic.join_path_validation"
      severity: "error" | "warning",
      issue: string,          // Human-readable violation message
      suggested_fix: string,  // Specific fix recommendation
      location?: string,      // Optional location info
      details?: object        // Optional structured metadata
    }
  ]
}
```

### Complete Multiple Query Schema

```typescript
{
  total_queries: number,       // Total queries in file
  valid_queries: number,       // Queries with is_valid=true
  invalid_queries: number,     // Queries with is_valid=false
  results: [                   // Array of query results
    {
      query_index: number,     // 1-based index
      query: string,
      dialect: string,
      is_valid: boolean,
      error_count: number,
      warning_count: number,
      violations?: [...]       // Same as single query
    }
  ]
}
```

## Schema Versioning

The JSON structure is currently at **version 1.0** and is considered stable. Future versions may add new fields but will maintain backward compatibility.

### Defensive Parsing

To future-proof your code, use defensive parsing:

```python
report = json.loads(output)

# Check for single vs multiple query format
if "results" in report:
    # Multiple queries
    for result in report["results"]:
        process_result(result)
else:
    # Single query
    process_result(report)

# Safe access to optional fields
violations = report.get("violations", [])
for v in violations:
    rule_id = v.get("rule_id", "unknown")
    severity = v.get("severity", "error")
    issue = v.get("issue", v.get("message", "No message"))  # Fallback to old "message" field
    suggested_fix = v.get("suggested_fix", "")
    details = v.get("details", {})
```

## Migration from Old Format

If you have code using the old format (pre-2024), note these changes:

**Old format:**
```json
{
  "rule_id": "...",
  "message": "...",
  "severity": "ERROR",
  "location": ""
}
```

**New format:**
```json
{
  "rule_id": "...",
  "severity": "error",
  "issue": "...",
  "suggested_fix": "...",
  "location": "...",
  "details": {}
}
```

**Migration notes:**
- `severity` values are now lowercase: `"ERROR"` → `"error"`, `"WARNING"` → `"warning"`
- `message` field renamed to `issue`
- Added `suggested_fix` field (required)
- Added `details` field (optional)
- `location` is now optional (was empty string by default)
