# Changelog

All notable changes to FastSSV will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
starting from 1.0.0. Pre-1.0 releases may contain breaking rule-set changes
between minor versions.

## [Unreleased]

### Added

- **`domain_specific.event_rows_counted_as_persons` (WARNING) replaces the
  three cardinality rules.** It fires when a SELECT's own FROM/JOIN list reads
  a table with many rows per person (event, era, observation_period or
  payer_plan_period tables), a `COUNT(*)` / `COUNT(col)` in that SELECT
  carries a person-count alias (`patient_count`, `num_persons`, `subjects`,
  …), and the SELECT neither counts `DISTINCT person_id` nor groups by a
  person key. Derived tables and CTEs are not looked into, and an
  observation_period bounded on both sides by a date counts as one row per
  person. Previously `drug_exposure_cardinality_validation` fired on any
  patient-named `COUNT(*)` anywhere in a statement that mentioned
  drug_exposure (all 54 agent-corpus findings were counts over subqueries
  already grouped by person_id) and missed the real overcount
  `COUNT(person_id) AS num_persons FROM drug_exposure`;
  `condition_occurrence_cardinality_validation` and
  `event_cardinality_validation` flagged plain event listings (which are
  correct) and missed counts over them. Agent corpus 54 → 0, expert 7 → 2
  (both genuine overcounts). Aliases that also name a kind of record
  (`patient_visits`, `patient_records`, `person_exposures`) count rows on
  purpose and are ignored.

  `domain_specific.event_rows_counted_as_persons` reads the alias's head noun
  and follows event-grain derived tables. Whether a count is presented as a
  count of people used to be decided by substring lists, so
  `patients_with_drug`, `overall_patients`, `available_patients`,
  `patients_on_therapy` and `patients_per_year` were silent although each
  counted exposure rows. The alias's head noun is now used (counter words
  dropped, phrase cut at its first preposition), and `COUNT(person_id)` is
  reported even without a person-named alias, unless the alias names something
  else (`n_visits`). `COUNT(DISTINCT <non-person key>)`, `SUM(1)`, windowed
  counts and a count labelled outside the derived table that computes it are
  now recognised. CTEs and derived tables that pass event rows through
  unchanged are followed, and `cohort` (several records per subject are
  allowed) joins the tables covered; the table list is generated from the CDM
  v5.4 field-level specification. An observation_period pinned by non-negative
  date differences, and a table filtered to one primary key per person, are no
  longer reported.

- **`joins.period_join_requires_date_overlap` (WARNING) merges the
  observation_period and payer_plan_period join rules.** A clinical table
  joined to either period table on `person_id` must also be bounded by the
  period's start and end dates (or `BETWEEN` them) in the same SELECT.
  Unqualified columns (`condition_start_date >=
  observation_period_start_date`) and offset bounds
  (`op.observation_period_start_date + 365 * INTERVAL '1 day'`) are now
  recognised, comma joins and `USING (person_id)` are covered, and a SELECT
  whose result cannot change under row duplication (`SELECT DISTINCT`, or only
  `COUNT(DISTINCT …)`/`MIN`/`MAX` with no period column in the output) is not
  reported. Expert corpus 8 → 1 queries; all 18 previous findings were
  unqualified-column or offset-bound false positives. `person_id` equalities
  are followed through other tables (`op.person_id = p.person_id AND
  co.person_id = p.person_id` is reported). An equality on a period bound
  counts as pinning one period (the "first observation period" pattern
  `op.observation_period_start_date = (SELECT MIN(…) … WHERE op2.person_id =
  op.person_id)`). The query of an `IN (…)` / `EXISTS (…)` is skipped, unless
  a predicate compares a period column with another table's column without
  pinning it: a one-sided washout admits the wrong period whatever the
  deduplication.

  `joins.period_join_requires_date_overlap` treats payer plan periods as
  overlapping and reads date differences. The CDM states that observation
  periods never overlap but payer plan periods can. A payer_plan_period bound
  equal to one value (the "first plan" subquery) no longer counts as selecting
  one plan, and the message and fix now say that containment keeps the plans
  in force at the event, which can be more than one. Two-sided containment
  written as a date difference (`x - start >= 365 AND x <= end`, T-SQL
  `DATEDIFF(day, start, x) >= 365`, DuckDB `date_diff`) is now recognised;
  previously it was reported. Tables wrapped in an unfiltered CTE or derived
  table are now checked.

- **`domain_specific.numeric_value_without_unit_constraint` (warning) replaces
  three unit rules.** `domain_specific.measurement_cross_unit_comparison`,
  `domain_specific.measurement_unit_validation` and
  `domain_specific.dose_era_cross_unit_comparison` are merged into one rule
  covering `measurement.value_as_number`, `observation.value_as_number` and
  `dose_era.dose_value`. It fires on an aggregate (AVG, SUM, MIN, MAX, STDDEV,
  VARIANCE, MEDIAN, percentiles) or on a non-zero numeric threshold when the
  query never pins `unit_concept_id`. Previously the measurement rules fired
  when the value was only NULL-tested inside an aggregate (`SUM(CASE WHEN
  value_as_number IS NULL …)`), treated an unfiltered `JOIN concept ON
  unit_concept_id = concept_id` as a unit constraint, accepted another table's
  `unit_concept_id` as the constraint, could both fire on one query, and
  missed values reached through CTEs or derived tables. The new rule follows
  column lineage. It accepts `= <id>`, an `IN` list or subquery of unit
  concepts (Circe/ATLAS cohorts list synonymous unit concepts), `GROUP
  BY`/`PARTITION BY unit_concept_id`, a filtered or grouped joined unit
  concept, or unit-aware `CASE` conversion inside the aggregate. It ignores
  comparisons with 0. It reports at most one finding per table and statement.

  `domain_specific.numeric_value_without_unit_constraint` matches unit
  constraints to the row they are written on. A unit filter in one UNION
  branch, on another alias of a self-join, or a join that equates two rows'
  units used to silence every aggregate and threshold in the statement; now a
  constraint counts only for the same row, followed through CTEs and derived
  tables. `unit_concept_id IS NULL` (and `= 0`) now counts as a constraint,
  since the CDM keeps the unit NULL for a quantity without one, and the
  suggested fix offers it. Thresholds inside CASE conditions are now checked,
  with a unit test in the same condition accepted.

- **`domain_specific.drug_exposure_implausible_value_filter` (warning)
  replaces `domain_specific.drug_days_supply_validation` and
  `domain_specific.drug_quantity_validation`.** The new rule fires only when a
  WHERE / JOIN ON condition admits no plausible value: `days_supply` 1–365,
  `quantity` > 0 (OHDSI Data Quality Dashboard CDM v5.4 thresholds). It
  evaluates the whole condition with three-valued logic, so NOT, AND and OR
  are honoured. Previously the days_supply rule flagged the standard guard
  `days_supply > 0` because it judged the literal rather than the predicate.
  The quantity rule flagged `NOT (quantity <= 0)`, and the two rules resolved
  unqualified columns differently. `quantity = 0` now fires, matching DQD. A
  range that still admits plausible values, such as `days_supply BETWEEN -10
  AND 500`, no longer fires. The message says the filter admits only values
  outside the Data Quality Dashboard plausible range; the CDM conventions only
  ask that `days_supply` over 365 be investigated.

  `domain_specific.drug_exposure_implausible_value_filter` no longer reports
  queries that exclude implausible rows. A condition under `NOT EXISTS` / `NOT
  IN`, in the right branch of EXCEPT or in an outer join's ON removes the
  implausible rows instead of selecting them and is now silent. HAVING
  conditions and a column with a constant offset (`days_supply - 1 < 0`) are
  now evaluated. The thresholds are generated from the CDM v5.4 field-level
  specification (quantity's lower bound is 0.0000001), and the message
  separates the CDM convention from the Data Quality Dashboard threshold and
  says that deliberate data-quality queries can ignore it.

- **`domain_specific.value_as_columns_conjunction` (warning) replaces
  `domain_specific.observation_value_as_columns_mutually_contextual` and the
  ERROR rule
  `domain_specific.measurement_value_as_number_and_concept_validation`.** One
  rule now covers measurement and observation. It fires when value conditions
  on two different `value_as_*` columns of the same row are AND-ed. Conditions
  are grouped per table alias, so a self-join that tests `o1.value_as_number`
  and `o2.value_as_concept_id` no longer fires. Joins that decode
  `value_as_concept_id` against `concept.concept_id` are not value conditions,
  and lineage follows derived tables. The measurement half was an ERROR
  although its own documentation said both columns can legitimately be
  populated. It is now a warning, so such queries are no longer blocked.
  Conditions are grouped by the base row actually read, so a CTE that projects
  `value_as_concept_id` from one observation row and `value_as_number` from
  another (a self-join) is not flagged when both are filtered downstream.

  `domain_specific.value_as_columns_conjunction` combines conditions per row
  across clauses and no longer overstates the effect. Conditions on the same
  measurement / observation row are now combined across JOIN ON, WHERE and CTE
  or derived-table layers, so a conjunction split between them is reported. A
  conjunct that also accepts the other column being NULL (`value_as_concept_id
  IS NULL OR value_as_concept_id = 0`) is no longer counted. The message no
  longer says the query "keeps almost no rows"; it says only rows with both
  columns populated can match, since CDM v5.4 provides for both being filled.

- **`temporal.implausible_date_filter` (WARNING) replaces three
  date-plausibility rules.**
  `temporal.clinical_event_date_in_future_validation`,
  `temporal.death_date_in_future_validation` and
  `data_quality.clinical_event_date_before_1900_validation` are merged into
  one WARNING rule over clinical event and death dates. It flags a predicate
  only when every date it admits lies after the current date (`>
  CURRENT_DATE`, `>= CURRENT_DATE + INTERVAL '30 days'`, `CURRENT_DATE < d`,
  `EXTRACT(YEAR FROM d) > EXTRACT(YEAR FROM CURRENT_DATE)`) or before the Data
  Quality Dashboard floor described below. Previously `d >= CURRENT_DATE -
  INTERVAL '30 days'`, a look-back window, was reported as "implies future
  dates". `DATE '…'`, `CAST('…' AS DATE)`, `DATEFROMPARTS` and `EXTRACT(YEAR
  …)` forms used to be missed and are now caught. The far-future-literal check
  is gone: date-shifted research CDMs such as MIMIC-IV hold events dated
  2100–2200, so `condition_start_date >= '2150-01-01'` can be correct there.
  End dates that the DQD requires to follow another date of the same row are
  exempt from the future check, so the "currently exposed" idiom `start <=
  CURRENT_DATE AND end >= CURRENT_DATE` is not reported; `>= CURRENT_DATE` is
  described as "the current date or later".

  `temporal.implausible_date_filter` uses the Data Quality Dashboard's bounds.
  The pre-1900 floor is replaced by each column's DQD `plausibleValueLow`
  (1950-01-01 for clinical events, eras, periods and cohort dates; 1850-01-01
  for `birth_datetime`), so `condition_start_date < '1950-01-01'` is now
  reported and the message names the floor. The list of columns is generated
  from the DQD field-level file and now includes the era tables,
  observation_period, payer_plan_period, cohort, `verbatim_end_date`,
  `nlp_datetime` and `birth_datetime`. Newly recognised: `LOCALTIMESTAMP`,
  `(SELECT CURRENT_DATE)`, `<date> - CURRENT_DATE > 0`, `DATEDIFF(day,
  GETDATE(), <date>) > 0`, `HAVING MIN(<date>) > CURRENT_DATE`,
  `DATE_TRUNC(...)` subjects and renamed columns. End dates exempt from the
  future check are now the columns the DQD requires to follow another date of
  the same row.

- **`temporal.nullable_date_column` (WARNING) replaces
  `temporal.nullable_end_date_null_handling` and
  `temporal.required_date_column_validation`.** The rule is keyed on CDM v5.4
  Required = No. It covers the `*_datetime` columns, `measurement_time`, and
  the end dates of condition_occurrence, procedure_occurrence, device_exposure
  and episode. `drug_exposure_end_date` and `visit_end_date` were treated as
  nullable and are no longer flagged, because v5.4 makes them required. A
  comparison of two dates in the same row (`condition_end_date <
  condition_start_date`) is a plausibility check and is not flagged either.
  One predicate used to produce up to three findings across the old rules; it
  now produces one. It also covers `person.birth_datetime`,
  `note_nlp.nlp_datetime` and `drug_exposure.verbatim_end_date`, looks through
  `EXTRACT` / `YEAR` / `DATE_PART`, and checks nullable `BETWEEN` bounds.
  Same-row comparisons other than the end-before-start plausibility check (for
  example `condition_end_date > condition_start_date`) are reported. The agent
  corpus gains 196 findings, all on `birth_datetime` used for birth year or
  age where the required `year_of_birth` would not drop persons.

  `temporal.nullable_date_column` checks the NULL test in the right scope. An
  `IS [NOT] NULL` test now suppresses a finding only when it sits in the WHERE
  / JOIN ON / HAVING of the SELECT that uses the column (or in the aggregate's
  `FILTER` clause); previously a test anywhere in the statement, including
  another CTE or a `CASE` in the projection list, silenced an unguarded
  filter. `IN` / `NOT IN` lists on an optional date are now reported, and
  columns are followed by lineage, so a renamed `condition_end_date` is
  judged. The list of optional columns is generated from the CDM v5.4
  field-level file (no change in content).

- **`joins.observation_period_unlinked` (WARNING).** Reports a SELECT that
  reads `observation_period` together with `person` or a person-level clinical
  table in the same FROM/JOIN list but never equates
  `observation_period.person_id` with a column of another source. Without that
  equality every clinical row is paired with every person's observation
  periods, so a containment test checks one patient's event against other
  patients' windows and multiplies rows. Linkage through renamed columns
  (`subject_id = op.person_id`), WHERE-clause equalities and `USING
  (person_id)` count; UNION branches and correlated `EXISTS` subqueries where
  `observation_period` is the only source are not checked. This case was
  previously a branch of `temporal.observation_period_anchoring`, where all 12
  of its expert-corpus findings were false positives. Casts around the link
  and both sides pinned to the same literal `person_id` count as linked.

  `joins.observation_period_unlinked` checks the join that actually brings in
  observation_period. A `USING (person_id)` elsewhere in the FROM list no
  longer counts as linking observation_period; only a `USING` or `NATURAL`
  join between observation_period and another source does. `person NATURAL
  JOIN observation_period` and `op.person_id IN (co.person_id)` are no longer
  reported, and tables wrapped in a CTE or derived table are now checked. The
  person-level table list is generated from the CDM v5.4 field-level
  specification.

- **`concept_standardization.concept_literal_without_hierarchy` (WARNING).** A
  clinical `*_concept_id` filtered on literal concept ids, in a SELECT that
  does not use `concept_ancestor`, may miss descendant concepts: an
  ingredient- or class-level literal matches almost no records and silently
  returns zero patients. It stays a WARNING because whether a literal has
  descendants cannot be known without the vocabulary, and exact-concept
  queries are legitimate. The rule does not fire on concept id 0 (the unmapped
  sentinel) or site-local ids >= 2,000,000,000, on `drug_era` / `dose_era`
  (ingredient-level by definition), on columns of non-event tables such as
  `drug_strength` or `results.cohort`, on exclusions (`NOT IN`), CASE labels
  and aggregates, or in CTEs that are never read or that seed a
  `concept_ancestor` step. A `concept_ancestor` join suppresses the warning
  only in the SELECT that uses it, and the literal may appear on either side
  of `=`. On the Study 1 corpora it fires on 39 expert and 1,361 agent
  queries.

  `concept_standardization.concept_literal_without_hierarchy` no longer
  exempts hierarchy seeds taken from event rows. A CTE that filters an event
  table on a literal and feeds concept_ancestor seeds the expansion with
  records coded at exactly that concept. When nobody is coded there, the
  expansion returns nothing (on DuckDB: 0 patients instead of 1), so it is now
  reported. An expansion of another column in the same SELECT no longer
  silences the literal. The rule now covers every record concept column
  (visit_concept_id included) and every spelling of a literal: CTE renames,
  casts, quoted numbers, `= ANY(ARRAY[...])`, `BETWEEN n AND n`, literal
  subqueries, VALUES lists and `concept.concept_id` after a join. The message
  says the loss happens "if these concepts have descendants".

- **`anti_patterns.cte_shadows_omop_table` (WARNING), "CTE Shadows OMOP CDM
  Table".** Warns when a CTE is named after a table of the CDM v5.4
  specification (`cohort`, `concept`, `person`, `condition_occurrence`, …). In
  PostgreSQL a WITH query hides any real table of the same name for the rest
  of the statement, so every unqualified reference binds to the CTE: the query
  is harder to read, a later edit that adds a real CDM reference breaks, and
  fixes suggested by other rules (`JOIN concept c ON …`) bind to a CTE that
  lacks the columns they use. Derived-table aliases (`FROM (SELECT …) AS
  concept`) are not reported: they hide no table. The table names come from
  the CDM v5.4 specification, so CTEs named `attribute_definition` or
  `location_history`, which are not v5.4 tables, are not reported. The
  suggested fix is to rename the CTE; schema-qualifying every CDM reference is
  offered as a fallback.

- **Every rule carries examples and the published sources it is derived
  from.** `Rule` gains two class attributes next to the existing `example_bad`
  / `example_good`: `references`, a tuple of `Reference(title, url)`, and
  `extra_examples`, a tuple of `RuleExample(sql, severity, note, strict)` with
  one entry per additional severity or variant the rule emits. All 92 rules
  set them. Sources that many rules share (the CDM v5.4 specification and
  conventions, the v5.3 → v5.4 changes page, The Book of OHDSI chapters,
  THEMIS, the Data Quality Dashboard thresholds) live in the new
  `fastssv.core.references` module, whose `cdm_table()` and `section()`
  helpers keep titles and URLs uniform. `GET /v1/rules` returns `example_bad`,
  `example_good`, `extra_examples` and `references` for each rule. The
  `/rules` page shows the extra examples and a Sources list and gives each
  rule the anchor `#rule-<rule_id>`; each finding in the validator UI links to
  it ("Examples & sources"). `docs/rules_reference.md` has a section for every
  rule, with Examples and Sources. `tests/test_rule_metadata.py` enforces this
  registry-wide: `example_bad` must trigger the rule, `example_good` must not,
  each extra example must emit its declared severity, references must be
  distinct `https://` URLs, and the docs section must cite every reference
  URL. A rule registered without them fails the suite. Among the variants now
  shown are the strict-mode WARNING of
  `concept_standardization.standard_concept_enforcement` and the
  `concept_synonym` case of `invalid_reason_enforcement`.

- **Cross-statement scope for `data_quality.schema_validation`.** The
  rule used to validate every `exp.Table` node against the OMOP catalog,
  one statement at a time — so multi-statement scripts that build a
  scratch table in statement N and read from it in statement N+1
  (OHDSI Achilles is the canonical case: `CREATE TABLE
  scratch.tempResults_104 AS …; SELECT … FROM tempResults_104; DROP
  TABLE scratch.tempResults_104; ANALYZE statsView_105;`) generated
  hundreds of "Table 'tempResults_104' does not exist in OMOP CDM 5.4
  schema" false positives. The CLI and the FastAPI runner now scan the
  whole submission once before per-statement validation, collect
  every name introduced by `CREATE TABLE` / `CREATE VIEW` (both CTAS
  and column-def forms), and bind them to a new
  `ValidationContext.local_tables` frozenset; the schema rule then
  treats those names like CTEs (skipped). The scan is statement-by-statement
  with `split_sql_statements` so one unparseable template (`@param`
  placeholders, the typical SqlRender leftover) doesn't poison the
  pool. New context-manager helper `with_local_tables(frozenset)`
  nests cleanly inside `with_strict_mode`. The schema rule also
  picks up intra-tree CREATEs on its own when invoked in
  combined / single-call mode. Backward-compatible:
  `ValidationContext.local_tables` defaults to empty so single-statement
  callers see no change. Tests: `tests/test_rules.py::TestSchemaValidation`
  (`test_cross_statement_scope_combined_input`, `test_cross_statement_scope_via_context`,
  `test_analyze_target_not_flagged`, `test_drop_table_target_not_flagged`).

- **MCP `validate_sql` gains `include_warnings` (default `false`).** The
  agent-facing tool now returns error-severity findings only unless asked;
  `warning_count` is still reported. In the feedback study, warnings fed
  to an LLM caused revision churn and correct→wrong edits (intent-dependent
  advice such as observation-period anchoring) without improving
  correctness, while errors-only feedback matched or beat the full report
  on every model. The HTTP API and CLI are unchanged.

- **Per-rule exception isolation, surfaced as `meta.rule_execution_error`.**
  Previously every execution loop (`validate_sql`,
  `validate_sql_structured`, the legacy category helpers) called
  `rule.validate()` bare, so a single rule raising on an unusual AST shape
  aborted the whole batch (library/CLI) or became a 500 (API). Each rule
  now runs isolated: a raising rule is logged with its traceback, skipped,
  and reported as a single WARNING violation with
  `rule_id == "meta.rule_execution_error"` (new exported constant
  `RULE_EXECUTION_ERROR_RULE_ID`) whose `details["failed_rule_id"]` names
  the culprit. WARNING rather than ERROR because the *query* isn't known
  to be wrong — the validator is — so a clean query doesn't flip to
  INVALID over an internal bug. All other rules still run.

- **API hardening knobs and headers.** Four additions to the FastAPI
  service, each addressing a specific production gap:
  - `FASTSSV_API_MAX_CONCURRENT_VALIDATIONS` (default 8): per-worker bound
    on concurrent validation work. A timed-out validation keeps running on
    its thread (CPU-bound sqlglot parses can't be cancelled), so under a
    burst of adversarial slow-parse submissions the pool used to stay
    pinned even after clients got 408s; the API and UI now fail fast with
    503 + `Retry-After` when saturated instead. Permits are tied to the
    *worker thread's* lifetime, not the request's: a 408 does not free
    capacity while its abandoned parse is still burning a thread, and
    acquisition is an atomic check on the event loop (no
    check-then-acquire race).
  - `FASTSSV_API_RATE_LIMIT_STORAGE_URI` (default empty = in-memory):
    slowapi storage backend. The in-memory default is per-process — with N
    gunicorn workers the effective limit is N × `FASTSSV_API_RATE_LIMIT`
    and counters reset on restart; pointing this at a shared backend
    (e.g. `redis://…`) makes the limit real across workers.
  - `FASTSSV_API_TRUSTED_PROXY_HOSTS` (default `*`): which peers'
    `X-Forwarded-For`/`-Proto` headers are trusted when
    `FASTSSV_API_BEHIND_PROXY=true`. The previous hard-coded `*` let any
    directly-reachable client spoof its IP (and rotate rate-limit buckets)
    by forging `X-Forwarded-For`; operators should narrow this to the
    proxy's IP/CIDR.
  - A `Content-Security-Policy` header on every response
    (`default-src 'self'`; inline scripts/styles allowed — the templates
    use inline `<script>` blocks and `onclick=` handlers; all JS/CSS
    assets are vendored under `/static`).
  Also: `/v1/health` is now exempt from rate limiting so LB/kubelet
  probes can't be throttled into 429s by client traffic sharing the same
  source IP, and `deploy/docker-compose.yml` gained cgroup resource
  ceilings (`mem_limit`/`cpus`/`pids_limit`, overridable via
  `FASTSSV_MEM_LIMIT`/`FASTSSV_CPUS`) so a runaway parse is contained by
  the kernel. New env vars are documented in `deploy/.env.example` and
  wired through `deploy/docker-compose.yml`.

- **MCP (Model Context Protocol) Streamable HTTP endpoint at `/mcp`.** A
  new optional `[mcp]` extra (`uv add fastssv[mcp]`) brings in the
  official `mcp` Python SDK and mounts a stateless Streamable HTTP server
  alongside the existing FastAPI app. One tool is exposed: `validate_sql`
  (wraps `validate_sql_structured` with the same statement-split, strict
  mode and timeout behaviour as `/v1/validate`). Rule discovery happens
  via the `rule_id` returned on each violation, or the existing
  `/v1/rules` HTTP endpoint and `docs/rules_reference.md` — there is no
  separate `list_rules` MCP tool, since a static catalog is a poor fit
  for the tool primitive (it's the wrong primitive — resources would be
  better — and the `rule_id`s the model needs are surfaced in
  `validate_sql` results). The endpoint is built per the
  [2025-11-25 spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#streamable-http)
  in stateless mode (`stateless_http=True`, `json_response=True`), so no
  session store or SSE resumption layer is required. New env vars:
  `FASTSSV_API_MCP_ENABLED` (default `false` — opt-in because the endpoint
  is unauthenticated at the application layer),
  `FASTSSV_API_MCP_ALLOWED_ORIGINS` (CSV/JSON list, enforced by a dedicated
  middleware that 403s requests with a present-but-unlisted `Origin` —
  per the spec's DNS-rebinding mitigation), and `FASTSSV_API_MCP_AUTH_MODE`
  (reserved Literal pinned to `"none"`; widening it later is how we'd opt
  into OAuth 2.1 without churning the env-var name). **Authentication is
  intentionally unauthenticated at the application layer** — the [MCP
  authorization spec](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
  is OPTIONAL, and FastSSV's stateless validation surface has no
  per-user resources, so the deployment expectation is that operators
  gate `/mcp` at the reverse proxy (oauth2-proxy, mTLS, network ACLs).
  The `[mcp]` extra is added to `deploy/Dockerfile` and the new env vars
  are wired through `deploy/docker-compose.yml` and `deploy/.env.example`.
  When `FASTSSV_API_MCP_ENABLED=true` but the `mcp` package is missing,
  `_maybe_build_mcp_app` raises `RuntimeError` at startup so the
  misconfiguration fails loudly in non-interactive deployments (CI,
  docker) instead of silently booting without `/mcp`. With the toggle
  off (the default) and the `mcp` package missing, the app boots
  normally — `[api]`-only installs still work.

### Changed

- **Every finding is now independent of the SQL dialect; `dialect` is used
  only to parse.** FastSSV reports deviations from what the OMOP CDM defines
  (tables, columns, column types, vocabulary values, OHDSI conventions), not
  whether a given engine would run the SQL. Previously several rules chose
  their severity, and in places whether to report at all, from the dialect,
  because case sensitivity and implicit casts differ between engines; the same
  query could be an ERROR on PostgreSQL, a WARNING on Snowflake and silent on
  SQL Server. Now the same SQL gives the same (rule_id, severity, message) on
  every dialect it parses on, and messages state the CDM fact (the defined
  value or type) rather than an engine's behaviour. Remaining differences come
  only from how sqlglot parses each dialect (a construct one dialect does not
  support, a `@param` read as a column, BigQuery's double-quoted strings).

- **`data_quality.column_type_validation` is an ERROR on every dialect.** A
  varchar or date column compared with a number (`domain_id = 19`,
  `condition_start_date >= 2020`), an integer column compared with a quoted
  non-integer (`year_of_birth = 'unknown'`) and two columns of different type
  families (`person_id = person_source_value`) were ERROR on PostgreSQL, partly
  ERROR on DuckDB and WARNING on every other dialect; they are now ERROR
  everywhere, and the message cites the column's CDM type instead of
  "PostgreSQL has no operator…" / "On this dialect an implicit cast may run
  it". `value_as_string > '100'` stays a WARNING (it orders text). What counts
  as an integer literal is one definition for every dialect: optional
  surrounding whitespace, an optional sign and decimal digits. `'1e3'`,
  `'1.5'`, `'0x1F'` and `'8_507'` against an integer column, accepted before
  on DuckDB or PostgreSQL, are now ERROR. Sources: the PostgreSQL
  type-conversion page and the Book of OHDSI implicit-casts section are
  replaced by the CDM v5.4 field-level specification and the CONCEPT and
  CONDITION_OCCURRENCE table pages.

- **`data_quality.concept_id_string_comparison`: a quoted non-integer is an
  ERROR on every dialect.** `gender_concept_id = 'FEMALE'` was ERROR on
  PostgreSQL and DuckDB and WARNING elsewhere; it is now ERROR everywhere,
  with a message stating that the CDM defines the column as INTEGER. A quoted
  integer (`= '8507'`) stays a WARNING. The integer definition is the one
  `column_type_validation` uses, so `'0x1F'` and `'8_507'` move from WARNING
  to ERROR. The PostgreSQL and Book of OHDSI implicit-cast sources are
  replaced by the CDM CONCEPT page and the field-level specification; the
  BigQuery literal-coercion source stays for the quoted-integer warning.

- **Wrong-case vocabulary values are an ERROR on every dialect.**
  `data_quality.canonical_string_value_validation` (`vocabulary_id =
  'rxnorm'`), `concept_standardization.standard_concept_value_validation`
  (`standard_concept = 's'`), `concept_standardization.domain_vocabulary_validation`
  and `concept_standardization.unit_vocabulary_validation` (`vocabulary_id =
  'ucum'` on a unit join) were ERROR on PostgreSQL and DuckDB, WARNING on
  other case-sensitive engines and silent on SQL Server / MySQL. Vocabulary
  IDs and `standard_concept` values are case-sensitive values the vocabulary
  defines, so a case-only mismatch is now an ERROR everywhere, whatever an
  engine's collation; LIKE patterns are matched case-sensitively unless the
  SQL uses ILIKE, LOWER or UPPER. Misspelt IDs (`'ICD-10-CM'`), dead values in
  a list, negated values and non-restricting positions keep their WARNING. The
  Book of OHDSI "Case sensitivity in string comparisons" source is replaced by
  the CDM VOCABULARY / DOMAIN / CONCEPT_CLASS pages, the field-level
  specification and the Book of OHDSI Vocabularies section; the domain and
  unit rules gain a lowercase example.

- **`domain_specific.vocabulary_relationship_boolean_comparison`: integer and
  boolean operands are an ERROR on every dialect.** `is_hierarchical = 1`,
  `= TRUE`, `IS TRUE` and a bare `WHERE is_hierarchical` were reported only on
  PostgreSQL and BigQuery and accepted on engines that coerce. The CDM defines
  both flags as varchar(1) holding '0' / '1', so they are now ERROR everywhere,
  with a message that states the CDM type. The PostgreSQL and BigQuery
  documentation sources are replaced by the field-level specification.

- **`anti_patterns.ambiguous_column_reference` counts only CDM tables, and
  reports only names that are ambiguous on every engine.** A name exposed by
  two CTEs or derived tables of the query, or by a CDM table and a CTE, is no
  longer reported: which CDM tables share a column name is CDM knowledge,
  while a clash between sources the query defines itself is SQL scoping that
  the database reports on its own. On PostgreSQL it also reported a name merged by
  `USING` / `NATURAL JOIN` that a later source exposes again, and a name that
  matches a select-list alias in GROUP BY / HAVING / WHERE; DuckDB, BigQuery
  and Snowflake resolve those, so they are no longer reported on any dialect.
  The message no longer appends PostgreSQL's SQLSTATE 42702, and that error-code
  source is dropped.

- **`data_quality.schema_validation` reports engine system columns.**
  PostgreSQL's `ctid`, `xmin`, `xmax`, `cmin`, `cmax` and `tableoid` were
  skipped on PostgreSQL; they are not CDM columns, so they are now reported
  like any other undefined column, on every dialect. BigQuery's
  `information_schema.tables`, which its parser reads as one dotted table
  name, is now recognised as a system catalogue like on other dialects.

- **SQL quoted in finding messages is rendered the same for every dialect.**
  `free_text_column_misuse`, `implausible_date_filter`,
  `datetime_between_date_literal`, `observation_period_anchoring`,
  `negative_concept_id_validation`, `vocabulary_table_protection`,
  `note_nlp_offset_is_character_position` and `visit_length_of_stay_arithmetic`
  rendered the offending expression in the input dialect (`INT64`,
  `GETDATE()`, `sum(...)`), and a division parsed as DuckDB SQL was shown with
  an added `NULLIF(…, 0)`. They now render one dialect-neutral form.

- **ERROR-tier corrections from a final check on PostgreSQL 16 and DuckDB.**
  Every rule that can raise an ERROR was run against queries built to break
  it, and each reported false ERROR was confirmed on both engines. An ERROR
  now means the query fails, or certainly returns wrong rows, on any
  conformant CDM; findings that depend on data or intent are WARNINGs. Per
  rule:
  - `anti_patterns.destructive_operations_on_clinical_tables` treats a table
    the statement itself creates as temporary (`SELECT … INTO TEMP TABLE
    death`, `CREATE OR REPLACE TEMP TABLE death AS …`) as session-local: the
    statement is not reported, and later statements of the same input (or
    API batch) that write `death` are not reported either. A schema whose
    name contains `cdm` or `omop` but names a results, scratch or temp
    schema (`cdm_results.drug_era`, `omop_scratch.condition_era`) used to
    be taken as the CDM schema by substring match; such targets now get the
    rule's WARNING. The rule's docstring and documentation state why a write
    to the CDM schema stays an ERROR: FastSSV validates analytics SQL and
    the Book of OHDSI makes that schema read-only to the end user. The
    write is not claimed to fail or to return wrong rows.
  - `anti_patterns.ambiguous_column_reference` takes the visibility of a
    name in an ON clause from the JOIN of the SELECT being judged. A
    correlated name inside a subquery's own JOIN (`… JOIN death d ON … AND
    EXISTS (SELECT 1 FROM concept c JOIN concept_ancestor ca ON
    ca.ancestor_concept_id = gender_concept_id) JOIN provider pr …`, or the
    same inside a `CROSS JOIN LATERAL`) used to see every source of the
    outer SELECT, including `provider`, which is joined later and not yet
    visible; such queries run on PostgreSQL and DuckDB and were ERRORs.
  - `anti_patterns.join_key_validation` no longer treats
    `cohort_definition_id` as an entity key. The CDM v5.4 field-level
    specification marks it as neither a primary key nor a foreign key to a
    primary key (`cohort_definition.cohort_definition_id` refers to
    `COHORT.COHORT_DEFINITION_ID`, and COHORT has no primary key), so
    `cohort.cohort_definition_id = concept.concept_id`, which tools that
    number cohorts by concept id write, is no longer an ERROR. The generated
    entity-key list now keeps a foreign key only when it points at its
    target table's primary key; no other column changed.
  - `temporal.end_before_start_validation` reads an untyped string literal
    compared with a DATE column as a date, as PostgreSQL and DuckDB coerce
    it. `visit_start_date >= '2023-06-01 10:00' AND visit_start_date <=
    '2023-06-01'` returns the visits of 2023-06-01 on both engines and was
    reported as an empty range (ERROR). A `TIMESTAMP` literal, a cast of the
    column to TIMESTAMP and a `*_datetime` column keep their time of day, so
    `visit_start_date >= TIMESTAMP '2023-06-01 10:00' AND visit_start_date
    <= '2023-06-01'` is still an ERROR.
  - `concept_standardization.domain_vocabulary_validation`,
    `concept_standardization.standard_concept_value_validation` and
    `concept_standardization.era_table_standard_concepts` describe an
    impossible predicate inside a scalar subquery of the select list
    correctly. `SELECT co.person_id, (SELECT c.concept_name FROM concept c
    WHERE c.concept_id = co.condition_concept_id AND c.standard_concept =
    'Y') AS nm FROM condition_occurrence co` returns every
    condition_occurrence row; the message used to say the query returns no
    rows and now says column `nm` is NULL on every row (0 for a COUNT). The
    finding stays an ERROR, since the column is certainly dead.
  - `concept_standardization.concept_domain_validation` and
    `concept_standardization.domain_vocabulary_validation` document where
    each column's domain comes from. Four of the checked fields have no
    fkDomain in the CDM v5.4 field-level CSV; their domain is the one the
    specification text of the field names (`cdm_source.cdm_version_concept_id`
    Metadata, `drug_strength.drug_concept_id` Drug,
    `location.country_concept_id` Geography,
    `measurement.operator_concept_id` Meas Value Operator). The code comment
    used to attribute all of them to the CSV; both rules now cite the CSV and
    the CDM v5.4 table section of each of the four. Behaviour is unchanged.
  - `data_quality.schema_validation` reports a column missing from `cohort`
    or `cohort_definition` as a WARNING. The CDM v5.4 field-level CSV places
    these two tables in their own schema (`databaseSchema` = cohort), apart
    from the CDM and vocabulary tables, and analytic tools create their own
    tables under those names (`cohort_definition.cohort_name`,
    `cohort.row_id`), so such a column is not certain to fail. The message
    says the table may be a tool's own and the column was not checked, and no
    patch is offered. The set of tables is read from the CSV's
    `databaseSchema` column, which the rule now cites. Columns missing from
    CDM and vocabulary tables (`concept.concept_class`, `concept.concept_level`,
    `drug_exposure.start_date`) stay ERRORs.
  - `data_quality.negative_concept_id_validation` raises its ERROR only when
    the predicate restricts the rows the statement returns. In an outer
    join's ON clause (`LEFT JOIN concept c ON … AND c.concept_id = -1`) it
    attaches no row and the outer rows are kept; inside NOT EXISTS / NOT IN
    the empty subquery removes no outer row; inside an IN / EXISTS subquery
    under OR the branch is dead. Each of these was an ERROR saying the query
    returns nothing and is now a WARNING that says what happens instead. A
    subquery is judged through the predicate that holds it, like the
    domain_specific row-scope helpers.
  - `data_quality.concept_id_string_comparison` recognises integer input
    the way `data_quality.column_type_validation` does: digits with
    underscores and `0x` / `0o` / `0b` prefixes. `gender_concept_id =
    '8_507'` and `= '0x1F'` run on PostgreSQL 16 and DuckDB and were reported
    as values that cannot be converted (ERROR); they are now the quoted-integer
    WARNING. The unquoting patch is offered only for plain digits.
  - `data_quality.canonical_string_value_validation` words a negated filter
    correctly. `vocabulary_id <> 'snomed'` and `NOT IN ('rxnorm')` were said
    to match no row; on PostgreSQL and DuckDB no row holds the miscased value,
    so the filter excludes nothing and keeps every row, including the SNOMED
    rows it was meant to drop. The message now says so. Severities are
    unchanged.
  - `data_quality.vocabulary_table_protection` states, in its docstring
    and documentation, why a write to a vocabulary table is an ERROR: it is
    the rule set's one declared exception, shared with
    `anti_patterns.destructive_operations_on_clinical_tables`. FastSSV
    validates analytics SQL and the Book of OHDSI makes the CDM schema
    read-only to the end user; the write is not claimed to fail or to
    return wrong rows. Behaviour is unchanged.
  - `domain_specific.drug_era_concept_class_validation` no longer checks
    `drug_strength.ingredient_concept_id`. The CDM v5.4 field-level CSV leaves
    its fkClass empty (only the Data Quality Dashboard copy sets Ingredient),
    and the Athena release's drug_strength rows point at 3,642 Precise
    Ingredient concepts as well, so `concept_class_id = 'Precise Ingredient'`
    there returns rows. A class restriction on a concept that reaches the era
    only through an OR branch (`de.drug_concept_id IN (SELECT … WHERE
    concept_class_id = 'Clinical Drug') OR …`) is now a WARNING: that branch
    is empty, but the others still return rows.
  - `domain_specific.person_birth_field_validation` reports a month outside
    1-12, a day outside 1-31 and a month/day pair that never occurs as a
    WARNING, not an ERROR. The Data Quality Dashboard thresholds the rule
    cites tolerate a share of violating rows, so the CDM does not guarantee
    that none exist, and DQD's own check query (`month_of_birth > 12`) was an
    ERROR. The message now says rows can match only in non-conformant data.
    An inverted BETWEEN stays an ERROR: it is empty for any data. The rule's
    main example is now the inverted BETWEEN.
  - `joins.drug_exposure_drug_strength_join_validation` treats every
    FROM/JOIN source of a SELECT as a node of the relation. A link through a
    CTE or derived table built with JOIN, GROUP BY or DISTINCT (`JOIN drugs d
    ON d.drug_concept_id = de.drug_concept_id JOIN drug_strength ds ON
    ds.drug_concept_id = d.drug_concept_id`), a correlated EXISTS that
    equates both tables' `drug_concept_id`, and an unqualified renamed column
    (`JOIN s ON ds_drug = de.drug_concept_id`) now count. Each of these
    returns the right pairs on PostgreSQL and DuckDB and was reported as an
    unrelated (Cartesian) read, an ERROR.
  - `joins.concept_metadata_join_validation` and
    `joins.concept_relationship_relationship_join_validation` report a key
    joined to the referenced table's name column (`concept.domain_id =
    domain.domain_name`, `relationship_id = relationship_name`) as a WARNING.
    The join returns correct rows wherever the name equals the id, which in
    Athena v5.0 27-AUG-25 holds for every main clinical domain (30 of 50
    domains), 2 of 113 vocabularies, 208 of 433 concept classes and 72 of 722
    relationships; the message gives those counts. A key joined to a column
    that never holds an id (`vocabulary_version`, `vocabulary_reference`,
    `is_hierarchical`, `defines_ancestry`) stays an ERROR. The rules' main
    examples are now those ERROR cases.
  - `data_quality.column_type_validation` and
    `data_quality.concept_id_string_comparison` judge a quoted value against
    an integer column by the dialect's own integer input, from one shared
    definition. DuckDB parses the string as a number and rounds it, so
    `year_of_birth = '1e3'` or `gender_concept_id = '1.5'` runs there and is
    no longer an ERROR on the `duckdb` dialect, while `'0o17'` and `'-0x1F'`,
    which DuckDB rejects, now are. PostgreSQL's syntax is unchanged. Both
    syntaxes were checked against PostgreSQL 16 and DuckDB on fuzzed input.
  - `data_quality.vocabulary_table_protection` uses the schema logic of
    `anti_patterns.destructive_operations_on_clinical_tables`: a schema named
    for results, scratch or temp space (`vocab_scratch.concept`,
    `cdm_results.concept_ancestor`) gets the WARNING, not the ERROR.

- **Web UI example "Unmapped source concept" replaced by "Source concept in
  hierarchy".** The old example filtered `condition_source_concept_id <> 0`,
  which the narrowed `standard_concept_enforcement` no longer reports. The new
  example matches a source concept against `concept_ancestor` and shows the
  warning that rule now emits.

- **Warning-tier redesign: rules narrowed to the claim they can actually
  make.** The Study 1 pattern-level adjudication of the 0.3.0 warning tier
  found that the highest-volume warning rules described something absent from
  the query: the parser found the pattern, but the rule's assertion about it
  was false under OMOP semantics. Each rule below keeps its `rule_id`; what
  changes is when it fires and what it claims.
  `concept_standardization.standard_concept_enforcement`, the third rule of
  this review, has its own entry below.
  - `concept_standardization.concept_ancestor_self_include_redundancy` now
    fires only on `UNION ALL`. It previously also flagged plain `UNION`, `OR`
    predicates and `IN (…)` lists as "duplicating the anchor", but `UNION`
    deduplicates by definition, `OR` selects rows rather than emits them, and
    `IN` is set membership. All 20,766 expert-corpus findings (every one the
    Circe codeset shape `… UNION select c.concept_id from CONCEPT c join
    CONCEPT_ANCESTOR ca …`) were adjudicated false observations. `UNION ALL`
    branches tagged with distinct marker literals emit tuples that are not
    duplicates and are not flagged; nor is a `UNION ALL` whose every consumer
    deduplicates the rows (`DISTINCT`) or reads them as a set (`IN`,
    `EXISTS`), followed through CTE references. More self-row exclusions on
    the `concept_ancestor` branch silence it: `min/max_levels_of_separation`
    `>`, `=`, `IN` or `BETWEEN` excluding 0, `ancestor_concept_id <>
    descendant_concept_id` (including `NOT (… = …)`), and
    `descendant_concept_id <> <anchor>` / `NOT IN (<anchor>)`; a reversed
    comparison such as `5 > min_levels_of_separation` is not mistaken for one.
    Only an id emitted explicitly by one branch and via the self row by the
    other branch counts; an overlap inside a single plain-`UNION` branch is
    deduplicated there. Re-run over both Study 1 corpora the rule now produces
    zero findings, against 20,768 before.
  - `concept_standardization.multiple_maps_to_targets` now flags only the two
    shapes that assert a single mapping target: a scalar subquery returning
    `concept_id_2` (fails at execution on a fan-out) and `LIMIT 1` on the
    mapping lookup (silently drops targets). The "`'Maps to'` without
    `DISTINCT` / `GROUP BY`" check was removed: retaining every target is
    complete, not wrong, and duplicate intermediate rows are collapsed by
    `COUNT(DISTINCT …)` or the set-valued consumer (957 findings adjudicated
    false observations). The scalar detector also misread `IN (SELECT …)` and
    `EXISTS` as scalar (17 findings); subqueries under `IN`, `EXISTS`, `ANY`,
    `ALL`, `FROM` or `JOIN` are now recognised as set-valued, and `LIMIT 1` on
    an aggregate-only SELECT is ignored. Re-run over both Study 1 corpora the
    rule now produces zero findings, against 976 before. Aggregate scalar
    subqueries such as `(SELECT MIN(concept_id_2) … 'Maps to')`, which return
    one row by construction and are the rule's own suggested fix, are not
    flagged either. The `LIMIT` / scalar check applies to the SELECT that
    itself reads the mapping, so an outer `ORDER BY … LIMIT 1` over fact rows,
    or an outer scalar concept-name lookup around an aggregate mapping
    subquery, is not reported.

- **`concept_standardization.standard_concept_enforcement` redesigned to match
  the OMOP CDM v5.4 contract.** The 0.3.0 rule fired on every
  `<event>_concept_id` reference without a `standard_concept = 'S'` filter,
  although the CDM makes the ETL responsible for populating those columns with
  standard concepts and every OHDSI analytical tool (Achilles, Atlas cohorts,
  HADES packages) relies on that; on one Achilles batch it produced 119
  warnings, all noise. Two concerns remain under the one `rule_id`:
  1. **Source concepts matched against the hierarchy** (every mode, WARNING).
     `concept_ancestor` holds only standard and classification concepts, so
     comparing a `*_source_concept_id` with `concept_ancestor` ids (a join, an
     IN-subquery or a CTE codeset) keeps only records whose source code
     happens to be standard and silently drops the rest. Filtering on source
     concepts by themselves (literal ids, codesets, ATLAS-style source-concept
     criteria) is not flagged, and an unrelated `concept_ancestor` or `'Maps
     to'` elsewhere in the query no longer suppresses the finding. A source id
     matched against the standard side of a `'Maps to'` link is reported by
     `joins.maps_to_direction`. This branch absorbs the reliable sub-case of
     the removed `concept_standardization.source_concept_id_warning`.
  2. **Standard concepts, strict mode only** (WARNING). Strict mode keeps a
     check for ETL-validation workflows: a `STANDARD_CONCEPT_FIELDS` reference
     is reported unless the concept row of that field is checked by
     `standard_concept = 'S'`, a `'Maps to'` mapping or a `concept_ancestor`
     expansion. A literal concept id does not count, since it can name a
     non-standard concept.

  Default mode no longer reports standard `<event>_concept_id` fields at all.
  An intermediate version flagged them whenever the query joined `concept`,
  `concept_ancestor` or `concept_relationship` without `standard_concept =
  'S'`; the Study 1 adjudication labelled all 6,949 such findings false
  observations (6,762 on LLM-written SQL, 187 on OHDSI packages), because
  joining a CDM-guaranteed standard column to `concept` reaches only that same
  concept, `concept_ancestor` descendants are standard by construction, and
  `'Maps to'` targets are standard by vocabulary definition.

  Schema exports: `fastssv.schemas` re-exports `SOURCE_CONCEPT_FIELDS` (the 22
  `*_source_concept_id` columns of CDM v5.4) and `VOCABULARY_TABLES` alongside
  `STANDARD_CONCEPT_FIELDS`. A pre-existing bug in `STANDARD_CONCEPT_FIELDS`
  listed four `payer_plan_period.*_source_concept_id` columns as standard;
  they now live in `SOURCE_CONCEPT_FIELDS`, and
  `tests/test_schema_consistency.py::test_standard_and_source_concept_sets_are_disjoint`
  guards against overlap. The message follows the context: a `NOT IN` against
  `concept_ancestor` keeps the non-standard-coded records, an outer join or
  CASE treats them as outside the hierarchy, and a filter drops them. The
  strict-mode branch skips deliberate unmapped / non-standard queries: a
  standard field compared with concept 0 (`= 0`, `IN (0)`, `<> 0`) or an
  explicit `standard_concept IS NULL` / `<> 'S'` filter, where requiring
  `standard_concept = 'S'` would empty the result (concept 0 has a NULL
  `standard_concept`).

  The suggested strict-mode filter keeps unmapped records (`standard_concept =
  'S' OR concept_id = 0`). In default mode the rule also reports a source
  concept id that reaches `concept_ancestor` through a joined concept row.

- **`joins.concept_relationship_requires_relationship_id` is a WARNING only.**
  A `concept_relationship` instance read without a `relationship_id`
  restriction returns every relationship type ('Is a', 'Subsumes', attribute
  links) at once, but whether that changes the result depends on the concept
  looked up (a non-standard source code often has one standard target; a
  standard concept also links to its parents, children and attributes), which
  the SQL does not show. An intermediate version reported an ERROR when the
  lookup reached a clinical `*_concept_id` predicate; that tier is gone, the
  message says the rows can include other concepts, and strict mode no longer
  escalates the rule. Any non-negated predicate on `relationship_id` counts as
  a restriction: `=`, `IN`, `LIKE` (except `LIKE '%'`), a regex,
  `LEFT(relationship_id, 7) = …`, `IS NOT DISTINCT FROM`, `LOWER` / `UPPER` /
  `TRIM` / `COALESCE` wrappers and `JOIN relationship USING
  (relationship_id)`; `<>`, `IS NULL` / `IS NOT NULL`, `NOT IN` and other
  negated forms do not. `joins.maps_to_direction` and
  `anti_patterns.concept_relationship_transitive_misuse` read the same forms.
  Projecting `relationship_id` exempts a lookup only when the column reaches
  the statement output (`SELECT *` / `cr.*` counts); a CTE that projects it
  and is never read downstream is reported. The rule does not fire on DDL or
  on scans that never constrain `concept_id_1` / `concept_id_2` (`WHERE
  FALSE`, counts, peeks); a `NOT EXISTS` over every relationship type (an
  orphan-concept check) is not flagged, and `EXISTS`, `IN` and join forms get
  the same verdict. Lookups are found through lineage, so an unrestricted
  instance whose `concept_id_1` is filtered downstream of a CTE is reported.
  The rule's `example_bad` uses a standard seed (201826).

- **`joins.maps_to_direction` flags the reversed lookup instead of the
  harmless self-map.** It now fires when a `*_source_concept_id` is matched
  against the standard side of a mapping (`concept_id_2` of 'Maps to',
  `concept_id_1` of 'Mapped from'), using column lineage and the relationship
  restriction of that very concept_relationship instance. Joining a standard
  `*_concept_id` to `concept_id_1` (a self-map, since standard concepts map to
  themselves) and CTE columns that merely share a source-field name are no
  longer flagged.

- **`anti_patterns.concept_relationship_transitive_misuse` requires a single
  hierarchical relationship on every hop.** Chains of two or more 'Is a' (or
  'Subsumes') lookups now fire, including chains across CTEs and
  IN-subqueries; chains with any non-hierarchical or mixed hop (siblings,
  'Maps to' → 'Is a') do not, and no hop inside a recursive CTE is counted
  (the anchor-to-recursive hop used to be reported as a chain). The message
  says that `concept_ancestor` covers standard and classification concepts
  only, so a recursive CTE is the way to walk non-standard hierarchies.

  `anti_patterns.concept_relationship_transitive_misuse` follows the hierarchy
  direction. A chain mixing 'Subsumes' and reversed 'Is a' rows that walks one
  way (both down), and a chain joined through a concept row, were missed; both
  are now reported. A chain that can be read both ways is reported once, in
  the direction anchored to an outside value.

- **`concept_standardization.standard_concept_value_validation` absorbs the
  empty-string check and judges every string value.**
  `concept.standard_concept` holds `'S'`, `'C'` or NULL. `standard_concept =
  ''` is an ERROR: the CDM stores NULL for non-standard concepts, so a bare
  empty-string filter matches nothing; `= '' OR standard_concept IS NULL` is
  accepted for loaders that write `''`. A list that also holds a valid value
  (`IN ('S', 'Y')`) still returns rows, so it is a WARNING. Negated invalid
  values (`<> 'N'`, `NOT IN ('N', '')`, `<> ''`) are WARNINGs explaining that
  they equal `IS NOT NULL`. A lowercase `'s'` / `'c'` is an ERROR on
  PostgreSQL and DuckDB and a WARNING on other case-sensitive engines. The
  auto-fix for `= 'N'` is `IS NULL`; an earlier patch rewrote it to `= 'S'`,
  inverting the intent. CTE renames, casts, `LIKE 'Y'`, HAVING and CASE labels
  are detected; template placeholders (`'{std}'`, `'@std'`, `'${std}'`) are
  skipped. A non-string literal (`standard_concept = 1`, `IN ('S', 1)`, `=
  TRUE`) is a type defect and is reported by
  `data_quality.column_type_validation` instead.

- **`concept_standardization.concept_domain_validation` reports only domain_id
  filters that contradict the column.** A missing `domain_id` filter on a
  `*_concept_id = concept.concept_id` join is no longer reported: the join is
  on the concept primary key and the column's domain is fixed by the CDM, so
  the filter changes nothing (this was 377 of 11,654 agent queries and every
  finding the rule produced). The rule now checks only columns whose CDM v5.4
  field-level specification names an `fkDomain`, adds every
  `*_type_concept_id` (expected `'Type Concept'`), follows column lineage
  through CTEs, matches filters per SELECT, and counts only predicates joined
  by AND. A contradicting filter is an ERROR; a filter that also lists other
  domains is a WARNING. It is no longer escalated by strict mode. Columns
  without an `fkDomain` (`observation.observation_concept_id`,
  `death.cause_concept_id`, `provider.specialty_concept_id`,
  `observation.qualifier_concept_id`, specimen and modifier fields) are no
  longer checked. `domain_id = 'Metadata'` is accepted on any column: concept
  0 has domain 'Metadata', so the filter counts unmapped rows.

  `concept_standardization.concept_domain_validation` takes its domain table
  from the CDM v5.4 field-level specification. admitted_from / discharged_to
  no longer accept 'Place of Service': the specification gives 'Visit', and
  every Place of Service concept is non-standard.
  drug_strength.drug_concept_id, location.country_concept_id and the Metadata
  fields are now checked. IN-subqueries, derived tables over concept,
  unqualified columns, `LIKE` without wildcards, `UPPER(...)` and a WHERE
  filter on an outer-joined concept are now detected.

- **`concept_standardization.domain_vocabulary_validation` no longer flags
  standard procedure vocabularies.** ICD10PCS, ICD9Proc and OPCS4 are standard
  in the Procedure domain (Book of OHDSI, Table 5.2), so filtering
  `procedure_concept_id` on them was a false ERROR; the CPT4-on-measurement
  check was removed with it. Negated filters (`<>`, `NOT IN`), filters inside
  an OR, IN lists that also admit a standard vocabulary, and filters in the ON
  clause of an outer join are no longer reported. The class severity is now
  ERROR, matching what the rule emits. Each concept join is paired only with
  its own SELECT (joins in nested CTEs and subqueries were matched against the
  outer `WHERE`, producing false ERRORs), and column renames through CTEs are
  followed, so a renamed `*_source_concept_id` is not treated as the standard
  column. The joined `condition_concept_id` / `drug_concept_id` must trace to
  a CDM table that stores the domain's standard concepts
  (`condition_occurrence`, `condition_era`, `drug_exposure`, `drug_era`,
  `dose_era`, `drug_strength`), so a scratch table with a column of that name
  no longer triggers it. The message keeps the literal's case (`'ICD9CM'`,
  previously `'icd9cm'`) and says standard concepts come "chiefly" from SNOMED
  and ICDO3 (or RxNorm, RxNorm Extension and CVX); Nebraska Lexicon and OMOP
  Extension also contribute a few standard Condition concepts.

  `concept_standardization.domain_vocabulary_validation` checks every column
  with an fkDomain against the vocabularies of the release. For each domain,
  the vocabularies that hold Standard concepts come from the Athena v5.0
  27-AUG-25 release, so vocabularies such as Read and every fkDomain column
  are covered (MedDRA is licence-restricted and absent from the release, so it
  is treated like a local vocabulary). Unknown vocabulary names are left
  alone, since they may be local. A query that selects or groups by
  vocabulary_id is no longer downgraded to a WARNING: it returns no rows all
  the same. Comma joins, concept in FROM, IN-subqueries, EXISTS, derived
  tables and LIKE patterns are now detected. Unit columns are left to
  unit_vocabulary_validation.

- **`concept_standardization.unit_vocabulary_validation` is classed as ERROR,
  accepts `vocabulary_id = 'None'` and ignores filters that cannot empty the
  join.** Negated filters, `IS NULL`, filters inside an OR, IN lists that also
  admit `'UCUM'`, filters in an outer join's ON clause and same-named aliases
  in other SELECTs no longer raise an ERROR. An IN list is reported once
  rather than once per value. Concept 0 ('No matching concept', vocabulary
  'None') is the CDM value for an unmapped unit, so restricting the unit join
  to it is legitimate; the previous patch rewrote 'None' to 'UCUM'. Every
  standard Unit-domain concept in the current vocabulary release is UCUM
  (1,039 concepts), so other vocabularies remain an ERROR.  The class severity
  is now ERROR, matching what the rule emits. It fires only when the joined
  column traces, through CTE and derived-table renames, to `measurement`,
  `observation`, `device_exposure` or `dose_era.unit_concept_id`, or a
  `drug_strength` amount / numerator / denominator unit column; previously any
  column named `unit_concept_id` qualified, so a CTE of source-side unit ids
  (SNOMED has 1,362 Unit-domain concepts) gave a false ERROR.
  `specimen.unit_concept_id` has no CDM v5.4 fkDomain and is no longer
  checked.

  `concept_standardization.unit_vocabulary_validation`: the vocabulary survey
  is an ERROR, and lowercase `'ucum'` is detected. The "valid for exploratory
  analysis" WARNING is gone. Lowercase `'ucum'` is an ERROR on PostgreSQL and
  DuckDB and a WARNING on other case-sensitive engines. Comma joins, concept
  in FROM, IN-subqueries, EXISTS, derived tables and LIKE patterns are now
  detected.

- **`concept_standardization.invalid_reason_enforcement` (strict mode) checks
  `drug_strength` and `source_to_concept_map` on their own validity columns.**
  Both tables carry `valid_start_date`, `valid_end_date` and `invalid_reason`
  (CDM v5.4); previously they were treated as derived tables and told to join
  `concept`. A read of either table now needs a validity predicate on that
  table's own rows; a `standard_concept = 'S'` filter on `concept` no longer
  excuses it. Schema probes (`WHERE FALSE`, `WHERE 0 = 1`, bare `LIMIT` peeks)
  are exempt. Default-mode behaviour is unchanged (silent). A `concept` alias
  joined to a clinical `*_concept_id` and filtered on `concept_name` /
  `domain_id` is no longer asked for `invalid_reason IS NULL`: that would drop
  events recorded with since-deprecated concepts, and clinical data is out of
  the rule's scope. A validity filter is no longer asked for after a
  `concept_ancestor` expansion: the CDM v5.4 conventions state that only valid
  and Standard Concepts participate in CONCEPT_ANCESTOR, so the strict-mode
  WARNING for `concept_ancestor` in the primary FROM, joined to a clinical
  table, chained through `concept`, or filtered on a literal
  `ancestor_concept_id` / `descendant_concept_id` is gone. The
  `concept_synonym` variant stays, because CONCEPT_SYNONYM stores synonyms of
  deprecated and upgraded concepts too (352,521 of 4,564,919 rows in Athena
  v5.0 27-AUG-25).

  `concept_standardization.invalid_reason_enforcement` (strict mode) no longer
  asks for filters that change nothing or lose data. concept_relationship is
  not checked, because Athena ships only valid rows. A concept row that feeds
  the source side of a 'Maps to' lookup is exempt: retired source codes keep
  their mappings, and the suggested filter dropped half of the NDC mappings. A
  concept decoded in an outer join is exempt. `BETWEEN valid_start_date AND
  valid_end_date` counts as a validity filter. Checks are made per table
  instance. Only a constant-false WHERE counts as a probe; a `LIMIT 1` peek is
  now reported. That exemption now covers concept_synonym too.

- **`anti_patterns.concept_name_lookup` catches case-folded, trimmed,
  unqualified and synonym name lookups.** Before, the rule only matched a
  bare, table-qualified `concept.concept_name` column, so it missed
  `LOWER(concept_name) LIKE '%…%'`, `UPPER(c.concept_name) = '…'`, the TRIM
  family, `SELECT … FROM concept WHERE concept_name = '…'` (unqualified), and
  names renamed through a CTE. Now the rule strips case-folding, trimming and
  cast wrappers and resolves the column through lineage. It also covers
  `concept_synonym.concept_synonym_name`, `<>`, `NOT IN`, `SIMILAR TO` and
  regex matches. Only literal names and patterns count; a name pattern built
  from another column is not flagged. A `LIKE` or `ILIKE` with no wildcard is
  reported as a plain name comparison. Exact, non-case-folded comparisons note
  that `=` is case-sensitive in PostgreSQL (gender concept 8507 is `MALE`). On
  the Study 1 agent corpus the rule now fires on 1,598 queries instead of 617,
  and on 17 expert queries instead of 8. Blank literals (`concept_name <> ''`,
  `= ''`, `LIKE '%'`) do not count as identifying concepts by name.

  `anti_patterns.concept_name_lookup` recognises function-form name matches.
  `POSITION(...)`, `STRPOS`, `CHARINDEX`, `STARTS_WITH`, `CONTAINS`, `LIKE ANY
  (ARRAY[...])` and patterns built by concatenating literals over
  `concept_name` / `concept_synonym_name` are now reported like `LIKE`.

- **`anti_patterns.concept_code_requires_vocabulary_id` now covers code joins
  and unqualified columns.** It replaces the WARNING path of
  `joins.concept_join_validation`. The rule now also flags
  `concept.concept_code` joined column-to-column (for example to
  `condition_source_value`, or to a seed CTE) without a `vocabulary_id`
  constraint on that alias. It recognises unqualified `concept_code` on a
  schema-qualified `concept`, and accepts `vocabulary_id LIKE …`, `IN
  (subquery)`, or a column-to-column `vocabulary_id` match in either order as
  the constraint. Code filters on a CTE are judged where `concept` is actually
  read. The old join warning in `joins.concept_join_validation` fired when
  `vocabulary_id` was matched in the other column order, or constrained by a
  literal in `ON`/`WHERE`. Agent corpus: 23 queries instead of 3. Expert:
  unchanged at 6. The suggested vocabulary is described in the entry on
  vocabulary suggestions below. The `ADD` fix qualifies `vocabulary_id` with
  the concept alias in scope (`x.vocabulary_id`, or `concept.vocabulary_id`
  when the table has no alias) instead of a hard-coded `c.`.

- **`data_quality.source_value_field_usage` exempts mapping audits.** A `GROUP
  BY` on a `*_source_value` column is no longer flagged when the same SELECT
  also groups by the paired `*_concept_id` or `*_source_concept_id`. Nor is it
  flagged when a SELECT reading that table pins the paired concept id to
  unmapped (`= 0`, `IN (0)`, `IS NULL`). These are the Achilles, CdmOnboarding
  and MethodEvaluation source-code reports. Columns with no standardized
  counterpart (`person_source_value`, `dose_unit_source_value`, …) and columns
  of non-CDM tables are skipped. `value_source_value` is paired with
  `value_as_concept_id`. Expert corpus: 1 query instead of 21. Agent: 1
  instead of 3.

  `data_quality.source_value_field_usage` recognises every way of grouping and
  every mapping review. GROUP BY ordinals and output aliases, SELECT DISTINCT,
  window PARTITION BY, and source values wrapped in CAST / UPPER / COALESCE
  are now checked. A review restricted to the study's concepts (`WHERE
  condition_concept_id = 201826`) is no longer reported, and an unmapped-rows
  filter exempts only the table instance it applies to, not every read of the
  table in the statement.

- **`data_quality.concept_id_string_comparison` skips unrendered SqlRender
  parameters and states accurate engine behaviour.**
  `'@gender_concept_ids'`-style placeholders are skipped. The description no
  longer claims PostgreSQL bypasses the index: PostgreSQL resolves the untyped
  literal to integer. The rule's case is that BigQuery rejects `INT64 =
  STRING` and that non-numeric strings fail everywhere. The column is judged
  by its lineage: a `*_concept_id` recast to text in a CTE or derived table,
  or one from a non-CDM table, is skipped.

  `data_quality.concept_id_string_comparison` blocks literals that are not
  integers. `gender_concept_id = 'FEMALE'` fails on PostgreSQL and DuckDB and
  is now an ERROR on those dialects (it was a WARNING, and no other rule
  reported it); quoted integers remain a portability WARNING. Columns are
  recognised by lineage to the CDM columns that hold concept ids (generated
  from the field-level CSV), so `concept_id_1` and `domain_concept_id_1` are
  covered, and BETWEEN is checked.

- **`joins.concept_synonym_join_validation` reports text matches as a
  WARNING.** An equality between `concept_synonym_name` and a concept text
  column (`concept_name`, `concept_code`) was an ERROR; it is now a WARNING
  whether bare or wrapped in `LOWER` / `UPPER` / `TRIM` (the wrapped form was
  previously not reported at all). The WARNING says the equality is a lexical
  lookup, not the `concept_synonym.concept_id = concept.concept_id` key,
  because a deliberate text lookup (e.g. finding concepts whose synonym equals
  another concept's name) cannot be told apart from a mistaken key. Key-column
  mismatches such as `cs.language_concept_id = c.vocabulary_id` or
  `cs.concept_id = c.concept_code` remain ERRORs, and a text predicate on a
  pair already joined on `concept_id` is still not reported. No findings on
  either Study 1 corpus.

  `joins.concept_synonym_join_validation` reads `IN` subqueries.
  `cs.concept_id IN (SELECT concept_code …)` is reported like the join form.

- **`anti_patterns.ambiguous_column_reference` is now scope-correct and an
  ERROR.** It previously counted every table anywhere under a SELECT,
  including tables inside its CTE bodies, derived tables and IN-subqueries. It
  also covered only five hard-coded column names and ignored `JOIN ... USING`.
  So it flagged unqualified `person_id` in single-source scopes (180 agent /
  1,372 expert queries, all false) while missing real ambiguities over CTE
  self-joins. It now resolves each unqualified name against the FROM/JOIN
  sources of its own SELECT: the CDM v5.4 catalogue for base tables,
  projection lists for CTEs and derived tables. `USING`/`NATURAL` merges,
  ON-clause visibility, comma-join precedence and ORDER BY / DISTINCT ON
  output names are honoured. Sources whose columns are unknown are never
  counted. Every finding is now a query PostgreSQL rejects with `column
  reference "x" is ambiguous` (SQLSTATE 42702), so the severity is ERROR (2
  agent / 0 expert queries). A bare `ORDER BY` / `DISTINCT ON` name also
  matches output columns produced by `*` / `t.*` (`SELECT co.*,
  p.gender_concept_id … ORDER BY person_id` runs in PostgreSQL and DuckDB).
  Sources built with `* EXCLUDE / EXCEPT / REPLACE / RENAME` or `COLUMNS(…)`
  are treated as unknown (the shared `column_origins` helper returns unknown
  for them too). For non-PostgreSQL dialects, references that may name a
  select-list alias (GROUP BY / HAVING / WHERE / later projections) are not
  flagged, and the message no longer claims a PostgreSQL error for other
  dialects. For non-PostgreSQL dialects, a name merged by `JOIN … USING (col)`
  / `NATURAL JOIN` is not flagged even when a later source also exposes it,
  since DuckDB and similar engines bind it to the merged column; PostgreSQL
  rejects that query, so it is still flagged there.

  `anti_patterns.ambiguous_column_reference` sees LATERAL subqueries,
  parenthesised joins and correlated references. An unqualified column exposed
  both by a table and by a `LATERAL (...)` subquery, one inside a
  parenthesised join tree (`JOIN (a JOIN b ON ...) ON ...`), and an
  unqualified column of a subquery that resolves to an ambiguous name of the
  enclosing SELECT were missed; PostgreSQL and DuckDB reject all three.

- **`anti_patterns.join_key_validation` no longer emits name-suffix WARNINGs
  and traces lineage.** The "Suspicious JOIN condition: columns share '_id'
  suffix" warning fired on role-named keys such as `cohort.subject_id =
  person.person_id` and `cohort_definition_id = target_id` (~290 expert
  findings, all false). Every genuine `*_id` mismatch it caught is already an
  ERROR from the table-aware `joins.*_join_validation` rules. The ERROR path
  (entity ID such as person_id, visit_occurrence_id, provider_id, care_site_id
  or location_id = concept_id) now follows both sides through CTEs, derived
  tables and renaming aliases, so `WITH x AS (SELECT person_id AS pid FROM
  person) ... ON x.pid = c.concept_id` is caught.

- **`anti_patterns.no_distinct_on_primary_key_column` only flags single-table
  SELECTs.** The "DISTINCT used on primary key column ... in a query with
  joins" variant is gone: after a 1:N join (e.g. `person JOIN
  condition_occurrence`) the key repeats, and DISTINCT is what keeps the
  result per person (28 agent / 4 expert findings were false). The rule now
  fires only when the SELECT reads one base OMOP table with no joins and
  projects its primary key. Joins inside EXISTS subqueries no longer count as
  joins, and CTEs named like OMOP tables are ignored. The non-CDM keys
  `episode_event_id` and `death_id` were dropped from the key list. When the
  SQL text contains more than one `DISTINCT` (for example `COUNT(DISTINCT …)`
  beside the flagged subquery), the fix is FREEFORM instead of a REMOVE of the
  first match.

- **`data_quality.union_vs_union_all_clinical_events` skips UNIONs whose
  de-duplication cannot change the result.**
  - No longer flagged:
    - arms tagged with distinct literal labels whose event-level arms are
      already unique (e.g. Achilles `'drug_exposure' cdm_table ... GROUP BY`);
    - UNIONs that only feed `COUNT(DISTINCT ...)`, MIN/MAX, `SELECT DISTINCT`,
      `IN` or `EXISTS`;
    - UNIONs where every arm is `SELECT DISTINCT`.
  - Unlabelled single-row aggregate arms (`SELECT COUNT(...) ... UNION SELECT
    COUNT(...)`) are still flagged, with their own message: equal values
    collapse into one row.
  - A chain `A UNION B UNION C` is reported once.
  - Agent 2 → 1, expert 11 → 3. UNIONs of vocabulary rows (concept rows
    filtered by a clinical-table subquery) are not clinical event rows and are
    not flagged. The unlabelled-arm message reads "no label column that
    distinguishes them", which also covers arms sharing an identical label.

  `data_quality.union_vs_union_all_clinical_events` decides by lineage. A
  UNION whose arms project only CDM foreign-key columns (`person_id`,
  `visit_occurrence_id`, `*_concept_id`) is a set of entities and is not
  reported, whatever the output columns are called (`person_id AS subject_id`
  used to be reported). Arms that read event tables through CTEs are now
  checked, and every CDM table carrying `person_id` counts as event data
  (adding the era tables, `death`, `observation_period`, `payer_plan_period`).

- **`joins.care_site_join_validation` is now an ERROR and reports every direct
  `care_site_id = location.location_id` join.** The two columns come from
  unrelated id sequences, so the join is always wrong. A correct `… →
  care_site → location` chain elsewhere in the statement no longer hides a bad
  join, and the check covers the tables that actually carry `care_site_id` in
  CDM v5.4 (person, provider, visit_occurrence, visit_detail). No corpus
  findings before or after.

  `joins.care_site_join_validation` resolves operands by lineage.
  `<table>.care_site_id = location.location_id` is found behind CTEs, derived
  tables, casts, unqualified columns and `IN (SELECT location_id …)`.

- **`domain_specific.cost_event_id_polymorphic_resolution` reports a wrong
  `cost_domain_id` (ERROR) and a missing one (WARNING), judging each cost read
  on its own.** CDM v5.4 says the table `cost_event_id` points into is
  inferred from `cost_domain_id`. Matching the key with an event table while
  the restriction rules that table's domain out (`'Procedure'` with
  `drug_exposure`, `NOT (cost_domain_id = 'Drug')`, `LOWER(cost_domain_id) =
  'Drug'`) is an ERROR: every row it returns pairs a cost with an unrelated
  event. A join with no restriction was an ERROR; it is now a WARNING, because
  it is wrong only where the cost table holds costs of several domains. An
  `IN` list that also admits other domains is a WARNING. Restrictions in
  either operand order, through `LOWER` / `UPPER` / `CAST`, with `LIKE` /
  `ILIKE`, or as `IN (SELECT …)` (never a mismatch) count; earlier `'Drug' =
  c.cost_domain_id`, `LOWER(c.cost_domain_id) = 'drug'`, `ILIKE` and `IN
  (SELECT …)` raised a false "no cost_domain_id filter" ERROR. Restrictions
  are tied to the cost read they constrain, so a filter in one UNION branch,
  on another alias or under an OR no longer covers an unrestricted join
  elsewhere, and cost read through a CTE or derived table is followed.
  `cost_event_id IS [NOT] NULL` is not reported. `visit_detail` (named in the
  COST table description) is recognised, and the table-to-domain map is
  generated from the CDM v5.4 field-level specification. The restriction
  parser is shared with `domain_specific.event_field_polymorphic_resolution`.
  Absorbs `joins.cost_table_domain_validation`. A domain id that differs from
  the right one only in case (`'drug'`) is reported by
  `data_quality.canonical_string_value_validation`, and a number compared with
  `cost_domain_id` by `data_quality.column_type_validation`; earlier versions
  of this rule reported the wrong case themselves, exempting the `tsql` and
  `mysql` dialects.

- **`domain_specific.visit_detail_visit_occurrence_reference` is a WARNING and
  judges how visit_detail and visit_occurrence are tied within each SELECT.**
  A `person_id` join between visit_detail and visit_occurrence pairs each
  detail with every visit of the person, which is a mistake unless the query
  asks about the person's other visits, so the finding is a WARNING (it was an
  ERROR). It reports a pair whose `person_id` values are equated (directly,
  through another table such as `cohort.subject_id`, or through CTE and
  derived-table wrappers) when their `visit_occurrence_id` values are never
  equated (directly, through `CAST`, through `USING (visit_occurrence_id)` or
  through another table); a correct link elsewhere in the statement no longer
  hides an unlinked pair. Joins on non-person keys (dates, `care_site_id`,
  `visit_detail_id = visit_occurrence_id`) are left to the person-linkage and
  primary-key join rules. Not reported: pairs linked only through an event
  table (`co.visit_occurrence_id` / `co.visit_detail_id`); an equality between
  visit ids (`nxt.preceding_visit_occurrence_id = vd.visit_occurrence_id`) or
  an inequality on them (`vd.visit_occurrence_id <> vo.visit_occurrence_id`),
  which mark the person's other visits as intended; a date comparison that
  places the visit after the detail ends or before it starts
  (`vo.visit_start_date > vd.visit_detail_end_date`); two independent
  outer-joined look-ups cut by `ROW_NUMBER() … = 1`, or a direct pairing whose
  output reads only one of the two tables; and `DISTINCT`, `COUNT(DISTINCT)`
  or `IN` / `EXISTS` queries in which one of the two tables is read for
  nothing but `person_id`. A person-plus-date-containment or same-start-date
  join is reported. Absorbs
  `joins.visit_detail_join_validation`.

- **`joins.visit_occurrence_inner_join_validation` works per SELECT and only
  on real links.** It fires when an event table is linked to visit_occurrence
  on `visit_occurrence_id` by an inner join, including a comma join linked in
  WHERE, and stays silent when the SELECT also filters a visit attribute
  (`visit_concept_id`, visit dates, `visit_occurrence_id IS NOT NULL`), which
  marks the restriction to visit-linked events as intended. The "Implicit
  INNER JOIN" message is gone: it fired on any derived table with more than
  one table in it (109 expert findings, all false) and never on an actual
  comma join. Agent 1 → 0, expert 112 → 1. visit_detail is not covered (its
  `visit_occurrence_id` is required in CDM v5.4, so an inner join drops
  nothing). The LEFT JOIN patch rewrites the keyword of the flagged join
  itself; when the event table is the joined side, or the keyword cannot be
  located unambiguously, a FREEFORM instruction is returned. Previously the
  patch could target another join and produce `LEFT LEFT JOIN`.

  `joins.visit_occurrence_inner_join_validation` recognises stated intent and
  more join forms. It no longer warns when the query removes visit-less events
  explicitly (`<event>.visit_occurrence_id IS NOT NULL`), groups by a visit
  column, or filters a visit attribute in HAVING. A `vo.<col> IS NULL`
  predicate no longer counts as such a filter, since it is true for the rows a
  LEFT JOIN would add. Inner joins written with `USING (visit_occurrence_id)`,
  casts, or tables wrapped in CTEs or derived tables are now reported. The
  table list is generated from the CDM v5.4 field-level specification (tables
  whose `visit_occurrence_id` is optional).

- **`domain_specific.cost_currency_concept_id` covers all CDM v5.4 cost
  amounts and follows lineage.** The amount set adds `paid_by_primary`,
  `paid_ingredient_cost`, `paid_dispensing_fee` and `amount_allowed`. Amounts
  reached through CTEs (Achilles cost analyses) are detected, and a currency
  filter or grouping inside the derived table or CTE being aggregated now
  counts. Previously `SELECT SUM(x) FROM (SELECT total_paid AS x FROM cost
  WHERE currency_concept_id = …) t` fired, while Achilles' renamed-CTE cost
  distributions were missed. A multi-value `currency_concept_id IN (…)` still
  fires, and `suggested_fix` no longer implies otherwise. A currency
  constraint must be a single literal or parameter (`= 44818668`, `IN
  (44818668)`, `= $1`) or a GROUP BY / PARTITION BY, applied to the same cost
  row that feeds the aggregate (in the aggregating SELECT or a CTE / derived
  table it reads). A join of `currency_concept_id` to `concept`, an `IN
  (subquery)`, or a filter on a different `cost` row inside a nested subquery
  no longer silences the rule, since none restricts the currency of the summed
  rows. Aggregates fed by the same `cost` read, such as the Achilles mean and
  percentile SELECTs over one CTE, produce a single finding naming the
  innermost aggregate. Expert corpus: 21 findings.

  `domain_specific.cost_currency_concept_id` covers every aggregate that
  combines amounts and recognises more ways of fixing the currency. STDDEV,
  VARIANCE, MEDIAN and PERCENTILE_CONT over cost amounts were not reported;
  any aggregate except COUNT, ARRAY_AGG and STRING_AGG now is, including in
  HAVING and ORDER BY. `GROUP BY 1` on the currency column, grouping on or
  filtering the concept row joined to `currency_concept_id` (`cur.concept_code
  = 'USD'`), and a scalar subquery for the currency no longer fire. Two
  currencies joined by OR no longer count as one fixed currency. The amount
  columns are generated from the CDM v5.4 field-level specification. The
  suggested fix now leads with GROUP BY, because `currency_concept_id` is
  optional and a filter drops rows that leave it empty.

- **`domain_specific.drug_strength_numerator_denominator_for_concentration` no
  longer fires on `amount_value IS NULL`.** That predicate selects the
  concentration formulations instead of excluding them. `IS NOT NULL` and `NOT
  (amount_value IS NULL)` still fire. Projecting only `amount_value` is
  reported as returning NULL for concentration-based drugs rather than
  "excluding" them. The suggested fix no longer divides `numerator_value` by
  `denominator_value`, which mixed an amount with a ratio; it uses
  `COALESCE(amount_value, numerator_value)`, carrying the matching unit and
  the denominator columns, as on the CDM drug-dose page.

  `domain_specific.drug_strength_numerator_denominator_for_concentration`
  judges each read of drug_strength and covers aggregates. `SUM(quantity *
  amount_value)`, `AVG(amount_value)`, a filter in JOIN ON and a HAVING
  condition were silent although each skips every concentration row; they are
  now reported. A `numerator_value` anywhere in the statement used to silence
  the rule, so a UNION branch on another read hid the finding; now only the
  same read counts, and an amount filter is excused only by a condition on
  `numerator_value`. A read restricted to the amount model (`numerator_value
  IS NULL`, `denominator_unit_concept_id IS NULL`) is no longer reported. The
  messages say "concentration or rate" (inhalers, transdermal systems and some
  extended-release tablets are stored in the numerator too) instead of
  "liquids/injectables".

- **`temporal.observation_period_anchoring` reports what the CDM passage
  names: absence tests and rates; the unlinked case is now
  `joins.observation_period_unlinked`.** The CDM v5.4 OBSERVATION_PERIOD user
  guide says that absence of an event outside the observation periods "cannot
  be construed as evidence of absence", that incidence or prevalence rates
  "should only be calculated for the time of active OBSERVATION_PERIOD
  records", and that outside events "can be used for inclusion criteria". The
  rule now warns on a per-person absence test over a clinical event table
  (`NOT EXISTS`, `NOT IN`, `LEFT JOIN ... IS NULL`, `EXCEPT`) and on a count
  of events divided by a count of persons, when observation_period is not read
  by that query, an enclosing query, or a CTE they use. Previously it warned
  on any calendar-window filter on recorded events (`EXTRACT(YEAR FROM
  condition_start_date) = 2020`), exempted every query that touched a table
  whose name ends in `cohort` (including CTEs such as `dx_cohort`), and
  treated a `>= '1900-01-01'` lower bound as hiding the upper bound paired
  with it; all three are gone, and relative-window absence tests (washout) are
  now reported. Agent-corpus findings fall from 551 to 2. The
  person_id-linkage check that the rule used to carry is
  `joins.observation_period_unlinked` (see Added).

- **`domain_specific.person_birth_field_validation` is operator-aware and
  takes its bounds from the DQD thresholds.** It judges only `=`, `IN` and
  `BETWEEN` (`BETWEEN SYMMETRIC` is never an inverted range), and fires only
  when no admitted value is plausible; one-sided guards such as `year_of_birth
  > 1899` are not reported. A predicate is judged only when it is reached from
  WHERE, HAVING or an inner JOIN ON through AND and parentheses, so conditions
  under `OR`, in an outer join's ON, inside NOT EXISTS, or inside CASE / IF /
  a function (`WHERE CASE WHEN month_of_birth = 99 THEN NULL ELSE
  month_of_birth END = 6`) are not judged, and columns are followed through
  CTEs, so a computed column that reuses a birth-field name (`EXTRACT(DOY FROM
  birth_datetime) AS day_of_birth`) is ignored. The rule warned on every
  `year_of_birth` (or `EXTRACT(YEAR FROM birth_datetime)`) after the current
  year, a threshold that changed with the date the validator ran and is wrong
  on date-shifted CDMs, where such years are real. That check is gone. The
  lower bound is now DQD's 1850 instead of 1900, still a WARNING.
  `month_of_birth` outside 1-12 and `day_of_birth` outside 1-31 are one
  finding whatever the literal: `= 0` and `> 12` / `>= 13` are reported like
  `= 13` (a WARNING; see the ERROR-tier corrections entry). The rule also reads quoted and cast literals,
  `EXTRACT(MONTH | DAY FROM birth_datetime)` and `MONTH()` / `DAY()`, and
  reports a month/day pair that never occurs (`month_of_birth = 2 AND
  day_of_birth = 30`). Predicates inside NOT EXISTS are no longer judged. The
  bounds are generated from the DQD field-level CSV. `EXTRACT(YEAR FROM
  birth_datetime)`, `YEAR(birth_datetime)` and `DATE_PART('year',
  birth_datetime)` are judged like `year_of_birth`; an inverted `BETWEEN` (low
  > high) is an ERROR on any birth field.

- **`domain_specific.visit_length_of_stay_arithmetic` absorbs
  `domain_specific.visit_outpatient_same_day_validation`.** LOS is flagged
  when it is aggregated (AVG, MEDIAN, percentiles, …) over visit types that
  mix multi-day and same-day visits; `SUM` is not, because zeros do not change
  a total. It is not flagged when the same `visit_occurrence` alias is
  restricted to the inpatient hierarchy (9201, 262, 42898160 and descendants),
  restricted through a subquery, join or `<>`, or grouped / partitioned by,
  nor when the SELECT keeps only visits that end after they start. Visit-type
  restrictions are read per SELECT and alias, so an ER filter on a different
  alias no longer triggers the same-day check. A per-row LOS projection is no
  longer flagged. The 9203 (Emergency Room Visit) concept was wrongly listed
  as inpatient-like and is now in the same-day set. The rule also flags a
  filter that requires two or more days of stay on outpatient / ER visits.
  This check is direction-aware: `DATEDIFF(...) < 30` was flagged before and
  is not now. It also handles Postgres `visit_end_date - visit_start_date`,
  and messages quote the original T-SQL `DATEDIFF` text. The
  `COALESCE(visit_end_date, …)` advice is gone because `visit_end_date` is
  required in v5.4.

  `domain_specific.visit_length_of_stay_arithmetic` follows the visit row.
  Length of stay computed per row in a CTE or derived table and averaged
  outside is now checked like LOS written inside the aggregate. A
  visit_concept_id filter on a different row (a subquery, another alias) no
  longer switches the check off, and equating two rows' visit types is not
  read as a restriction. A care_site `place_of_service_concept_id` filter
  joined to the visit's care site is recognised as a visit-type restriction.
  `visit_end_date > visit_start_date + 2` is read as a length-of-stay
  threshold, and a threshold under OR is no longer treated as a row filter.
  The visit type concept sets are now generated from the vocabulary (unchanged
  content).

  `domain_specific.visit_length_of_stay_arithmetic` follows visit-type
  restrictions through the primary key. A restriction on another
  visit_occurrence row that a row filter equates with the aggregated visit on
  `visit_occurrence_id` (`vo.visit_occurrence_id IN (SELECT
  visit_occurrence_id FROM visit_occurrence WHERE visit_concept_id = 9201)`, a
  correlated `EXISTS`, or a join on the key) now counts as a restriction on
  that visit, so such queries no longer get a diluted-average warning. `NOT
  IN` / `NOT EXISTS` do not count.

- **`domain_specific.visit_detail_dates_within_parent_visit` only flags
  filters that can hold solely outside the parent visit.** CDM v5.4 nests a
  visit_detail inside the visit_occurrence that subsumes it, and allows it to
  share that visit's start or end date. The rule used to fire on `<=` / `>=`
  (catching same-day ICU admissions) and on any comparison with any
  visit_occurrence alias once some visit_detail–visit_occurrence join existed
  (catching lookbacks to prior visits). It now fires only on strict `<` / `>`
  between `*_date` columns of the visit_detail and the visit_occurrence it is
  joined to on `visit_occurrence_id`, when the comparison restricts rows (a
  conjunct of WHERE or an INNER JOIN ON, outside NOT / NOT EXISTS / EXCEPT /
  OR / CASE / outer-join ON). It also covers `visit_detail_start_date >
  visit_end_date` and `visit_detail_end_date < visit_start_date`.  `JOIN
  visit_occurrence USING (visit_occurrence_id)` and NATURAL joins are
  recognised as the parent-visit link.

  `domain_specific.visit_detail_dates_within_parent_visit` covers datetimes,
  offsets and wrapped tables. The rule skipped `*_datetime` columns on the
  grounds that they default to midnight; CDM v5.4 sets an unknown time to
  NULL, so datetime pairs are now judged like date pairs (a date is not
  compared with a datetime). Differences (`vd.visit_detail_start_date -
  vo.visit_start_date < 0`), widening offsets (`< vo.visit_start_date -
  INTERVAL '1 day'`), casts, and CTE or derived-table wrappers of either table
  are now recognised. The message no longer claims the query returns no rows
  on conformant data; it says the filter selects misnested records.

- **`data_quality.note_nlp_offset_is_character_position` targets the uses that
  silently mis-order a varchar.** `note_nlp.offset` is `varchar(50)` in CDM
  v5.4. Previously the rule flagged JOINs, numeric-literal comparisons and
  arithmetic, which are either exact (string equality) or rejected by
  PostgreSQL (no `varchar > integer` operator; casts from string types are
  explicit-only), and allowed `ORDER BY offset`. It now flags
  `<`/`<=`/`>`/`>=` against a string literal or another uncast offset,
  `BETWEEN` with string bounds, `ORDER BY offset` (including window ordering)
  and `MIN`/`MAX(offset)`, all of which run and return lexicographic results.
  Its `example_bad`/`example_good` changed accordingly. A bare query-level
  `ORDER BY` name resolves to the output alias first (PostgreSQL rule), so
  `CAST(offset AS INTEGER) AS "offset" … ORDER BY "offset"` is silent and
  `nn."offset" AS pos … ORDER BY pos` is flagged.

  `data_quality.note_nlp_offset_is_character_position` follows the column's
  lineage. `note_nlp.offset` is now recognised unqualified next to other
  tables, through CTEs, derived tables and renames, and in `GREATEST` /
  `LEAST`; a CTE that casts it to an integer is not flagged.

- **`data_quality.note_nlp_nlp_date_for_temporal_filtering` covers
  `nlp_datetime` and scopes its exemptions per SELECT.** A `note.note_date`
  filter anywhere in the statement used to silence the rule everywhere; it now
  only exempts the SELECT that applies it. Comparisons whose other side itself
  reads `nlp_date`/`nlp_datetime`/`note_date`/`note_datetime` (latest-NLP-run
  selection, processing-lag audits) are no longer flagged.

  `data_quality.note_nlp_nlp_date_for_temporal_filtering` finds time windows
  written any way. It now reads JOIN ... ON and HAVING as well as WHERE,
  follows nlp_date through casts, arithmetic, `EXTRACT(YEAR ...)` / `YEAR()`,
  CTEs and derived tables. Equality on the date in a SELECT that also
  restricts nlp_system (choosing one processing run) and comparisons with a
  subquery (an incremental-load watermark) are no longer reported.

- **`domain_specific.note_nlp_snippet_misuse` only flags selecting rows by
  matching the text.** It used to fire on any comparison mentioning `snippet`
  or `lexical_variant`, including `LENGTH(snippet) > 50`, `snippet <> ''` and
  CASE expressions in SELECT. It now fires, in WHERE / JOIN ON, on `=` / `IN`
  against non-empty string literals, LIKE / ILIKE / regex matches with a real
  pattern, and equality to `concept.concept_name` /
  `concept_synonym.concept_synonym_name`. Joins to arbitrary text columns of
  other tables are no longer flagged. `LOWER(nn.lexical_variant) =
  LOWER(c.concept_name)` is also caught.

  `domain_specific.note_nlp_snippet_misuse` accepts text filters on rows
  already selected by concept. Matching `snippet` / `lexical_variant` on rows
  that the query also selects by `note_nlp_concept_id` (a negation or context
  cue in the window, or unmapped rows with concept 0) is no longer reported;
  the suggested fix was already in such queries. The rule now follows CTEs,
  derived tables and casts, and recognises POSITION / STRPOS / CHARINDEX,
  `REGEXP_CONTAINS` and `LIKE ANY`.

- **`anti_patterns.no_string_identification` is a WARNING and judges only
  columns with a concept counterpart.** The CDM conventions discourage
  `*_source_value` columns for standard analytics and allow them for quality
  assurance; they do not make a string filter wrong, so the rule no longer
  blocks queries. It stays silent on identifiers that have no concept column
  (`person_source_value`, `provider_source_value`, `care_site_source_value`,
  `location_source_value`, `family_source_value`, `dose_unit_source_value`),
  on blank literals, and on SELECTs that filter the paired concept column to 0
  (QA of unmapped records). It now sees through `LOWER` / `UPPER` / `TRIM` /
  `LEFT` / `SUBSTRING` / casts and CTE renames, and reports `<>`, `NOT IN`,
  `BETWEEN`, `SIMILAR TO` and regex matches. The REPLACE patch used to invent
  a column by renaming the suffix (`value_concept_id`, `person_concept_id`,
  `note_concept_id`, which do not exist); it now names the counterpart given
  by the CDM v5.4 specification (`value_as_concept_id`,
  `note_class_concept_id`, ...), and is omitted when the column is read
  through a CTE or derived table.

- **`anti_patterns.singleton_metadata_clinical_join` is a WARNING, renamed
  "Instance Metadata Paired With Clinical Rows".** Pairing CDM_SOURCE or
  METADATA with a clinical table repeats each clinical row once per metadata
  row. That is harmless with one row, and the specification does not limit the
  row count, so the finding is a WARNING and says so.
  - **No longer reported:** predicates that compare other values, such as
    `co.condition_start_date > cs.cdm_release_date`, a legitimate data-quality
    check that used to be an ERROR saying "no join condition".
  - **Still reported:** an equality between identifier columns of the two
    tables (`m.metadata_id = p.person_id`), since no key relates them.
  - **Newly reported:** the same pairing through a CTE or derived table. A
    one-row derived table is not reported.
  - **Suggested fix:** the scalar subquery now carries its caveat: it raises
    an error instead of repeating rows if the table holds more than one row.

- **`anti_patterns.type_concept_id_misuse` names its vocabulary release, and
  its ERROR is certain.**
  - **Severity.** An ERROR now requires every compared id to be a concept of
    the embedded release (Athena v5.0 27-AUG-25) outside the 'Type Concept'
    domain. An id the release does not contain is a WARNING: a Type Concept
    added later (such as 723166 'Obituary', release v20260227) or a local
    concept. These were ERRORs saying "matches no rows".
  - **Shapes now caught.** Swapped operands, a cast or parentheses on the
    column, a CTE rename, `= ANY(ARRAY[...])`, a numeric string literal,
    `BETWEEN`, and `period_type_concept_id` (from the CDM v5.4 specification);
    `cost_type_concept_id` is reported as a WARNING.
  - **Exemptions removed.** Outer-join ON, CASE and FILTER positions are no
    longer exempt: a `LEFT JOIN ... AND condition_type_concept_id = 201826`
    silently empties the joined side.
  - **Wording.** A dead branch of an OR is a WARNING. The foreign-family
    WARNING cites the convention and no longer quotes an incomplete id range.

- **`anti_patterns.destructive_operations_on_clinical_tables` protects every
  CDM table the Book of OHDSI makes read-only.**
  - **Coverage.** The hand-picked list of 9 tables is replaced by every CDM
    v5.4 table except the vocabulary tables (reported by
    `data_quality.vocabulary_table_protection`) and the Results tables COHORT
    and COHORT_DEFINITION. This adds observation_period, device_exposure,
    specimen, note, the era tables, cost, payer_plan_period, location,
    care_site, provider, cdm_source and metadata. MERGE, CREATE OR REPLACE
    TABLE, SELECT ... INTO and DROP SCHEMA of a CDM schema are now detected.
  - **No longer reported.** T-SQL `#temp` tables.
  - **Severity.** Targets in a schema that is neither the default (`public`,
    `dbo`) nor named like a CDM schema (`scratch.person`), or that is named for
    a results, scratch or temp schema (`cdm_results.drug_era`), are WARNINGs
    instead of ERRORs.
  - **Batch context.** A table created unqualified or TEMP earlier in the same
    input now shadows the CDM name in `validate_sql_structured` too, not only
    in the API.

- **`anti_patterns.concept_code_requires_vocabulary_id` follows lineage and no
  longer guesses vocabularies.**
  - **Vocabulary suggestions.** The suggested `vocabulary_id` used to be
    inferred from the code's shape: 'E11.9' became ICD10CM, although the code
    exists in ICD10, ICD10CM and OPCS4. A vocabulary is now filled in only
    when exactly one vocabulary of the embedded release uses the shape
    (ICD9CM-style codes); otherwise the patch keeps `<vocab>` and the fix
    lists the candidate vocabularies.
  - **Newly reported.** Code filters through CTEs and derived tables,
    `BETWEEN`, `LEFT(concept_code, n)`, `HAVING`, and `JOIN concept USING
    (concept_code)`.
  - **Constraints.** A `vocabulary_id` predicate under OR no longer counts as
    a constraint, unless it is AND-connected to the code inside the same OR
    branch. A constraint in an enclosing or inner SELECT on the same concept
    row, an equality on that row's `concept_id`, and `COALESCE(vocabulary_id,
    '')` now count.

- **`anti_patterns.no_distinct_on_primary_key_column` takes its keys from the
  CDM v5.4 specification and follows lineage.**
  - **Keys.** The 18 hand-listed keys are replaced by the specification's 29,
    adding concept, observation_period, the era tables, death.person_id,
    metadata and the vocabulary tables.
  - **Newly reported.** A cast key, `DISTINCT *` / `DISTINCT t.*` over a
    single table, and CTE and derived-table reads that keep a single source.
    `COUNT(DISTINCT pk)` and `GROUP BY pk` are not reported: a redundant
    DISTINCT never changes the result.
  - **No longer reported.** Temporary (`#t`) tables that share a CDM name.

- **`anti_patterns.join_key_validation` covers every entity key and concept-id
  column of the CDM v5.4 specification.**
  - **Key lists.** The five hand-listed entity ids are replaced by every
    primary key of a non-vocabulary table and every foreign key to one;
    `concept_id` by every foreign key to CONCEPT (`*_concept_id`,
    `concept_id_1`, `descendant_concept_id`, ...).
  - **Newly reported.** Casts on either side and `entity_key IN (SELECT
    concept_id ...)`.
  - **No longer judged.** Columns of tables outside the CDM.

- **`temporal.end_before_start_validation` is a WARNING, with an ERROR only
  for empty ranges.** The CDM v5.4 specification does not state that an end
  date follows its start date; the Data Quality Dashboard treats the reverse
  as implausible and tolerates a share of such rows, so filters such as
  `drug_exposure_start_date >= '2023-06-01' AND drug_exposure_end_date <
  '2023-06-01'` now get a WARNING that cites the dashboard. Bounds on one
  column that no value satisfies (`start >= '2023-06-01' AND start <
  '2023-01-01'`, `BETWEEN` with the larger value first) are false for every
  row and stay an ERROR. A comparison of a row's own start and end columns
  (`visit_end_date < visit_start_date`) is no longer reported in any
  projection: it is the dashboard's plausibility check, and
  `temporal.nullable_date_column` already treats it that way. Previously the
  readmission join `v2.visit_start_date > v1.visit_end_date`, the overlap test
  written with literals on the left, a `UNION ALL` of two windows and `NOT
  (start > end)` were ERRORs; they are now silent. The start/end pairs are
  generated from the dashboard's field-level file, which adds
  procedure_occurrence, the era tables, observation_period and the
  `*_datetime` pairs, and drops `cohort`. Bounds are read per SELECT and per
  row source, through CTEs, derived tables and renames, with the literal on
  either side and T-SQL `'yyyymmdd'` literals.

- **`temporal.datetime_between_date_literal` judges the inclusive upper bound
  by type.** It is renamed "Datetime Column With a Date-Only Inclusive Upper
  Bound" (rule id unchanged) and reports any inclusive upper bound written as
  a date on a timestamp: `BETWEEN ... AND '2023-12-31'`, `<= '2023-12-31'`,
  `'2023-12-31' >= x`, whatever the lower bound, including `TO_DATE` /
  `MAKE_DATE` bounds, `HAVING MAX(x) <= ...` and CASE expressions. Previously
  the same range written with `>=` and `<=` was missed. The bounded value is
  typed by lineage, so `CAST(x AS DATE) BETWEEN ...` (the rule's own suggested
  fix), `x::date`, `DATE(x)`, `DATE_TRUNC('day', x)` and `TO_CHAR(x, ...)` are
  no longer reported, while a renamed `*_datetime` column is. The rule now
  also cites the CDM convention that unknown times are stored as 00:00:00,
  which is why it stays a WARNING.

- **`concept_standardization.concept_ancestor_max_levels_misuse` is a WARNING
  and reports only `max_levels_of_separation = 1`.** max_levels_of_separation
  is the longest path of a pair, so `= 1` drops direct children that also have
  a longer path (in the Athena v5.0 27-AUG-25 release, 'Diabetes mellitus' has
  24 descendants at min_levels_of_separation = 1 and 21 at
  max_levels_of_separation = 1). Restricting to children with no longer path
  can be intended, so the finding is a WARNING (it was an ERROR). `= 0`, `<>
  N`, `= 2` and `= 3` are no longer reported, and neither is a row that also
  restricts min_levels_of_separation. The restriction is recognised in every
  spelling (`IN (1)`, `BETWEEN 1 AND 1`, `> 0 AND < 2`, casts, CTEs, HAVING,
  CASE labels). The patch for `1 = max_levels_of_separation` no longer inverts
  the bound, and the description no longer claims that a pair can have several
  rows.

- **`concept_standardization.concept_ancestor_rollup_direction` is a
  WARNING.** The CDM allows querying concept_ancestor "the other way around",
  so a record concept joined to ancestor_concept_id with a fixed descendant
  returns the concept and its broader concepts, which can be intended (it was
  an ERROR). Only a literal restriction of descendant_concept_id counts, so
  queries that compare descendant_concept_id with another column are no longer
  reported. The rule now finds the descendant filter in JOIN ON, in
  IN-subqueries, CTEs and derived tables, on visit_occurrence, drug_era and
  every other record concept column, and through a joined concept row. The fix
  names the query's own alias.

- **`concept_standardization.era_table_standard_concepts` reports only
  predicates that remove every era row.** `IS NOT NULL`, `IN ('S', 'C')`, `NOT
  IN ('C')`, `NOT (... = 'C')`, CASE labels, aggregate `FILTER` clauses, OR
  branches, outer-join ON clauses and filters on another concept row no longer
  get "returns 0 rows" (on real data those queries return all 237,460 drug_era
  rows). Comma joins, CTEs, derived tables, IN-subqueries, EXISTS, casts and
  unqualified columns are now detected. `standard_concept IS NULL` on
  condition_era is a WARNING, because it selects the unmapped eras (concept
  0).

- **`concept_standardization.concept_ancestor_self_include_redundancy`
  suggests `descendant_concept_id <> ancestor_concept_id`.** The old fix,
  `min_levels_of_separation > 0`, also dropped real descendants. A literal or
  VALUES anchor branch is now detected. Consumers that ignore duplicates are
  recognised, including through chained CTEs: COUNT(DISTINCT ...), MIN/MAX,
  GROUP BY without counting aggregates. Marker columns are compared by
  position.

- **`concept_standardization.multiple_maps_to_targets` follows the mapping by
  lineage.**
  - LIMIT 1 is reported only when the SELECT outputs a mapping target, so
    `ORDER BY start_date LIMIT 1` over mapped events and existence checks are
    silent.
  - Newly detected: scalar subqueries in JOIN ON, mappings read through CTEs,
    `IN ('Maps to')`, `FETCH FIRST 1 ROW ONLY` and `DISTINCT ON` without ORDER
    BY.
  - A scalar subquery that already has LIMIT 1 cannot fail, so it is reported
    once, as the LIMIT finding.

- **`concept_standardization.source_concept_id_standard_filter` wording no
  longer says source concepts are non-standard "by definition".** The source
  field "may not necessarily be standard": source concepts coded in SNOMED,
  LOINC, RxNorm or CVX are Standard, so the filter keeps those records and
  drops the rest. The column list comes from the field-level specification:
  `specimen_source_concept_id` is removed and 13 real source fields are added.
  Negations, OR-ed join keys and nested alias reuse are no longer reported.
  Comma joins, concept in FROM, CTE renames, derived tables, IN-subqueries,
  casts and `IS NOT NULL` are now detected.

- **`concept_standardization.source_to_concept_map_validation` recognises a
  vocabulary taken from a joined column.** `source_vocabulary_id = k.vocab`
  counts as a restriction. CTEs, derived tables, CAST / UPPER, IN-subqueries
  and `USING (source_code)` are now detected.

- **`data_quality.schema_validation` treats the CDM schema as closed.** A
  table that is neither in the OMOP CDM v5.4 specification nor defined in the
  same input (CTE, `CREATE TABLE`, `SELECT ... INTO`, temp table) is an ERROR,
  including results and staging tables such as `results.achilles_results`.
  This is a declared policy, like the write protection: such a table may
  exist, but a query that reads it cannot be validated against the CDM, and
  agent-written SQL that invents a table (`condition`, `drug_concept`) fails.
  When the name is a near miss of a CDM table (one or two edits, e.g.
  `condition_occurence`) and stands where a CDM table is expected, the finding
  names that table; a table removed in v5.4 (`attribute_definition`) is
  reported as removed. The substring-based REPLACE patch, which rewrote
  `results.cohort_inclusion_result` to `results.cohort`, is gone; a near-miss
  patch is never offered for a write target. The CohortGenerator
  `cohort_definition` column whitelist is removed; a column missing from the
  CDM definition of `cohort` or `cohort_definition` is now a WARNING derived
  from the specification's schema split (see the ERROR-tier corrections
  entry), not an exemption by column name. INSERT targets are no longer
  exempt; they get the same treatment as DELETE / UPDATE / MERGE targets.

- **`data_quality.schema_validation` resolves columns like the engine does.**
  A name missing from every CDM table in scope is now reported (naming all of
  them) instead of skipped; `JOIN ... USING (col)` names and `INSERT INTO <cdm
  table> (col, ...)` lists are checked; columns read through `SELECT *` CTEs
  and derived tables are checked; output aliases of the same SELECT (`... AS
  measurement_start_date ... GROUP BY measurement_start_date`) are not
  base-column references and are not flagged.

- **`data_quality.free_text_column_misuse` is a WARNING and covers every
  free-text column.** The column list is generated from the CDM v5.4
  field-level CSV (36 varchar columns that are neither source values nor keys,
  including `drug_exposure.stop_reason`). Matches against concept text columns
  are found through lineage, casts, LOWER / UPPER / TRIM and `IN (SELECT
  ...)`. CAST to a number is a warning that the cast fails on the first
  non-numeric value (it was described as failing silently); TRY_CAST and
  SAFE_CAST are not flagged. `note_nlp.term_modifiers` is reported only when
  equated with another column, no longer for a `LIKE` in `ON`. Fix texts no
  longer suggest unrelated columns (`location.state` -> `country_concept_id`).
  Comparisons with a number moved to `data_quality.column_type_validation`.

- **`data_quality.canonical_string_value_validation` checks against a
  vocabulary release, by lineage.** The hand-written lists of vocabulary,
  domain and concept-class IDs, nine of whose entries did not exist (e.g.
  'Morphologic Abnormality', 'Unit', 'GemScript'), are replaced by every ID of
  Athena v5.0 27-AUG-25 (113 vocabularies, 50 domains, 433 concept classes),
  so suggested replacements always name a real ID. A column is checked when
  its lineage is a CDM column that holds these IDs (now including
  `source_to_concept_map.target_vocabulary_id` and `cost.cost_domain_id`),
  through pass-through CTEs and derived tables, casts, `<>`, `NOT IN` and any
  clause. A wrong case is an ERROR on PostgreSQL and DuckDB and a WARNING on
  other dialects (SQL Server and MySQL compare case-insensitively by default).
  ILIKE is no longer flagged. A dashed form of a known ID ('ICD-10-CM') stays
  a WARNING; an unknown hyphenated ID is no longer reported.

- **`data_quality.negative_concept_id_validation` reports only predicates that
  cannot be true.** `> -1`, `<> -1` and `NOT IN (-1)`, which keep every row,
  were ERRORs and are no longer reported; nor are sentinels computed in the
  query (`COALESCE(unit_concept_id, -1)`) or columns of non-CDM tables. The
  checked columns are generated from the CDM v5.4 field-level CSV (every
  foreign key to CONCEPT, 119 columns; the old list named three columns that
  do not exist and missed 43). ERROR when the predicate restricts the rows
  the statement returns; WARNING for a dead branch (OR, CASE, select list),
  an outer join's ON clause, a NOT EXISTS / NOT IN subquery, and a negative
  member of an IN list with valid ids. Upper-case SQL, parenthesised and cast
  literals, and `< 0` are now recognised.

- **`data_quality.vocabulary_table_protection` follows its sources.**
  `source_to_concept_map` is no longer protected: the CDM specification
  describes it as an ETL table for local source codes, no longer shipped with
  the vocabulary. INSERTs of local concepts (ids above 2,000,000,000) are not
  reported. ALTER TABLE and CREATE OR REPLACE TABLE on a vocabulary table are
  now reported. A vocabulary table name in a schema that is not the default or
  CDM / vocabulary schema is a WARNING (it may be a copy); T-SQL `#temp`
  tables and tables created earlier in the batch are skipped. The rule now
  cites the Book of OHDSI (4.2.2) and the CDM specification, which state that
  the vocabulary tables are read-only to analytic tools.

- **`domain_specific.event_field_polymorphic_resolution` checks which table
  the field concept names.** Matching `measurement_event_id` (or
  `observation_event_id`, `note_event_id`, `episode_event.event_id`) with a
  table's primary key while the field concept is restricted to another table's
  field concept (`1147070`, visit_occurrence, with `condition_occurrence_id`)
  is a new ERROR: every returned row pairs unrelated records. A join with no
  restriction was an ERROR; it is now a WARNING, because it is wrong only
  where the column links records of several tables. The rule no longer accepts
  or recommends `IS NOT NULL` / `IS NULL` on the field concept, which does not
  say which table the key points into. It now recognises the restriction in
  either operand order, through casts, as a parameter or scalar subquery, and
  when the field concept is chosen by name through a join to `concept`; these
  were ERRORs. `measurement_event_id IS NOT NULL` profiling is no longer
  reported. Restrictions are tied to the row they constrain (another alias, an
  OR operand or a different UNION branch no longer counts), and tables read
  through CTEs and derived tables are followed. The key and field-concept
  columns and the field concept ids are generated from the CDM v5.4
  field-level specification and the Athena vocabulary.

- **`domain_specific.drug_era_concept_class_validation` follows the concept
  row and no longer rejects satisfiable filters.** A class filter that admits
  no Ingredient on the concept matched with `drug_era.drug_concept_id` remains
  an ERROR, and the rule now also covers `dose_era.drug_concept_id`, which
  carries the same fkClass = Ingredient check (threshold 0) in the CDM v5.4
  field-level specification.
  The concept is now followed through `IN` subqueries, `EXISTS`, CTEs, derived
  tables, casts and unqualified columns, and `LIKE` patterns are judged. `IN
  ('Ingredient', …)` is no longer reported; `<> 'Ingredient'` and `NOT IN
  ('Ingredient')` are WARNINGs (they select only rows that fail the
  conformance check) instead of ERRORs; a class filter in an outer join's ON
  is a WARNING. A class filter on a concept read in another scope that reuses
  the alias is no longer reported. The REPLACE patch that turned `'Branded
  Drug'` into `'Ingredient'` (and so changed the question) is gone; the fix
  text points to drug_exposure or concept_ancestor. Case differences
  (`'ingredient'`) are left to
  `data_quality.canonical_string_value_validation`.

- **`domain_specific.drug_exposure_quantity_misuse` follows renamed columns,
  and its example runs.** `quantity` and the date column are now traced
  through CTEs and derived tables, so `x.s + x.q` over `(SELECT
  drug_exposure_start_date AS s, quantity AS q FROM drug_exposure) x` is
  reported. The violation example is now `drug_exposure_start_date +
  CAST(quantity AS INTEGER)`, which runs on PostgreSQL and DuckDB; the
  previous `date + quantity` was a type error there (quantity is a float).

- **`domain_specific.measurement_range_low_high_validation` is a WARNING.** A
  filter that keeps only rows with `range_low > range_high` runs and returns
  those rows, which is how a reference-range audit is written; the CDM defines
  the two columns as lower and upper limit of normal but does not forbid
  inverted rows. The rule no longer offers a patch that turned such an audit
  into its complement. `range_low >= range_high` is no longer reported (a
  single reference value can be stored as both limits). Literal bounds are now
  collected per measurement row, so UNION branches, self-joins and subqueries
  are not combined into a false contradiction, and predicates under NOT / NOT
  EXISTS are skipped. Derived tables, casts and `range_low - range_high > 0`
  are now recognised.

- **`domain_specific.observation_value_as_concept_confusion` is a WARNING and
  judges one row at a time.** The vocabulary has concepts that some source
  code maps to with both 'Maps to' and 'Maps to value' (903657 'Cigarette
  smoker'), so a conformant row can hold the same concept as question and
  answer; those concepts are no longer reported, and the rule no longer calls
  the pattern meaningless. Ids are now compared per observation row and only
  when both columns admit the same set: overlapping IN lists, self-joins, OR
  branches, subqueries and the 0 / 0 unmapped case were ERRORs and are now
  silent. The rule now also reports `observation_concept_id =
  value_as_concept_id` and conditions split between ON and WHERE or between a
  CTE and the outer query.

- **`joins.cohort_clinical_join_validation` is a WARNING and follows equality
  classes.** The CDM v5.4 specification allows provider and visit cohorts, so
  `subject_id` joined to another entity's key is no longer an error: it is a
  WARNING, and silent when the SELECT restricts
  `cohort_definition.subject_concept_id`. A clinical table is linked to the
  cohort when `subject_id` and its `person_id` fall in one class of equal
  values, through any other table (previously only `person`), casts,
  unqualified columns (the Book of OHDSI form `ON subject_id = person_id` was
  an error), and `WHERE` even when the statement also has `JOIN ... ON`. A
  cohort read through a CTE or derived table is checked, as are `person` and
  all 18 person-level tables (previously 12, without `person` or the era
  tables). T-SQL temporary tables named `#cohort` are not the CDM table.

- **`joins.clinical_pk_cross_join_validation` compares key entities by
  lineage.** An equality is reported when its two operands hold keys of
  different entities, one of them a clinical event; keys come from the CDM
  v5.4 field-level specification and operands are followed through casts,
  CTEs, derived tables and `IN` subqueries. Previously any event primary-key
  name equated with any other column was an error, so
  `MIN(condition_occurrence_id) AS first_id` joined back, a key renamed in a
  CTE, a `ROW_NUMBER` dedupe, and `co.visit_detail_id =
  vd.parent_visit_detail_id` (both foreign keys to visit_detail) were
  reported; they no longer are. `WHERE` equalities are read even when the
  statement has a `JOIN ... ON`, casts no longer hide the defect, and all
  person-level event keys are covered (observation_period, payer_plan_period,
  era and episode keys included). Pairs involving `person` or
  `visit_occurrence` keys are left to `joins.person_id_join_validation` and
  `joins.visit_occurrence_id_join_validation`. Findings carry a REPLACE patch
  to the table's foreign key when it is unique. The `location_history` entry,
  which is not a v5.4 table, is gone. The rule absorbs the wrong-key case of
  `joins.note_nlp_note_join_validation` (`nnlp.note_nlp_id = n.note_id`).

- **`joins.care_site_id_join_validation` covers care-site and location keys by
  lineage, and absorbs `joins.care_site_location_join_validation`.** Renamed
  "Care Site and Location Key Join Validation". It reports an equality whose
  operands hold keys of two different entities when one is a care-site or
  location key, with keys from the CDM v5.4 field-level specification.
  `person.location_id = care_site.location_id` (patients living at a care
  site's address) and comparisons of non-key columns are no longer errors. The
  defect is found in `WHERE`, comma joins, CTEs, derived tables, under casts,
  with unqualified columns and in DuckDB's FROM-first form, and an alias
  reused in another scope is no longer bound to `care_site`.
  `care_site.care_site_id = location.location_id` (the absorbed rule's case)
  is reported here, with a REPLACE patch to `care_site.location_id`.

- **`joins.concept_vocabulary_join_validation` is renamed
  `joins.concept_metadata_join_validation` and absorbs
  `joins.concept_concept_class_join_validation` and
  `joins.concept_domain_join_validation`.** One table-driven rule checks joins
  to vocabulary, domain and concept_class, with keys from the CDM v5.4
  field-level specification. A foreign key compared with a non-key column of
  the referenced table is reported with a REPLACE patch (an ERROR, or a
  WARNING for the name column; see the ERROR-tier corrections entry); other
  column pairs are WARNINGs unless the two sources are already linked. Casts, `LOWER` /
  `UPPER` / `TRIM` and `IN (SELECT …)` semi-joins no longer hide the defect, a
  metadata-concept foreign key (`vocabulary_concept_id = concept.concept_id`)
  counts as a link so a name filter on that pair is not reported, and other
  referencing tables (`source_to_concept_map.target_vocabulary_id`,
  `cost.cost_domain_id`) are covered.

- **`joins.concept_relationship_relationship_join_validation` accepts
  `reverse_relationship_id`.** Joining `concept_relationship.relationship_id`
  to `relationship.reverse_relationship_id` reaches the reverse relationship
  and is no longer an error. Operands are resolved by lineage, so the defect
  is found behind CTEs, derived tables, casts, unqualified columns, `IN`
  subqueries and `WHERE` clauses inside CTEs.

- **Join rules resolve a column only when the CDM table has it.** A CTE that
  selects a column CONCEPT does not have (`SELECT drug_concept_id FROM
  concept`) no longer makes `joins.concept_join_validation` report the
  downstream join; the missing column is `data_quality.schema_validation`'s
  finding.

- **`joins.fact_relationship_join_validation` is now a WARNING and judges each
  join on its own.** A join of `fact_id_1` / `fact_id_2` to a table's primary
  key needs a restriction on the matching `domain_concept_id_N` of the same
  fact_relationship row. Previously every such join was an ERROR unless the
  whole statement held exactly one integer literal for that column equal to a
  hard-coded Domain concept id, so resolving one polymorphic key against
  several tables (one LEFT JOIN or one UNION branch per domain), an
  unqualified or cast filter, a parameter, a subquery, a join to DOMAIN, and
  the CDM table concepts of the vocabulary (`1147330` = measurement) were all
  ERRORs. The CDM does not fix which concept stands for which table, and
  whether unrestricted rows exist depends on the data, so the finding is a
  warning. Both conventions found in the vocabulary (Domain concepts and CDM
  table concepts, Athena v5.0 27-AUG-25) are accepted, and so is any value the
  rule cannot read. A literal the vocabulary assigns to another table still
  warns. Joins to `person`, `care_site`, `visit_detail` and every other table
  with a primary key are now checked, as are unqualified, cast, CTE-wrapped
  and `IN (SELECT …)` joins. The ADD patch, which left a wrong filter in place
  and so returned no rows, is removed.

- **`joins.provider_join_validation` judges each side by the entity it
  identifies.** A predicate is reported when it equates a provider key with
  the key of another entity. Previously every equality between an event table
  and `provider` other than `provider_id = provider_id` was an ERROR,
  including `vo.care_site_id = pr.care_site_id`, which pairs a visit with the
  providers of its care site and is valid. Joins written in WHERE, with casts,
  through CTEs or derived tables, or as `IN (SELECT …)` are now checked too.
  `person_id = provider_id` is reported once, by
  `joins.person_id_join_validation`. The `example_bad` is now `vo.care_site_id
  = p.provider_id`.

- **`joins.visit_occurrence_id_join_validation` absorbs
  `joins.clinical_visit_detail_join_validation` and reads every join form.** A
  predicate is reported when it equates a visit key with the key of another
  entity, judged on the CDM columns the operands read through casts, CTEs,
  derived tables and `IN (SELECT …)`, in ON, WHERE or HAVING. Previously WHERE
  was skipped whenever any JOIN … ON existed, and casts and IN subqueries were
  not read. A visit / visit detail mix-up (`co.visit_occurrence_id =
  vd.visit_detail_id`, now also on `note` and `visit_occurrence`) keeps the
  removed rule's REPLACE patch to `co.visit_detail_id = vd.visit_detail_id`.
  `cohort.subject_id = visit_occurrence_id` is no longer an ERROR: the CDM
  allows visit cohorts. `visit_occurrence_id = person_id` is reported once, by
  `joins.person_id_join_validation`.

- **`joins.person_id_join_validation` absorbs
  `joins.person_location_join_validation` and judges columns by lineage.** A
  predicate is reported when it equates a person key with the key of another
  entity, judged on the CDM columns the operands read through casts, CTEs,
  derived tables and `IN (SELECT …)`, in ON, WHERE or HAVING. A derived column
  that is merely named `person_id` (`(SELECT gender_concept_id AS person_id
  FROM person) a JOIN concept c ON a.person_id = c.concept_id`) is no longer
  an ERROR, and a renamed person key is now checked. Previously WHERE was
  skipped whenever any JOIN … ON existed, and casts and IN subqueries were not
  read. `person_id = location_id` gets a REPLACE patch to `p.location_id =
  l.location_id`. A person key equated with a concept id is now reported only
  by `anti_patterns.join_key_validation`, and `person_id =
  visit_occurrence_id` / `= provider_id` only by this rule.

- **`joins.provider_care_site_join_validation` reads every join form.** The
  wrong-key check now covers predicates in WHERE, unqualified columns, casts,
  CTE and derived-table wrappers, `IN (SELECT …)` and `EXISTS`, judged on the
  CDM columns the operands read. Previously only qualified, un-cast equalities
  inside JOIN … ON were checked.

- **`joins.preceding_visit_occurrence_validation` no longer warns on the
  correct self-join.** The two warnings that asked for `vo.person_id =
  prev.person_id` and `prev.visit_end_date <= vo.visit_start_date` in addition
  to the foreign key are removed. The CDM defines
  `preceding_visit_occurrence_id` as a foreign key that finds the person's
  prior visit, so the key alone is the join; the ordering condition also
  dropped valid pairs such as an emergency visit that ends the day after the
  admission it precedes. The rule's `example_good` and suggested fix are now
  the plain foreign-key join. The remaining ERROR (the column equated with
  another entity's key) now covers WHERE, unqualified, cast, CTE-wrapped and
  `IN (SELECT …)` forms. A row compared with itself
  (`vo.preceding_visit_occurrence_id = vo.visit_occurrence_id`) is a data
  check and is no longer reported.

- **`joins.drug_exposure_drug_strength_join_validation` warns, not errors, on
  data-dependent keys.** A join on `drug_source_concept_id`, or of
  `drug_concept_id` to `ingredient_concept_id`, is now a WARNING: the source
  concept is a standard drug only when the source was coded in RxNorm, and an
  ingredient-level exposure matches every product containing the ingredient.
  Joins on columns that hold different kinds of value (`drug_exposure_id =
  drug_concept_id`) and SELECTs that read both tables without relating them
  stay ERRORs; the cartesian check now also sees tables wrapped in CTEs or
  derived tables. Once a SELECT relates the tables on `drug_concept_id`,
  further predicates between them (`de.quantity = ds.denominator_value`, an OR
  alternative) are no longer reported. The `example_bad` is now
  `de.drug_exposure_id = ds.drug_concept_id`.

- **Strict mode escalates one rule.** `--strict` (API `strict=true`) promotes
  WARNINGs to ERRORs only for
  `anti_patterns.concept_code_requires_vocabulary_id`, and it enables the
  strict-only checks of `concept_standardization.standard_concept_enforcement`
  (a WARNING) and `concept_standardization.invalid_reason_enforcement` (a
  WARNING). `concept_standardization.standard_concept_enforcement` and
  `joins.concept_relationship_requires_relationship_id` are no longer
  escalated, and `concept_standardization.concept_domain_validation` was
  removed from the set earlier in this release. A run that relied on
  `--strict` to fail on those findings now passes with WARNINGs.

- **A defect that two rules reported is now reported once.** Thirteen query
  shapes were reported by rules of two categories. Each now has one owner, and
  the other rule stays silent on it:
  - `relationship.is_hierarchical = 1` / `defines_ancestry = 1`:
    `domain_specific.vocabulary_relationship_boolean_comparison`
    (`data_quality.column_type_validation` skips the two flags).
  - A number or boolean compared with `concept.standard_concept` or
    `cost.cost_domain_id`, and `note_nlp.term_modifiers` compared with a
    concept id: `data_quality.column_type_validation`
    (`concept_standardization.standard_concept_value_validation`,
    `domain_specific.cost_event_id_polymorphic_resolution` and
    `data_quality.free_text_column_misuse` leave them).
  - A wrong-case `cost_domain_id` (`'drug'`):
    `data_quality.canonical_string_value_validation`.
  - Swapped period bounds (`BETWEEN op.observation_period_end_date AND
    op.observation_period_start_date`):
    `temporal.observation_period_date_range_logic`
    (`joins.period_join_requires_date_overlap` reads the pair as a containment
    attempt).
  - Two person-level tables with no predicate between them:
    `joins.clinical_person_id_linkage_validation`.
  - An entity key equated with a concept id (`v.care_site_id = c.concept_id`):
    `anti_patterns.join_key_validation` (`joins.care_site_id_join_validation`
    and `joins.clinical_pk_cross_join_validation` skip it).
  - `m.metadata_id = p.person_id`: `joins.person_id_join_validation` (ERROR),
    not `anti_patterns.singleton_metadata_clinical_join`.
  - `cohort.subject_id = co.visit_occurrence_id`:
    `joins.cohort_clinical_join_validation` (WARNING), since the CDM allows
    visit cohorts.

  A consequence warning is dropped by the validator's deduplication
  instead of by the rule. A `joins.clinical_person_id_linkage_validation`
  WARNING is dropped when every equality it found between the two sources is
  named by an ERROR of another rule (`co.person_id = p.person_source_value` by
  `data_quality.column_type_validation`, `co.visit_occurrence_id =
  vd.visit_detail_id` by `joins.visit_occurrence_id_join_validation`); it
  stays when the sources are not equated, are equated on non-key columns such
  as two dates, or on a wrong pair that no ERROR reports (`co.person_id =
  de.quantity`). To support this, rules that
  judge one equality record it in `details["predicate"]`, the linkage rule
  records the equalities it found in `details["predicates"]`; `details` is
  in-process only and not part of the JSON output. Calling a single rule's
  `validate()` directly still returns its own finding.

- **`temporal.observation_period_date_range_logic` reports only reversed
  period bounds, and no longer flags the canonical anchoring join.** A date is
  inside an observation period when `start <= date AND date <= end`. The rule
  now fires when the same column is held on or before
  `observation_period_start_date` and on or after
  `observation_period_end_date` of the same period: swapped `BETWEEN` bounds
  (`date BETWEEN end AND start`), both operators flipped (`start >= date AND
  end <= date`), or the two "outside the period" tests joined with `AND`
  instead of `OR` (`date < start AND date > end`). A period never starts after
  it ends, so such a pair matches nothing, or only one-day periods; the
  message says which. Either operand order, strict and non-strict operators,
  `=`, `BETWEEN`, `WHERE` / `JOIN ON` placement and constant day offsets (`+
  365`, `INTERVAL '30 days'`, `DATEADD(day, …)`) are handled, and the compared
  column may come from any table (event, cohort or CTE column). Previously the
  rule flagged every `>=` / `<=` comparison with the period column on the
  left, so the standard anchoring form `op.observation_period_start_date <=
  co.condition_start_date AND op.observation_period_end_date >=
  co.condition_start_date` got two ERRORs, while the same reversal written
  with the event column on the left, or with strict operators, was missed. It
  also flagged a period bound tested between an event's start and end
  (`op.observation_period_start_date BETWEEN vo.visit_start_date AND
  vo.visit_end_date`), which is satisfiable (a period commonly starts at the
  first visit) and is no longer reported. One-sided tests such as a washout,
  predicates under `OR` or `NOT` (the "event outside the period" audit),
  interval tests against two different columns, month or year offsets, and
  bounds read through a CTE stay silent. Study 1 corpora: 0 → 0 findings
  (agent and expert); the rule now judges 7,918 expert SELECT scopes that
  compare a column with the period bounds and reports none.

  `temporal.observation_period_date_range_logic` follows the period bounds by
  lineage. Reversed bounds read through a CTE or derived table that passes
  observation_period rows through (`WITH op AS (SELECT * FROM
  observation_period)`, renamed bounds), written as day differences (`x -
  start < 0`, `DATEDIFF(day, start, x) < 0`), or with a DATE-to-DATE cast on
  one side are now reported. Bounds aggregated in a CTE (`MIN(start)`,
  `MAX(end)`) are not judged.

- **Rule messages no longer go beyond their sources.**
  `anti_patterns.singleton_metadata_clinical_join` called `cdm_source` a
  "single-row" table, which the CDM v5.4 specification does not state, and its
  `metadata` message was ungrammatical ("metadata is a stores …"). Both
  messages now say the table describes the CDM instance and shares no key with
  patient-level data. `temporal.implausible_date_filter` no longer asserts
  that pre-1900 dates "are placeholder or corrupted values"; it cites the
  OHDSI Data Quality Dashboard, which treats event dates before 1950 as
  implausible. Detection is unchanged in both rules.

- **`data_quality.episode_requires_concept_filter` and
  `data_quality.fact_relationship_requires_relationship_concept_filter` are
  now WARNINGs.** The CDM v5.4 specification defines `episode_concept_id` and
  `relationship_concept_id` but does not require a filter on them, so an
  unfiltered read is a risk, not a certain error. Previously both rules
  reported an ERROR for any statement that did not filter the column, such as
  `SELECT e.episode_id FROM episode e WHERE e.person_id = 5`. They now warn
  that rows of every episode type (or every relationship type, in both
  directions) are mixed, and they stay silent when the mix is visible or
  intended: the SELECT projects or groups by the column, projects a column of
  the `concept` row joined to it, or the column is restricted downstream of a
  CTE. `BETWEEN` and joins to a concept-set table now count as restrictions;
  `<>`, `NOT IN` and `IS [NOT] NULL` no longer do, because they let every type
  through. Findings are per table instance, so a UNION with one filtered and
  one unfiltered read is reported for the unfiltered arm. `episode_event` is
  reported only when its `episode_id` is never used. Strict mode does not
  escalate either rule. Study 1 corpora: 0 → 0 findings (agent and expert).

  `data_quality.episode_requires_concept_filter` and
  `data_quality.fact_relationship_requires_relationship_concept_filter` report
  aggregates that mix types. The "profiling read" exemption silenced any
  aggregate-only SELECT or aggregate grouped by the table's own columns,
  including "episodes per person", the very count the episode rule warns
  about. A read is now exempt only when it analyses nothing: the table is its
  only source, with no WHERE / GROUP BY / HAVING, and the SELECT is a LIMIT
  peek or a whole-table COUNT(*). Grouping by the type column stays silent
  because it shows the type. The episode rule no longer reports a lookup by
  primary key (`episode_id = 42`), and `WHERE 1 = 1.0` is no longer mistaken
  for a constant-false probe.

- **`data_quality.column_type_validation` reports only comparisons that are
  certain to fail.** PostgreSQL gives a quoted literal the type of the column
  it is compared with, so `condition_start_date = '2020-01-01'` and
  `year_of_birth = '1980'` are valid. The rule used to report both as ERRORs,
  along with `gender_concept_id = '8532'`, which
  `data_quality.concept_id_string_comparison` already reports as a WARNING. It
  now flags a varchar or date column against a numeric literal (`domain_id =
  19`, `condition_start_date >= 2020`), a numeric column against a quoted
  non-number (`year_of_birth = 'unknown'`), a date column against a quoted
  non-date, and two columns of incompatible type families (`person_id =
  person_source_value`). Quoted literals on `*_concept_id` columns, unrendered
  template parameters (`'@start_date'`), columns recast in a CTE, and the
  free-text columns of `data_quality.free_text_column_misuse` are left alone.
  Coverage grew in three ways: unqualified columns are resolved through
  lineage (`WHERE vocabulary_id = 8` used to be silent without a table alias),
  range operators, `IN` and `BETWEEN` are checked, and column-to-column
  mismatches are found in `WHERE` as well as `JOIN … ON`. The "mixed literal
  types" finding is gone; each literal is judged on its own. Study 1 corpora:
  agent 0 → 0; expert 4 → 3 ERRORs (the removed one compared
  `gender_concept_id` with the unrendered parameter `'@gender_concept_ids'`;
  the three that remain are QueryLibrary queries comparing `relationship_id`
  with integers).

  `data_quality.column_type_validation` severity follows the dialect. ERROR on
  PostgreSQL, which rejects every class it reports. On DuckDB, ERROR for a
  date against a number, a literal that does not convert, and `<` / `>` /
  BETWEEN between varchar and a number; WARNING for `=`, `<>`, `IN` between
  varchar and numbers and for equality between integer and varchar columns,
  which DuckDB, SQL Server and MySQL cast implicitly so the outcome depends on
  the data. Other dialects: WARNING. Comparisons are now checked wherever they
  appear (HAVING, CASE, select list) and through `IN (SELECT ...)`; T-SQL
  `#temp` tables named like CDM tables are skipped. A varchar column ordered
  is a new WARNING when `value_as_string` is ordered against a quoted number
  (`value_as_string > '100'`): it orders text, so '9' > '100'.

- **`joins.era_forbidden_join_validation` is now a WARNING for an era table
  joined to a visit table on `person_id` alone ("Era to Visit Join Without
  Date Relation").** The rule id is unchanged. Previously every join from
  `condition_era`, `drug_era` or `dose_era` to `visit_occurrence`,
  `visit_detail`, `provider` or `care_site` was an ERROR, directly or through
  any intermediate table. That flagged valid queries: visits during a drug era
  (`vo.visit_start_date BETWEEN de.drug_era_start_date AND
  de.drug_era_end_date`), and an era reaching `provider` through
  `person.provider_id`. The CDM only establishes that era tables carry no
  visit key, so the rule now reports the one certain defect: an era table and
  `visit_occurrence` / `visit_detail` whose `person_id` values a SELECT
  equates (directly, through `person`, or with `USING`) with nothing else
  relating the two, which pairs every era of a person with every visit of that
  person and inflates `COUNT(*)` and `SUM`. It is silent when any other
  comparison relates the era and the visit (in ON, WHERE or the select list,
  directly or through a third table), when one side is pinned by its primary
  key, when duplication cannot change the result (`SELECT DISTINCT`, `GROUP
  BY` without aggregates, `COUNT(DISTINCT …)` / `MIN` / `MAX` only, `IN` /
  `EXISTS`), and when the SELECT feeds a later step that receives columns of
  both tables. The "indirect join" finding and the `provider` / `care_site`
  checks are gone; a wrong key such as `era.person_id = provider.provider_id`
  is still an ERROR from `joins.person_id_join_validation`. Finding `details`
  now carry `type: era_visit_person_only_join`, `era_table` and `visit_table`
  (`forbidden_table`, `era_column` and `forbidden_column` are removed).
  Callers that gate on ERROR will no longer fail on this rule. Study 1
  corpora: 0 → 0 findings (agent and expert).

  `joins.era_forbidden_join_validation` follows the pairing into later steps.
  An era / visit pairing built on `person_id` alone inside a CTE or derived
  table is now reported when the query that reads it neither relates an era
  value to a visit value nor de-duplicates (`WITH j AS (… drug_era de JOIN
  visit_occurrence vo ON vo.person_id = de.person_id) SELECT COUNT(*) FROM
  j`); previously any CTE that passed on columns of both tables was exempt.
  INSERT and CREATE TABLE AS stay exempt. Tables wrapped in a CTE or derived
  table are now checked. `OVERLAPS` and expressions that combine era and visit
  dates (`LEAST(...) - GREATEST(...)`) count as relating the two, so
  overlap-length queries are no longer reported.

- **CLI default report path is now derived from the input file name.**
  `fastssv <file>.sql` used to always write `output/validation_report.json`,
  so consecutive runs on different SQL files silently overwrote each
  other's reports. The default is now `output/<file>_report.json`
  (e.g. `fastssv phenotype.sql` → `output/phenotype_report.json`);
  stdin input keeps the old `output/validation_report.json` default,
  and an explicit `--output`/`-o` still takes precedence. Existing
  scripts that relied on the fixed default path for *file* input need
  either `-o output/validation_report.json` or the new derived name.

- **Unrendered OHDSI SqlRender templates are now detected at the
  validator entry point and short-circuited with a single WARNING.**
  Files such as `Achilles/inst/sql/sql_server/analyses/cost_distribution_template.sql`
  contain `@<identifier>` placeholders (`@domainId`, `@costColumn`,
  `@vocabDatabaseSchema`, …) that SqlRender substitutes before
  execution. Running the rule catalogue against those templates used
  to produce truthful-but-useless errors — every name-matching rule
  reported "table 'tmpach_@domainid_cost_raw' does not exist in CDM",
  "REPLACE: the misspelled name…" — for every placeholder fragment.
  `fastssv.validate_sql_structured` (and `fastssv.validate_sql`) now
  call a new `fastssv.core.helpers.looks_like_unrendered_template`
  detector *before* parsing, and when it matches return a single
  structured violation with `rule_id="meta.unrendered_sqlrender_template"`,
  `severity=WARNING`, and a "run SqlRender first" suggested fix. The id
  is exported as `fastssv.TEMPLATE_RULE_ID` (in `__all__`, alongside
  `PARSE_ERROR_RULE_ID` / `NOT_SQL_RULE_ID`) so consumers can filter on
  it without hard-coding the string.
  Detection deliberately targets `@<word>` only in
  syntactically-impossible TSQL positions: embedded inside an
  identifier (`tmpach_@domainId_cost_raw`), as a qualifier before a
  dot (`@vocabDatabaseSchema.concept`), or as a qualified component
  after a dot (`B.@domainId_concept_id`). TSQL's own `DECLARE @cnt`
  variable declarations, comparisons against `@var`, and `@@version` /
  `@@rowcount` system functions are NOT flagged, so existing T-SQL
  workflows are unaffected. String literals (`'alice@example.com'`)
  and SQL comments (`-- TODO @param`) are stripped before scanning so
  legitimate `@` characters in quoted regions don't trip the detector.
  Tests:
  `tests/test_parse_sql.py::test_template_detected`,
  `…test_template_not_detected`,
  `…test_unrendered_template_short_circuits_with_warning`,
  `…test_unrendered_template_check_runs_before_parse_error`,
  `…test_clean_sql_not_intercepted_by_template_gate`. On the 301-file
  Achilles `sql_server/analyses` corpus this empties the error set on
  the four template-shaped files (`cost_distribution_template.sql`,
  `raw_cost_template.sql`, `create_result_concept_table.sql`,
  `merge_achilles_tables.sql`).

- **`anti_patterns.destructive_operations_on_clinical_tables` now
  exempts protected names shadowed by an intra-batch
  ``CREATE [TEMP] TABLE``.** Achilles 2004 builds a 7-domain intersection
  by creating session-local scratch tables — one of which happens to be
  ``CREATE TEMP TABLE death AS SELECT DISTINCT person_id FROM cdm.death``
  — and tidies them up at the end with ``drop table death;``. The bare
  ``death`` there is the analyst's own scratch (the real OMOP table is
  read via the schema-qualified ``cdm.death``), but the rule stripped
  the schema and matched on the name alone, firing a spurious ERROR.
  The rule now extracts ``(name, db)`` pairs from each destructive
  target via ``exp.Table.name`` / ``exp.Table.db`` and consults a new
  ``ValidationContext.local_unqualified_tables`` frozenset — the subset
  of the batch's created tables that were created *without* a schema
  qualifier (or with TEMP/TEMPORARY), collected by a new
  ``fastssv.core.helpers.collect_locally_defined_unqualified_tables``
  and populated by the CLI/API alongside ``local_tables``. The subset
  matters: the broader ``local_tables`` set (which
  ``data_quality.schema_validation`` uses) strips schema qualifiers, so
  a ``CREATE TABLE backup.death AS …`` anywhere in the batch would have
  silenced a later unqualified ``DELETE FROM death`` — which still hits
  the clinical table on the search path. Only unqualified/TEMP creates
  genuinely shadow. An unqualified target whose name is in that subset
  is treated as the scratch shadow and skipped; a schema-qualified
  target (``cdm.death``, ``cdm.measurement``, …) always counts as the
  real OMOP table regardless of any shadow in the batch; and an
  unqualified protected name with no local define is still flagged. On
  the Achilles batch this turns one false positive (2004) into a clean
  pass while preserving the policy on the rule's actual intent.
  ``with_local_tables`` gained an optional second parameter for the
  subset (defaults to the first argument for callers that don't
  distinguish). Tests:
  `tests/test_rules.py::TestDestructiveOperationsOnClinicalTables::test_gap_004_drop_locally_defined_shadow_passes`,
  `…test_gap_004_drop_protected_without_shadow_still_fires`,
  `…test_gap_004_schema_qualified_drop_still_fires_even_with_shadow`,
  `…test_gap_004_insert_into_locally_defined_shadow_passes`,
  `…test_gap_004_schema_qualified_create_does_not_shadow`,
  `…test_gap_004_end_to_end_batch_collectors`, plus collector coverage
  in `tests/test_helpers_cte.py`.

- **`domain_specific.cost_event_id_polymorphic_resolution` now looks through
  `CAST(...)` / `(...)` wrappers when detecting the `cost_domain_id` filter.**
  Achilles renders every string literal as `CAST('Drug' AS TEXT)` /
  `CAST('Procedure' AS VARCHAR(255))` because the underlying `.sql.handlebars`
  templates are cross-dialect, so a valid `WHERE c.cost_domain_id =
  CAST('Drug' AS TEXT)` clause was invisible to the rule's literal matching
  and it fired an ERROR (as did the since-merged
  `joins.cost_table_domain_validation`, at WARNING). The rule now calls a new
  `fastssv.core.helpers.unwrap_cast` peeler on the RHS of `=` and on every
  value inside `IN (...)` before the literal check, so wrapped literals are
  recognised, including nested wrappers (`((CAST('x' AS TEXT)))`);
  wrong-domain mismatches and missing filters still fire. On the 301-file
  Achilles batch this turns 17 cost-related findings into clean passes
  (1502-1511, 1602-1608). Tests:
  `tests/test_rules.py::TestCostEventIdPolymorphicResolution`.

- **`domain_specific.visit_detail_visit_occurrence_reference` narrowed to a
  linkage-correctness check.** The previous version fired on every query that
  referenced `visit_detail` without `visit_occurrence` on the assumption that
  visit-level context is always needed. That over-fired on legitimate
  detail-grain analyses — e.g. Achilles 1303 (distinct
  `visit_detail_concept_id` per person), 1306 (age by detail concept), 1313
  (length-of-stay by detail concept) — none of which need `visit_occurrence`
  columns. The rule now triggers only when *both* tables are referenced and
  there is no equality joining them on `visit_occurrence_id`; the remaining
  trigger catches the real bug (e.g. joining on `person_id` alone, which fans
  rows out within a person). Column-on-wrong-table mistakes such as
  `vd.visit_concept_id` remain covered by `data_quality.schema_validation`
  from the CDM column catalogue, so this rule no longer duplicates that check.
  The rule file also moved from `src/fastssv/rules/domain_specific/visit/` to
  the table-matching `src/fastssv/rules/domain_specific/visit_detail/`
  directory per the `AGENTS.md` convention (`rule_id` and import path via the
  package are unchanged). The rule title in the docs reference is now "Visit
  Detail Visit Occurrence Linkage". Its later per-SELECT pairing check and
  WARNING severity are described in the entry above.

- **`ANALYZE <table>` is now accepted at the parse layer.** Added
  `sqlglot.exp.Analyze` to `_VALID_TOP_LEVEL_STATEMENTS` in
  `fastssv.core.helpers`. Previously, sqlglot returned `exp.Analyze`
  and the validator escalated it to a `parse.syntax_error` violation
  ("Input did not parse as a SQL statement (got Analyze)"); OHDSI
  Achilles emits ~90 `ANALYZE` statements per run to refresh planner
  stats between CTAS and downstream selects, all of which became
  noise in batch reports. ANALYZE is semantically a no-op for static
  validation — schema rule now also skips `ANALYZE` targets along
  with the existing CTAS-target skip (see broadened `_DDL_TARGET_PARENTS`
  tuple covering `Create`, `Drop`, `Alter`, `Analyze`, `TruncateTable`).
  Tests: `tests/test_parse_sql.py::test_real_sql_parses_cleanly`
  (`ANALYZE tempResults_104`, `ANALYZE scratch.tmpach_0`).

- **`concept_standardization.standard_concept_enforcement`'s suggested
  fix is now CTE-shadow-aware.** When the rule fires AND a CTE named
  `concept` or `concept_relationship` is defined **at the top level of
  the statement**, the message now recommends the schema-qualified
  `JOIN omop.concept c ON …` form and explicitly calls out the shadow
  (the old hardcoded `JOIN concept c ON …` would bind to the CTE and
  fail at execution with _"column standard_concept does not exist"_,
  making the fix un-applyable). Scope is deliberately restricted to the
  top-level WITH (`tree.args["with_"]`) rather than tree-global: CTEs
  defined inside a nested subquery (IN / EXISTS / FROM-derived) are
  lexically out of scope for a JOIN added at the outer SELECT, so the
  generic fix is already executable in that case and emitting the
  shadow-note would be misleading. The broader "any matching CTE
  anywhere in the tree" signal is handled separately by
  `anti_patterns.cte_shadows_omop_table`. The default message — used
  when no top-level shadow is present — is unchanged. Regression tests:
  `TestStandardConceptMapping::test_suggested_fix_is_schema_qualified_under_cte_shadow`,
  `…test_suggested_fix_unchanged_without_cte_shadow`, and
  `…test_suggested_fix_unchanged_when_concept_cte_is_nested_subquery`
  (the new case, added in response to a Copilot review flag that the
  earlier tree-global `collect_cte_names` over-approximated scope).

### Removed

- **Seven rules that check general SQL rather than the OMOP CDM.** FastSSV's
  scope is OMOP compliance: a finding must rest on what the CDM defines
  (tables, columns, column types, vocabulary values, OHDSI conventions).
  Whether SQL is valid, or idiomatic, on a given database is left to that
  database. These rules needed no OMOP knowledge and are removed:
  `anti_patterns.having_without_group_by`,
  `anti_patterns.null_comparison_operator`,
  `anti_patterns.limit_without_order_by`,
  `anti_patterns.comma_separated_cross_join`,
  `joins.left_join_then_where_on_right_table`,
  `temporal.date_diff_interval_comparison` and
  `data_quality.non_standard_date_literal_format`. A query that pairs two
  person-level tables without linking them is still reported by
  `joins.clinical_person_id_linkage_validation`. Selecting a removed id
  (`--rules …`, API `rule_ids`) raises `KeyError`.

- **Rule-set revision: 74 rule ids removed (one of them renamed), 12 added;
  with the seven general-SQL rules above removed as well, the registry goes
  from 154 rules (0.3.0) to 85 (34 ERROR, 51 WARNING).** The
  warning tier was re-checked against the OMOP CDM v5.4 specification and the
  Study 1 adjudication, and then every rule was audited against its cited
  sources. 34 rules were retired because their claim does not hold under OMOP
  semantics or a source, or they never fired; 39 were merged into a successor
  that covers the same defect; `joins.concept_vocabulary_join_validation` was
  renamed `joins.concept_metadata_join_validation`. Selecting a removed id
  (`--rules …`, API `rule_ids`) now raises `KeyError`; use the successor id
  from the table below. The rationale for each retirement is in the entries
  that follow, and each successor is described under Added or Changed.

  Merged or renamed (old id → successor):

  | Old id | Successor |
  |---|---|
  | `anti_patterns.type_concept_id_domain_filter` | `concept_standardization.concept_domain_validation` |
  | `domain_specific.visit_occurrence_type_domain` | `concept_standardization.concept_domain_validation` |
  | `concept_standardization.source_concept_id_warning` | `concept_standardization.standard_concept_enforcement` |
  | `data_quality.standard_concept_null_handling` | `concept_standardization.standard_concept_value_validation` |
  | `joins.cost_table_domain_validation` | `domain_specific.cost_event_id_polymorphic_resolution` |
  | `domain_specific.drug_days_supply_validation` | `domain_specific.drug_exposure_implausible_value_filter` |
  | `domain_specific.drug_quantity_validation` | `domain_specific.drug_exposure_implausible_value_filter` |
  | `domain_specific.condition_occurrence_cardinality_validation` | `domain_specific.event_rows_counted_as_persons` |
  | `domain_specific.drug_exposure_cardinality_validation` | `domain_specific.event_rows_counted_as_persons` |
  | `domain_specific.event_cardinality_validation` | `domain_specific.event_rows_counted_as_persons` |
  | `domain_specific.dose_era_cross_unit_comparison` | `domain_specific.numeric_value_without_unit_constraint` |
  | `domain_specific.measurement_cross_unit_comparison` | `domain_specific.numeric_value_without_unit_constraint` |
  | `domain_specific.measurement_unit_validation` | `domain_specific.numeric_value_without_unit_constraint` |
  | `domain_specific.measurement_value_as_number_and_concept_validation` | `domain_specific.value_as_columns_conjunction` |
  | `domain_specific.observation_value_as_columns_mutually_contextual` | `domain_specific.value_as_columns_conjunction` |
  | `joins.visit_detail_join_validation` | `domain_specific.visit_detail_visit_occurrence_reference` |
  | `domain_specific.visit_outpatient_same_day_validation` | `domain_specific.visit_length_of_stay_arithmetic` |
  | `joins.observation_period_join_validation` | `joins.period_join_requires_date_overlap` |
  | `joins.payer_plan_period_join_validation` | `joins.period_join_requires_date_overlap` |
  | `data_quality.clinical_event_date_before_1900_validation` | `temporal.implausible_date_filter` |
  | `temporal.clinical_event_date_in_future_validation` | `temporal.implausible_date_filter` |
  | `temporal.death_date_in_future_validation` | `temporal.implausible_date_filter` |
  | `temporal.nullable_end_date_null_handling` | `temporal.nullable_date_column` |
  | `temporal.required_date_column_validation` | `temporal.nullable_date_column` |
  | `domain_specific.observation_value_as_string_numeric_comparison` | `data_quality.column_type_validation` |
  | `domain_specific.specimen_source_id_not_specimen_id` | `data_quality.column_type_validation` |
  | `anti_patterns.attribute_definition_invalid_join` | `data_quality.schema_validation` |
  | `domain_specific.cdm_v53_to_v54_column_renames` | `data_quality.schema_validation` |
  | `domain_specific.episode_event_no_person_id` | `data_quality.schema_validation` |
  | `domain_specific.event_date_column_correctness` | `data_quality.schema_validation` |
  | `domain_specific.visit_detail_has_no_preceding_visit_occurrence_id` | `data_quality.schema_validation` |
  | `joins.care_site_location_join_validation` | `joins.care_site_id_join_validation` |
  | `domain_specific.death_join_to_person_not_to_clinical_event` | `joins.clinical_person_id_linkage_validation` |
  | `joins.death_visit_occurrence_join_validation` | `joins.clinical_person_id_linkage_validation` |
  | `joins.note_nlp_note_join_validation` | `joins.clinical_pk_cross_join_validation` |
  | `joins.concept_concept_class_join_validation` | `joins.concept_metadata_join_validation` |
  | `joins.concept_domain_join_validation` | `joins.concept_metadata_join_validation` |
  | `joins.person_location_join_validation` | `joins.person_id_join_validation` |
  | `joins.clinical_visit_detail_join_validation` | `joins.visit_occurrence_id_join_validation` |
  | `joins.concept_vocabulary_join_validation` | `joins.concept_metadata_join_validation` (renamed) |

  Retired without a successor:
  `anti_patterns.concept_ancestor_mixed_with_concept_relationship_redundantly`,
  `anti_patterns.duplicate_column_alias`,
  `anti_patterns.standard_concept_or_with_classification`,
  `anti_patterns.top_as_synthetic_data`,
  `concept_standardization.concept_ancestor_cross_domain`,
  `concept_standardization.concept_class_id_ingredient_for_drug_grouping`,
  `concept_standardization.concept_synonym_language_concept_id`,
  `concept_standardization.maps_to_target_standard_validation`,
  `data_quality.concept_name_whitespace`,
  `data_quality.fact_relationship_no_self_reference`,
  `data_quality.fact_relationship_valid_concepts`,
  `data_quality.incorrect_percentile_calculation`,
  `data_quality.union_concept_id_domain_indicator`,
  `data_quality.unmapped_concept_handling`,
  `domain_specific.cohort_definition_syntax_not_executable_sql`,
  `domain_specific.condition_visit_hierarchy_validation`,
  `domain_specific.cost_paid_ingredient_cost_drug_specific`,
  `domain_specific.cost_payer_plan_period_id_join`,
  `domain_specific.death_cause_source_concept_validation`,
  `domain_specific.drug_exposure_sig_parsing`,
  `domain_specific.drug_strength_validity_filter`,
  `domain_specific.episode_parent_id_self_join`,
  `domain_specific.location_history_entity_id_requires_domain_id`,
  `domain_specific.measurement_duplicate_detection`,
  `domain_specific.procedure_occurrence_quantity_semantics`,
  `domain_specific.visit_detail_admitted_discharged_domain`,
  `domain_specific.visit_event_temporal_validation`,
  `domain_specific.year_of_birth_age_arithmetic`,
  `joins.concept_alias_reuse_validation`,
  `joins.concept_ancestor_name_resolution`,
  `joins.concept_relationship_concept_join_validation`,
  `joins.join_path_validation`, `temporal.death_date_before_birth_validation`,
  `temporal.future_information_leakage`.

- **Rule audit: 12 rules retired.** Every rule was audited against its cited
  sources, against equivalent rewrites of its own examples, and against the
  Study 1 corpora. These rules were retired because their claim does not rest
  on a source, they blocked correct queries, or they never fired; selecting
  one of their ids now raises `KeyError`.
  - `concept_standardization.concept_ancestor_cross_domain`: judged only 12
    hard-coded ancestor ids against one vocabulary release, and its
    statement-wide alias state turned a domain filter on another `c` (another
    CTE or UNION branch) into a blocking "returns no rows" ERROR; it fired on
    none of 62,617 corpus queries.
  - `data_quality.fact_relationship_no_self_reference`: its claim that no
    relationship type describes a fact related to itself has no source, and
    the one realistic author of the pattern is a data-quality audit that wants
    exactly those rows; no corpus query contains it.
  - `data_quality.incorrect_percentile_calculation`: one OHDSI QueryLibrary
    copy-paste bug turned into a detector keyed on that query's names; it
    asserted identical values without comparing the expressions and missed 8
    of 15 equivalent rewrites.
  - `domain_specific.condition_visit_hierarchy_validation`: hard-wired to
    condition_occurrence, and its precondition (an event linked to
    visit_detail on `visit_detail_id` while visit_occurrence is also read)
    occurs in no corpus query; the finding depends on intent.
  - `domain_specific.cost_payer_plan_period_id_join`: judged ON and WHERE
    separately, so a correct key in ON plus another column pair in WHERE was
    an ERROR; no corpus query joins cost to payer_plan_period, and its own
    example was double-reported with contradicting advice.
  - `domain_specific.death_cause_source_concept_validation`: the convention it
    cited covers every `*_source_concept_id` field, yet only death was
    checked, and the general source-concept rule was already retired earlier
    in this release.
  - `domain_specific.episode_parent_id_self_join`: three correct hierarchy
    queries (siblings, the parent's events, a parent read through a derived
    table) were ERRORs, and "it must only join to episode.episode_id" is
    stronger than the specification; wrong-key joins are reported by the
    general key rules.
  - `domain_specific.location_history_entity_id_requires_domain_id`:
    LOCATION_HISTORY is a CDM v6.0 table that v5.4 does not define (reading it
    is now an unknown-table finding of `data_quality.schema_validation`), and
    correct domain filters written as IN, UPPER(), ILIKE or a parameter were
    ERRORs.
  - `joins.concept_alias_reuse_validation`: it did not look for a duplicate
    alias: it fired when one concept alias was equated with two concept-id
    columns, which is valid SQL, while engines already reject a real duplicate
    alias with a precise message.
  - `joins.concept_ancestor_name_resolution`: it inferred intent from words in
    output aliases (descendant, child, parent, …), which no source defines,
    and matched aliases statement-wide; an intent-dependent finding cannot be
    an ERROR.
  - `joins.concept_relationship_concept_join_validation`: an ERROR decided by
    words in a table alias; renaming the alias, a CTE or a CAST removed the
    finding, and 7,330 agent and 164 expert queries join the two tables
    without a finding. Direction defects that lineage can establish are
    `joins.maps_to_direction`'s.
  - `temporal.death_date_before_birth_validation`: its "impossible" predicate
    is satisfied by valid data (a newborn who dies on the day of birth has
    `death_date < birth_datetime`), it blocked the Data Quality Dashboard's
    own plausibleAfterBirth check, and no corpus query compares death with
    birth.

- **`domain_specific.cdm_v53_to_v54_column_renames`,
  `domain_specific.event_date_column_correctness`,
  `domain_specific.episode_event_no_person_id`,
  `domain_specific.visit_detail_has_no_preceding_visit_occurrence_id` and
  `anti_patterns.attribute_definition_invalid_join` are merged into
  `data_quality.schema_validation`.** Every true positive they reported is a
  missing column or table, now reported once, with the v5.4 rename or the
  table that holds the column in the message and in the REPLACE patch. The old
  rules' false positives on output aliases and on unqualified names that
  resolve to the other visit table are not carried over.

- **`domain_specific.observation_value_as_string_numeric_comparison` and
  `domain_specific.specimen_source_id_not_specimen_id` are merged into
  `data_quality.column_type_validation`.** `value_as_string > 100` and
  `measurement_event_id = specimen_source_id` remain errors on PostgreSQL,
  with hints naming `value_as_number` and `specimen.specimen_id`;
  varchar-to-varchar joins on `specimen_source_id` are no longer flagged.

- **`attribute_definition` and `location_history` left the CDM catalogue**
  (`fastssv.schemas.CDM_COLUMN_TYPES`), which now holds exactly the 39 tables
  of the v5.4 field-level specification. Reading either table is reported by
  `data_quality.schema_validation`.

- **`joins.care_site_location_join_validation`,
  `joins.concept_concept_class_join_validation`,
  `joins.concept_domain_join_validation`,
  `joins.note_nlp_note_join_validation`,
  `joins.death_visit_occurrence_join_validation` and
  `domain_specific.death_join_to_person_not_to_clinical_event`** are merged
  into `joins.care_site_id_join_validation`,
  `joins.concept_metadata_join_validation` (formerly
  `joins.concept_vocabulary_join_validation`),
  `joins.clinical_pk_cross_join_validation` and
  `joins.clinical_person_id_linkage_validation`. Their true-positive shapes
  are reported by the merge targets; see the entries above.

- **`joins.clinical_visit_detail_join_validation` and
  `joins.person_location_join_validation` are merged into
  `joins.visit_occurrence_id_join_validation` and
  `joins.person_id_join_validation`.** Both judged one wrong key pair that the
  merge targets now report by lineage, with the same REPLACE patches
  (`co.visit_detail_id = vd.visit_detail_id`, `p.location_id =
  l.location_id`); see the entries for the two targets under Changed.

- **`domain_specific.cohort_definition_syntax_not_executable_sql`.** The
  rule raised an ERROR when a query pattern-matched SQL keywords or OMOP
  table names in `cohort_definition.cohort_definition_syntax`, on the claim
  that the column "is not executable SQL". CDM v5.4 defines the column as
  "Syntax or code to operationalize the Cohort Definition", so stored SQL
  is a conformant value and searching it is a legitimate query. No certain
  defect remained to report. Study 1 corpora: 0 findings. Selecting the id
  (`--rules …`, API `rule_ids`) now raises `KeyError`.

- **`concept_standardization.maps_to_target_standard_validation`.** The
  rule warned that a `'Maps to'` target (`concept_id_2`) "is not
  guaranteed to be standard" unless re-checked against
  `concept.standard_concept = 'S'`. By OHDSI vocabulary construction every
  `'Maps to'` relationship points at a standard concept, so the recommended
  join could never exclude a row. All 2,372 findings in Study 1 (2,247 on
  LLM-written SQL, 125 on OHDSI packages) were adjudicated false
  observations. Anything that selected the rule by id (`--rules …`, API
  `rule_ids`) will now receive a `KeyError` for that id.

- **`anti_patterns.duplicate_column_alias`.** The rule treated any
  expression projected twice under different aliases as a "likely
  copy-paste error". Every one of its 2,989 expert-corpus findings was a
  deliberate idiom — Circe's zero-duration events (`start_date` =
  `end_date`), age-band boundary scaffolding, FeatureExtraction's
  `is_binary` / `missing_means_zero` flags, `start_date` / `sort_date`
  window scaffolding — and the claim has no OMOP-semantic grounding that
  a narrower version could rest on. Removed rather than redesigned.

- **`concept_standardization.source_concept_id_warning`.** Its claim ("do not
  filter on source concepts") contradicted OHDSI practice, where
  source-concept criteria are supported, and duplicated
  `standard_concept_enforcement`; the reliable sub-case (source concepts
  matched against the hierarchy) now lives in `standard_concept_enforcement`.

- **`data_quality.unmapped_concept_handling`.** It could never fire (every
  filter it collected was also treated as handling concept_id 0), and its
  premise was wrong: a filter on a specific concept id cannot match unmapped
  (concept_id = 0) rows, so there is nothing to handle.

- **`data_quality.standard_concept_null_handling`.** Its `= 'N'` and `= NULL`
  findings duplicated the ERRORs of
  `concept_standardization.standard_concept_value_validation` and of the
  NULL-comparison rule (since retired); the empty-string case moved to
  `standard_concept_value_validation`.

- **`anti_patterns.type_concept_id_domain_filter` and
  `domain_specific.visit_occurrence_type_domain`, merged into
  `concept_standardization.concept_domain_validation`.** A clinical
  `domain_id` filter on a concept joined to any `*_type_concept_id` is now an
  ERROR there. A missing filter on `visit_type_concept_id`, which
  `visit_occurrence_type_domain` reported as an ERROR, is no longer reported.

- **`anti_patterns.concept_ancestor_mixed_with_concept_relationship_redundantly`.**
  It fired on any query containing both `concept_ancestor` and an `'Is
  a'`/`'Subsumes'` filter, including legitimate source-vocabulary traversals,
  which `concept_ancestor` does not hold (it contains only standard and
  classification concepts). It never fired on either study corpus.

- **`concept_standardization.concept_class_id_ingredient_for_drug_grouping`.**
  It keyed on column-alias names and recommended joining `drug_exposure`
  directly to Ingredient-class concepts, which matches almost nothing because
  exposures are recorded at product level. It never fired on either study
  corpus.

- **`anti_patterns.standard_concept_or_with_classification`.** Admitting `'C'`
  alongside `'S'` cannot change a result that is joined to event
  `*_concept_id` columns (classification concepts are never stored there), and
  it is the standard way to reach ATC classes on the ancestor side of
  `concept_ancestor`. It fired once in 62,617 queries.

- **`data_quality.concept_name_whitespace`.** The rule told users to wrap
  `concept_name` in `TRIM()` because names "may contain trailing whitespace".
  OHDSI vocabulary builds check and clean untrimmed names before release,
  `concept_name` is `varchar(255)` with no blank-padding, and none of the
  ~6.6k concepts in the Eunomia GiBleed, Synthea and MIMIC vocabulary extracts
  has a padded Athena-sourced name. On the Study 1 agent corpus it fired on
  648 queries, and every finding was labelled a false observation. Exact name
  filters are still reported, by `anti_patterns.concept_name_lookup`.

- **`concept_standardization.concept_synonym_language_concept_id`.** It never
  fired on either Study 1 corpus. It also missed `=` and `LOWER()` lookups.
  Its claim was weak: a non-English synonym that matches an English search
  string still belongs to the concept being searched for. Name lookups on
  `concept_synonym_name` are now reported by
  `anti_patterns.concept_name_lookup`.

- **`anti_patterns.top_as_synthetic_data` removed.** It fired on any `LIMIT` +
  `ROW_NUMBER()` query reading a table. That includes the ordinary ranked
  top-N report (`ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) ... LIMIT 10`).
  The SQL Server number-generator idiom it targeted never appears in agent SQL
  (0 agent / 2 expert findings, both labelled false).

- **`data_quality.union_concept_id_domain_indicator` removed.** Its premise,
  that concept_id values from different domains are ambiguous in a UNION
  without a domain column, is wrong: OMOP CDM v5.4 `concept.concept_id` is
  unique across all domains, and the domain is recoverable from
  `concept.domain_id` (5/5 expert findings labelled false).

- **`joins.join_path_validation`.** It could not follow concept-set CTE chains
  or scalar-subquery lookups, so every agent-corpus finding (12) was a false
  observation; the defects it aimed at (cross joins with `concept`, joins on
  the wrong concept column) are reported by
  `joins.concept_join_validation`.

- **`domain_specific.measurement_duplicate_detection`.** CDM v5.4 gives
  measurement a surrogate key only; several measurements per person, concept
  and day are legitimate, so grouping by that "natural key" changes a query's
  meaning. The detector also treated any `COUNT(*)` in the statement as a
  measurement aggregate (51 expert findings, all false).

- **`domain_specific.drug_strength_validity_filter`.** In the CDM v5.4,
  drug_strength `valid_start_date` / `valid_end_date` / `invalid_reason`
  describe concept validity, not a history of formulations. OHDSI's own dose
  code (DrugUtilisation, DrugExposureDiagnostics) does not filter them, and
  all 14 study firings were schema probes or such code. Vocabulary-validity
  hygiene for drug_strength stays available in strict mode through
  `concept_standardization.invalid_reason_enforcement`.

- **`domain_specific.cost_paid_ingredient_cost_drug_specific`.** SUM/AVG skip
  the NULLs that non-drug cost rows carry, so the claimed distortion does not
  occur. The rule missed `cost_domain_id = CAST('Drug' AS TEXT)`, so both of
  its expert-corpus firings were false. The harmful shape, joining
  `cost_event_id` without a domain filter, is already an error in
  `domain_specific.cost_event_id_polymorphic_resolution`.

- **`domain_specific.drug_exposure_sig_parsing`.** It fired on any `LEFT(sig,
  n)` in a query containing an unrelated numeric CAST, and it missed regex
  `SUBSTRING(sig FROM '\d+')`. Its suggested fix (use drug_strength) cannot
  replace sig, because drug_strength has no frequency. It never fired on
  either study corpus.

- **`domain_specific.procedure_occurrence_quantity_semantics`.** It judged
  intent from the alias name alone (`SUM(quantity) AS procedure_count`) and
  never fired on either study corpus.

- **`domain_specific.year_of_birth_age_arithmetic`.** It flagged
  `YEAR(start_date) - year_of_birth`, the age expression OHDSI's Circe and
  FeatureExtraction emit. CDM v5.4 requires only `year_of_birth`, so this is
  the convention, not a defect. Before removal it fired on 64 agent and 94
  expert queries.

- **`temporal.future_information_leakage`.** It never fired on either study
  corpus. It also deferred to `temporal.observation_period_anchoring` in a
  case that rule no longer covers, so its own `example_bad` produced no
  finding from either rule.

- **`domain_specific.visit_event_temporal_validation`.** It flagged clinical
  events dated before the start of the visit they are linked to. CDM v5.4 does
  not require that: a condition's start date can be its onset, and medication
  history or pre-admission results can be linked to a visit and dated earlier,
  so the filter is a legitimate question rather than a defect. The comparison
  also wasn't tied to the alias joined on `visit_occurrence_id`, and `<=`
  (same-day events) fired. The only certain part of the claim, visit_detail
  dated outside its parent visit, is covered by
  `domain_specific.visit_detail_dates_within_parent_visit`.

- **`domain_specific.visit_detail_admitted_discharged_domain`.** It warned on
  every hard-coded `admitted_from_concept_id` / `discharged_to_concept_id`
  literal, including the correct ones (8870 Emergency Room, and 0, which CDM
  v5.4 prescribes for home / self-referral), because a static check cannot
  know a literal's domain. Wrong-domain filters through a concept join remain
  an ERROR in `concept_standardization.concept_domain_validation`.

- **`data_quality.fact_relationship_valid_concepts`.** It demanded
  `invalid_reason IS NULL` on every concept joined from fact_relationship,
  including pure label lookups. Applying that fix to recorded facts silently
  drops rows whose relationship or domain concept was later deprecated.
  `concept_standardization.invalid_reason_enforcement` already exempts lookup
  joins by design.

### Fixed

- **Error tier: the eight rules behind the Study 1 consensus false positives
  now follow column lineage and SELECT scope.** Two-rater adjudication of the
  0.3.0 error tier found 15 false positives from eight rules; each came from
  judging a column by its alias name instead of its source. A new helper,
  `core.helpers.column_origins` / `resolve_column_source`, follows a column
  through CTEs, derived tables, `*`/`t.*` expansion, casts and set operations
  (matching later UNION branches by position) to the base column it reads,
  settling unqualified references over several sources with the CDM catalogue.
  Per rule:
  - `concept_standardization.standard_concept_value_validation` no longer
    flags a derived column that reuses the name (`CASE … END AS
    standard_concept` recoding `'S'` to `'standard'`), and now does check a
    pass-through of `concept.standard_concept` from a subquery or CTE.
  - `joins.drug_exposure_drug_strength_join_validation` recognises the join
    through a CTE or dbplyr derived-table layers, and the semi-join
    `drug_concept_id IN (SELECT drug_concept_id FROM <drug_strength CTE>)`.
  - `joins.concept_join_validation` resolves join sides by lineage, so an
    alias reused in different CTEs (`std` for concept inside a CTE and for the
    CTE outside it) and renamed concept-id columns no longer collide.
  - `joins.person_id_join_validation` flags `person_id` joined to another CDM
    entity's key only (a CDM column other than `person_id`/`subject_id`, or a
    CDM key name such as `provider_id`); pipeline columns the CDM does not
    define, such as TreatmentPatterns' `subject_id_origin`, are no longer
    judged.
  - `concept_standardization.concept_domain_validation` keys domain filters by
    SELECT scope: a `c.domain_id` filter in one CTE no longer counts for
    another CTE's alias `c`, which produced a false domain-mismatch ERROR.
  - `domain_specific.visit_detail_visit_occurrence_reference` no longer fires
    when every SELECT reading both tables is cut to its first row per
    partition (`ROW_NUMBER() OVER (PARTITION BY …) AS rn` … `WHERE rn = 1`),
    so the unlinked join cannot fan rows out.

  Re-run on every adjudicated Study 1 census example, all 15 false positives
  are cleared and all 90 true-positive examples still fire.

- **Schema resolver: unqualified columns resolve in their own scope.**
  `data_quality.schema_validation` inferred the table of an unqualified
  column from *every* table in the statement, so in `INSERT INTO cohort
  (...) SELECT person_id, start_date FROM #final_cohort` the SELECT-side
  columns were attributed to the INSERT target and reported as missing
  (3,405 findings on one Circe footer in Study 1), and columns selected
  from a derived table or CTE were attributed to the one CDM table joined
  next to it (645 findings). Resolution now uses the column's enclosing
  SELECT (then outer scopes for correlated references) and is skipped
  when that scope reads from any source whose columns are unknown (CTE,
  derived table, temp or non-CDM table). `alias.*` is no longer treated
  as a column named `*` (98 findings), and T-SQL temp tables
  (`#name`, flagged `temporary` by the parser) are never reported as
  missing CDM tables. Genuine missing columns on single-table or
  qualified references are still reported.

- **`joins.person_id_join_validation` accepts `subject_id`.**
  `person_id = cohort.subject_id` (and the same column on cohort-derived
  temp tables) is the CDM's own cohort linkage, not a mismatched key —
  174 false positives on expert SQL. Other mismatches (`person_id =
  visit_occurrence_id`) are still flagged.

- **`concept_standardization.standard_concept_enforcement` (strict mode)
  recognises codeset CTEs.** A CTE or derived table built from
  `concept_ancestor` (`WITH x AS (SELECT descendant_concept_id AS concept_id
  FROM concept_ancestor WHERE ancestor_concept_id = N UNION SELECT N)`) joined
  or `IN`-filtered against a standard `*_concept_id` is standard by CDM
  definition, exactly like the direct subquery form the rule already exempted.
  Literal-only CTEs are not treated as codesets.

- **Unrendered `{placeholder}` templates are detected.** Identifier-only
  curly-brace placeholders (`drug_concept_id = {naproxen_id}`) now trigger
  `meta.unrendered_sqlrender_template` like `@param` placeholders do; ODBC
  escapes and JSON literals are not affected.

- **`data_quality.vocabulary_table_protection` no longer flags reads of
  vocabulary tables inside write statements.** Target extraction previously
  swept every table referenced under a DML node, so `INSERT INTO codesets
  SELECT … FROM concept` — the canonical Circe cohort-SQL shape — was
  reported as an INSERT *on* `concept`, and `UPDATE … FROM concept` /
  `MERGE … USING concept` misfired the same way. The rule now reports only
  the actual write target (`INSERT`/`UPDATE`/`DELETE`/`MERGE` primary
  target, plus each table listed in a multi-table `TRUNCATE`). Statements
  that genuinely write to a vocabulary table are unaffected. Regression
  tests cover the read-only INSERT…SELECT, UPDATE…FROM, and a MERGE whose
  write target is a vocabulary table.

- **`dialect="auto"` (the API/MCP default) no longer silently disables
  cross-statement local-table scoping.** `run_validation` passed the raw
  `"auto"` sentinel to `collect_locally_defined_tables` /
  `collect_locally_defined_unqualified_tables`, which can't parse with a
  pseudo-dialect and fail open to an empty set — so scratch tables
  created earlier in a batch were flagged as unknown OMOP tables whenever
  the caller relied on the default dialect. `"auto"` is now resolved via
  `detect_dialect` once on the whole submission (mirroring the CLI)
  before the collectors run; as a side effect every statement is
  validated under one consistently detected dialect (previously each
  statement re-detected independently) and the API response's `dialect`
  field reports the dialect actually used instead of echoing `"auto"`.

- **The JSON log formatter now serializes every field passed via
  `extra=`.** `JSONFormatter` previously emitted only a fixed allowlist
  (`duration_ms`, `rule_id`, `violation_count`), silently dropping
  everything else the API attaches — `request_id`, `sql_hash`, `dialect`,
  `strict`, `query_count`, `client`, error/warning counts. In practice
  that made request-ID correlation across log lines impossible even
  though the middleware generates and returns `x-request-id` on every
  response. All non-reserved record attributes are now included
  (non-JSON-serializable values fall back to `repr()`), and the API log
  calls in `/v1/validate` and the UI now also carry `request_id`.

- **CLI: removed the phantom `--categories` choices `analytics`,
  `performance`, and `schema`.** These were accepted by argparse but
  matched zero registered rules, so e.g. `fastssv --categories schema
  file.sql` ran nothing and reported `Validation VALID` — a silently
  misleading false-clean. The valid choices are the six real categories
  (`anti_patterns`, `concept_standardization`, `data_quality`,
  `domain_specific`, `joins`, `temporal`).

- **CLI: a missing or unreadable SQL file now prints a one-line
  `error: cannot read SQL from …` message and exits with code 2** instead
  of dumping a raw `FileNotFoundError` traceback. Exit codes are now:
  0 valid, 1 invalid, 2 input/usage error.

- **`data_quality.schema_validation` no longer flags `CREATE TABLE …
  AS SELECT` targets as nonexistent OMOP tables.** The rule walks every
  `sqlglot.exp.Table` node and previously validated the CTAS target the
  same way as a `FROM` reference, so OHDSI Achilles SQL — which emits
  `CREATE TABLE scratch.tmpach_N AS SELECT … FROM cdm.<omop_table>` —
  produced false-positive errors like `Table 'tmpach_0' does not exist
  in OMOP CDM 5.4 schema`. Targets of `CREATE` are now skipped (the
  table is being defined, not referenced); source tables inside the
  `SELECT` are still validated, so `CREATE TABLE scratch.x AS SELECT …
  FROM cdm.persn` still errors on the misspelled `persn`. Regression
  guard: `tests/test_rules.py::TestSchemaValidation::test_ctas_target_not_validated_as_omop_table`
  and `…::test_ctas_still_validates_source_tables`.

- **`data_quality.schema_validation` is now scope-aware for column
  resolution.** `comprehensive_schema_validation` previously resolved
  every `Column` ref through the global `extract_aliases` dict, which
  is a flat name -> table map. When the same alias was used across
  CTEs (for example `omop.concept c` inside one CTE and `cond_occ c` in
  the outer SELECT), the dict collapsed to last-write-wins and the
  rule misattributed columns — yielding spurious errors like
  _"Column 'person_id' does not exist in table 'concept'"_ on
  perfectly valid SQL. The rule now walks each `Column`'s enclosing
  `Select` scope to build a scope-local alias map (correlated outer
  scopes are still visible; inner scopes shadow). On a representative
  11k-query benchmark this drops `schema_validation` errors from 341
  to 131. The new helpers (`_local_aliases`, `_resolve_column_table`)
  are private to the rule, since the global `extract_aliases` is still
  the right contract for rules that work on join-condition pairs.
  Regression test:
  `tests/test_rules.py::TestSchemaValidation::test_alias_collision_across_ctes_does_not_false_positive`.

- **`data_quality.schema_validation` no longer flags `DATEDIFF` unit
  keywords as missing columns.** sqlglot parses `DATEDIFF(day, x, y)`
  with `day` as a `Column` node (rather than a literal/Var). Without
  filtering, the schema check produced
  _"Column 'day' does not exist in table 'condition_occurrence'"_ for
  every query that used `DATEDIFF`/`DATEADD`/`DATETRUNC`/`EXTRACT` etc.
  The rule now skips unqualified `Column` nodes whose name is a
  recognised time-unit keyword (`day`, `month`, `year`, `hour`,
  `minute`, …) and whose parent is one of the date-arithmetic
  function types. Regression test:
  `tests/test_rules.py::TestSchemaValidation::test_datediff_unit_keyword_not_treated_as_column`.

- **`joins.visit_occurrence_inner_join_validation` no longer warns on
  non-VOID join keys.** OMOP_043's premise is that an `INNER JOIN
  visit_occurrence ON event.visit_occurrence_id = vo.visit_occurrence_id`
  silently drops the 20–60% of clinical events with NULL
  `visit_occurrence_id`. That premise only applies when the JOIN key is
  actually `visit_occurrence_id` — but `_is_vo_join` previously fired
  on any INNER JOIN that *involved* `visit_occurrence`, regardless of
  key. The canonical cohort-builder shape
  `cohort c JOIN visit_occurrence vo ON c.person_id = vo.person_id`
  (where `person_id` is NOT NULL on both sides and no rows can be
  dropped due to NULL visit linkage) was therefore flagged as a
  potentially-data-losing INNER JOIN. The rule now gates on a new
  `_join_on_uses_visit_occurrence_id` check that walks `exp.EQ` nodes
  in the ON clause and only returns True when `visit_occurrence_id`
  appears on at least one side of an *equality* (not just anywhere in
  the ON clause — an earlier draft walked every `exp.Column` and
  reintroduced the same false positive class via non-equality
  predicates like `vo.visit_occurrence_id IS NOT NULL` or
  `vo.visit_occurrence_id > 0`; caught in code review). Mixed-key joins
  (`ON co.visit_occurrence_id = vo.visit_occurrence_id AND co.person_id = vo.person_id`)
  still fire because VOID linkage is in play. (The comma-join branch was later
  replaced; see the `visit_occurrence_inner_join_validation` entry under
  Changed.)
  Regression tests:
  `tests/test_rules.py::TestVisitOccurrenceInnerJoinValidation::test_omop_043_person_id_join_to_visit_does_not_fire`,
  `…test_omop_043_mixed_keys_still_fires`,
  `…test_omop_043_void_in_non_equality_predicate_does_not_fire`,
  `…test_omop_043_void_comparison_predicate_does_not_fire`,
  and `…test_omop_043_void_equality_to_literal_still_fires`.

- **OMOP-table-targeting rules no longer false-positive on user CTEs that
  shadow OMOP table names.** `core.helpers.has_table_reference` is the
  gating check used by ~50 rules that key off specific OMOP tables
  (`cohort`, `person`, `concept`, the join family, etc.) — and it
  previously treated `WITH cohort AS (...) ... FROM cohort c` as a
  reference to the OMOP `cohort` table, so a CTE named `cohort` with
  its own `person_id` column would trip
  `joins.cohort_clinical_join_validation` (and the like) with messages
  like _"Invalid FK join between cohort and condition_occurrence:
  cohort.person_id = …"_ even though the SQL was perfectly valid. The
  helper is now CTE-aware *per Table node*: for each unqualified
  reference, the helper walks the reference's ancestors and checks
  every WITH clause hung off them; if a CTE with the matching name
  is visible from that scope (and the reference isn't inside the CTE's
  own body — non-recursive self-reference rule), the reference is
  treated as the CTE. The walk checks *every* ancestor's WITH slot,
  not just `Select` nodes — sqlglot attaches the WITH of
  `WITH x AS (…) SELECT … UNION SELECT … FROM x` to the `Union` node
  and that of `WITH x AS (…) INSERT INTO t SELECT … FROM x` to the
  `Insert`, so a Select-only walk (an earlier draft, caught in code
  review) missed both shapes and treated their CTE references as real
  OMOP tables. Schema-qualified references (`mydb.cohort`)
  always bypass shadowing per standard SQL scoping. The per-node
  walk avoids over-approximating CTE visibility: a CTE named `cohort`
  defined inside a nested subquery does NOT shadow an outer
  `FROM cohort` (an earlier draft of this fix used a tree-global
  `collect_cte_names` intersection and would have silently suppressed
  in that case — caught in code review). A new `collect_cte_names(tree)`
  helper is also exported for callers that *do* want the tree-global
  set (e.g. `anti_patterns.cte_shadows_omop_table`, which flags any
  shadow no matter how nested) — its docstring is explicit about the
  over-approximation. `extract_aliases` is intentionally unchanged —
  rules with their own CTE-bridge logic depend on its current bare-name
  semantics.
  Regression tests added in `tests/test_helpers_cte.py` (covering
  outer-WITH shadow, schema-qualified bypass, nested-WITH non-shadow,
  self-reference resolution, and the WITH-on-UNION / WITH-on-INSERT
  attachment shapes) and
  `tests/test_rules.py::TestCohortClinicalJoinValidation`.

- **`deploy/.env.example` documents `FASTSSV_API_BEHIND_PROXY`.** The
  reverse-proxy toggle added with HTTPS support is wired through
  `deploy/docker-compose.yml` (compose-level default `true`) and read by
  `Settings.behind_proxy` in `src/fastssv/api/config.py` (in-code default
  `false`), but it was missing from `deploy/.env.example`. Operators
  copying the example file as a config reference now see the variable,
  what it does (trust `X-Forwarded-*` from an upstream TLS terminator so
  generated URLs reflect the external scheme/host), and the
  compose-vs-code default split. No behaviour change — purely a
  documentation gap fix.

- **`data_quality.schema_validation` keeps checking a table when a CTE
  shares its name.** A CTE shadows an *unqualified* name only, but the
  rule tested every reference against a flat set of CTE aliases, so
  `WITH drug_exposure AS (…) SELECT de.invalid_reason FROM
  omop.drug_exposure de` silently skipped column checking on the real
  table — `drug_exposure` was "a CTE". AI-written OMOP SQL names CTEs
  after CDM tables constantly, so this cost sensitivity on exactly the
  corpus the rule matters most for. Alias binding now treats a name as
  CTE-bound only when the reference carries no schema/catalog prefix
  (new public helper `core.helpers.is_schema_qualified`). The
  *table-existence* check stays name-based on purpose: a CTE name is
  never an OMOP table wherever it is defined, and Circe emits
  `codesets` / `qualified_events` in every generated cohort. Regression
  guards: `tests/test_rules.py::TestScopeAndProvenanceFixes::
  test_schema_qualified_table_checked_inside_same_named_cte`,
  `…::test_unqualified_reference_still_shadowed_by_cte`,
  `…::test_cte_name_never_reported_as_missing_table`.

- **`joins.cohort_clinical_join_validation` no longer flags INSERT targets,
  unrelated CTEs, or the `cohort → person → clinical` bridge written with
  an unqualified `subject_id`.** Three separate over-fires: (i) `INSERT INTO
  cohort SELECT … FROM condition_occurrence` counted the write target as an
  unjoined cohort source (24/24 expert findings in Study 1); (ii) a CTE
  profiling `observation_period` and another CTE reading `cohort` were
  treated as co-used even though no SELECT reads both; (iii)
  `cohort INNER JOIN person ON subject_id = person.person_id INNER JOIN
  observation_period ON observation_period.person_id = person.person_id`
  lost the bridge because the bare `subject_id` resolved to no table. The
  rule now excludes write targets, only considers clinical tables read in
  the same SELECT scope as a cohort source (base table or derived
  `… AS cohort`), and attributes an unqualified `subject_id` to the cohort
  side. 16 → 0 distinct expert statements.

- **`joins.clinical_person_id_linkage_validation` recognises every
  person-scoped link the CDM defines.** The connectivity graph used to admit
  only `person_id = person_id` edges (plus orphan-FK anti-join checks), so
  `condition_occurrence co JOIN visit_occurrence vo ON co.visit_occurrence_id
  = vo.visit_occurrence_id`, which cannot cross patients, was reported. Two
  clinical tables now count as linked when an equality ties person-scoped keys
  of the same family (`person_id`; `visit_occurrence_id` /
  `preceding_visit_occurrence_id`; `visit_detail_id` /
  `preceding_visit_detail_id` / `parent_visit_detail_id`) or a polymorphic
  event key (`measurement_event_id`, `observation_event_id`, `note_event_id`)
  with an event primary key. The link may run through CASTs, through any other
  table or CTE (`co.person_id = c.subject_id AND de.person_id = c.subject_id`)
  or through the same literal (`co.person_id = 42 AND p.person_id = 42`); the
  next-visit join `nxt.preceding_visit_occurrence_id = vd.visit_occurrence_id`
  is no longer an ERROR. Joins on dates or unrelated columns still fire. The
  rule's `example_bad`, a `visit_occurrence_id` join it already treated as
  linked, is replaced by a date join that it reports. 8 → 0 distinct expert
  statements.

- **`anti_patterns.type_concept_id_misuse` flags only literals that are not
  Type Concepts.** The rule exists to catch a clinical concept written into a
  `*_type_concept_id` column (`condition_type_concept_id = 201826`, an ERROR),
  yet it fired on every filter of that column, including `drug_type_concept_id
  IN (38000175, 38000180, 43542356)`: restricting a query to a data source,
  which the rule's own docstring lists as legitimate. It now checks `=` / `IN`
  comparisons against an embedded list of every concept in the 'Type Concept'
  domain (standard, legacy and deprecated; Athena v5.0 27-AUG-25) plus 0. An
  intermediate version hand-coded id ranges and missed standard Type Concepts
  such as 32809 (Case Report Form), 32883–32886, 703249 and 705183, so
  `observation_type_concept_id = 32883` (Survey) was a false ERROR.
  Comparisons with a column, a subquery or a `!=` are not flagged, nor are
  completeness metrics (`SUM(CASE WHEN x_type_concept_id IS NULL …)`) or a
  join that decodes the type name (`JOIN concept t ON
  co.condition_type_concept_id = t.concept_id`); a wrong `domain_id` on that
  concept row is reported by
  `concept_standardization.concept_domain_validation`. A Type Concept from
  another table's legacy family (`condition_type_concept_id = 38000280`, an
  'Observation Type', the rule's one adjudicated true positive in Study 1) is
  a WARNING, reported only when every compared id is foreign; the 32xxx block
  and concepts without a table-specific legacy class are never reported. An
  `IN` list that mixes Type Concepts with clinical ids is a WARNING without an
  automatic patch; the REPLACE patch moves the predicate onto the clinical
  column only when every compared id is a non-Type concept. 8 → 0 distinct
  expert statements.

- **`data_quality.canonical_string_value_validation` only checks columns
  that resolve to an OMOP table.** Matching by column name alone flagged
  `domain_id`, `vocabulary_id` and `standard_concept` values on CTEs and
  result tables that happen to reuse the names (18 distinct expert
  statements, 0 true positives). The column must now resolve to a CDM table
  in scope. 18 → 0.

- **`domain_specific.visit_detail_visit_occurrence_reference` requires co-use
  in one SELECT scope.** It and the since-merged
  `joins.death_visit_occurrence_join_validation` gated on whole-statement
  presence of the two tables, so UNION branches, separate derived tables and
  INSERT targets that merely mention both were flagged. They now use the
  shared `tables_co_used` helper. 7 → 0 and 4 → 1 expert statements; the
  survivor (FeatureExtraction's per-person `visit_detail × visit_occurrence`
  pairing) is a legitimate flag.

- **Shared helpers.** `core.helpers` gains `select_local_aliases` (alias →
  base table for one SELECT's own FROM/JOIN, immune to alias reuse across
  scopes) and `select_is_profiling_read` (the peek / aggregate-only /
  grouped-by-own-columns test above), alongside the earlier
  `select_scope_tables` / `tables_co_used`.

- **`domain_specific.vocabulary_relationship_boolean_comparison` follows the
  CDM v5.4 type of the relationship flags.** `relationship.is_hierarchical`
  and `defines_ancestry` are varchar(1) holding '1' / '0'. The rule used to
  raise an ERROR on the correct `is_hierarchical = '1'` and accept `= 1` / `=
  TRUE`. It now accepts '1' / '0', reports values that can never match
  ('true', 'Y', 2, -1) in every dialect, and in PostgreSQL also reports
  integer or boolean operands and bare `WHERE is_hierarchical`, which
  PostgreSQL rejects as type errors. Lineage-aware, so a derived column
  reusing the name is not checked. Integer and boolean operands are also
  reported on BigQuery, which has no implicit INT64 → STRING coercion, and
  `is_hierarchical <> 'Y'` is described as filtering nothing rather than
  matching no rows. A CTE or derived table that projects the flag through a
  cast (`is_hierarchical::int AS is_hierarchical`) ends the check, since the
  value is no longer varchar.

  `domain_specific.vocabulary_relationship_boolean_comparison` looks through
  `LOWER` / `UPPER` / `TRIM` and checks `LIKE` and `IS TRUE`.
  `LOWER(is_hierarchical) = 'yes'` and `is_hierarchical LIKE 'Y'` (patterns
  that neither '1' nor '0' matches) are now reported in every dialect, and
  `is_hierarchical IS TRUE` / `IS FALSE` is reported on PostgreSQL and
  BigQuery, where it is a type error.

- **Metadata-concept foreign keys are no longer reported as invalid joins.**
  `vocabulary.vocabulary_concept_id`, `domain.domain_concept_id`,
  `concept_class.concept_class_concept_id` and
  `concept_synonym.language_concept_id` are foreign keys to
  `concept.concept_id` in CDM v5.4. The vocabulary join rule raised an ERROR
  on the first, the domain join rule warned on the second, the concept_class
  rule warned on the third, and `joins.concept_synonym_join_validation` raised
  an ERROR on the fourth (for example when looking up a synonym's language
  name). `joins.concept_metadata_join_validation`, which now holds the
  vocabulary, domain and concept_class checks, and
  `joins.concept_synonym_join_validation` accept these joins and still flag
  wrong pairs such as `concept.vocabulary_id = vocabulary.vocabulary_name`.

- **`joins.concept_join_validation` no longer emits WARNINGs.** Its
  `concept_code`-without-`vocabulary_id` path moved to
  `anti_patterns.concept_code_requires_vocabulary_id` (see Changed). The rule
  now only raises its ERROR for `*_concept_id` columns joined to a concept
  column other than `concept_id`.

  `joins.concept_join_validation` checks every SELECT and sees through casts
  and `IN` subqueries. Previously a `WHERE` equality was read only at the top
  of the statement, so the defect inside a CTE or a `UNION` branch, behind
  `CAST(... AS VARCHAR)` (which makes the join run and match nothing) or in
  `IN (SELECT concept_code …)` was missed. The concept-id side is any column
  whose CDM v5.4 foreign key is `concept.concept_id`. Columns of the
  vocabulary reference tables and of `concept_synonym` are left to their own
  rules, so one predicate is no longer reported by two join rules.

- **Correlated column references no longer bind to a same-named alias in
  another scope.** Lineage resolution (`resolve_column_source`) now looks for
  a qualifier in the enclosing SELECTs; if no SELECT in that chain defines it,
  the reference resolves to no table instead of falling back to the
  statement-wide alias map. Previously `WITH names AS (SELECT c.concept_id
  FROM concept c) SELECT … FROM condition_occurrence c WHERE EXISTS (…
  o.observation_concept_id = c.condition_concept_id)` was reported as an
  invalid concept join (ERROR). This applies to every rule that uses the
  helper.

- **`joins.concept_metadata_join_validation` and
  `joins.concept_synonym_join_validation` check each SELECT's joins
  separately.** They no longer read an outer `visit_occurrence v` or
  `drug_exposure d` as `vocabulary` or `domain` when a subquery reuses the
  alias. In `joins.concept_synonym_join_validation`, an extra predicate on a
  pair already joined by `concept_id` (e.g. `AND cs.concept_synonym_name =
  c.concept_name`) is a filter and is no longer an ERROR.

- **`data_quality.schema_validation` resolves unqualified columns outward like
  PostgreSQL.** An unqualified column missing from a subquery's only CDM table
  used to be reported against that table even when it was a correlated
  reference to the outer query (`EXISTS (SELECT 1 FROM observation_period op
  WHERE … condition_start_date BETWEEN …)`, a scalar subquery reading the
  outer `year_of_birth`, `UPDATE cohort SET … (SELECT … WHERE op.person_id =
  subject_id)`). It is now reported only when no visible enclosing scope
  (outer SELECTs, the UPDATE/DELETE target) can supply the name; CTE bodies
  and non-LATERAL derived tables do not see the outer query, so those still
  report. An outer scope reading a pipeline/temp table whose columns are
  unknown suppresses the finding. Tables in the `information_schema`,
  `pg_catalog` and `sys` system catalogues are no longer reported as missing
  CDM tables.

- **`joins.drug_exposure_drug_strength_join_validation` no longer reports
  "used but not joined" on linked queries.** The check runs per SELECT scope
  (a UNION of separate counts is not a join) and treats `USING` / `NATURAL`
  joins, CAST-wrapped keys and transitive links (`de.drug_concept_id =
  c.concept_id … ds.drug_concept_id = c.concept_id`, or through
  `concept_ancestor`) as joins. A drug_exposure/drug_strength pair with no
  predicate relating them in one scope still errors. A correlated reference
  from a subquery (`ds.drug_concept_id IN (SELECT … WHERE
  ca.ancestor_concept_id = de.drug_concept_id)`) also counts as a link.

- **`joins.person_id_join_validation` ignores polymorphic foreign keys.**
  `fact_relationship.fact_id_1/2`, `episode_event.event_id` and the
  `*_event_id` columns (cost, note, observation, measurement) point at a table
  named by a companion column, which may be person, so joining them to
  `person_id` is no longer an error. The REPLACE autofix (`<other>.person_id`)
  is emitted only when the other table has a `person_id` column; otherwise the
  fix is prose.

- **An OR-ed join key no longer counts as a required concept join** in
  `concept_standardization.domain_vocabulary_validation`,
  `concept_standardization.unit_vocabulary_validation` and
  `concept_standardization.concept_domain_validation`. With `JOIN concept c ON
  c.concept_id = co.condition_concept_id OR c.concept_id =
  co.condition_source_concept_id`, rows also arrive through the second branch,
  so a source-vocabulary or other-domain filter does not empty the join. These
  rules previously reported such joins as an ERROR.

- **`joins.visit_occurrence_id_join_validation` only reports joins to a
  different CDM identifier.** `visit_occurrence_id` may be equated with
  `preceding_visit_occurrence_id` (also a visit id) and with the polymorphic
  event keys (`cost.cost_event_id`, `fact_relationship.fact_id_1/2`,
  `location_history.entity_id`, `episode_event.event_id`, `*_event_id`), whose
  domain qualifier is checked by the polymorphic-resolution rules. CTE and
  derived-table columns the CDM does not define are no longer judged.
  Previously `cost.cost_event_id = visit_occurrence.visit_occurrence_id` with
  `cost_domain_id IN ('Visit', …)` and `vd.visit_occurrence_id =
  vo.preceding_visit_occurrence_id` were ERRORs.

- **`joins.preceding_visit_occurrence_validation` accepts any visit id as the
  partner of `preceding_visit_occurrence_id`.** Any table's
  `visit_occurrence_id` (e.g. a visit_detail's parent visit), another
  `preceding_visit_occurrence_id`, or a polymorphic event key is valid; only a
  different CDM identifier (`person_id`, `visit_detail_id`, …) is an ERROR. A
  `preceding_visit_occurrence_id` read from a CTE is not judged. Previously
  `vd.visit_occurrence_id = vo.preceding_visit_occurrence_id` was an ERROR
  ("must join to visit_occurrence").

- **`data_quality.schema_validation` no longer blames DML columns on an
  unrelated table.** Outside any SELECT (UPDATE SET/WHERE, DELETE WHERE,
  MERGE, INSERT … ON CONFLICT), an unqualified column used to be attributed to
  the only CDM table anywhere in the statement: `DELETE FROM cohort_tmp WHERE
  subject_id NOT IN (SELECT person_id FROM person)` reported "subject_id does
  not exist in person". It is now checked only when the statement's sole row
  source (its target, with no UPDATE … FROM / DELETE … USING / MERGE … USING
  source) is a CDM table. Column lists on CTEs and derived tables (`WITH
  params(target_concept) AS (…)`, `AS params(target_concept)`) are honoured
  when resolving correlated names, names in the ORDER BY of a UNION /
  INTERSECT / EXCEPT are treated as output columns, and PostgreSQL system
  columns (`ctid`, `xmin`, `xmax`, `cmin`, `cmax`, `tableoid`) are not
  reported under the postgres dialect.

- **Rule example concept ids corrected.** The `/rules` examples of three rules
  used concept ids that did not mean what the example said:
  `episode_concept_id = 32528` (the non-standard "Disease First Occurrence";
  now 32533, "Disease Episode"), and `relationship_concept_id = 44818859` and
  `581411` in the two fact_relationship rules (a SNOMED vocabulary
  relationship and "Measurement to Observation"; now 46233683, "Systolic to
  diastolic blood pressure measurement", and 581436, "Parent to Child
  Measurement").

- **`joins.clinical_pk_cross_join_validation` no longer flags polymorphic
  keys.** The CDM v5.4 polymorphic keys `measurement_event_id`,
  `observation_event_id`, `note_event_id`, `cost_event_id`,
  `fact_relationship.fact_id_1` / `fact_id_2`, `episode_event.event_id` and
  `location_history.entity_id` hold another table's primary key by design.
  Previously only `cost_event_id` was exempt, so `m.measurement_event_id =
  co.condition_occurrence_id` and `fr.fact_id_1 = co.condition_occurrence_id`
  were reported as ERRORs. Whether the companion `*_event_field_concept_id` /
  `domain_concept_id_N` names the right table remains the job of
  `domain_specific.event_field_polymorphic_resolution` and the
  fact_relationship rules. Joins between two different entities' keys
  (`co.condition_occurrence_id = vo.visit_occurrence_id`) are still ERRORs.
  Study 1 corpora: 0 → 0 findings (agent and expert).

- **`joins.care_site_id_join_validation` checks the CDM v5.4 tables that carry
  `care_site_id`.** These are `person`, `visit_occurrence` and `visit_detail`.
  `provider` is the fourth such table, and it stays with
  `joins.provider_care_site_join_validation`, so a bad provider join is
  reported once. The rule's table list used to include `condition_occurrence`,
  `procedure_occurrence`, `drug_exposure`, `device_exposure`, `measurement`,
  `observation` and `specimen`. None of these has a `care_site_id`, so any
  join from them to care_site was reported with a fix pointing at a column
  that does not exist. Study 1 corpora: 0 → 0 findings (agent and expert).

- **`domain_specific.measurement_operator_concept_validation` reports only
  operator tests that can never be true, with the operators named correctly.**
  CDM v5.4 restricts `measurement.operator_concept_id` to the 'Meas Value
  Operator' domain. The rule used to raise an ERROR on any comparison with an
  id outside a hard-coded list of five, so `operator_concept_id = 0`, `> 0`,
  `<> 0`, `NOT IN (…, 0)` and the common `operator_concept_id IS NULL OR
  operator_concept_id IN (0, 4172703)` were all blocked, and the domain
  members 4172702 and 4329646 were rejected. It now judges only `=` and `IN`,
  accepts every standard concept of the domain (generated from the
  vocabulary), 0, and any list that contains a valid value. A row filter that
  can never match stays an ERROR; the same test inside CASE, under OR or in an
  outer join's ON is a WARNING, because only that branch is dead. Columns are
  followed through derived tables and casts, and quoted ids (`= '8554'`) are
  read. Its messages and suggested fix had `=` and `>=` swapped: 4172703 is
  `=` and 4171755 is `>=`.

- **CTEs named like the table they read no longer crash lineage.** In `WITH
  person AS (SELECT * FROM person ...)` the inner `person` is the base table;
  the CTE lookup used to return the CTE itself and recurse until
  `RecursionError` (reported as `meta.rule_execution_error` by
  `data_quality.column_type_validation`). Inside a CTE body only earlier CTEs,
  and itself under WITH RECURSIVE, are now visible.

## [0.3.0] - 2026-05-06

### Changed

- **`parse_sql` is now `lru_cache`-d on `(sql, dialect)`.** A single
  `validate_sql_structured` call dispatches to ~150 registered rules,
  each of which calls `parse_sql(sql, dialect)` independently — so the
  same SQL was being tokenised and parsed by `sqlglot.parse` dozens of
  times per request. Caching collapses that to one parse per unique
  (sql, dialect) pair (measured 73 hits / 74 calls = 98.6% hit ratio
  on a representative validation). `maxsize=128` keeps the cache
  bounded; the function's contract is unchanged. Safe because rules
  treat the returned AST as read-only — a sweep of `rules/` and
  `core/` confirmed no AST mutation. If you ever introduce mutation in
  a rule, drop the cache or deep-copy the trees.

- **`domain_specific/` layout normalised; off-pattern rules moved into
  table subpackages.** Three rules previously lived flat at the top of
  `src/fastssv/rules/domain_specific/` despite being table-specific,
  contradicting the documented "table rules under `<table>/`,
  cross-cutting rules flat" convention:

  | Old path | New path |
  | --- | --- |
  | `domain_specific/cost_currency_concept_id.py` | `domain_specific/cost/cost_currency_concept_id.py` |
  | `domain_specific/cost_paid_ingredient_cost_drug_specific.py` | `domain_specific/cost/cost_paid_ingredient_cost_drug_specific.py` |
  | `domain_specific/dose_era_cross_unit_comparison.py` | `domain_specific/dose_era/dose_era_cross_unit_comparison.py` |

  `rule_id`s are unchanged (the directory nesting under
  `domain_specific/<table>/` is organisational and never appears in the
  id), so anyone calling `get_rule("domain_specific.cost_currency_concept_id")`
  etc. is unaffected. Internally, `domain_specific/__init__.py` now
  imports each table subpackage as a side-effect-only module
  (`from . import cohort, condition, …`) instead of mixing
  `from .<table> import *` with explicit class imports for the off-pattern
  trio. Direct submodule imports
  (`from fastssv.rules.domain_specific.cost.cost_currency_concept_id import …`)
  are the documented contract; nothing in the tree imported these classes
  via the parent package.

- **Validation helpers consolidated at the package root.** The six
  `validate_<category>` helpers (legacy string-error API) now live in
  `src/fastssv/__init__.py` next to `validate_sql` /
  `validate_sql_structured`; `src/fastssv/rules/__init__.py` is now a
  pure side-effect module that triggers `@register` for every rule.
  The documented import is and always was `from fastssv import
  validate_<category>`. `from fastssv.rules import validate_<category>`
  no longer resolves — only `tests/test_rules.py` used that form, and it
  has been updated.

- **`deploy/.dockerignore` removed.** `deploy/docker-compose.yml` builds
  with `context: ..` (the repo root), so Docker reads the root
  `.dockerignore` and never consulted `deploy/.dockerignore`. The dead
  file was inconsistent with the live one (different exclusion lists)
  and an obvious tripwire for future readers. Per-Dockerfile ignore
  files would need the BuildKit-recognised name `Dockerfile.dockerignore`
  in `deploy/`, which is not what was there.

- **BREAKING: two `domain_specific` rule IDs renamed to match the
  documented 2-segment `<category>.<rule_name>` format.** Two legacy
  rules predated the convention and used a 3-segment id derived from
  their table subpackage:

  | Old `rule_id` | New `rule_id` |
  | --- | --- |
  | `domain_specific.note.note_nlp_snippet_misuse` | `domain_specific.note_nlp_snippet_misuse` |
  | `domain_specific.vocabulary.relationship_boolean_comparison` | `domain_specific.vocabulary_relationship_boolean_comparison` |

  The README documents `<category>.<rule_name>` as the stable format,
  and pre-1.0 minor releases explicitly allow breaking rule-set changes
  (see the "Stability" section), so these are normalised in this
  release rather than left to fester. The `vocabulary` rule's source
  file was also renamed
  `vocabulary/relationship_boolean_comparison.py` →
  `vocabulary/vocabulary_relationship_boolean_comparison.py` so its
  filename matches the new id and the table-prefix convention used by
  every other nested `domain_specific` rule (`measurement_*.py`,
  `drug_*.py`, `visit_*.py`, …). **Anyone calling
  `get_rule("domain_specific.note.note_nlp_snippet_misuse")` or
  `get_rule("domain_specific.vocabulary.relationship_boolean_comparison")`
  must update to the new ids** — there is no compatibility shim.
  Saved JSON validation reports that reference the old ids will need
  to be regenerated. Documentation in
  [`docs/rules_reference.md`](docs/rules_reference.md) and the rule's
  unit tests in `tests/test_rules.py` were updated in lockstep.

### Added

- **`SECURITY.md` with a private-reporting policy.** New file at the repo
  root pointing reporters at GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
  flow as the primary channel (no email exposure needed). Documents the
  pre-1.0 supported-versions policy (latest minor only), realistic
  response targets (7-day acknowledgement, 21-day assessment, 90-day fix
  or coordinated disclosure), explicit scope (in: `src/fastssv/`, the
  optional `api/` service, the `deploy/` image, `.github/workflows/`;
  out: third-party dependency CVEs, user-supplied SQL behaviour,
  downstream deployment configuration), and a coordinated-disclosure
  default with reporter credit. `CONTRIBUTING.md` now points at it from
  the "Reporting issues" paragraph.

- **`CONTRIBUTING.md` with an explicit AI-assisted PR policy.** The new
  file at the repo root sets the contract for both human-authored and
  AI-assisted contributions. AI-assisted PRs are welcome subject to two
  standards: (1) Linux-kernel-style disclosure and accountability —
  human submitter signs the DCO, an `Assisted-by: AGENT_NAME:MODEL_VERSION`
  trailer per the kernel's [`coding-assistants`](https://docs.kernel.org/process/coding-assistants.html)
  format, prompts and scope disclosed in the commit body, and reviewers
  applying scrutiny in proportion to how much was machine-generated; and
  (2) any agent skills shipped under `.agents/skills/` follow the
  [agentskills.io](https://agentskills.io) format that
  [`tiangolo/library-skills`](https://github.com/tiangolo/library-skills)
  builds on (a `SKILL.md` with YAML frontmatter inside a
  `.agents/skills/<name>/` directory). Drive-by, undisclosed, or
  undefendable machine output ("AI slop") will be closed.

- **`add-rule` skill under `.agents/skills/add-rule/`.** A self-contained
  walkthrough for adding a new validation rule — category selection, file
  layout, the `Rule` + `@register` template, category `__init__.py` wiring,
  test expectations, and pre-flight commands. Symlinked at
  `.claude/skills/add-rule` so Claude Code (which does not yet read
  `.agents/`) picks it up. AGENTS.md now points to the skill from the
  "Adding a validation rule" section instead of duplicating the steps.

### Changed

- **`AGENTS.md` rewritten for higher signal-to-token ratio.** The agent
  guidance file at the repo root has been condensed from ~11 KB of prose
  paragraphs to ~5.6 KB of bullets and tables, while preserving every
  load-bearing rule (uv-only workflow, `[api]` extra transitive-dep rule,
  `cors_origins` validator caveat, post-change stale-reference sweep,
  `prek` hook list, UI smoke-test requirement for `src/fastssv/api/`
  changes, `[tool.ruff]` ignore set, coverage gate, cross-tool layout).
  The `## Cross-tool layout` section also documents the new
  `.claude/skills/<name>` → `.agents/skills/<name>` symlink convention
  and points at the new `CONTRIBUTING.md` for the AI-assisted PR policy.
  The `CLAUDE.md` symlink picks up the rewrite automatically. No code or
  runtime change.

### Removed

- **`viz` and `langfuse` optional-dependency groups dropped from
  `pyproject.toml`.** Neither group was imported anywhere in `src/` or
  `tests/` — `matplotlib`, `networkx`, `langfuse`, and `dotenv` had been
  declared as optional extras (`fastssv[viz]`, `fastssv[langfuse]`) but
  carried no runtime or test usage to support them. The empty placeholders
  invited contributors to assume an integration existed that did not, and
  bloated the lockfile resolution. Anyone who was relying on `pip install
  fastssv[viz]` or `[langfuse]` to pull these libraries will need to install
  them directly. The corresponding AGENTS.md guidance and the `[viz]`
  lower-bound note have been removed.

### Changed

- **`api` extra slimmed by switching to `fastapi[standard]`.** The group
  now reads `fastapi[standard]>=0.115`, `gunicorn`, `slowapi`,
  `pydantic-settings`. The previously explicit `uvicorn[standard]`,
  `jinja2`, `python-multipart` entries are gone — `fastapi[standard]`
  pulls all three (plus `httpx`, `email-validator`, `fastapi-cli`)
  transitively, so listing them at the top level was redundant and risked
  drifting out of sync with FastAPI's own pins. `httpx` was likewise
  removed from the `dev` extra; `tests/api/` consumes it via
  `fastapi.testclient.TestClient`, which now resolves through the `api`
  extra that CI already installs alongside `dev`. No user-visible API
  behaviour change; install footprint is roughly equivalent.

### Added

- **Python 3.14 added to the supported version matrix.** CI now exercises
  3.10, 3.11, 3.12, 3.13, and 3.14 on every push, and the PyPI
  `Programming Language :: Python :: 3.14` classifier advertises the new
  ceiling on `pypi.org/project/fastssv`. Python 3.14 went stable on
  2025-10-07; with `requires-python = ">=3.10"` already covering it, the
  only remaining gaps were test coverage and the published metadata, both
  of which this change closes. No source or dependency changes were
  required — `sqlglot` and the rest of the runtime tree already resolve
  on 3.14.

### Changed

- **Pre-commit configuration migrated from `.pre-commit-config.yaml` to
  `prek.toml`.** [Prek](https://github.com/j178/prek) is a faster, drop-in
  Rust reimplementation of `pre-commit` that prefers a native TOML config
  (`prek.toml` > `.pre-commit-config.yaml` in its discovery order). The hook
  set is unchanged — same `pre-commit-hooks` (`trailing-whitespace`,
  `end-of-file-fixer`, `check-yaml`, `check-toml`, `check-merge-conflict`,
  `check-added-large-files --maxkb=500`) and `ruff-pre-commit` (`ruff-check
  --fix`, `ruff-format`) revisions, same `exclude` pattern. Contributors
  should now run `uvx prek run --all-files` instead of `uvx pre-commit run
  --all-files`; the YAML file has been removed, so anyone still invoking
  upstream `pre-commit` directly needs to switch to `prek`.

  **Migration for existing contributors:** if you previously ran
  `pre-commit install`, the generated `.git/hooks/pre-commit` script still
  shells out to upstream `pre-commit`, which now errors on every commit
  because its config file is gone. Recover with a one-time:

  ```sh
  uvx pre-commit uninstall   # remove the stale generated hook
  uvx prek install           # install the prek-managed equivalent
  ```

  Contributors who never installed the auto-on-commit hook (i.e. only ever
  ran `uvx pre-commit run` manually) only need to swap the command for
  `uvx prek run`. Closes #27.

- **All `docs/*.md` filenames lowercased.** `docs/API.md` → `docs/api.md`,
  `docs/JSON_OUTPUT.md` → `docs/json_output.md`, `docs/LOGGING.md` →
  `docs/logging.md`, `docs/PLUGIN_ARCHITECTURE.md` →
  `docs/plugin_architecture.md`, `docs/RULES_REFERENCE.md` →
  `docs/rules_reference.md`, `docs/SEMANTIC_RULES_GUIDE.md` →
  `docs/semantic_rules_guide.md`. The previous SHOUTING_SNAKE filenames were
  inconsistent with `docs/index.md`, `docs/architecture.md`, and the rest of
  the project's lowercase naming. All ~50 cross-references in the docs
  themselves, `README.md`, `zensical.toml` (nav), `examples/logging_demo.py`,
  and previous `[Unreleased]` CHANGELOG bullets were updated in the same
  pass. **URL impact:** the deployed site URLs change correspondingly —
  `https://fastomop.github.io/fastSSV/API/` → `/api/`, `/JSON_OUTPUT/` →
  `/json_output/`, etc. Old bookmarks will 404; the site is too new to have
  meaningful external link rot, but worth noting.

- **README slimmed from 733 lines to ~110 lines.** The README had grown into
  a near-duplicate of the docs site — duplicate "Why FastSSV", per-category
  rule walkthroughs (~140 lines of "Key X Rules" subsections that drifted
  from the registry), Validation Architecture / Layer documentation, full
  schema-coverage tables, and a Project Structure tree referencing modules
  that no longer exist (`core/omop_schema.py`, `core/rule_layer.py`,
  `schemas/cdm_schema.py`, `rules/schema/`). The new README is a landing
  page: install + quick CLI/Python use + the canonical "what it catches"
  example + the OHDSI positioning + a docs table. Everything else lives in
  `docs/` and is linked from the table. Also fixed a stale Python import
  example that referenced a nonexistent `validate_anti_patterns` symbol.

### Fixed

- **Documentation correctness pass.** Multiple doc pages had drifted from the
  registry and the API surface; fixes applied across `docs/`:
  - **Severities corrected** for `concept_standardization.standard_concept_enforcement`,
    `temporal.observation_period_anchoring`, `joins.maps_to_direction`, and
    `concept_standardization.concept_domain_validation` — all four are
    `Severity.WARNING` in the registry but were variously documented as ERROR
    across `architecture.md`, `semantic_rules_guide.md`, and the per-rule entry
    in `rules_reference.md` (which contradicted its own quick-reference table).
  - **Removed-symbol references retired.** `semantic_rules_guide.md` no longer
    documents `SOURCE_CONCEPT_FIELDS` as a live symbol or
    `hierarchy_expansion_required` as an implemented rule — both were removed
    in 0.2.0. Short historical notes explain why.
  - **Stale rule count fixed**: `semantic_rules_guide.md` claimed "7
    production-ready rules"; the registry has 154. Replaced with a
    pointer to `rules_reference.md` and a `get_all_rules()` snippet for the
    live count.
  - **`api.md` violation shape** now matches `fastssv/api/models.py:Violation`
    — `suggested_fix` renamed to `fix`, `details` removed (the field is not
    on the wire). The dialect enum is expanded from `auto|postgres|tsql` to
    the full nine values the model actually accepts.
  - **`json_output.md` `validate_sql()` schema** now lists the `parse_error`
    and `dialect` keys returned by the function. The invalid `--dialect mysql`
    example replaced with a real choice.
  - **Architecture directory tree** now references `schemas/cdm_column_types.py`
    (the actual file post-0.2.0); the deleted `schemas/cdm_schema.py` no
    longer appears.
  - **De-duplicated the rule-author walkthrough.** `architecture.md` and
    `semantic_rules_guide.md` no longer paraphrase the four-step rule
    creation recipe; both now link to the canonical version in
    `plugin_architecture.md`. The walkthrough also points at
    `tests/test_rules.py` (the real canonical test file per `AGENTS.md`)
    instead of the previously-shown `tests/test_my_new_rule.py`.
  - **`plugin_architecture.md`**: dropped the "Migration from Older Patterns"
    section — the deprecated function-style API it referenced was never in
    the public registry.
  - **Index page** now includes a Logging card alongside the other landing
    cards (it had a nav slot but no entry on the home grid).
  - **`logging.md` Related Documentation** section now links to the in-site
    pages (`api.md`, `json_output.md`, `plugin_architecture.md`) instead of
    GitHub README anchors.
  - **CLI JSON report shape rewritten across the docs.** `json_output.md`,
    `README.md`, `plugin_architecture.md` (`RuleViolation.to_dict()` example),
    and the cross-reference paragraph in `api.md` previously documented an
    older wire shape — a top-level `violations[]` array with `suggested_fix`
    and `details` fields on each entry. The actual implementation
    (`cli.py:build_validation_result`, `core/base.py:RuleViolation.to_dict`)
    splits violations into `errors[]` and `warnings[]` arrays, emits a single
    `fix` field (string for prose, patch object for mechanical), and
    intentionally omits `details` from the wire. All four pages now match
    the real output, verified by running the CLI on the canonical example
    query and capturing the JSON. The README's "What it catches" example
    query was also replaced — the previous query (`SELECT person_id FROM
    condition_occurrence WHERE condition_concept_id IN (201826, 443238)`)
    no longer fires under the current rule calibration; the new example
    (a bare `concept_name LIKE '%aspirin%'` lookup) reliably produces three
    warnings whose actual JSON is what's shown.

### Changed

- **Documentation site moved from MkDocs + Material for MkDocs to
  [Zensical](https://zensical.org/).** Zensical is the next-generation static
  site generator from the Material for MkDocs team. The site itself is
  visually unchanged (same nav, same orange palette, same dark-mode toggle,
  same `docs/` content tree), but the build pipeline is now native to
  Zensical: the `[docs]` extra in `pyproject.toml` pulls `zensical` instead
  of `mkdocs-material`, the `Docs` GitHub Actions workflow runs
  `zensical build --strict`, and the contributor commands are now
  `zensical serve` / `zensical build` instead of `mkdocs serve` /
  `mkdocs build`. Configuration moved from `mkdocs.yml` (YAML) to
  `zensical.toml` (TOML) at the repo root — Zensical's native format, so the
  old `pymdownx.emoji` `!!python/name:` tag hack is gone (`emoji_index` and
  `emoji_generator` are now plain string references to
  `zensical.extensions.emoji.*`), and the `nav` block is expressed as TOML
  inline tables. Three template-style bracketed placeholders in
  `docs/plugin_architecture.md` (`[What's wrong]`, `[Why it matters]`,
  `[How to fix it]`) were backslash-escaped — Zensical's stricter
  link-reference parser was treating them as broken links under `--strict`.
  Motivation: MkDocs has been unmaintained since August 2024; Zensical is
  the upstream-recommended replacement and produces a noticeably faster
  rebuild loop during local docs work.

### Security

- **Polynomial ReDoS in `split_sql_statements` fixed (CodeQL
  `py/polynomial-redos`).** `_has_sql_content` in
  [`src/fastssv/core/helpers.py`](src/fastssv/core/helpers.py) used
  `re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)` to strip block
  comments before checking whether a statement carried any SQL. On
  inputs of the form `"/*" + "a/*"*N` (an unclosed block comment with
  many nested `/*` markers) the engine retried from every starting
  position, giving O(N²) runtime: a 60 KB payload took ~2.5 s and a
  120 KB payload ~10.6 s of CPU per call. Because `split_sql_statements`
  is called on every request to `POST /validate` and the UI's
  `/ui/validate`, a single crafted submission within the default
  `max_sql_bytes` cap of 100 000 bytes could pin an API worker for
  ~7 s. The helper has been rewritten as a single linear scan over the
  string (no regex, no backtracking), and a regression test in
  `tests/test_integration.py` asserts the helper scales linearly
  (doubling input ≈ doubles runtime, not quadruples). No change to
  observable splitting behaviour.

## [0.2.0] - 2026-04-30

### Fixed

- **`joins.join_path_validation` false positive on subquery-only `concept`
  usage.** The rule fired with a join-shaped message ("concept may not be
  properly joined to the clinical tables via standard concept fields") on
  queries that didn't JOIN concept at all — they used it as a scalar
  lookup (`WHERE p.gender_concept_id = (SELECT concept_id FROM concept …)`)
  or an IN-subquery filter. The message and its suggested fix were framed
  for the JOIN shape and didn't apply, which historically confused users
  and LLM agents into adding a JOIN to clinical (the wrong fix) when the
  right concern was tightening the inner lookup. Added
  `_vocab_table_used_only_in_subquery`: when every reference to `concept`
  (or `concept_relationship`) is nested inside a `Subquery` node, the rule
  now suppresses — the relevant data-quality concerns (deprecated
  concepts, whitespace mismatches, ambiguous lookups) are already covered
  by `invalid_reason_enforcement`, `concept_name_whitespace`, and
  `standard_concept_enforcement`. Actual JOINs with bad linkage shapes
  still fire.

- **`concept_standardization.invalid_reason_enforcement` asymmetry on the
  `concept_ancestor` cohort idiom — now fires on all three forms.** The
  rule originally only detected primary-FROM usage of derived vocabulary
  tables; the IN-subquery form happened to work because the inner SELECT
  has `concept_ancestor` in its FROM, but direct-JOIN and chained-JOIN
  forms slid past silently. Replaced the JOIN-form heuristic with a
  cleaner one: `_concept_ancestor_filtered_by_hierarchy_literal` checks
  for a literal predicate (`=` or `IN (…)` of literals) on
  `concept_ancestor.{ancestor,descendant}_concept_id` in WHERE / JOIN-ON.
  That signal is form-agnostic and reliably distinguishes cohort-source
  usage from lookup-decoration:
  - **Source** (rule fires): a literal hierarchy filter is the cohort
    selector. Caught by the new check whether concept_ancestor lives in
    primary FROM, a direct JOIN, or a chained JOIN through `concept`.
  - **Lookup** (rule stays silent): no literal filter on concept_ancestor's
    own hierarchy columns; the table is just decoration. Existing tests
    covering this shape (e.g. `FROM concept c JOIN concept_ancestor ca ON
    ca.ancestor_concept_id = c.concept_id WHERE c.concept_id = <literal>`)
    continue to pass.

  Previously the chained-JOIN form failed two ways at once: the
  `standard_concept_enforcement` rule false-positively required
  `standard_concept = 'S'` on a query that already guaranteed standardness,
  while `invalid_reason_enforcement` false-negatively missed the genuine
  deprecated-concept concern. Both gaps now closed.

- **`concept_standardization.standard_concept_enforcement` false positives on
  the `concept_ancestor` cohort idiom — three forms now suppressed.** The
  rule recognizes three semantically equivalent patterns as adequate
  standard-concept enforcement:
  1. **IN-subquery:** `<col> IN (SELECT descendant_concept_id FROM concept_ancestor [WHERE …])`
     and the inverse `ancestor_concept_id` form.
  2. **Direct JOIN:** `JOIN concept_ancestor ca ON <clinical>.<concept_id_col> = ca.descendant_concept_id`
     (or `ca.ancestor_concept_id`) — the more common idiom in OHDSI cohort SQL.
  3. **Chained JOIN via `concept`:** `JOIN concept c ON <clinical>.<concept_id_col> = c.concept_id`
     followed by `JOIN concept_ancestor ca ON c.concept_id = ca.descendant_concept_id`
     (or `ca.ancestor_concept_id`). Users adopt this shape when they also
     want to project columns from `concept` (e.g. `concept_name`) in the
     SELECT list. The intermediate `concept` join is a relay; the
     standard-concept guarantee is transitive through the chain.

  concept_ancestor is, by OMOP CDM definition, a hierarchy over Standard
  Concepts only — both `ancestor_concept_id` and `descendant_concept_id` are
  guaranteed-standard, so feeding rows from concept_ancestor into a
  `*_concept_id` slot (whether via subquery, direct JOIN, or chained JOIN
  through concept) transitively guarantees the standard-concept property
  and an additional `standard_concept = 'S'` filter would be redundant.
  Previously produced spurious warnings (and ERROR-level violations under
  strict mode) on every cohort query using these idioms. Suppression is
  scope-limited to direct references; CTE-indirected patterns still fire
  (existing behavior preserved).

### Changed

- **`temporal.future_information_leakage` rework.** The rule now defers to
  `temporal.observation_period_anchoring` when `observation_period` is not
  joined at all. Previously both rules fired on the same root cause,
  doubling the violation count, and the leakage rule shipped a patch
  referencing an `<op>.observation_period_end_date` placeholder alias that
  the query never defined — un-applyable without coordination with the
  other rule's JOIN-introducing fix. When `observation_period` IS joined
  but no upper bound is asserted, the rule still fires and now resolves
  the query's actual alias (e.g. `op`, or the bare table name when no
  alias is used) and substitutes it directly into the `ADD` patch and the
  per-violation suggested fix. Message text reframed: this is a follow-up-
  window / immortal-time-bias check, not ML-style look-ahead leakage.
  `rule_id` unchanged for backward compatibility.

### Added

- **`parse.not_sql_input` parse-error variant.** When the input is natural-
  language prose rather than SQL (e.g. an LLM refusal or explanation passed
  through to the validator by mistake), the parse-error path now emits a
  distinct `parse.not_sql_input` violation instead of `parse.syntax_error`.
  The suggested fix explicitly tells callers *not* to retry with a different
  dialect — the previous default `Try dialect='tsql' …` hint was actively
  misleading for non-SQL input and could send autonomous agent loops into
  pointless dialect-retry cycles. Detection is a small heuristic in
  `core/helpers.looks_like_prose`: if the first identifier-like token (after
  stripping leading whitespace, comments, and parens) is alphabetic but not
  a known SQL statement starter, the input is classified as prose. The new
  rule_id is exported as `fastssv.NOT_SQL_RULE_ID`.
- **HTTP API** (`fastssv.api`). Optional FastAPI service exposing the
  validator over HTTP. Installed via `pip install "fastssv[api]"`.
  Endpoints: `POST /v1/validate`, `GET /v1/rules`, `GET /v1/health`,
  plus Swagger UI at `/docs`. Production guardrails baked in: body-size
  limit, parse timeout, rate limiting, strict CORS, security headers,
  structured JSON logging (SQL body never logged — only hash),
  consistent error schema, versioned routes. See [docs/api.md](docs/api.md).
- **HTMX web UI** served from the same app: `GET /` for an interactive
  SQL validator, `GET /rules` for a browsable rule list with filter.
  Server-rendered via Jinja2, vendored HTMX (`fastssv/api/static/`), no
  build step. The UI sends `POST /ui/validate` which returns HTML
  fragments; shares all middleware (body-size, timeout, rate limit,
  security headers) with the JSON API.
- **Strict mode exposed on both HTTP surfaces.** `POST /v1/validate`
  accepts an optional `strict: bool` (default `false`) and echoes it
  back in the response. The UI gains a "Strict mode" toggle button next
  to the dialect dropdown (styled distinctly from the Validate action).
  Behavior matches the CLI `--strict` flag — best-practice warnings
  escalate to errors.
- **SQL syntax highlighting in the web UI.** Vendored Prism.js core +
  SQL grammar (~22 KB) and a tiny `sql-editor.js` (~30 lines) overlay a
  highlighted `<pre><code>` behind a transparent textarea. Zero build
  step, no CDN dependency. Theme colors match the light/dark palette.
- **Per-query attribution on the JSON API and UI.** Submissions
  containing multiple `;`-separated statements are now split at the
  HTTP layer and validated independently, matching the CLI's
  behavior. `ValidationResponse` gains `query_count` and a
  `results: [QueryResult]` list where each entry carries its
  `query_index`, `sql`, `is_valid`, and its own `errors`/`warnings`.
  Top-level aggregate fields (`is_valid`, `error_count`,
  `warning_count`, `errors`, `warnings`) are preserved as
  cross-statement summaries — no breaking change. The UI renders one
  collapsible panel per query with the SQL and its violations
  attributed correctly.
- Shared helper `fastssv.core.helpers.split_sql_statements` — the
  CLI's comment- and quote-aware splitter promoted from a private CLI
  function so the API and UI can reuse it.
- `fastssv.core.validation_context.with_strict_mode()` /
  `with_validation_context()` context managers. Backing state moved to
  `contextvars.ContextVar` so concurrent API requests don't race on
  strict-mode state; `asyncio.to_thread` copies the context to the
  worker thread automatically.
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

### LLM-friendly `suggested_fix` rewrite

- Every rule's `suggested_fix` was rewritten from prose ("Use ... instead of
  ...") to an imperative, single-line, edit-shaped form intended for an LLM
  to consume and apply directly. The new style:
  - Leads with an imperative verb in caps: `REPLACE:`, `ADD:`, `REMOVE:`,
    `JOIN:`, `FILTER:`, `WRAP:`, `GROUP BY:`, `CAST:`.
  - Carries a concrete SQL fragment with `<placeholders>` for site-specific
    values (table aliases, column names, vocabulary ids).
  - Separates alternatives with `OR`. Stays at a single line, ≤250 chars
    where possible.
  - Example: `"REPLACE: \`<col> = NULL\` WITH \`<col> IS NULL\`. REPLACE:
    \`<col> <> NULL\` or \`<col> != NULL\` WITH \`<col> IS NOT NULL\`."`
- All 154 rules updated. Rules that build per-violation fixes dynamically
  (`joins.clinical_visit_detail_join_validation`,
  `joins.concept_alias_reuse_validation`,
  `joins.concept_concept_class_join_validation`,
  `joins.concept_domain_join_validation`,
  `joins.concept_join_validation`,
  `joins.concept_vocabulary_join_validation`) now also gain a class-level
  default `suggested_fix` so the `/v1/rules` listing and rule catalog show
  a useful fix even when no specific violation is in hand.
- **Breaking for downstream consumers** that parse `suggested_fix` as
  free-form English. The text is still human-readable but is now structured
  for machine application; any regex like `r"Use (.*) instead of"` will no
  longer match.

### Removed rules (redundancy cleanup)

- `anti_patterns.concept_class_table_join_uses_concept_class_id`
- `anti_patterns.domain_table_join_uses_domain_id`
- `anti_patterns.vocabulary_table_join_uses_vocabulary_id_string`

These three rules detected one narrow shape — joining the concept varchar
foreign key (`concept.<x>_id`) against the integer "concept-of-the-class"
column on the reference table (`<ref>.<x>_concept_id`). Their detection
was a strict subset of `joins.concept_<x>_join_validation`, which catches
the same shape plus broader wrong-column patterns. On the canonical bad
query a user got three near-identical error messages for one root cause.
Net effect: −3 rules, fewer duplicate violations, no detection loss.

### Rule calibration — gated `invalid_reason_enforcement` behind strict mode

`concept_standardization.invalid_reason_enforcement` previously fired as
a WARNING on virtually every realistic OMOP query that touched the
``concept`` table without an ``invalid_reason IS NULL`` filter — which
is most of them. The OMOP-conventions audit found it firing on 15 of
18 example_bad queries inside `concept_standardization/` alone, plus
many others elsewhere, drowning out signal from every other rule.

Behavior change:

- **Default mode (no `--strict`):** the rule is silent. `validate()`
  returns `[]` immediately when ``ValidationContext.strict_mode`` is
  False. Default-mode fires across the registry's example_bads dropped
  from 15 to 0.
- **Strict mode (`--strict` / `strict=True`):** the rule fires as
  WARNING. Strict mode here *enables* the rule rather than escalating
  it from WARNING to ERROR — the rule is opt-in, not severity-promoted.
- Removed the rule_id from
  ``ValidationContext._strict_escalation_rules`` (it no longer
  participates in the WARNING→ERROR escalation; the gating is the only
  effect of strict mode for this rule).

Tests updated: ``tests/test_strict_mode.py``'s normal-mode assertion
flipped to "silent"; strict-mode assertion flipped from ERROR to
WARNING; ``tests/test_rules.py::TestInvalidReasonEnforcement``'s
helper now installs strict mode for the duration of each rule call so
its 18 detection-logic tests continue to exercise the rule's
correctness when enabled.

Net: −15 false-positive warnings on realistic queries, no detection
loss when the user opts into strict mode, all 2,418 tests still pass.

### New rules — Tier A coverage additions

Three new rules and one enhancement, pushing OMOP best-practice coverage
from ~75–85% toward ~85–90% on the patterns a static SQL validator can
catch:

- **`domain_specific.event_field_polymorphic_resolution`** (error) — one
  parameterized rule covering the four remaining v5.4 polymorphic FKs:
  `note.note_event_id` requires `note_event_field_concept_id`,
  `observation.observation_event_id` requires `obs_event_field_concept_id`,
  `measurement.measurement_event_id` requires `meas_event_field_concept_id`,
  `episode_event.event_id` requires `episode_event_field_concept_id`.
  Mirrors the existing `cost.cost_event_id` and
  `location_history.entity_id` rules. After this, fastssv covers every
  polymorphic FK in v5.4 with consistent semantics.
- **`anti_patterns.limit_without_order_by`** (warning) — flags
  `LIMIT N`, `TOP N`, or `FETCH FIRST N ROWS ONLY` on a SELECT with no
  `ORDER BY`. Most engines make no row-order guarantee, so the result is
  non-deterministic across runs. Catches sampling, pagination, and CI
  flakiness bugs. Skips subqueries inside `EXISTS (...)` / `IN (...)`
  where row order is irrelevant.
- **`domain_specific.dose_era_cross_unit_comparison`** (warning) —
  symmetric mirror of `measurement_cross_unit_comparison` for
  `dose_era.dose_value`. Aggregating across mg, mcg, IU, mL, etc.
  produces meaningless averages without a `unit_concept_id` filter or
  GROUP BY.
- **Enhancement: `device_exposure` added to
  `domain_specific.event_cardinality_validation`'s target list** —
  `person → device_exposure` was the last v5.4 cardinality coverage
  gap. The single-line config change closes it without writing a new
  rule.

Net: +3 rules (151 → 154), and the cardinality rule now covers
`person → observation`, `person → device_exposure`, and
`visit_occurrence → visit_detail`. Sibling rules cover
`person → condition_occurrence`, `person → drug_exposure`, and
`measurement` duplicates separately, completing v5.4 fan-out coverage.

### New rules — coverage gaps from the OMOP-conventions audit

Four new rules close concrete gaps identified during the OMOP-coverage
audit. Each catches a real authoring bug that previously slipped past
fastssv:

- **`domain_specific.year_of_birth_age_arithmetic`** (warning) — flags
  age computed as `<year_expression> - person.year_of_birth` (e.g.
  `EXTRACT(YEAR FROM event_date) - p.year_of_birth >= 65`). This drops
  month and day, so a person born 1959-12-31 evaluates as 65 on
  2024-01-01 even though they are barely 64. Suggests using
  `birth_datetime` (or `MAKE_DATE(year_of_birth, COALESCE(month_of_birth, 7),
  COALESCE(day_of_birth, 1))`) for full-date arithmetic.
- **`domain_specific.visit_length_of_stay_arithmetic`** (warning) — fires
  on `visit_end_date - visit_start_date` (or `DATEDIFF` / `DATE_DIFF`
  shapes) without an inpatient `visit_concept_id` filter. Outpatient
  visits (9202) have `visit_end_date = visit_start_date` by spec, so
  mixed-population LOS averages dilute toward zero. Suggested fix: add
  `WHERE visit_concept_id IN (9201, 9203, 262, …)`.
- **`domain_specific.cost_event_id_polymorphic_resolution`** (error) —
  requires `cost.cost_domain_id` to be filtered when `cost.cost_event_id`
  appears in a JOIN ON or WHERE clause. `cost_event_id` is a polymorphic
  FK whose target table depends on `cost_domain_id`; joining without
  the filter either matches nothing or matches by coincidence. Mirrors
  the existing `domain_specific.location_history_entity_id_requires_domain_id`.
- **`domain_specific.event_cardinality_validation`** (warning) — covers
  the two cardinality gaps in v5.4: `person → observation` and
  `visit_occurrence → visit_detail` joined without aggregation produce
  silent fan-out. One parameterized rule rather than duplicating the
  existing `condition_occurrence_cardinality_validation` /
  `drug_exposure_cardinality_validation` shape per table.

Net: +4 rules (147 → 151). All four ship with `example_bad` /
`example_good` and pass the schema-consistency tests; full suite
remains green at 2,418 tests.

**Skipped from the same audit (with rationale):**

- *Events outside observation_period bounds* — too noisy as a static
  rule without cohort-context awareness.
- *Cohort-table overlapping intervals* — vague target; needs a clearer
  detection pattern before becoming a rule.
- *NULL date arithmetic* — already covered by
  `temporal.nullable_end_date_null_handling`.
- *Vocabulary version awareness, inter-event causality, STCM staleness* —
  out of scope for static SQL validation.
- *`provider.<source>_source_value` filter detection* — already covered
  by `data_quality.source_value_field_usage`.
- *Calibration of `concept_standardization.invalid_reason_enforcement`* —
  separate concern (gating in strict mode), not a new rule.

### Core cleanup — retired dead exports

After the schema work, an audit of `src/fastssv/core/` mapped every
public export to its consumers and turned up several that nothing in
`src/` or `tests/` had ever imported:

- **`core/logging.py`** — `PerformanceLogger` class and its accessor
  `get_performance_logger` were never instantiated outside the module.
  The class was a thin wrapper around `time.perf_counter()` with a
  `timed_operation` context manager and a hidden `FASTSSV_LOG_PERFORMANCE`
  env-var toggle. Both are removed; the env var is dropped from
  `.env.example` and `docs/logging.md`. The `examples/logging_demo.py`
  performance demo and the logging.md "Performance Tracking" section are
  retired in favour of the simpler pattern (`time.perf_counter()` plus
  `extra={"duration_ms": ...}` on the structured log record), which is
  what `core.logging.log_validation_complete` and `log_rule_execution`
  already do.
- **`core/registry.py`** — `clear_registry()` and `list_rules()` were
  test/convenience accessors with no callers anywhere. Removed.
- **`core/validation_context.py`** — `with_validation_context()` was
  redundant with `with_strict_mode()`, which is the only context manager
  consumers actually use. Removed.
- **`core/helpers.py`** — `has_equality_condition` and `has_in_condition`
  were declared in `__all__` but never imported externally. They are
  used only internally by `has_condition` (the dispatcher rules
  actually call). Renamed to `_has_equality_condition` /
  `_has_in_condition` and dropped from `__all__` to mark the boundary
  clearly. Also added `split_sql_statements` and `detect_dialect` to
  `__all__` (existing live exports that had been missed).

`core/__init__.py` did not re-export any of the removed names, so
nothing changes at the package boundary.

Net: −103 LOC across 4 files (1,157 → 1,054), zero behavior change,
zero test changes (all 2,418 tests still pass).

### Schema cleanup — folded `core/omop_schema.py` and the `rules/schema/` folder

After the schema work above, an audit of the rules folder turned up a
mismatch: `rules/schema/` contained exactly one rule
(`comprehensive_schema_validation.py`), and that rule's registered ID is
`data_quality.schema_validation`. Folder name and rule_id namespace
disagreed.

A second issue surfaced at the same time: `core/omop_schema.py` carried a
569-LOC `OMOP_SCHEMA` dict (using a `ColumnDef` dataclass with FK info)
that *independently* encoded the OMOP CDM v5.4 spec. It was consumed
solely by the rule above. That made it the third copy of the same OMOP
inventory — the kind of drift target the schemas/ refactor was meant to
eliminate.

Fixed:

- Moved `comprehensive_schema_validation.py` from `rules/schema/` into
  `rules/data_quality/`, matching its `data_quality.schema_validation`
  rule_id. Deleted the empty `rules/schema/` folder.
- Refactored the rule to derive its `is_valid_table` / `is_valid_column`
  / `get_all_tables` predicates inline from
  `fastssv.schemas.CDM_COLUMN_TYPES`. No new public API; the helpers
  are private to the rule.
- Deleted `src/fastssv/core/omop_schema.py` (`OMOP_SCHEMA`, `ColumnDef`,
  `is_valid_table`, `is_valid_column`, `get_all_tables`,
  `get_table_columns`). They were never imported outside that one rule.
- Fixed `tests/test_deduplication.py`: it referenced a fictional
  `schema.comprehensive_schema_validation` rule_id that was never
  actually registered. Replaced with a clearly-synthetic placeholder
  string so the dedup tests still exercise the "longer rule_id wins"
  branch without misleading anyone about which rules exist.

Net: −1 folder, −569 LOC of duplicated OMOP spec, no detection loss.
The `data_quality.schema_validation` rule continues to fire on the same
queries; it just reads from the canonical schema now.

### Schema cleanup — retired unused files and exports

After making `cdm_column_types.py` the single source of truth, an audit of
schema consumers showed that several files and exports had no live caller:

- **`schemas/cdm_schema.py` deleted** — the 540-LOC `CDM_SCHEMA` foreign-key
  graph was exported as part of the public API but consumed by zero rule.
  (Despite its name, `joins.join_path_validation` does not read it; that
  rule imports `STANDARD_CONCEPT_FIELDS` instead.)
- **`schemas/cdm_columns.py` deleted** — the derived `CDM_COLUMNS` view
  and `get_table_columns` accessor were folded into `cdm_column_types.py`,
  removing one layer of indirection.
- **`schemas/concept_class_id_canonical.py` deleted** — its 110-entry
  canonical-class map was used by exactly one rule
  (`data_quality.canonical_string_value_validation`), which already kept
  the canonical-domain and canonical-vocabulary maps inline. The
  concept-class map is now inline alongside them.
- **`schemas/semantic_schema.py` trimmed** — `SOURCE_CONCEPT_FIELDS` and
  `SOURCE_VOCABS` had no rule consumers and were retired.
  `STANDARD_CONCEPT_FIELDS` (the live one) stays.

**Breaking changes:** `from fastssv import CDM_SCHEMA`,
`SOURCE_CONCEPT_FIELDS`, and `SOURCE_VOCABS` no longer resolve. Anyone
who was reading those should be reading `CDM_COLUMN_TYPES` or
`CDM_COLUMNS` instead — both are still exported.

`schemas/` is now two files: `cdm_column_types.py` (canonical column +
type inventory plus the derived column-name view) and `semantic_schema.py`
(`STANDARD_CONCEPT_FIELDS` only). The schema-consistency test
(`tests/test_schema_consistency.py`) was simplified accordingly and
continues to enforce that every entry in `STANDARD_CONCEPT_FIELDS`
exists in `CDM_COLUMN_TYPES`.

### Schema single source of truth

`fastssv.schemas` is refactored so that `cdm_column_types.CDM_COLUMN_TYPES`
is the canonical, type-bearing OMOP CDM v5.4 inventory and everything else
is derived or asserted against it:

- `cdm_columns.CDM_COLUMNS` is now computed at import time as
  `{table: frozenset(CDM_COLUMN_TYPES[table].keys())}`. The hand-written
  duplicate dict is gone, eliminating the table/column drift that used to
  exist between the two files.
- `cdm_schema.CDM_SCHEMA` keeps primary-key and foreign-key edges, but a
  module-level consistency check raises a `RuntimeError` at import if any
  edge references a column that isn't in `CDM_COLUMN_TYPES`.
- `semantic_schema.SOURCE_CONCEPT_FIELDS` was reconciled against the v5.4
  spec — 11 entries that referenced columns the spec doesn't define
  (e.g. `specimen.specimen_source_concept_id`,
  `visit_occurrence.admitted_from_source_concept_id`) were removed; four
  real source-concept-id columns that had been missing
  (e.g. `provider.specialty_source_concept_id`,
  `payer_plan_period.payer_source_concept_id`) were added.
- `CDM_COLUMN_TYPES` gained typed entries for 16 previously-untyped
  tables (measurement, observation, procedure_occurrence, cost, note,
  note_nlp, drug_era, dose_era, episode, episode_event, specimen,
  payer_plan_period, metadata, cdm_source, cohort_definition,
  fact_relationship). `data_quality.column_type_validation` now has full
  v5.4 coverage instead of silently skipping ~40% of clinical tables.
- `CDM_SCHEMA` foreign-key edges were corrected against the v5.4 ERD: 18
  edges that referenced nonexistent columns
  (e.g. `condition_occurrence.care_site_id`, `cost.person_id`,
  `note_nlp.term_concept_id`) were removed or renamed
  (e.g. `visit_detail.visit_detail_parent_id` →
  `visit_detail.parent_visit_detail_id`,
  `episode_event.event_field_concept_id` →
  `episode_event.episode_event_field_concept_id`).
- `tests/test_schema_consistency.py` (172 parametrized tests) freezes
  this contract in CI so it cannot regress.

The `attribute_definition` (legacy v5.3) and `location_history` (optional
v5.4 extension) tables are retained because fastssv ships rules that
detect their misuse.

### Removed rules (low-value redundancy)

- `anti_patterns.concept_lookup_context` — soft stylistic nudge ("for
  robustness, consider wrapping in subquery") that overlapped with
  `concept_name_lookup` and `concept_code_requires_vocabulary_id` on the
  cases it actually flagged. The rule's escape conditions
  (`SELECT *`, any `_concept_id` projection, any concept→clinical join,
  any `concept_relationship` in scope) leaked through nearly every
  realistic analytical query, so in practice it fired only on a narrow
  shape already caught — typically with co-firing — by `concept_name_lookup`.
  No `example_bad`/`example_good` were authored for it. Its description
  self-acknowledged that "Direct filtering with vocabulary_id +
  concept_code is valid in OMOP", which is exactly the case
  `concept_code_requires_vocabulary_id` already enforces. Net effect: −1
  rule, no detection loss; the broader concept-lookup cases are still
  caught by the surviving two rules.

### Merged rules

- `anti_patterns.cdm_source_clinical_join` (OMOP_113) and
  `anti_patterns.metadata_clinical_join` (OMOP_121) are merged into
  `anti_patterns.singleton_metadata_clinical_join`.

The two old rules had identical detection shape (collect tables in SELECT
scope, flag if a singleton metadata table appears alongside any clinical
table) and a literally-duplicated 21-entry `CLINICAL_TABLES` list — they
differed only in which metadata table they checked. The merged rule
parameterizes the metadata-table list, keeping all original detection
coverage. **Breaking change:** consumers reading `rule_id` from JSON
output should map both old IDs to
`anti_patterns.singleton_metadata_clinical_join`.

- Four free-text-column rules in `data_quality/` are merged into
  `data_quality.free_text_column_misuse`:
  - `data_quality.condition_occurrence_stop_reason_is_free_text` (OMOP_107)
  - `data_quality.drug_exposure_lot_number_is_free_text` (OMOP_108)
  - `data_quality.note_nlp_term_modifiers_is_free_text` (GAP_014)
  - `data_quality.location_state_zip_not_joined_to_concept` (OMOP_106)

  All four detected the same anti-pattern shape (a free-text VARCHAR column
  joined to the concept table or compared to a numeric literal) with
  different `(table, column)` coordinates. ~1300 LOC of structural
  duplication is replaced by a single rule with a `FREE_TEXT_FIELDS`
  config table. Detection coverage is preserved, including the
  term_modifiers-only behaviours (CAST-to-numeric and any-JOIN
  detection), which are now driven by per-field flags.

- Three canonical-string-casing rules in `data_quality/` are merged into
  `data_quality.canonical_string_value_validation`:
  - `data_quality.concept_class_id_case_sensitivity` (VOCAB_007)
  - `data_quality.domain_id_case_sensitivity` (VOCAB_006)
  - `data_quality.vocabulary_id_validation` (VOCAB_004 / VOCAB_005)

  All three detected the same shape (find EQ/IN/LIKE filters on the
  column, look up in a canonical map, flag if mismatched) and differed
  only in (a) which canonical map applied and (b) whether hyphens were
  also flagged (vocabulary_id only). The merged rule encodes per-column
  behaviour in a `TARGETS` config table. The vocabulary-id hyphen warning,
  the wrap-in-function exception, and per-column severity tiering are all
  preserved.

  **Breaking change:** seven legacy rule_ids vanish; consumers reading
  `rule_id` from JSON output should remap them to either
  `data_quality.free_text_column_misuse` or
  `data_quality.canonical_string_value_validation` depending on which
  legacy rule they tracked.

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

### Earlier in this release — rule calibration pass

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
- After this release: **154 rules across 6 categories** (performance and analytics categories removed entirely; see "Removed rules" sections above for the full list).

---

## [0.1.0] - initial release

Initial public release of FastSSV. 168 rules across 8 categories covering
OMOP CDM v5.4 semantic correctness.
