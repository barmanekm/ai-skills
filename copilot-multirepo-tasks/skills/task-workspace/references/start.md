# Start a task / add a repo

1. Collect only what is missing:
   - **Task ID**: e.g. a ticket key `PROJ-123` (letters, digits, `.`, `_`, `-`).
   - **Repos**: run `tw --json list` for `repos_available`. If the user describes the work instead of naming repos, propose the likely repos and confirm.
   - **Title**: optional, one line.
   - **Base overrides**: only if the user explicitly names a branch ("lib against release/2.3"). Otherwise each repo uses its remote default branch.
2. Repo not cloned yet → ask for the URL, run `tw clone <url> [name]`.
3. Run `tw --json start <ID> <repo>... -t "<title>" [-b repo=branch]...`
   - Fetches each repo, fast-forwards its local base, branches `task/<ID>` from `origin/<base>`: the task starts from the latest code.
   - If `origin/task/<ID>` exists (started elsewhere), it resumes that branch (`how: "resumed ..."`). If that task used a non-default base, it must be passed again with `-b`.
   - If the first repo fails, nothing is left behind; if a later repo fails, the earlier ones stay: fix the cause and `tw add` the rest.
4. Existing task, extra repo: `tw add <ID> <repo> [base]`.
5. Report one line per repo (base, sha), then give the switch command:
   ```
   cd "$(tw path <ID>)" && copilot
   ```
   Code work happens in that new session, which is scoped to the task.
