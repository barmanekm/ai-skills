# Change a repo's base branch

Only on an explicit user request such as "sync lib with release/2.3 instead of main". If the user did not say it explicitly, ask; do not infer it.

1. Confirm repo and target branch. Check it exists: `git -C <repo> ls-remote --heads origin <branch>`.
2. `tw --json set-base <ID> <repo> <branch>`. The hook shows a confirmation prompt: expected.
   - `rebase` strategy: only the task's own commits move onto the new base (`rebase --onto`); commits inherited from the old base are dropped from the task branch.
   - `merge` strategy: the new base is merged in; commits from the old base stay. Warn the user.
   - `--no-sync` records the change without moving the branch (run `tw sync` later).
3. Conflicts → resolve as in sync.md.
4. Tell the user: if the branch was already pushed, the next push needs `tw push --repo <repo> --force-with-lease`, and an open PR must be retargeted to the new base.

Never edit `task.yaml` by hand (the hook blocks it).
