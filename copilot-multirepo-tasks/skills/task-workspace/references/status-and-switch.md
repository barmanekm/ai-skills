# Status and switching tasks

## Status
- Active task: `tw --json status` (add `--fetch` for fresh remote data; slower). All tasks: `tw --json status --all`.
- Content view: `tw --json diff`.
- Fields: `ahead` = task commits not in base; `behind` = base commits not yet synced; `changed` = uncommitted files; `unpushed` / `upstream: null` (never pushed) / `remote_ahead`; `in_progress` = unfinished rebase/merge; `on_task_branch: false` = someone checked out another branch (fix before anything else).
- Point out what's actionable: behind > 0 → sync; changed > 0 → commit; unpushed → push; `in_progress` → finish resolving (see sync.md); missing worktree → `tw add`.

## Switching
A session is bound to the directory it started in, so switching = a session in the other task's directory. No stashing is needed: uncommitted work stays in its own worktrees.

1. `tw --json status --all` → compact summary: task, title, changed/unpushed per repo.
2. Current task has uncommitted changes → mention they are safe where they are; offer a checkpoint commit only if the user wants one.
3. Give the command:
   ```
   cd "$(tw path <ID>)" && copilot            # new session
   cd "$(tw path <ID>)" && copilot --resume   # continue a previous session there
   ```
4. Target task behind its bases → suggest syncing once there.

Never edit, commit or push another task's files from this session (the hook denies it).
