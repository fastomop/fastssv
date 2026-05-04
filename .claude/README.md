# .claude/

Claude Code-specific entry point for this repo. Project-wide agent guidance lives in [`AGENTS.md`](../AGENTS.md) and shared assets live in [`.agents/`](../.agents/) (per the [agents.md](https://agents.md/) standard); this directory holds Claude-specific files plus per-file symlinks into `.agents/` so Claude Code sees the same content as other agents.

## Adding a shared agent asset

Put the source-of-truth file under `.agents/<name>` (or `.agents/skills/<name>/SKILL.md` for [agentskills.io](https://agentskills.io)-format skills) and create a symlink here:

```sh
ln -s ../.agents/<name> .claude/<name>                    # individual asset
ln -s ../../.agents/skills/<name> .claude/skills/<name>   # skill directory
```

See [`CONTRIBUTING.md`](../CONTRIBUTING.md#ai-assisted-contributions) for the skill format standard.

## Files

- `settings.json` — team-shared Claude Code settings (tracked).
- `settings.local.json` — your personal permission allowlist and overrides (gitignored).
- `skills/` — symlinks into [`.agents/skills/`](../.agents/skills/) so Claude Code finds shared skills.
