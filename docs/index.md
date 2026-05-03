# Welcome to FastSSV Documentation

**FastSSV** (Fast Semantic Static Validator) is a semantic validation
framework for OMOP CDM SQL queries. It catches the silent failures that pass
any SQL linter and fail any replication attempt.

!!! tip "OMOP SQL that runs without errors can still be analytically wrong."
    A query that silently drops 30% of patients because it misses concept
    descendants, filters on a deprecated concept, or applies a temporal
    constraint outside a patient's observation window will execute cleanly,
    return plausible numbers, and produce a flawed study. FastSSV catches these
    violations *before* they reach results.

## What's in these docs

<div class="grid cards" markdown>

-   :material-sitemap: **[Architecture](architecture.md)**

    How FastSSV is laid out, the rule engine, and the plugin registry.

-   :material-puzzle: **[Plugin system](PLUGIN_ARCHITECTURE.md)**

    Add your own rules without modifying core code.

-   :material-book-open-variant: **[Semantic rules guide](SEMANTIC_RULES_GUIDE.md)**

    The reasoning behind each rule category and when violations matter.

-   :material-format-list-checks: **[Rules reference](RULES_REFERENCE.md)**

    Every built-in rule with its ID, severity, and example violation.

-   :material-api: **[HTTP API](API.md)**

    Run FastSSV as a FastAPI service with an HTMX web UI.

-   :material-code-json: **[JSON output](JSON_OUTPUT.md)**

    The structured report format for CI integration.

-   :material-text-box-outline: **[Logging](LOGGING.md)**

    Configure log level, file destination, and JSON-structured output for
    log aggregators.

</div>

## Quick start

```bash
pip install fastssv
fastssv path/to/query.sql
```

For local development:

```bash
uv sync
uv run fastssv path/to/query.sql
```

## Why FastSSV

- **Targets silent failures.** Catches cases where SQL is valid, the query
  returns results, and those results are wrong.
- **Static and deterministic.** Parses SQL into an AST and checks structural
  patterns against the OMOP CDM v5.4 schema — same query, same result, every
  time, without a database connection.
- **AI-agnostic.** Validates SQL from humans, ATLAS, scripts, or AI agents
  identically.
- **Rule-based and extensible.** Every check is a discrete, documented rule
  with a unique ID, severity level, violation message, and suggested fix.
