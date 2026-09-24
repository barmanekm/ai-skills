#!/usr/bin/env python3
"""Copilot CLI sessionStart hook: inject the active task's context into the session."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))


def main():
    from workspace import Task, Workspace, find_root, format_status_line, real, repo_status

    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    cwd = real(data.get("cwd") or os.getcwd())
    root = find_root(cwd, fallback=False)
    if not root:
        return
    ws = Workspace(root)
    active = ws.active_task(cwd)

    if active:
        t = Task.load(ws, active)
        status = "\n".join(format_status_line(repo_status(ws, t, r)) for r in t.repos)
        with open(ws.manifest_path(active), encoding="utf-8") as f:
            manifest = f.read().strip()
        ctx = (f"ACTIVE TASK: {active} (directory {ws.task_dir(active)}, branch {t.branch}).\n"
               "Only edit, commit and push inside this directory; a hook enforces this. Use the "
               "task-workspace skill (`tw`) for git lifecycle and cross-repo-change for multi-repo code changes. "
               "Never change a base branch unless the user explicitly asks.\n\n"
               f"task.yaml:\n{manifest}\n\nstatus (+ahead/-behind base):\n{status}")
    elif cwd == ws.root:
        lines = []
        for tid in ws.task_ids():
            t = Task.load(ws, tid)
            lines.append(f"- {tid}: {t.title} [{', '.join(f'{r}←{b}' for r, b in t.repos.items())}]")
        ctx = ("You are at the ROOT of a multi-repo task workspace, not inside a task. Do not edit code here. "
               "Use the task-workspace skill to start, list, switch or finish tasks. Coding happens in a session "
               "started inside tasks/<ID>.\n\nTasks:\n" + ("\n".join(lines) or "(none)") +
               f"\n\nRepos available: {', '.join(ws.repo_names()) or '(none)'}")
    else:
        return
    print(json.dumps({"additionalContext": ctx}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
