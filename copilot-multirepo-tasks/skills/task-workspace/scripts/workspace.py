"""Shared library for the multi-repo task workspace.

Used by tw.py (CLI) and the Copilot hooks (guard.py, session_context.py).
Standard library only; Python 3.8+.

Layout:
    <root>/.taskws                 marker file
    <root>/repos/<repo>            canonical clones (never edited directly)
    <root>/tasks/<ID>/task.yaml    task manifest
    <root>/tasks/<ID>/<repo>       git worktree on branch task/<ID>
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
from typing import Dict, List, Optional

MARKER = ".taskws"
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
STRATEGIES = ("rebase", "merge")


class TwError(Exception):
    """User-facing error."""


class GitError(TwError):
    pass


# --------------------------------------------------------------------------- paths

def real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def within(path: str, base: str) -> bool:
    path, base = real(path), real(base)
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _configured_root() -> Optional[str]:
    """Workspace recorded by the installer, then $TASK_WORKSPACE, then ~/workspace."""
    candidates = []
    cfg = os.path.join(SCRIPT_DIR, ".workspace")
    if os.path.isfile(cfg):
        with open(cfg, encoding="utf-8") as f:
            candidates.append(f.read().strip())
    candidates += [os.environ.get("TASK_WORKSPACE", ""), os.path.join(os.path.expanduser("~"), "workspace")]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, MARKER)):
            return real(c)
    return None


def find_root(start: Optional[str] = None, fallback: bool = True) -> Optional[str]:
    """Walk up from `start` to the directory containing .taskws."""
    d = real(start or os.getcwd())
    while True:
        if os.path.isfile(os.path.join(d, MARKER)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return _configured_root() if fallback else None


def branch_prefix() -> str:
    return os.environ.get("TW_BRANCH_PREFIX", "task/")


def branch_name(task_id: str) -> str:
    return branch_prefix() + task_id


def default_strategy() -> str:
    s = os.environ.get("TW_STRATEGY", "rebase")
    return s if s in STRATEGIES else "rebase"


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def valid_id(task_id: str) -> bool:
    return bool(_ID_RE.match(task_id or ""))


class Workspace:
    def __init__(self, root: str):
        self.root = real(root)

    @classmethod
    def discover(cls, start: Optional[str] = None) -> "Workspace":
        root = find_root(start)
        if not root:
            raise TwError("no task workspace found (no .taskws marker). Run 'tw init' in your workspace root.")
        return cls(root)

    @property
    def tasks_dir(self) -> str:
        return os.path.join(self.root, "tasks")

    @property
    def repos_dir(self) -> str:
        return os.path.join(self.root, "repos")

    @property
    def archive_dir(self) -> str:
        return os.path.join(self.tasks_dir, ".archive")

    def task_dir(self, task_id: str) -> str:
        return os.path.join(self.tasks_dir, task_id)

    def manifest_path(self, task_id: str) -> str:
        return os.path.join(self.task_dir(task_id), "task.yaml")

    def repo_home(self, repo: str) -> str:
        return os.path.join(self.repos_dir, repo)

    def worktree(self, task_id: str, repo: str) -> str:
        return os.path.join(self.task_dir(task_id), repo)

    def has_task(self, task_id: str) -> bool:
        return valid_id(task_id) and os.path.isfile(self.manifest_path(task_id))

    def task_ids(self) -> List[str]:
        if not os.path.isdir(self.tasks_dir):
            return []
        return sorted(t for t in os.listdir(self.tasks_dir) if not t.startswith(".") and self.has_task(t))

    def repo_names(self) -> List[str]:
        if not os.path.isdir(self.repos_dir):
            return []
        return sorted(r for r in os.listdir(self.repos_dir)
                      if os.path.exists(os.path.join(self.repos_dir, r, ".git")))

    def active_task(self, cwd: Optional[str] = None) -> Optional[str]:
        """Task ID if cwd is inside tasks/<ID>, else None."""
        cwd = real(cwd or os.getcwd())
        if not within(cwd, self.tasks_dir) or cwd == real(self.tasks_dir):
            return None
        tid = os.path.relpath(cwd, real(self.tasks_dir)).split(os.sep)[0]
        return tid if self.has_task(tid) else None


# --------------------------------------------------------------------------- task.yaml
# A strict YAML subset is parsed by hand so behaviour is identical everywhere and no
# third-party package is needed. All values are strings (so branch "1.10" stays "1.10").
# Supported: comments, blank lines, `key: value`, one level of nested mapping,
# plain / "double" / 'single' quoted scalars.

_PLAIN_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9._/@+-]*$")
_RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "y", "n", "~"}


def yaml_scalar(value: str) -> str:
    if _PLAIN_SAFE.match(value) and value.lower() not in _RESERVED:
        return value
    return json.dumps(value, ensure_ascii=False)  # JSON strings are valid YAML double-quoted scalars


def _parse_scalar(raw: str, lineno: int) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] == '"':
        try:
            val, end = json.JSONDecoder().raw_decode(raw)
        except ValueError:
            raise TwError(f"task.yaml line {lineno}: bad double-quoted string")
        rest = raw[end:].strip()
        if rest and not rest.startswith("#"):
            raise TwError(f"task.yaml line {lineno}: unexpected text after string")
        return str(val)
    if raw[0] == "'":
        out, i = [], 1
        while i < len(raw):
            if raw[i] == "'":
                if i + 1 < len(raw) and raw[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                rest = raw[i + 1:].strip()
                if rest and not rest.startswith("#"):
                    raise TwError(f"task.yaml line {lineno}: unexpected text after string")
                return "".join(out)
            out.append(raw[i])
            i += 1
        raise TwError(f"task.yaml line {lineno}: unterminated single-quoted string")
    m = re.search(r"\s#", raw)
    return (raw[:m.start()] if m else raw).strip()


def yaml_load(text: str) -> Dict[str, object]:
    result: Dict[str, object] = {}
    block: Optional[Dict[str, str]] = None
    for n, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        lead = line[: len(line) - len(line.lstrip())]
        if "\t" in lead:
            raise TwError(f"task.yaml line {n}: tabs are not allowed for indentation")
        key, sep, rest = stripped.partition(":")
        if not sep or not key.strip():
            raise TwError(f"task.yaml line {n}: expected 'key: value'")
        key = key.strip().strip("\"'")
        if not lead:
            if rest.strip() in ("", "{}"):
                block = {}
                result[key] = block
            else:
                result[key] = _parse_scalar(rest, n)
                block = None
        else:
            if block is None:
                raise TwError(f"task.yaml line {n}: unexpected indentation")
            block[key] = _parse_scalar(rest, n)
    return result


class Task:
    def __init__(self, task_id: str, title: str, strategy: str, created: str, repos: Dict[str, str]):
        self.id = task_id
        self.title = title
        self.strategy = strategy
        self.created = created
        self.repos = dict(repos)  # repo -> base branch (insertion ordered)

    @classmethod
    def new(cls, task_id: str, title: str = "", strategy: Optional[str] = None) -> "Task":
        return cls(task_id, title or task_id, strategy or default_strategy(),
                   datetime.date.today().isoformat(), {})

    @classmethod
    def load(cls, ws: Workspace, task_id: str) -> "Task":
        if not ws.has_task(task_id):
            raise TwError(f"task '{task_id}' not found in {ws.tasks_dir}")
        with open(ws.manifest_path(task_id), encoding="utf-8") as f:
            data = yaml_load(f.read())
        repos = data.get("repos") or {}
        if not isinstance(repos, dict):
            raise TwError(f"task.yaml of {task_id}: 'repos' must be a mapping of repo: base-branch")
        strategy = str(data.get("strategy") or default_strategy())
        if strategy not in STRATEGIES:
            raise TwError(f"task.yaml of {task_id}: strategy must be one of {', '.join(STRATEGIES)}")
        return cls(task_id, str(data.get("title") or task_id), strategy, str(data.get("created") or ""), repos)

    def dump(self) -> str:
        lines = [
            "# Managed by tw. Change bases with `tw set-base`, add repos with `tw add`.",
            f"id: {yaml_scalar(self.id)}",
            f"title: {yaml_scalar(self.title)}",
            f"strategy: {self.strategy}",
            f"created: {yaml_scalar(self.created)}",
            "repos:",
        ]
        lines += [f"  {yaml_scalar(r)}: {yaml_scalar(b)}" for r, b in self.repos.items()]
        return "\n".join(lines) + "\n"

    def save(self, ws: Workspace) -> None:
        path = ws.manifest_path(self.id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.dump())
        os.replace(tmp, path)

    @property
    def branch(self) -> str:
        return branch_name(self.id)


# --------------------------------------------------------------------------- git

def git_run(cwd: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, env=env)


def git(cwd: str, *args: str) -> str:
    p = git_run(cwd, *args)
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed in {cwd}:\n{msg}")
    return p.stdout.strip()


def git_ok(cwd: str, *args: str) -> bool:
    return git_run(cwd, *args).returncode == 0


def is_git_repo(path: str) -> bool:
    return os.path.exists(os.path.join(path, ".git"))


def fetch(cwd: str) -> None:
    git(cwd, "fetch", "--prune", "--quiet", "origin")


def remote_branch_exists(cwd: str, branch: str) -> bool:
    return git_ok(cwd, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}")


def local_branch_exists(cwd: str, branch: str) -> bool:
    return git_ok(cwd, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")


def current_branch(cwd: str) -> Optional[str]:
    p = git_run(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    return p.stdout.strip() if p.returncode == 0 else None


def default_branch(cwd: str) -> str:
    for attempt in range(2):
        p = git_run(cwd, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip().split("/", 1)[1]
        if attempt == 0:
            git_run(cwd, "remote", "set-head", "origin", "--auto")
    for b in ("main", "master", "develop"):
        if remote_branch_exists(cwd, b):
            return b
    raise TwError(f"cannot determine the default branch of {cwd}")


def changed_files(cwd: str) -> List[str]:
    out = git(cwd, "status", "--porcelain")
    return [l for l in out.splitlines() if l.strip()]


def is_dirty(cwd: str) -> bool:
    return bool(changed_files(cwd))


def rev_count(cwd: str, rev_range: str) -> Optional[int]:
    p = git_run(cwd, "rev-list", "--count", rev_range)
    return int(p.stdout.strip()) if p.returncode == 0 else None


def upstream(cwd: str) -> Optional[str]:
    p = git_run(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return p.stdout.strip() if p.returncode == 0 else None


def short_sha(cwd: str) -> str:
    return git(cwd, "rev-parse", "--short", "HEAD")


def conflicted_files(cwd: str) -> List[str]:
    out = git_run(cwd, "diff", "--name-only", "--diff-filter=U").stdout
    return [l for l in out.splitlines() if l.strip()]


def ff_local_base(home: str, base: str) -> Optional[str]:
    """Best-effort fast-forward of the canonical clone's local base branch. Returns a warning or None."""
    if not local_branch_exists(home, base):
        return None
    if current_branch(home) == base:
        if is_dirty(home):
            return f"{os.path.basename(home)}: local {base} has uncommitted changes; not fast-forwarded"
        if not git_ok(home, "merge", "--ff-only", "--quiet", f"origin/{base}"):
            return f"{os.path.basename(home)}: local {base} has diverged from origin; not fast-forwarded"
    else:
        git_run(home, "fetch", "--quiet", "origin", f"{base}:{base}")  # refuses non-ff silently
    return None


# --------------------------------------------------------------------------- status

def repo_status(ws: Workspace, task: Task, repo: str, do_fetch: bool = False) -> Dict[str, object]:
    base = task.repos[repo]
    wt = ws.worktree(task.id, repo)
    st: Dict[str, object] = {"repo": repo, "path": wt, "base": base, "exists": os.path.isdir(wt)}
    if not st["exists"]:
        return st
    if do_fetch:
        git_run(wt, "fetch", "--prune", "--quiet", "origin")
    up = upstream(wt)
    st.update({
        "branch": current_branch(wt) or "DETACHED",
        "on_task_branch": current_branch(wt) == task.branch,
        "ahead": rev_count(wt, f"origin/{base}..HEAD"),
        "behind": rev_count(wt, f"HEAD..origin/{base}"),
        "changed": len(changed_files(wt)),
        "upstream": up,
        "unpushed": rev_count(wt, "@{u}..HEAD") if up else None,
        "remote_ahead": rev_count(wt, "HEAD..@{u}") if up else None,
        "in_progress": _in_progress(wt),
    })
    return st


def _in_progress(wt: str) -> Optional[str]:
    gitdir = git_run(wt, "rev-parse", "--git-dir").stdout.strip()
    if not gitdir:
        return None
    gitdir = gitdir if os.path.isabs(gitdir) else os.path.join(wt, gitdir)
    if os.path.isdir(os.path.join(gitdir, "rebase-merge")) or os.path.isdir(os.path.join(gitdir, "rebase-apply")):
        return "rebase"
    if os.path.isfile(os.path.join(gitdir, "MERGE_HEAD")):
        return "merge"
    return None


def format_status_line(st: Dict[str, object]) -> str:
    if not st.get("exists"):
        return f"  {st['repo']:<22} MISSING WORKTREE (restore with: tw add <ID> {st['repo']})"
    push = "not pushed" if not st["upstream"] else f"unpushed:{st['unpushed']}"
    if st.get("remote_ahead"):
        push += f" remote-ahead:{st['remote_ahead']}"
    flag = f"  [{st['in_progress']} in progress]" if st.get("in_progress") else ""
    if not st.get("on_task_branch"):
        flag += "  [NOT ON TASK BRANCH]"
    return (f"  {st['repo']:<22} {str(st['branch']):<26} base:{st['base']:<16} "
            f"+{st['ahead']}/-{st['behind']}  changed:{st['changed']}  {push}{flag}")
