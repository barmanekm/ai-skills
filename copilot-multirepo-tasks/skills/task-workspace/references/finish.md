# Finish a task

1. Run from outside the task directory (the workspace root session is ideal).
2. `tw --json status <ID> --fetch`. Uncommitted changes or unpushed commits → show them and ask what to do. Never proceed silently.
3. Optionally check the PRs are merged (GitHub MCP tools or `gh pr list --head task/<ID> --state all`) and report.
4. `tw --json finish <ID>` (hook asks for confirmation). It refuses while work is unsaved and lists `problems`. Use `--force` only if the user explicitly accepts losing that work.
5. Remote branches stay. If the user wants them deleted, list `git -C repos/<repo> push origin --delete task/<ID>` per repo and run only after confirmation.
