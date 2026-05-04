# AGENTS.md

Shared guidance for AI coding assistants working on **fastSSV** (Fast Semantic Static Validator). This file follows the open [AGENTS.md](https://agents.md/) standard so it works across Claude Code, OpenCode, Codex, Cursor, and any other agent that reads it. Tool-specific entry points (`CLAUDE.md`, `.claude/…`) are tracked symlinks into the shared assets — see [Cross-tool layout](#cross-tool-layout) below.

## What this project is

- A static, semantic validator for SQL queries written against the [OMOP CDM](https://ohdsi.github.io/CommonDataModel/). It catches schema, vocabulary, and modelling mistakes that pass syntax-check but fail at run-time or produce silently-wrong analytics.
- Built on top of [`sqlglot`](https://github.com/tobymao/sqlglot). Validation rules live under `src/fastssv/rules/<category>/`, one rule per file. Each rule self-registers via a decorator that the package's `__init__.py` files trigger by importing each module.
- Ships a CLI entry point (`fastssv …`, declared in `[project.scripts]`) and an optional FastAPI service (`[api]` extra; runs under gunicorn via the `deploy/` Docker image).

## Setup

The project uses **uv** end-to-end. Install it once ([instructions](https://docs.astral.sh/uv/getting-started/installation/)), then from the repo root:

```sh
uv sync --frozen --extra dev --extra api    # full development environment
uv sync --frozen --extra docs               # if working on the docs site
```

`uv sync` creates `.venv/`, downloads a matching Python interpreter if one isn't already on the system, and installs the locked dependency set exactly. `requires-python = ">=3.10"`.

## Common commands

| Task                | Command                                                       |
| ------------------- | ------------------------------------------------------------- |
| Run tests           | `uv run --frozen --no-sync pytest tests/ -v`                  |
| Tests + coverage    | `uv run --frozen --no-sync pytest tests/ --cov`               |
| Lint                | `uvx ruff check src/ tests/`                                  |
| Auto-format         | `uvx ruff format src/ tests/`                                 |
| Build sdist + wheel | `uv build`                                                    |
| Serve docs locally  | `uv run --frozen --no-sync zensical serve`                    |
| Build container     | `docker compose -f deploy/docker-compose.yml build`           |
| Run container       | `docker compose -f deploy/docker-compose.yml up`              |
| Run pre-commit hooks | `uvx prek run --all-files`                                   |

The `--no-sync` flag on `uv run` skips re-validating the lock on every invocation; `uv sync --frozen` (above) already established the env.

## Repo layout

```
src/fastssv/          package source
  api/                optional FastAPI service ([api] extra)
  rules/              validation rules; one per file, self-registering
tests/                pytest suite (unit + api integration)
docs/                 zensical source (configured by zensical.toml at repo root)
deploy/               Dockerfile + docker-compose for the API service
.github/workflows/    CI: tests.yml, docs.yml, publish.yml
```

## Code style

- Formatter / linter: **ruff** — config in `pyproject.toml` under `[tool.ruff]`. Line length 120, target Python 3.10+.
- Project-wide ignores: `E501` (line-too-long — the 120-line cap is set, but pre-existing lines exceed), `E741` (`l`/`I` variable names), `E402` (intentional in some `__init__.py` for circular-import avoidance), `F841` (unused locals — some rules build WIP regex/match variables that aren't yet wired up).
- One per-file ignore: `src/fastssv/__init__.py` waives `F401` because it imports submodules purely for the `@register` side effect. Category `__init__.py` files don't need this — they re-export rule classes via `__all__`, so F401 doesn't fire.
- `tests/test_rules.py` is in `extend-exclude` (skipped by ruff but still run by pytest).
- Coverage configuration sits in `[tool.coverage.*]` with `fail_under = 79`.

## Build backend

- `[build-system].build-backend = "uv_build"`. Both sdist and wheel come from `uv build`.
- PyPI release is automated by `.github/workflows/publish.yml` on a `v*` tag — the workflow checks the tag matches `[project].version` before publishing.

## Adding a new validation rule

Categories live under `src/fastssv/rules/`: `anti_patterns/`, `concept_standardization/`, `data_quality/`, `domain_specific/`, `joins/`, `temporal/`. Pick the one that fits.

1. Create `src/fastssv/rules/<category>/<descriptive_snake_name>.py` (no `rule_` prefix — match existing naming, e.g. `datetime_between_date_literal.py`, `observation_period_anchoring.py`).
2. In that file, define a class that subclasses `Rule` (from `fastssv.core.base`) and decorate it with `@register` (from `fastssv.core.registry`). Set `rule_id = "<category>.<snake_name>"` and a human-readable `name`. Look at any existing rule in the same category as a template.
3. Wire the class into `src/fastssv/rules/<category>/__init__.py` — add `from .<your_file> import <YourRuleClass>` and append `"<YourRuleClass>"` to `__all__`. (No edit needed in `src/fastssv/rules/__init__.py` — it imports the category packages, not individual rules.)
4. Add a unit test in `tests/test_rules.py` (the canonical file for rule tests) covering both passing and failing SQL.
5. Run `uv run --frozen --no-sync pytest tests/test_rules.py -v`.

## Changelog

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semver from 1.0.0 onward. **Update it for every user-visible change** — new rules, rule behaviour changes, CLI/API surface changes, build/dependency changes, removed features, bug fixes — under the `## [Unreleased]` section, in the appropriate `### Added` / `### Changed` / `### Fixed` / `### Removed` / `### Deprecated` / `### Security` subsection. Skip purely internal changes (refactors, comment-only edits, test reshuffles) unless they alter observable behaviour.

Match the existing entry style: a bold-prefixed lead-in summarising what changed, followed by a short paragraph explaining the context, the previous behaviour, the new behaviour, and any user impact.

## After making changes

Before reporting a task complete — for **every** kind of change, including code changes, not just docs/config:

1. **Sweep the repo for stale references — code, docs, config, CI, and Docker alike.** Whenever you rename or remove anything that other code or text might still mention — a public function, class, module, parameter, `rule_id`, severity, exception type, CLI flag, command, dependency, config key, file, or feature — grep the whole tree for the old name and update every surviving hit. Don't stop when your direct edit compiles or your immediate test passes; actively review the existing code and prose for downstream effects. Cover the call sites and tests in `src/` and `tests/`, scripts under `scripts/` and snippets under `examples/`, prose and code blocks under `docs/`, the `README.md`, the `## [Unreleased]` block in `CHANGELOG.md`, `AGENTS.md` (and its `CLAUDE.md` symlink), the GitHub Actions workflows under `.github/workflows/`, and `deploy/Dockerfile` + `deploy/docker-compose.yml`. The same applies to behaviour changes that don't rename anything: if you change a function's contract, default, or returned shape, walk every caller and update assumptions, comments, docstrings, and tests that still describe the old shape. Stale references rot silently — they look correct until the next reader follows a dead link, copies a command that no longer exists, or imports a symbol that has moved.
2. **Run the pre-commit hooks** with `uvx prek run --all-files` (or `uvx prek run` to scope to staged files only). [Prek](https://github.com/j178/prek) is a faster, drop-in reimplementation of `pre-commit`; it reads the project's native [`prek.toml`](https://prek.j178.dev/configuration/) (the precedence is `prek.toml` > `.pre-commit-config.yaml`, and this repo only ships the TOML form). The hooks defined there cover trailing whitespace, end-of-file fixers, YAML/TOML validity, merge-conflict markers, large-file guards, and `ruff check --fix` + `ruff format`. Fix anything they flag before handing the change back.

## Conventions

- **Use `uv sync` / `uv add` for dependency work — never `uv pip install`.** CI workflows and the Dockerfile all use lockfile-respecting `uv sync --frozen`. `uv pip` is treated as a compatibility shim only.
- Prefer editing existing files over creating new ones.
- Keep `[project.optional-dependencies]` extras grouped: `dev`, `viz`, `langfuse`, `docs`, `api`. New optional groups go alongside.
- The `[viz]` extra deliberately uses lower bounds (`networkx>=3.3`, `matplotlib>=3.7`) so the lock resolves under py3.10. Do not tighten without first bumping `requires-python` and dropping py3.10 from the test matrix.
- The API's `cors_origins` setting accepts either an empty string, a comma-separated list, or a JSON list (see the `field_validator` in `src/fastssv/api/config.py`). Don't undo that tolerance — `pydantic-settings>=2` would otherwise crash on the empty-string default that `deploy/docker-compose.yml` passes through.

## Cross-tool layout

`AGENTS.md` is the canonical project-wide agent file (per the [agents.md](https://agents.md/) spec). Tool-specific entry points alias into it via tracked symlinks so every contributor sees the same content regardless of which agent they use:

- `CLAUDE.md` → `AGENTS.md` (Claude Code)
- `.claude/<file>` → `.agents/<file>` (per-file, when `.agents/` gains shared skills/prompts)

Personal preferences (your own permission allowlist, ad-hoc env vars) belong in `.claude/settings.local.json`, which stays gitignored. Shared, team-level Claude Code settings go in `.claude/settings.json`.

When you add a new shared agent asset (skill, prompt, slash-command snippet), put the source-of-truth file in `.agents/<name>` and add a symlink at `.claude/<name>` so Claude Code finds it:

```sh
ln -s ../.agents/<name> .claude/<name>
```

(Contributors on Windows need WSL, "Developer Mode" enabled, or `git config core.symlinks true` plus admin rights for the symlinks to materialise correctly.)
