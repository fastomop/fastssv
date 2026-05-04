# Contributing to FastSSV

Thanks for considering a contribution. FastSSV is a static, semantic validator for OMOP CDM SQL — most contributions are either new validation rules or improvements to the existing rule set, the CLI, or the FastAPI service.

This file is the contract for **human-authored** and **AI-assisted** contributions alike. Everything build- and tooling-related lives in [AGENTS.md](AGENTS.md); this file covers the policy and review side.

## Quick start

1. Fork and clone the repo.
2. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and sync the dev environment:

   ```sh
   uv sync --frozen --extra dev --extra api
   ```

3. Make your change. For a new validation rule, follow the [`add-rule` skill](.agents/skills/add-rule/SKILL.md).
4. Run the full pre-flight before opening a PR:

   ```sh
   uvx prek run --all-files
   uv run --frozen --no-sync pytest tests/ -v --cov
   ```

5. Update `CHANGELOG.md` under `## [Unreleased]`.
6. Open a PR against `main`.

The full operational checklist (stale-reference sweep, UI smoke for API changes, conventions) lives in [AGENTS.md → After making changes](AGENTS.md#after-making-changes).

## Developer's Certificate of Origin

Every commit must be signed off:

```sh
git commit -s -m "..."
```

That `Signed-off-by:` trailer asserts the [Developer's Certificate of Origin 1.1](https://developercertificate.org/) — that you wrote (or have the right to submit) the change under the project's Apache-2.0 licence. **Only humans may sign off.** If a contribution is AI-assisted, the human submitter signs and is fully responsible for the change.

## AI-assisted contributions

**FastSSV accepts AI-assisted PRs.** Coding agents (Claude Code, Codex, Cursor, Copilot, …) are useful tools — particularly for rule scaffolding, test generation, and OMOP schema lookups. We will **not** accept "AI slop": low-effort, undisclosed, or unreviewed machine output that the human submitter cannot defend.

We accept AI-assisted PRs **if and only if** they meet both of the following standards.

### 1. Linux-kernel-style disclosure and accountability

We follow the spirit of the Linux kernel's [coding-assistants](https://docs.kernel.org/process/coding-assistants.html) and [tool-generated content](https://docs.kernel.org/process/generated-content.html) policies, adapted to this project:

- **The human submitter signs the DCO and owns the change.** AI agents must not add a `Signed-off-by:` trailer. Whoever signs off "is expected to understand and to be able to defend everything you submit. If you are unable to do so, then do not submit the resulting changes." (kernel docs, verbatim)
- **Disclose AI assistance with an `Assisted-by:` trailer** in the commit message, in the kernel format:

  ```
  Assisted-by: AGENT_NAME:MODEL_VERSION [TOOL1] [TOOL2]
  ```

  The square brackets above are the kernel docs' "optional placeholder" notation (matching shell-style usage), not literal characters — the emitted trailer has bare, space-separated tool names. Examples:

  ```
  Assisted-by: Claude:claude-opus-4-7
  Assisted-by: Codex:gpt-5 sqlglot-debug
  ```

  `AGENT_NAME` is the agent or tool name; `MODEL_VERSION` is the specific model; trailing tools are optional specialised analysis aids (e.g. a custom `sqlglot` traversal helper, coccinelle, sparse). **Don't list general developer tools** (git, ruff, gcc, your editor) — those are noise.

- **Disclose the prompts and the scope** in the commit message body (or the PR description if multiple commits share context):
  - What was AI-assisted vs. hand-written?
  - What prompts produced the change? For short prompts, paste them. For long sessions, summarise the prompts and the nature of the assistance.
  - How did you test the change?
- **Expect extra scrutiny in proportion to how much was AI-generated.** Reviewers may request additional tests, ask for the prompt log, or treat the change at lower priority than human-authored work. This is by design.
- **Outright machine-generated patches without human review are not welcome.** "Drive-by" PRs from automation that the submitter has not actually read and understood will be closed.

### 2. Library-skills / agentskills.io format for shipped skills

Any agent skill that ships under `.agents/skills/<name>/` must follow the open [agentskills.io](https://agentskills.io) format that [`tiangolo/library-skills`](https://github.com/tiangolo/library-skills) builds on:

- The skill is a directory `.agents/skills/<name>/` containing a `SKILL.md`.
- `SKILL.md` starts with YAML frontmatter:

  ```yaml
  ---
  name: <kebab-or-snake-name>
  description: <one-sentence trigger — when an agent should invoke this skill>
  ---
  ```

- The body is plain Markdown: short, action-oriented, and self-contained. A skill should be readable end-to-end without external docs.
- A matching tool-specific symlink lives under `.claude/skills/<name>` so Claude Code finds it (Claude Code does not yet read `.agents/`):

  ```sh
  ln -s ../../.agents/skills/<name> .claude/skills/<name>
  ```

- Skills should be **kept in sync** with the code they document. If you change the rule registration mechanism, the `add-rule` skill must update in the same PR. Stale skills are worse than no skills — they teach agents to write broken code with confidence.

See [`.agents/skills/add-rule/SKILL.md`](.agents/skills/add-rule/SKILL.md) as the reference example.

## Standard PR checklist

For every PR, AI-assisted or not:

- [ ] `uvx prek run --all-files` passes (whitespace, EOL, YAML/TOML, ruff check + format).
- [ ] `uv run --frozen --no-sync pytest tests/ -v --cov` passes; coverage stays at or above `fail_under = 79`.
- [ ] New rule? Has a passing-SQL and failing-SQL test in `tests/test_rules.py`.
- [ ] Touches `src/fastssv/api/`? You booted the service locally and clicked through index, rules listing, and a sample validation in the browser — or you said so explicitly in the PR description.
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` for any user-visible change.
- [ ] No new top-level dependencies without prior discussion.
- [ ] DCO sign-off (`git commit -s`).
- [ ] If AI-assisted: `Assisted-by:` trailer in the commit and prompt/scope disclosure in the commit body or PR description.

## Reporting issues

Open a GitHub issue with: the SQL that triggered the unexpected behaviour, the dialect (`--dialect`), the expected vs. actual output, and the FastSSV version (`fastssv --version`). For security issues, follow [SECURITY.md](SECURITY.md) — use GitHub's private vulnerability reporting rather than opening a public issue.

## Licence

By contributing you agree your contribution is licensed under [Apache 2.0](LICENSE), the same licence covering the rest of FastSSV.
