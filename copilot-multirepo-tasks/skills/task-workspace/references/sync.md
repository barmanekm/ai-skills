# Sync with base branches

Base branch per repo and the strategy (`rebase` default, or `merge`) come from `task.yaml`.

1. `tw --json sync` (all repos) or `tw --json sync <repo>...`. Uncommitted changes are autostashed and restored.
2. All `results` `up-to-date`/`synced` → one-line summary, done.
3. `result: "conflict"` for a repo (exit code 2):
   - The JSON lists `files`, and the exact `continue` / `abort` commands.
   - Open each file, resolve the markers keeping the intent of both sides. If the right resolution is unclear, stop and ask the user, showing both sides.
   - `git -C <repo> add <files>`, then run the `continue` command. Repeat if the rebase stops again.
   - To back out: the `abort` command restores the pre-sync state. Tell the user.
   - Other repos in the same run were already processed; see their results.
4. After a **rebase** sync of an already-pushed branch, the next push needs `--force-with-lease`; say so.
5. Repos in the task depend on each other → run the builds/tests after syncing to confirm they still work together.

Do not change which branch a repo syncs with here (see set-base.md).
