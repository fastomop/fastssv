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

-   :material-puzzle: **[Plugin system](plugin_architecture.md)**

    Add your own rules without modifying core code.

-   :material-book-open-variant: **[Semantic rules guide](semantic_rules_guide.md)**

    The reasoning behind each rule category and when violations matter.

-   :material-format-list-checks: **[Rules reference](rules_reference.md)**

    Every built-in rule with its ID, severity, and example violation.

-   :material-api: **[HTTP API](api.md)**

    Run FastSSV as a FastAPI service with an HTMX web UI.

-   :material-link-variant: **[MCP server](mcp.md)**

    Expose FastSSV's validator over the Model Context Protocol's
    Streamable HTTP transport.

-   :material-code-json: **[JSON output](json_output.md)**

    The structured report format for CI integration.

-   :material-text-box-outline: **[Logging](logging.md)**

    Configure log level, file destination, and JSON-structured output for
    log aggregators.

</div>

## Quick start

From inside a uv project (`uv init` first if you don't have one):

```bash
uv add fastssv
uv run fastssv path/to/query.sql
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
