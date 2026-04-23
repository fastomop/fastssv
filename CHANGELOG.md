# Changelog

All notable changes to FastSSV will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
starting from 1.0.0. Pre-1.0 releases may contain breaking rule-set changes
between minor versions.

## [Unreleased]

### Added

- **HTTP API** (`fastssv.api`). Optional FastAPI service exposing the
  validator over HTTP. Installed via `pip install "fastssv[api]"`.
  Endpoints: `POST /v1/validate`, `GET /v1/rules`, `GET /v1/health`,
  plus Swagger UI at `/docs`. Production guardrails baked in: body-size
  limit, parse timeout, rate limiting, strict CORS, security headers,
  structured JSON logging (SQL body never logged — only hash),
  consistent error schema, versioned routes. See [docs/API.md](docs/API.md).
- **HTMX web UI** served from the same app: `GET /` for an interactive
  SQL validator, `GET /rules` for a browsable rule list with filter.
  Server-rendered via Jinja2, vendored HTMX (`fastssv/api/static/`), no
  build step. The UI sends `POST /ui/validate` which returns HTML
  fragments; shares all middleware (body-size, timeout, rate limit,
  security headers) with the JSON API.
- **Dockerfile** at `deploy/Dockerfile` — multi-stage, non-root user,
  `HEALTHCHECK`, gunicorn + uvicorn worker.
- **`deploy/docker-compose.yml`** — one-command container deploy:
  `docker compose -f deploy/docker-compose.yml up --build`. Wraps the
  existing Dockerfile, read-only root FS, `no-new-privileges`, health
  check, and env-override defaults for log level / rate limit /
  body-size / timeout / CORS / worker count.
- **`fastssv serve` subcommand** — one command launches the HTTP API +
  web UI on host Python without Docker. Dev default is `uvicorn.run()`
  in-process; `--reload` for auto-reload; `--prod` switches to
  gunicorn + uvicorn workers. Existing `fastssv <sqlfile>` behavior is
  unchanged.
- `api` optional extra in `pyproject.toml` (`fastapi`,
  `uvicorn[standard]`, `gunicorn`, `slowapi`, `pydantic-settings`,
  `jinja2`, `python-multipart`).
- `httpx` added to the `dev` extra for `TestClient`-based API tests.
- `[tool.setuptools.package-data]` entry so templates and static assets
  are packaged in the wheel.
- **Coverage measurement.** `pytest-cov` added to the `dev` extra; branch
  coverage configured in `pyproject.toml` with sensible exclusions
  (`__main__`, `TYPE_CHECKING`, `abstractmethod`, etc.). Default
  `pytest tests/` stays fast — pass `--cov` explicitly (or run the new
  CI `coverage` job) to measure. CI fails below 79% combined line+branch
  coverage. Current baseline: ~81.5%.
- **~40 new tests** surfaced by the first coverage run, targeting the
  coldest modules: unit tests for `core.deduplication` (54% → 98%),
  test classes for four previously-untested anti-pattern rules
  (`concept_lookup_context`, `top_as_synthetic_data`,
  `null_comparison_operator`, `concept_name_lookup`), and CLI
  integration tests covering the multi-query batch path, stdin
  reading, comment/quote-aware `_split_queries`, and the
  `_clean_llm_output` helper (`cli.py` 65% → 95%).

### Removed (dead-code cleanup)

- **`fastssv.fixer`** — orphaned 185-line module (`QueryFixer` and
  `fix_sql_file`). Not imported, invoked, or referenced from CLI, API,
  docs, or tests. Same shape as the `rule_layer` removal.
- Three unused helpers in `fastssv.core.omop_schema`: `get_column_type`,
  `get_primary_keys`, `get_foreign_keys` (the active `get_column_type`
  lives in `fastssv.schemas.cdm_column_types`).
- Orphaned `fastssv.core.rule_layer` module (`RuleLayer` enum and
  `RuleMetadata` dataclass were planned scaffolding that nothing
  consumed).
- `Rule.metadata` class attribute (tied to the removed module).
- `ColumnDef.nullable` field in `fastssv.core.omop_schema` — set on
  every column but never read.
- Unused `query_index` parameter on `cli.build_validation_result`.
- Several unused locals (`concept_alias` in
  `maps_to_target_standard_validation`, `table_norm` in
  `fact_relationship_valid_concepts`, leftover regex variables in
  `fixer.py`).

## [0.2.0]

This release is the result of a calibration pass against real OHDSI/Achilles
workloads that reduced false positives from ~200 across 8 sampled corpora to
single digits, with all real bugs still caught. See the "Removed rules" and
"Tightened rules" sections below.

### Added

- `PARSE_ERROR_RULE_ID` constant exported from the top-level package. When SQL
  cannot be parsed, `validate_sql_structured()` now returns a single
  `RuleViolation` with this rule_id and ERROR severity, instead of silently
  returning an empty list. `validate_sql()` gains a `parse_error` field in its
  result dict.
- Explicit rejection of empty, whitespace-only, and comment-only input at the
  parser level (previously these were silently treated as zero-violation
  "clean" queries).
- Python 3.10, 3.11, 3.12, and 3.13 are all now officially supported and
  tested in CI.
- `ruff check` step added to CI for basic lint coverage.
- Optional `[build]` extra containing `build` and `twine` for release
  workflows (previously these were runtime dependencies).

### Changed

- **Runtime dependencies narrowed to `sqlglot>=24.6.0` only.** Previously
  `build`, `pip`, and `twine` were erroneously listed as runtime dependencies,
  causing `pip install fastssv` to pull in ~40MB of unused packaging tools.
  These are now in the `[build]` optional extra.
- Python floor lowered from 3.12 to 3.10.

### Removed rules (rule calibration pass — all known to produce false positives on standard OHDSI/Achilles query patterns)

- `data_quality.null_grouping_handling` — fired on `GROUP BY state` / `GROUP BY zip` patterns where NULL buckets are legitimately desired; generic SQL concern, not OMOP-specific.
- `analytics.percentile_methodology` — fired on the OHDSI Achilles quartile idiom (`CASE WHEN order_nr < .25 * population_size`); stylistic recommendation, not silent failure.
- `performance.cross_join_large_table` — fired on `percentile_table CROSS JOIN cdm.person` patterns where the crossed side is a one-row scalar; performance hint, not OMOP semantic.
- `anti_patterns.sql_server_functions_in_postgres` — dialect-portability enforcement in a tool that claims to be dialect-agnostic. FastSSV auto-detects dialect.
- `analytics.integer_division_precision_loss` — fired on `DATEDIFF(day, ...) / 365` year-bucketing; standard Achilles pattern.
- `analytics.division_by_zero_risk` — defensive SQL advice, not OMOP semantic. Also false-positive on window-function denominators that cannot be zero.
- `concept_standardization.concept_relationship_valid_date_range_check` — demanded `valid_end_date >= CURRENT_DATE` even when `invalid_reason IS NULL` was present; belt-and-suspenders check that generated noise on standard queries.
- `concept_standardization.maps_to_chain_follow_to_terminal` — redundant with `concept_standardization.maps_to_target_standard_validation`.
- `joins.concept_relationship_incomplete_join` — premise ("always join concept to both CR sides") was wrong for discovery queries and CR-to-CR relationship chains.
- `concept_standardization.hierarchy_expansion_required` — assumed the user provided an ancestor concept; fired on specific-concept-id filters which are equally valid.
- `anti_patterns.concept_relationship_missing_relationship_filter` — redundant with `joins.concept_relationship_requires_relationship_id` (which has better tightening).

### Removed categories

- `performance/` — only rule was removed above.
- `analytics/` — only two rules were removed above.

### Tightened rules (narrower detection to eliminate false positives, real bugs still caught)

- `data_quality.unmapped_concept_handling` — recognizes `col = <literal>` and `col IN (<literals>)` as implicit zero handling; no longer suggests redundant `AND col > 0` on top of pinning filters.
- `concept_standardization.invalid_reason_enforcement`:
  - Source/lookup distinction: only warns when vocabulary tables are used as source (filtered by vocabulary_id / domain_id / etc.), not as lookup joins for concept-name decoding.
  - Suppressed when query already filters `standard_concept = 'S'` (standard concepts are typically also valid).
  - Derived-table branch now requires the derived table (concept_ancestor, etc.) to appear in a primary FROM, not merely in a JOIN.
- `temporal.observation_period_anchoring` — skips vocabulary-only queries that have no clinical fact table.
- `data_quality.incorrect_percentile_calculation` — only fires on the genuine copy-paste bug (percentile_25/median/percentile_75 sharing an identical threshold); no longer over-fires on the standard Achilles pattern with distinct .25/.50/.75 thresholds.
- `anti_patterns.concept_lookup_context` — recognizes `concept_relationship` in the SELECT's FROM/JOINs as a valid lookup context.
- `joins.concept_relationship_requires_relationship_id` — skips when `relationship_id` is in `GROUP BY` (explicit exploratory analysis).
- `concept_standardization.multiple_maps_to_targets` — skips subqueries in FROM/JOIN position (derived tables, not scalars) or with their own DISTINCT/GROUP BY.
- `joins.concept_alias_reuse_validation`:
  - Scoped analysis per-SELECT (nested subqueries and UNION arms are no longer conflated).
  - Narrowed cross-table-reuse warning to primary+source / primary+type / source+type collisions.
- `joins.concept_join_validation` — `_is_vocab_safe_column` narrowed from {concept_code, vocabulary_id, domain_id, concept_class_id} to just `concept_code`; the others are legitimate FK-lookup join columns.
- `joins.join_path_validation` — added fallbacks for (a) JOIN targets with unqualified ON columns, and (b) implicit comma-joins via WHERE.
- `domain_specific.drug_exposure_cardinality_validation` — only fires when COUNT has a patient-intent alias (`num_persons`, `patient_count`, etc.); record-level aliases (`exposure_count`, `cn`, `drug_type_count`) are trusted as explicit intent.
- `anti_patterns.ambiguous_column_reference` — verifies the column actually exists in ≥2 in-scope tables via `CDM_COLUMNS` schema, instead of just counting tables.
- `concept_standardization.standard_concept_enforcement` — skips `*_type_concept_id` columns (type concepts are data-provenance tokens, not clinical concepts subject to standard/non-standard distinction).

### Fixed

- `pyproject.toml` runtime dependencies now include only `sqlglot`.
- `core.helpers.parse_sql` correctly rejects empty, whitespace-only, and comment-only input (previously `[None]` from sqlglot was treated as a successful parse).
- `core.helpers.extract_join_conditions` bug-impact: rules using `extract_join_conditions` would drop equalities involving unqualified columns; `join_path_validation` now has a fallback that handles these cases.

### Rule count

- Before this release: 168 rules across 8 categories (concept_standardization, temporal, joins, data_quality, domain_specific, anti_patterns, performance, analytics).
- After this release: 157 rules across 6 categories (performance and analytics categories removed entirely).

---

## [0.1.0] - initial release

Initial public release of FastSSV. 168 rules across 8 categories covering
OMOP CDM v5.4 semantic correctness.
