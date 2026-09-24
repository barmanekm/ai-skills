#!/usr/bin/env python3
"""tw - multi-repo task workspace manager (git worktrees, one per repo per task).

Run `tw help` for usage. Read commands accept --json for machine-readable output.
Exit codes: 0 ok, 1 error, 2 partial failure (e.g. sync conflicts, rejected push).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from workspace import (  # noqa: E402
    STRATEGIES, Task, TwError, Workspace, changed_files, conflicted_files, current_branch,
    default_branch, fetch, ff_local_base, format_status_line, git, git_run, is_dirty, is_git_repo,
    local_branch_exists, rev_count, remote_branch_exists, repo_status, short_sha, upstream, valid_id,
)


# --------------------------------------------------------------------------- output

class Out:
    def __init__(self, as_json: bool):
        self.json = as_json
        color = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and not as_json
        self.c = (lambda code, s: f"\033[{code}m{s}\033[0m") if color else (lambda code, s: s)

    def info(self, msg: str = "") -> None:
        if not self.json:
            print(msg)

    def ok(self, msg: str) -> None:
        self.info(f"{self.c('32', '✓')} {msg}")

    def warn(self, msg: str) -> None:
        print(f"{self.c('33', 'warn:')} {msg}", file=sys.stderr)

    def bold(self, s: str) -> str:
        return self.c("1", s)

    def emit(self, data) -> None:
        if self.json:
            print(json.dumps(data, indent=2))


PARTIAL = 2


# --------------------------------------------------------------------------- helpers

def resolve_task(ws: Workspace, task_id: str | None) -> Task:
    tid = task_id or ws.active_task()
    if not tid:
        raise TwError("no task ID given and the current directory is not inside a task")
    return Task.load(ws, tid)


def split_id_and_repos(ws: Workspace, items: list[str]) -> tuple[str | None, list[str]]:
    """Allow `tw sync [ID] [repo...]`: first item is an ID only if such a task exists."""
    if items and ws.has_task(items[0]):
        return items[0], items[1:]
    return None, items


def selected_repos(task: Task, repos: list[str] | None) -> list[str]:
    if not repos:
        return list(task.repos)
    unknown = [r for r in repos if r not in task.repos]
    if unknown:
        raise TwError(f"not part of task {task.id}: {', '.join(unknown)}")
    return repos


def require_task_branch(ws: Workspace, task: Task, repo: str) -> str:
    wt = ws.worktree(task.id, repo)
    if not os.path.isdir(wt):
        raise TwError(f"{repo}: worktree missing at {wt}")
    cur = current_branch(wt)
    if cur != task.branch:
        raise TwError(f"{repo} is on '{cur}', expected '{task.branch}'. Refusing to touch it.")
    return wt


def add_worktree(ws: Workspace, task: Task, repo: str, base: str | None) -> tuple[str, str]:
    home = ws.repo_home(repo)
    if not is_git_repo(home):
        raise TwError(f"repos/{repo} not found. Clone it first: tw clone <url> {repo}")
    wt = ws.worktree(task.id, repo)
    if os.path.exists(wt):
        raise TwError(f"{wt} already exists")
    fetch(home)
    base = base or default_branch(home)
    if not remote_branch_exists(home, base):
        raise TwError(f"{repo}: origin/{base} does not exist")
    warning = ff_local_base(home, base)
    br = task.branch
    if local_branch_exists(home, br):
        git(home, "worktree", "add", wt, br)
        how = f"reused local branch {br}"
    elif remote_branch_exists(home, br):
        git(home, "worktree", "add", "--track", "-b", br, wt, f"origin/{br}")
        how = f"resumed origin/{br}"
    else:
        git(home, "worktree", "add", "--no-track", "-b", br, wt, f"origin/{base}")
        how = f"new branch from origin/{base}"
    return base, how + (f" ({warning})" if warning else "")


AGENTS_MD = """# Task {id}

This directory is a multi-repo task. Each subdirectory is a git worktree on branch
`{branch}`. Repos and base branches are in `task.yaml` (never edit it by hand).

- Only edit files inside this directory.
- Use the task-workspace skill / `tw` for git lifecycle (status, sync, commit, push).
- Change a repo's base branch only when the user explicitly asks.
- Changes spanning repos: use the cross-repo-change skill (providers before consumers).
"""


# --------------------------------------------------------------------------- commands

def cmd_init(a, out):
    root = os.path.realpath(a.dir or os.getcwd())
    for d in ("repos", "tasks"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    open(os.path.join(root, ".taskws"), "a").close()
    out.ok(f"initialised task workspace at {root}")
    out.emit({"root": root})


def cmd_clone(a, out):
    ws = Workspace.discover()
    name = a.name or os.path.basename(a.url.rstrip("/").rstrip(os.sep))
    if not a.name and name.endswith(".git"):
        name = name[:-4]
    dest = ws.repo_home(name)
    if os.path.exists(dest):
        raise TwError(f"repos/{name} already exists")
    p = git_run(ws.repos_dir, "clone", a.url, dest)
    if p.returncode != 0:
        raise TwError(f"clone failed:\n{p.stderr.strip()}")
    base = default_branch(dest)
    out.ok(f"cloned {name} (default branch: {base})")
    out.emit({"repo": name, "path": dest, "default_branch": base})


def cmd_start(a, out):
    ws = Workspace.discover()
    if not valid_id(a.id):
        raise TwError("task ID must be letters, digits, '.', '_' or '-' (e.g. PROJ-123)")
    if ws.has_task(a.id):
        raise TwError(f"task {a.id} already exists. Use 'tw add {a.id} <repo>' to add repos.")
    overrides = {}
    for item in a.base or []:
        repo, sep, branch = item.partition("=")
        if not sep or not branch:
            raise TwError(f"--base expects repo=branch, got '{item}'")
        overrides[repo] = branch
    unknown = set(overrides) - set(a.repos)
    if unknown:
        raise TwError(f"--base given for repos not in the task: {', '.join(sorted(unknown))}")

    task = Task.new(a.id, a.title, a.strategy)
    task.save(ws)
    out.info(out.bold(f"Starting task {task.id}"))
    results = []
    try:
        for repo in a.repos:
            base, how = add_worktree(ws, task, repo, overrides.get(repo))
            task.repos[repo] = base
            task.save(ws)
            wt = ws.worktree(task.id, repo)
            results.append({"repo": repo, "base": base, "path": wt, "sha": short_sha(wt), "how": how})
            out.ok(f"{repo:<20} base:{base:<16} @ {short_sha(wt)}  ({how})")
    except TwError:
        if not task.repos:  # nothing created: leave no half-made task behind
            shutil.rmtree(ws.task_dir(task.id), ignore_errors=True)
        raise
    with open(os.path.join(ws.task_dir(task.id), "AGENTS.md"), "w", encoding="utf-8") as f:
        f.write(AGENTS_MD.format(id=task.id, branch=task.branch))
    out.info(f"\ncd \"{ws.task_dir(task.id)}\"")
    out.emit({"task": task.id, "dir": ws.task_dir(task.id), "branch": task.branch, "repos": results})


def cmd_add(a, out):
    ws = Workspace.discover()
    task = Task.load(ws, a.id)
    if a.repo in task.repos:
        raise TwError(f"{a.repo} is already part of {task.id}")
    base, how = add_worktree(ws, task, a.repo, a.base_branch)
    task.repos[a.repo] = base
    task.save(ws)
    out.ok(f"added {a.repo} to {task.id} (base origin/{base}; {how})")
    out.emit({"task": task.id, "repo": a.repo, "base": base, "how": how})


def cmd_list(a, out):
    ws = Workspace.discover()
    data = []
    for tid in ws.task_ids():
        t = Task.load(ws, tid)
        data.append({"id": t.id, "title": t.title, "strategy": t.strategy, "created": t.created,
                     "dir": ws.task_dir(t.id), "repos": t.repos})
        out.info(f"{out.bold(f'{t.id:<20}')} {t.title}")
        for r, b in t.repos.items():
            out.info(f"    {r:<24} ← {b}")
    if not data:
        out.info("no tasks. Start one with: tw start <ID> <repo>...")
    out.info(f"\nrepos available: {', '.join(ws.repo_names()) or '(none; use tw clone)'}")
    out.emit({"root": ws.root, "tasks": data, "repos_available": ws.repo_names()})


def cmd_path(a, out):
    ws = Workspace.discover()
    task = resolve_task(ws, a.id)
    out.info(ws.task_dir(task.id))
    out.emit({"task": task.id, "dir": ws.task_dir(task.id)})


def cmd_current(a, out):
    ws = Workspace.discover()
    tid = ws.active_task()
    if not tid:
        out.emit({"task": None})
        raise TwError("not inside a task directory")
    out.info(tid)
    out.emit({"task": tid, "dir": ws.task_dir(tid)})


def cmd_status(a, out):
    ws = Workspace.discover()
    ids = ws.task_ids() if a.all else [resolve_task(ws, a.id).id]
    data = []
    for tid in ids:
        t = Task.load(ws, tid)
        repos = [repo_status(ws, t, r, a.fetch) for r in t.repos]
        data.append({"id": t.id, "title": t.title, "strategy": t.strategy, "branch": t.branch, "repos": repos})
        out.info(f"{out.bold(t.id)}  {t.title}  [strategy: {t.strategy}]")
        for st in repos:
            out.info(format_status_line(st))
    out.emit(data if a.all else data[0])


def cmd_sync(a, out):
    ws = Workspace.discover()
    tid, repos = split_id_and_repos(ws, a.items)
    task = resolve_task(ws, tid)
    results, failed = [], False
    for repo in selected_repos(task, repos):
        base = task.repos[repo]
        wt = require_task_branch(ws, task, repo)
        fetch(wt)
        warning = ff_local_base(ws.repo_home(repo), base)
        if warning:
            out.warn(warning)
        behind = rev_count(wt, f"HEAD..origin/{base}")
        if behind == 0:
            results.append({"repo": repo, "result": "up-to-date"})
            out.ok(f"{repo} already up to date with origin/{base}")
            continue
        args = (["rebase", "--autostash", f"origin/{base}"] if task.strategy == "rebase"
                else ["merge", "--autostash", "--no-edit", f"origin/{base}"])
        p = git_run(wt, *args)
        if p.returncode == 0:
            results.append({"repo": repo, "result": "synced", "commits_pulled": behind, "sha": short_sha(wt)})
            out.ok(f"{repo} synced {behind} commit(s) from origin/{base} ({task.strategy}) @ {short_sha(wt)}")
        else:
            failed = True
            files = conflicted_files(wt)
            results.append({"repo": repo, "result": "conflict", "files": files, "path": wt,
                            "continue": f"git -C \"{wt}\" {task.strategy} --continue",
                            "abort": f"git -C \"{wt}\" {task.strategy} --abort"})
            out.warn(f"{repo}: {task.strategy} onto origin/{base} has conflicts in: {', '.join(files) or '?'}\n"
                     f"      resolve, `git add`, then: git -C \"{wt}\" {task.strategy} --continue  (or --abort)")
    out.emit({"task": task.id, "strategy": task.strategy, "results": results})
    return PARTIAL if failed else 0


def cmd_set_base(a, out):
    ws = Workspace.discover()
    task = Task.load(ws, a.id)
    if a.repo not in task.repos:
        raise TwError(f"{a.repo} is not part of {task.id}")
    old, new = task.repos[a.repo], a.branch
    if old == new:
        out.ok(f"{a.repo} already bases on {new}")
        out.emit({"repo": a.repo, "base": new, "changed": False})
        return 0
    wt = require_task_branch(ws, task, a.repo)
    fetch(wt)
    if not remote_branch_exists(wt, new):
        raise TwError(f"{a.repo}: origin/{new} does not exist")
    task.repos[a.repo] = new
    task.save(ws)
    out.ok(f"{a.repo} base changed: {old} → {new}")
    result = {"repo": a.repo, "old_base": old, "base": new, "changed": True, "synced": False}
    if a.no_sync:
        out.info(f"  (branch not moved; run 'tw sync {task.id} {a.repo}')")
        out.emit(result)
        return 0
    if task.strategy == "merge":
        out.warn(f"{a.repo} uses merge: commits from '{old}' that are not in '{new}' stay in the branch.")
        p = git_run(wt, "merge", "--autostash", "--no-edit", f"origin/{new}")
    else:
        # move only the task's own commits (those after the fork point with the OLD base)
        fork = git_run(wt, "merge-base", "HEAD", f"origin/{old}").stdout.strip()
        p = (git_run(wt, "rebase", "--autostash", "--onto", f"origin/{new}", fork) if fork
             else git_run(wt, "rebase", "--autostash", f"origin/{new}"))
    if p.returncode != 0:
        result["conflict_files"] = conflicted_files(wt)
        out.emit(result)
        raise TwError(f"{task.strategy} conflicts in {wt}: {', '.join(result['conflict_files'])}. "
                      f"Resolve, then 'git -C \"{wt}\" {task.strategy} --continue'.")
    result.update(synced=True, sha=short_sha(wt))
    out.ok(f"{a.repo} now on origin/{new} @ {short_sha(wt)}. If already pushed, the next push needs --force-with-lease.")
    out.emit(result)


def cmd_diff(a, out):
    ws = Workspace.discover()
    task = resolve_task(ws, a.id)
    data = []
    for repo, base in task.repos.items():
        wt = ws.worktree(task.id, repo)
        if not os.path.isdir(wt):
            continue
        commits = [l for l in git_run(wt, "log", "--format=%h %s", f"origin/{base}..HEAD").stdout.splitlines() if l]
        files = changed_files(wt)
        if not commits and not files:
            continue
        stat = git_run(wt, "diff", "--shortstat", "HEAD").stdout.strip()
        data.append({"repo": repo, "base": base, "commits": commits, "uncommitted": files, "stat": stat})
        out.info(out.bold(f"── {repo}") + f" (vs origin/{base})")
        for c in commits:
            out.info(f"  commit {c}")
        for f in files:
            out.info(f"  {f}")
        if stat:
            out.info(f"  {stat}")
    if not data:
        out.info("no changes in any repo of this task")
    out.emit({"task": task.id, "repos": data})


def cmd_commit(a, out):
    ws = Workspace.discover()
    task = resolve_task(ws, a.id)
    msg = a.message.strip()
    if not msg:
        raise TwError("empty commit message")
    if not (msg.startswith(task.id) or msg.startswith(f"[{task.id}]")):
        msg = f"[{task.id}] {msg}"
    results = []
    for repo in selected_repos(task, a.repo):
        wt = ws.worktree(task.id, repo)
        if not os.path.isdir(wt) or not is_dirty(wt):
            continue
        require_task_branch(ws, task, repo)
        git(wt, "add", "-A")
        git(wt, "commit", "--quiet", "-m", msg)
        line = git(wt, "log", "-1", "--format=%h %s")
        results.append({"repo": repo, "commit": line})
        out.ok(f"{repo}: {line}")
    if not results:
        out.info("nothing to commit")
    out.emit({"task": task.id, "committed": results})


def cmd_push(a, out):
    ws = Workspace.discover()
    task = resolve_task(ws, a.id)
    results, failed = [], False
    for repo in selected_repos(task, a.repo):
        wt = require_task_branch(ws, task, repo)
        base = task.repos[repo]
        if rev_count(wt, f"origin/{base}..HEAD") == 0:
            continue
        if upstream(wt) and git(wt, "rev-parse", "HEAD") == git(wt, "rev-parse", "@{u}"):
            continue
        if is_dirty(wt):
            out.warn(f"{repo} has uncommitted changes (pushing committed work only)")
        args = ["push", "--quiet", "-u", "origin", task.branch]
        if a.force_with_lease:
            args.insert(1, "--force-with-lease")
        p = git_run(wt, *args)
        if p.returncode == 0:
            results.append({"repo": repo, "result": "pushed"})
            out.ok(f"{repo} pushed {task.branch}")
            continue
        failed = True
        err = p.stderr.strip()
        rejected = any(k in err.lower() for k in ("non-fast-forward", "rejected", "fetch first", "stale info"))
        results.append({"repo": repo, "result": "rejected" if rejected else "error", "stderr": err})
        if rejected:
            out.warn(f"{repo}: push rejected (non-fast-forward, usually after a rebase sync). "
                     f"If intended: tw push {task.id} --repo {repo} --force-with-lease")
        else:
            out.warn(f"{repo}: push failed:\n{err}")
    if not results:
        out.info("nothing to push")
    out.emit({"task": task.id, "results": results})
    return PARTIAL if failed else 0


def cmd_finish(a, out):
    ws = Workspace.discover()
    task = Task.load(ws, a.id)
    if os.path.realpath(os.getcwd()).startswith(os.path.realpath(ws.task_dir(task.id))):
        raise TwError(f"cd out of {ws.task_dir(task.id)} first")
    problems = []
    for repo, base in task.repos.items():
        wt = ws.worktree(task.id, repo)
        if not os.path.isdir(wt):
            continue
        if is_dirty(wt):
            problems.append(f"{repo}: uncommitted changes")
        if upstream(wt):
            if rev_count(wt, "@{u}..HEAD"):
                problems.append(f"{repo}: unpushed commits")
        elif rev_count(wt, f"origin/{base}..HEAD"):
            problems.append(f"{repo}: commits never pushed")
    if problems and not a.force:
        out.emit({"task": task.id, "finished": False, "problems": problems})
        raise TwError(f"refusing to finish {task.id}:\n  " + "\n  ".join(problems) +
                      "\nFix these, or re-run with --force (DISCARDS that work).")
    for repo in task.repos:
        wt, home = ws.worktree(task.id, repo), ws.repo_home(repo)
        if os.path.isdir(wt):
            args = ["worktree", "remove"] + (["--force", "--force"] if a.force else []) + [wt]
            git(home, *args)
        git_run(home, "branch", "-D", task.branch)
        out.ok(f"{repo}: worktree and local branch removed (remote branch untouched)")
    os.makedirs(ws.archive_dir, exist_ok=True)
    dest = os.path.join(ws.archive_dir, f"{task.id}-{time.strftime('%Y%m%d%H%M%S')}")
    shutil.move(ws.task_dir(task.id), dest)
    out.ok(f"task {task.id} archived to {dest}")
    out.emit({"task": task.id, "finished": True, "archived_to": dest, "discarded": problems})


# --------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tw", description="Multi-repo task workspace manager.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    def add(name, fn, help_, aliases=()):
        sp = sub.add_parser(name, help=help_, aliases=list(aliases))
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, "create a workspace (repos/, tasks/, .taskws)")
    sp.add_argument("dir", nargs="?")
    sp = add("clone", cmd_clone, "clone a repo into repos/")
    sp.add_argument("url"); sp.add_argument("name", nargs="?")
    sp = add("start", cmd_start, "start a task: fetch latest base, create worktrees")
    sp.add_argument("id"); sp.add_argument("repos", nargs="+")
    sp.add_argument("-t", "--title", default="")
    sp.add_argument("-b", "--base", action="append", metavar="REPO=BRANCH", help="non-default base (repeatable)")
    sp.add_argument("-s", "--strategy", choices=STRATEGIES, default=None)
    sp = add("add", cmd_add, "add a repo to an existing task")
    sp.add_argument("id"); sp.add_argument("repo"); sp.add_argument("base_branch", nargs="?")
    add("list", cmd_list, "list tasks and available repos", aliases=["ls"])
    sp = add("path", cmd_path, "print a task's directory")
    sp.add_argument("id", nargs="?")
    add("current", cmd_current, "print the task of the current directory")
    sp = add("status", cmd_status, "per-repo branch, ahead/behind, changes, push state", aliases=["st"])
    sp.add_argument("id", nargs="?"); sp.add_argument("-a", "--all", action="store_true")
    sp.add_argument("--fetch", action="store_true")
    sp = add("sync", cmd_sync, "update task repos from their base branches")
    sp.add_argument("items", nargs="*", metavar="[ID] [repo]")
    sp = add("set-base", cmd_set_base, "change the branch a repo syncs with")
    sp.add_argument("id"); sp.add_argument("repo"); sp.add_argument("branch")
    sp.add_argument("--no-sync", action="store_true")
    sp = add("diff", cmd_diff, "commits and uncommitted changes per repo")
    sp.add_argument("id", nargs="?")
    sp = add("commit", cmd_commit, "commit all changes per repo with [ID] prefix")
    sp.add_argument("id", nargs="?"); sp.add_argument("-m", "--message", required=True)
    sp.add_argument("-r", "--repo", action="append")
    sp = add("push", cmd_push, "push task branches")
    sp.add_argument("id", nargs="?"); sp.add_argument("-r", "--repo", action="append")
    sp.add_argument("--force-with-lease", action="store_true")
    sp = add("finish", cmd_finish, "remove worktrees + local branches, archive the task")
    sp.add_argument("id"); sp.add_argument("--force", action="store_true")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("help", "-h", "--help"):
        parser.print_help()
        return 0
    a = parser.parse_args(argv)
    out = Out(getattr(a, "json", False))
    if not getattr(a, "fn", None):
        parser.print_help()
        return 1
    try:
        return a.fn(a, out) or 0
    except TwError as e:
        if out.json:
            print(json.dumps({"error": str(e)}))
        else:
            print(f"{out.c('31', 'error:')} {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
