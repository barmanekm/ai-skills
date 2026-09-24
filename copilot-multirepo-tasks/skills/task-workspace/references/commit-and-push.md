# Commit and push

Only the active task's worktrees are touched; other tasks using the same repos are separate worktrees.

## Commit
1. `tw --json diff` → short per-repo summary of what will be committed. Flag anything that looks unintended: debug code, secrets, generated files, local-link config from cross-repo work.
2. Propose one message for the change as a whole (imperative, ≤ 72 chars). `tw` adds the `[ID]` prefix. Let the user adjust.
3. `tw --json commit -m "<message>"`. Different messages per repo → run once per repo with `-r <repo>`.

## Push (only when asked to push or open PRs)
4. `tw --json push`. Repos with nothing new are skipped.
5. `result: "rejected"` (exit 2), usually after a rebase sync → explain, and with the user's agreement run `tw push -r <repo> --force-with-lease` (hook asks for confirmation).

## Pull requests (optional)
6. One PR per pushed repo from `task/<ID>` into that repo's base from `task.yaml`, via the GitHub MCP tools or `gh pr create`.
   - Same title in all PRs.
   - Each body links the sibling PRs and states the merge order (providers such as shared libraries first).

Never `git push --force`/`-f` (blocked). Never commit in `repos/` or another task (blocked).
