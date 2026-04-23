# FastSSV Logging System

FastSSV includes a comprehensive logging system for debugging, monitoring, and performance analysis. This guide covers all aspects of logging configuration and usage.

## Table of Contents

- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Log Levels](#log-levels)
- [Log Formats](#log-formats)
- [CLI Logging](#cli-logging)
- [Python API Logging](#python-api-logging)
- [Performance Tracking](#performance-tracking)
- [Production Best Practices](#production-best-practices)
- [Examples](#examples)

---

## Quick Start

### Console Logging (Default)

By default, FastSSV logs to stderr with INFO level:

```bash
# Simple validation with default logging
fastssv query.sql
```

### Enable Debug Logging

```bash
# CLI argument
fastssv query.sql --log-level DEBUG

# Or environment variable
export FASTSSV_LOG_LEVEL=DEBUG
fastssv query.sql
```

### Log to File

```bash
# CLI argument
fastssv query.sql --log-file logs/validation.log

# Or environment variable
export FASTSSV_LOG_FILE=logs/validation.log
fastssv query.sql
```

---

## Configuration

FastSSV logging can be configured via:

1. **CLI Arguments** (highest priority)
2. **Environment Variables**
3. **Default Values** (lowest priority)

### Environment Variables

Set these in your `.env` file or shell:

```bash
# Log level
FASTSSV_LOG_LEVEL=INFO

# Log file path (optional)
FASTSSV_LOG_FILE=logs/fastssv.log

# Log format
FASTSSV_LOG_FORMAT=detailed

# Performance tracking
FASTSSV_LOG_PERFORMANCE=true
```

### CLI Arguments

Override environment variables:

```bash
fastssv query.sql \
  --log-level DEBUG \
  --log-file logs/debug.log \
  --log-format json
```

---

## Log Levels

FastSSV supports standard Python log levels:

| Level | Description | Use Case |
|-------|-------------|----------|
| **DEBUG** | Detailed diagnostic information | Development, troubleshooting |
| **INFO** | General informational messages | Production monitoring (default) |
| **WARNING** | Warning messages | Potential issues |
| **ERROR** | Error messages | Application errors |
| **CRITICAL** | Critical errors | System failures |

### What's Logged at Each Level

**DEBUG:**
- Rule execution details (each rule)
- Rule selection logic
- Timing for each rule execution
- Internal state changes

**INFO:**
- Validation start/completion
- Query count and results
- Total violations found
- File I/O operations
- Performance metrics (when enabled)

**WARNING:**
- Configuration issues
- Deprecated feature usage
- Non-fatal problems

**ERROR:**
- SQL parsing failures
- File I/O errors
- Validation exceptions

---

## Log Formats

### Simple Format

Minimal output for human readability:

```bash
FASTSSV_LOG_FORMAT=simple fastssv query.sql
```

Output:
```
INFO: Starting validation: 245 characters, dialect=postgres
INFO: Validation complete: 157 rules, 2 errors, 3 warnings
```

### Detailed Format (Default)

Includes timestamps and logger names:

```bash
FASTSSV_LOG_FORMAT=detailed fastssv query.sql
```

Output:
```
2026-04-20 19:30:15 - fastssv.cli - INFO - Starting validation: 245 characters, dialect=postgres
2026-04-20 19:30:15 - fastssv - INFO - Running all 157 rules
2026-04-20 19:30:15 - fastssv - INFO - Validation complete: 157 rules, 2 errors, 3 warnings
```

### JSON Format

Structured logs for machine parsing and log aggregation:

```bash
FASTSSV_LOG_FORMAT=json fastssv query.sql
```

Output:
```json
{"timestamp": "2026-04-20 19:30:15", "level": "INFO", "logger": "fastssv.cli", "message": "Starting validation: 245 characters, dialect=postgres"}
{"timestamp": "2026-04-20 19:30:15", "level": "INFO", "logger": "fastssv", "message": "Running all 157 rules"}
{"timestamp": "2026-04-20 19:30:15", "level": "INFO", "logger": "fastssv", "message": "Validation complete: 157 rules, 2 errors, 3 warnings", "violation_count": 5}
```

**Benefits:**
- Easy parsing with tools like `jq`
- Compatible with log aggregation systems (Elasticsearch, Splunk, etc.)
- Structured fields for filtering and analysis

---

## CLI Logging

### Basic Usage

```bash
# Default INFO logging to console
fastssv query.sql

# Debug logging
fastssv query.sql --log-level DEBUG

# Log to file
fastssv query.sql --log-file logs/validation.log

# JSON logs for production
fastssv query.sql --log-format json --log-file logs/validation.json
```

### What's Logged

The CLI logs:

1. **Input Processing:**
   - SQL file path or stdin reading
   - SQL length in characters
   - Number of queries detected

2. **Configuration:**
   - Dialect detection
   - Strict mode status
   - Rule selection

3. **Validation:**
   - Validation start
   - Progress for multiple queries
   - Violation counts per query
   - Performance metrics

4. **Output:**
   - Report file path
   - Final results summary

### Multi-Query Logging

When validating multiple queries:

```bash
fastssv queries.sql --log-level INFO
```

Output:
```
2026-04-20 19:30:15 - fastssv.cli - INFO - Read SQL input: 5432 characters from queries.sql
2026-04-20 19:30:15 - fastssv.cli - INFO - Split into 25 queries
2026-04-20 19:30:15 - fastssv.cli - INFO - Processing 25 queries individually
2026-04-20 19:30:15 - fastssv.cli - INFO - Query 1: VALID (0 errors, 2 warnings, 45.23ms)
2026-04-20 19:30:15 - fastssv.cli - INFO - Query 2: INVALID (1 errors, 0 warnings, 38.91ms)
...
2026-04-20 19:30:16 - fastssv.cli - INFO - Batch validation complete: 25 queries, 15 valid, 10 invalid, 1250.45ms total
```

---

## Python API Logging

### Setup Logging in Code

```python
from fastssv.core.logging import setup_logging, get_logger

# Configure logging
logger = setup_logging(
    level="DEBUG",
    log_file="logs/my_app.log",
    log_format="json"
)

# Or use existing logger
from fastssv import validate_sql_structured

logger.info("Starting SQL validation")
violations = validate_sql_structured(sql, dialect="postgres")
logger.info(f"Found {len(violations)} violations")
```

### Module-Specific Loggers

Get a logger for your module:

```python
from fastssv.core.logging import get_logger

logger = get_logger(__name__)  # e.g., "my_app.validation"

logger.debug("Processing query")
logger.info("Validation complete")
logger.warning("Potential issue detected")
logger.error("Validation failed")
```

### Performance Logging

Use the performance logger for timing operations:

```python
from fastssv.core.logging import get_performance_logger

perf_logger = get_performance_logger("my_app")

with perf_logger.timed_operation("SQL Validation"):
    violations = validate_sql_structured(sql)
# Logs: "SQL Validation completed" with duration_ms
```

Enable performance tracking:

```python
import os
os.environ["FASTSSV_LOG_PERFORMANCE"] = "true"
```

---

## Performance Tracking

### Enable Performance Logging

```bash
# Environment variable
export FASTSSV_LOG_PERFORMANCE=true
fastssv query.sql --log-level INFO

# Or in Python
import os
os.environ["FASTSSV_LOG_PERFORMANCE"] = "true"
```

### What's Tracked

When performance logging is enabled:

1. **Total validation time** - Overall duration
2. **Per-query time** - For multi-query validation
3. **Per-rule time** - Execution time for each rule (DEBUG level)

### Example Output

```
2026-04-20 19:30:15 - fastssv.cli - INFO - Validation complete: 157 rules, 2 errors, 3 warnings {"duration_ms": 125.45}
2026-04-20 19:30:15 - fastssv - DEBUG - Rule concept_standardization.concept_ancestor_rollup_direction: 0 violation(s) {"rule_id": "concept_standardization.concept_ancestor_rollup_direction", "violation_count": 0, "duration_ms": 2.34}
```

### JSON Format with Performance

JSON logs include structured timing data:

```json
{
  "timestamp": "2026-04-20 19:30:15",
  "level": "INFO",
  "logger": "fastssv.cli",
  "message": "Validation complete: 157 rules, 2 errors, 3 warnings",
  "duration_ms": 125.45,
  "violation_count": 5
}
```

---

## Production Best Practices

### 1. Log Level

Use `INFO` in production, `DEBUG` for troubleshooting:

```bash
# Production
FASTSSV_LOG_LEVEL=INFO

# Troubleshooting
FASTSSV_LOG_LEVEL=DEBUG
```

### 2. Log Format

Use JSON for production (easier to parse):

```bash
FASTSSV_LOG_FORMAT=json
FASTSSV_LOG_FILE=logs/fastssv.json
```

### 3. Log Rotation

Use `logrotate` or similar tools to manage log files:

```bash
# /etc/logrotate.d/fastssv
/var/log/fastssv/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
}
```

### 4. Log Aggregation

Collect logs in centralized systems:

- **Elasticsearch + Kibana:** Search and visualize logs
- **Splunk:** Enterprise log management
- **CloudWatch/Stackdriver:** Cloud-native logging

Example: Send JSON logs to Elasticsearch:

```bash
fastssv query.sql --log-format json | \
  while read line; do
    curl -X POST "http://localhost:9200/fastssv-logs/_doc" \
         -H 'Content-Type: application/json' \
         -d "$line"
  done
```

### 5. Performance Monitoring

Enable performance logging in production to track slowdowns:

```bash
FASTSSV_LOG_PERFORMANCE=true
FASTSSV_LOG_FORMAT=json
```

Then query logs for slow validations:

```bash
# Find validations over 1 second
cat logs/fastssv.json | jq 'select(.duration_ms > 1000)'
```

---

## Examples

### Example 1: Debug Mode with File Logging

```bash
fastssv complex_query.sql \
  --log-level DEBUG \
  --log-file logs/debug.log \
  --log-format detailed
```

### Example 2: Production JSON Logging

```bash
export FASTSSV_LOG_LEVEL=INFO
export FASTSSV_LOG_FORMAT=json
export FASTSSV_LOG_FILE=logs/production.json
export FASTSSV_LOG_PERFORMANCE=true

fastssv batch_queries.sql
```

### Example 3: Python API with Custom Logger

```python
from fastssv import validate_sql_structured
from fastssv.core.logging import setup_logging, get_performance_logger

# Setup
logger = setup_logging(level="INFO", log_format="json")
perf = get_performance_logger("my_app")

# Validate with timing
sql = "SELECT * FROM condition_occurrence WHERE condition_concept_id = 201826;"

with perf.timed_operation("SQL Validation", level=logging.INFO):
    violations = validate_sql_structured(sql, dialect="postgres")

logger.info(
    f"Validation complete: {len(violations)} violations",
    extra={"violation_count": len(violations)}
)
```

### Example 4: Parsing JSON Logs with jq

```bash
# Count violations by severity
cat logs/fastssv.json | \
  jq -r 'select(.violation_count) | .level' | \
  sort | uniq -c

# Find slowest rules
cat logs/fastssv.json | \
  jq -r 'select(.rule_id and .duration_ms) | "\(.duration_ms)\t\(.rule_id)"' | \
  sort -rn | head -10

# Extract all errors
cat logs/fastssv.json | \
  jq 'select(.level == "ERROR")'
```

---

## Troubleshooting

### No Logs Appearing

1. Check log level: Use `DEBUG` to see more output
2. Check file permissions if logging to file
3. Verify environment variables: `echo $FASTSSV_LOG_LEVEL`

### Too Much Log Output

1. Increase log level to `WARNING` or `ERROR`
2. Disable performance logging: `FASTSSV_LOG_PERFORMANCE=false`
3. Filter specific loggers in production

### Log File Growing Too Large

1. Implement log rotation (see Production Best Practices)
2. Use JSON format and stream to log aggregation system
3. Reduce log level in production

---

## Related Documentation

- [CLI Usage](../README.md#quick-start)
- [Python API](../README.md#python-api)
- [Configuration](.env.example)
- [Contributing](../README.md#contributing)
