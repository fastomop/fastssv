# Changelog

All notable changes to FastSSV will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
starting from 1.0.0. Pre-1.0 releases may contain breaking rule-set changes
between minor versions.

## [Unreleased]

### Changed

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
