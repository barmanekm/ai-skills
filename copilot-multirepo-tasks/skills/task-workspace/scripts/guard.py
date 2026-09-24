#!/usr/bin/env python3
"""Copilot CLI preToolUse hook: keep the agent inside the active task.

Active task = the tasks/<ID> directory the Copilot session runs in.
  deny: edits outside the active task, anything in repos/, hand edits of task.yaml,
        git write commands aimed at other tasks, `tw <cmd> <other-ID>`, force push without lease
  ask:  tw set-base, tw finish, tw push --force-with-lease

A seatbelt, not a sandbox: shell commands are pattern-matched. Internal errors never
block a tool call (the hook exits 0 with no decision).
"""
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

EDIT_TOOLS = {"edit", "create", "str_replace_editor", "str_replace", "apply_patch", "write"}
SHELL_TOOLS = {"bash", "shell", "powershell"}
GIT_WRITE = re.compile(
    r"\bgit\b[^;&|]*?\b(commit|push|reset|rebase|merge|cherry-pick|revert|checkout|switch|"
    r"stash|clean|restore|am|apply|worktree\s+remove|branch\s+-[dDmM])\b")
TW_MUTATING = {"sync", "set-base", "commit", "push", "finish", "add"}


def decide(decision, reason):
    print(json.dumps({"permissionDecision": decision, "permissionDecisionReason": reason}))
    sys.exit(0)


def edit_paths(args):
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return []
    if not isinstance(args, dict):
        return []
    out = [args[k] for k in ("path", "file_path", "filePath", "file", "target") if isinstance(args.get(k), str)]
    for k in ("paths", "files"):
        if isinstance(args.get(k), list):
            out += [x for x in args[k] if isinstance(x, str)]
    for k in ("patch", "input", "content"):
        v = args.get(k)
        if isinstance(v, str) and "*** " in v:
            out += re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", v, re.M)
    return out


def tw_calls(tokens):
    """Yield (subcommand, remaining tokens) for every `tw ...` / `tw.py ...` invocation."""
    for i, t in enumerate(tokens):
        if os.path.basename(t) in ("tw", "tw.py", "tw.cmd") and i + 1 < len(tokens):
            j = i + 1
            while j < len(tokens) and tokens[j] == "--json":
                j += 1
            if j < len(tokens):
                yield tokens[j], tokens[j + 1:]


def main():
    from workspace import Workspace, find_root, real, within

    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    tool = str(data.get("toolName") or data.get("tool_name") or "").lower()
    args = data.get("toolArgs", data.get("tool_input", {}))
    cwd = data.get("cwd") or os.getcwd()

    root = find_root(cwd, fallback=False)
    if not root:
        return  # not in a task workspace: stay out of the way
    ws = Workspace(root)
    active = ws.active_task(cwd)
    task_dir = ws.task_dir(active) if active else None

    def resolve(p):
        p = os.path.expanduser(p.strip().strip("'\""))
        return real(p if os.path.isabs(p) else os.path.join(cwd, p))

    def check_target(path, what):
        if within(path, ws.repos_dir):
            decide("deny", f"{what} {path} is inside repos/ (canonical clones). Work in a task worktree instead.")
        if task_dir:
            if real(path) == real(ws.manifest_path(active)):
                decide("deny", "task.yaml must not be edited by hand. Use `tw set-base` / `tw add` "
                               "(set-base only when the user explicitly asks).")
            if within(path, root) and not within(path, task_dir):
                decide("deny", f"{what} {path} is outside the active task {active}. "
                               f"Only files under {task_dir} may be changed in this session.")

    if tool in EDIT_TOOLS:
        for p in edit_paths(args):
            check_target(resolve(p), "File")
        return
    if tool not in SHELL_TOOLS:
        return

    cmd = args.get("command") if isinstance(args, dict) else args
    if not isinstance(cmd, str) or not cmd.strip():
        return

    if re.search(r"\bgit\b[^;&|]*\bpush\b", cmd) and re.search(r"(\s--force(?!-with-lease)\b|\s-f\b)", cmd):
        decide("deny", "Plain force push is blocked. Use `tw push --force-with-lease` after confirming with the user.")
    if "task.yaml" in cmd and re.search(r"(sed\s+-i|>\s*\S*task\.yaml|\btee\b|\bmv\b|\brm\b|Set-Content|Out-File)", cmd):
        decide("deny", "task.yaml must not be modified by shell commands. Use `tw set-base` / `tw add`.")

    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()

    calls = list(tw_calls(tokens))
    for sub, rest in calls:  # denials first
        positional = [x for x in rest if not x.startswith("-")]
        if (sub in TW_MUTATING and active and positional and positional[0] != active
                and ws.has_task(positional[0])):
            decide("deny", f"This session belongs to task {active}; refusing `tw {sub} {positional[0]}`. "
                           f"Start a session in that task's directory instead.")
    for sub, rest in calls:  # then confirmations
        if sub == "set-base":
            decide("ask", "Changing a repo's base branch. Approve only if you explicitly asked for this.")
        if sub == "finish":
            decide("ask", "Finishing a task removes its worktrees and local branches.")
        if sub == "push" and "--force-with-lease" in rest:
            decide("ask", "Force-pushing (with lease) rewrites the remote task branch.")

    if GIT_WRITE.search(cmd):
        for t in tokens:
            if t.startswith(("/", "~", "..")) or ((os.sep in t or "/" in t) and not t.startswith("-")):
                p = resolve(t)
                if within(p, root):
                    check_target(p, "git operation on")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # never block on internal errors
        pass
