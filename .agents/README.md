# .agents/

Shared, team-level assets for AI coding assistants — skills, prompt snippets, slash-command definitions, etc. Anything an agent should pick up when working on this repo.

This directory pairs with the root [`AGENTS.md`](../AGENTS.md), which is the project's canonical agent guidance file (per the [agents.md](https://agents.md/) standard).

## Tool-specific aliases

For each shared asset placed here, create a symlink under the appropriate tool-specific directory so that tool finds it. For Claude Code:

```sh
ln -s ../.agents/<file> .claude/<file>
```

The directory is intentionally tracked even when sparse so the convention is visible to new contributors.
