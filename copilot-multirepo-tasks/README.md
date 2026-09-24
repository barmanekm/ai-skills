# Multi-repo task toolkit for GitHub Copilot CLI

One workspace, many repos, many parallel tasks. Every task gets its own git worktree per
repository, so a repo can be part of several tasks at once and switching never needs
stashing. Copilot drives it through two skills; hooks keep each session inside its task.

## Install

```bash
python3 install.py              # workspace at ~/workspace
python3 install.py ~/dev/ws     # or anywhere else
```

Requirements: Python 3.8+ (standard library only), git ≥ 2.23. Works on macOS, Linux and
Windows. Put `~/.local/bin` on your PATH. Re-running is safe; an earlier bash-based install
is migrated automatically (existing tasks keep working).

| Installed to | What |
|---|---|
| `~/.copilot/skills/task-workspace/` | skill: `SKILL.md`, `references/`, `scripts/` |
| `~/.copilot/skills/cross-repo-change/` | skill: `SKILL.md`, `references/local-linking.md` |
| `~/.local/bin/tw` (`tw.cmd` on Windows) | shim to `scripts/tw.py` |
| `~/.copilot/agents/task-manager.agent.md` | optional housekeeping agent (shell/read/search) |
| `~/.copilot/hooks/multirepo-tasks.json` | sessionStart + preToolUse hooks (bash and PowerShell) |
| `~/.copilot/copilot-instructions.md` | a marked rules block; your own content is kept |

## Package layout

```
skills/task-workspace/
├── SKILL.md                    router: model, tw cheat sheet, intent → reference
├── references/                 start, status-and-switch, sync, set-base, commit-and-push, finish
└── scripts/
    ├── workspace.py            shared library: discovery, task.yaml, git, status
    ├── tw.py                   CLI
    ├── guard.py                preToolUse hook
    └── session_context.py      sessionStart hook
skills/cross-repo-change/       SKILL.md + references/local-linking.md
agents/task-manager.agent.md
tests/test_tw.py                end-to-end tests against local bare remotes
install.py
```

## Workspace layout

```
<ws>/
├── .taskws                 marker
├── repos/<repo>            canonical clones — never edited
└── tasks/<ID>/
    ├── task.yaml           repos, base branch per repo, sync strategy
    ├── AGENTS.md
    └── <repo>/             worktree on branch task/<ID>
```

`task.yaml` stays YAML:

```yaml
# Managed by tw. Change bases with `tw set-base`, add repos with `tw add`.
id: PROJ-123
title: "Rate limiting"
strategy: rebase
created: "2026-09-24"
repos:
  api: main
  shared-lib: release/2.3
  legacy: "1.10"
```

It is parsed by a small built-in reader for this strict subset (comments, quotes, one nested
mapping), so there is no PyYAML dependency and every value is a string. A branch named `1.10`
can't silently turn into the number 1.1.

## Daily flow

```bash
tw clone git@github.com:org/api.git             # once per repo
```

In Copilot, at the workspace root: *"start PROJ-123 in api and shared-lib, shared-lib against
release/2.3"*. Then `cd "$(tw path PROJ-123)" && copilot` and work. *"sync"*, *"commit and push,
open PRs"*, *"switch to PROJ-99"*, *"what's pending?"*, *"PROJ-123 is merged, clean up"* all go
through **task-workspace**. *"This change needs to go into shared-lib and api"* goes through
**cross-repo-change**. Everything also works by hand: `tw help`.

## `tw` highlights

- `--json` on every command (`tw --json status`) gives Copilot structured results instead of
  columns to parse. Exit code `2` means partial failure (sync conflict, rejected push).
- `start` fetches every repo and branches from the latest `origin/<base>`. If
  `origin/task/<ID>` exists, it resumes it. If the first repo fails, nothing is left behind.
- `sync` autostashes, and on conflict reports the files plus the exact continue/abort commands.
- `set-base` with rebase moves only the task's own commits (`rebase --onto`).
- `status` flags unfinished rebases/merges and worktrees that are on the wrong branch.
- `finish` refuses while anything is uncommitted or unpushed and never deletes remote branches.
- Env: `TW_STRATEGY` (rebase|merge), `TW_BRANCH_PREFIX` (default `task/`).

## Guardrails

- **deny:** edits outside the active task, anything in `repos/`, hand edits of `task.yaml`,
  git writes aimed at other tasks, `tw <cmd> <other-ID>`, `git push --force/-f`
- **ask:** `tw set-base`, `tw finish`, `tw push --force-with-lease`

Shell checks are pattern-based: a seatbelt, not a sandbox. Hooks never block on their own
internal errors.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Optional tuning

Skills don't pre-approve shell, so Copilot asks before running `tw`. Once you've reviewed them,
add `allowed-tools: shell` to `task-workspace/SKILL.md` frontmatter to skip those prompts.
