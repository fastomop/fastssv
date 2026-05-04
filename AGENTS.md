# AGENTS.md

Shared guidance for AI coding agents on **FastSSV** (Fast Semantic Static Validator). Format follows the [agents.md](https://agents.md/) standard so every agent (Claude Code, Codex, OpenCode, Cursor, …) reads the same file. See [Cross-tool layout](#cross-tool-layout) for tool-specific entry points.

For the AI-assisted PR policy (Linux-kernel-style disclosure, DCO, library-skills format), see [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is

A static, semantic validator for SQL written against the [OMOP CDM v5.4](https://ohdsi.github.io/CommonDataModel/), built on [`sqlglot`](https://github.com/tobymao/sqlglot). Catches schema, vocabulary, and modelling errors that pass syntax check but produce silently-wrong analytics — no DB connection. Ships a CLI (`fastssv …`) and an optional FastAPI service (`[api]` extra).

## Setup

`requires-python = ">=3.10"`. The project uses **uv** end-to-end ([install](https://docs.astral.sh/uv/getting-started/installation/)):

```sh
uv sync --frozen --extra dev --extra api    # full dev env
uv sync --frozen --extra docs               # docs work
```

## Commands

| Task | Command |
| --- | --- |
| Tests | `uv run --frozen --no-sync pytest tests/ -v` |
| Tests + coverage | `uv run --frozen --no-sync pytest tests/ --cov` |
| Lint | `uvx ruff check src/ tests/` |
| Format | `uvx ruff format src/ tests/` |
| Pre-commit | `uvx prek run --all-files` |
| Build sdist + wheel | `uv build` |
| Serve API locally | `uv run --frozen --no-sync fastssv serve --reload` |
| Serve docs | `uv run --frozen --no-sync zensical serve` |
| Build container | `docker compose -f deploy/docker-compose.yml build` |
| Run container | `docker compose -f deploy/docker-compose.yml up` |

`--no-sync` on `uv run` skips lock revalidation; `uv sync --frozen` above already established the env.

## Layout

```
src/fastssv/
  api/                FastAPI service ([api] extra) — Jinja templates, htmx UI
  core/               base Rule, registry, helpers
  rules/<category>/   validation rules; one rule per file, self-registering
  schemas/            OMOP CDM column types and standard-concept field set
tests/                pytest (unit + api integration); rule tests in tests/test_rules.py
docs/                 zensical source (zensical.toml at repo root)
deploy/               Dockerfile + docker-compose for the API
.github/workflows/    tests.yml, docs.yml, publish.yml
.agents/              shared agent assets (skills, prompts) — see Cross-tool layout
```

Rule categories: `anti_patterns`, `concept_standardization`, `data_quality`, `domain_specific`, `joins`, `temporal`.

## Code style

- **ruff** (`[tool.ruff]` in `pyproject.toml`). Line length 120, target 3.10+.
- Project ignores: `E501` (pre-existing long lines), `E741` (`l`/`I` names), `E402` (intentional in some `__init__.py` for circular-import avoidance), `F841` (WIP regex/match locals in some rules).
- `src/fastssv/__init__.py` waives `F401` — it imports submodules purely for `@register` side effects.
- `tests/test_rules.py` is in `extend-exclude` (skipped by ruff, still run by pytest).
- Coverage gate: `fail_under = 79` under `[tool.coverage.*]`.

## Build & release

- `[build-system].build-backend = "uv_build"`. `uv build` produces sdist + wheel.
- Tag-driven publish: `.github/workflows/publish.yml` fires on `v*` and aborts unless the tag matches `[project].version`.

## Adding a validation rule

A dedicated [Skill](.agents/skills/add-rule/SKILL.md) walks through this. Short version:

1. Create the rule module in the package matching its category. Five categories are flat — file at `src/fastssv/rules/<category>/<snake_name>.py` (no `rule_` prefix; match existing names like `datetime_between_date_literal.py`). `domain_specific` is nested — table-specific rules live at `src/fastssv/rules/domain_specific/<table>/<table>_<snake_name>.py` (e.g. `domain_specific/measurement/measurement_cross_unit_comparison.py`); cross-cutting domain rules stay flat at `src/fastssv/rules/domain_specific/<snake_name>.py`.
2. Subclass `Rule` (`fastssv.core.base`) and decorate with `@register` (`fastssv.core.registry`). Set `rule_id = "<category>.<snake_name>"` — 2-segment is the [documented stable format](README.md#stability) and the convention across all 6 categories. The directory nesting under `domain_specific/<table>/` is organisational only and must not appear in the id. Also set `name`, `description`, `severity`, `suggested_fix`.
3. Wire the class into the **closest** `__init__.py`: flat categories use `src/fastssv/rules/<category>/__init__.py`; `domain_specific` table rules use `src/fastssv/rules/domain_specific/<table>/__init__.py` (the parent imports each table subpackage for its `@register` side effects). Add `from .<file> import <Class>` and append `"<Class>"` to `__all__`. Leave `src/fastssv/rules/__init__.py` alone — it imports category packages, not individual rules.
4. Unit-test passing and failing SQL in `tests/test_rules.py`.
5. `uv run --frozen --no-sync pytest tests/test_rules.py -v`.

## Changelog

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); semver from 1.0.0 onward. **Update `## [Unreleased]` for every user-visible change** — new rules, rule behaviour shifts, CLI/API surface, build/dependency changes, removals, fixes — under the right `### Added/Changed/Fixed/Removed/Deprecated/Security` heading. Skip purely internal changes unless they alter observable behaviour. Match the existing style: bold lead-in summarising the change, then a short paragraph on context, previous vs new behaviour, and user impact.

## After making changes

For every kind of change, before reporting done:

1. **Sweep for stale references.** Whenever you rename or remove a public symbol, `rule_id`, severity, exception, CLI flag, command, dependency, config key, file, or feature — grep the whole tree and update every hit. Cover `src/`, `tests/`, `scripts/`, `examples/`, `docs/`, `README.md`, `## [Unreleased]` in `CHANGELOG.md`, `AGENTS.md` (and the `CLAUDE.md` symlink), `.github/workflows/`, `deploy/Dockerfile`, `deploy/docker-compose.yml`. Same applies to behaviour changes that don't rename anything: walk every caller, comment, docstring, and test still describing the old contract. Stale references rot silently.
2. **Run pre-commit hooks**: `uvx prek run --all-files` (or `uvx prek run` for staged files). [Prek](https://github.com/j178/prek) reads [`prek.toml`](https://prek.j178.dev/configuration/) — trailing whitespace, EOL, YAML/TOML validity, merge markers, large-file guard, `ruff check --fix`, `ruff format`. Fix anything flagged.
3. **Verify end-to-end — backend AND frontend.** Green `pytest` is necessary but not sufficient: the API web UI (`src/fastssv/api/ui.py`, Jinja templates under `src/fastssv/api/templates/`, htmx + CSS under `src/fastssv/api/static/`) only has thin smoke tests. For changes touching `src/fastssv/api/`, dependencies, the deploy bundle, or anything that could affect request handling or asset serving — boot locally with `uv run --frozen --no-sync fastssv serve --reload` and click through index, rules listing, and a sample SQL validation. If you can't verify the UI, say so explicitly in the handoff.

## Conventions

- **`uv sync` / `uv add` only — never `uv pip install`.** CI and Docker use `uv sync --frozen`; `uv pip` is a compat shim.
- Edit existing files over creating new ones.
- `[project.optional-dependencies]` extras grouped: `dev`, `docs`, `api`. New optional groups go alongside.
- The `[api]` extra uses `fastapi[standard]`, which transitively pulls `uvicorn[standard]`, `jinja2`, `python-multipart`, and `httpx`. Don't re-add those at the top level — let FastAPI manage them. Only add deps FastAPI doesn't pull in (current additions: `gunicorn`, `slowapi`, `pydantic-settings`).
- The API's `cors_origins` setting accepts an empty string, a comma-separated list, or a JSON list (see the `field_validator` in `src/fastssv/api/config.py`). Don't undo that tolerance — `pydantic-settings>=2` would otherwise crash on the empty-string default that `deploy/docker-compose.yml` passes through.

## Cross-tool layout

`AGENTS.md` is the canonical project-wide agent file ([agents.md](https://agents.md/) spec). Tool-specific entry points alias into the shared assets via tracked symlinks so every contributor sees the same content regardless of which agent they use:

- `CLAUDE.md` → `AGENTS.md` (Claude Code)
- `.claude/<file>` → `.agents/<file>` for individual shared assets
- `.claude/skills/<name>` → `../../.agents/skills/<name>` for skills

Personal preferences (your own permission allowlist, ad-hoc env vars) belong in `.claude/settings.local.json`, which is gitignored. Shared, team-level Claude Code settings go in `.claude/settings.json`.

To add a shared agent asset: put the source-of-truth in `.agents/<name>` (or `.agents/skills/<name>/SKILL.md` for skills following the [agentskills.io](https://agentskills.io) format that [`tiangolo/library-skills`](https://github.com/tiangolo/library-skills) builds on), then symlink:

```sh
ln -s ../.agents/<name> .claude/<name>
ln -s ../../.agents/skills/<name> .claude/skills/<name>
```

(Windows contributors: WSL, "Developer Mode" enabled, or `git config core.symlinks true` plus admin rights.)
