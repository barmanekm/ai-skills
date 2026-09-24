---
name: task-workspace
description: Manage multi-repo tasks in the task workspace - start a task (latest default branch of each repo into isolated git worktrees), switch between tasks, show status, sync with base branches, change a repo's base branch on explicit request, commit and push only the current task's changes, and finish/clean up tasks. Use for any task lifecycle or git housekeeping request, e.g. "start PROJ-1 in api and web", "sync", "commit and push", "switch to PROJ-2", "what's pending", "PROJ-1 is merged, clean up".
---

# Task workspace

## Model

```
<workspace>/
├── repos/<repo>          canonical clones — never edit or commit here
└── tasks/<ID>/
    ├── task.yaml         repos, base branch per repo, sync strategy — never edit by hand
    └── <repo>/           git worktree on branch task/<ID>
```

- A repo can be part of many tasks at once; each task has its own worktree, so tasks never interfere.
- The **active task** is the `tasks/<ID>` directory this session runs in. Hooks deny changes outside it.
- At the workspace root you may start, list, inspect and finish tasks, but not edit code.

## The `tw` CLI

All git mechanics go through `tw`: on PATH, or `python3 scripts/tw.py` from this skill's directory. Never create branches or worktrees, rebase, or push by hand when a `tw` command exists. Add `--json` whenever you need to reason about the result (e.g. `tw --json status`). ID arguments default to the active task.

| Command | Purpose |
|---|---|
| `tw list` | tasks, their repos/bases, and repos available to add |
| `tw status [ID] [--all] [--fetch]` | branch, +ahead/-behind base, changed files, push state |
| `tw diff [ID]` | commits and uncommitted changes per repo |
| `tw start <ID> <repo>... [-t title] [-b repo=branch] [-s rebase\|merge]` | new task from latest bases |
| `tw add <ID> <repo> [base]` | add a repo to a task |
| `tw clone <url> [name]` | add a repo to `repos/` |
| `tw sync [ID] [repo...]` | bring task repos up to date with their bases |
| `tw set-base <ID> <repo> <branch> [--no-sync]` | change a repo's base (explicit request only) |
| `tw commit [ID] -m "msg" [-r repo]...` | commit per repo, message prefixed `[ID]` |
| `tw push [ID] [-r repo]... [--force-with-lease]` | push task branches |
| `tw path [ID]` / `tw current` | task directory / active task |
| `tw finish <ID> [--force]` | remove worktrees + local branches, archive |

Exit code 2 = partial failure (sync conflict, rejected push). Read the JSON `results`.

## Pick the procedure

Read the matching reference file before acting:

| User intent | Reference |
|---|---|
| start / begin / pick up a task, add a repo | `references/start.md` |
| status, what's pending, switch/resume another task | `references/status-and-switch.md` |
| sync, update, pull latest, rebase on main | `references/sync.md` |
| "sync <repo> with <branch> instead" (explicit) | `references/set-base.md` |
| commit, push, open PRs | `references/commit-and-push.md` |
| done, merged, abandon, clean up | `references/finish.md` |

For code changes spanning several repos, use the **cross-repo-change** skill.

## Always

- Say in one line what a state-changing command will do, and to which repos, before running it.
- Never change a base branch unless the user explicitly asked. Never edit `task.yaml`.
- Never force-push without lease. Force-with-lease, set-base and finish show a confirmation prompt: expected.
- On failure, show the relevant error lines, explain plainly, propose the next command. Don't work around `tw` safety checks with raw git.
- Keep reports short: one line per repo.
