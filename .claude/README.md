# .claude/

Claude Code-specific entry point for this repo. Project-wide agent guidance lives in [`AGENTS.md`](../AGENTS.md) and shared assets live in [`.agents/`](../.agents/) (per the [agents.md](https://agents.md/) standard); this directory holds Claude-specific files plus per-file symlinks into `.agents/` so Claude Code sees the same content as other agents.

## Adding a shared agent asset

Put the source-of-truth file under `.agents/<name>` and create a symlink here:

```sh
ln -s ../.agents/<name> .claude/<name>
```

## Files

- `settings.json` — team-shared Claude Code settings (tracked).
- `settings.local.json` — your personal permission allowlist and overrides (gitignored).
