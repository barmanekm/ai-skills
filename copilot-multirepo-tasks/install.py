#!/usr/bin/env python3
"""Install the multi-repo task toolkit for GitHub Copilot CLI (macOS, Linux, Windows).

    python3 install.py [WORKSPACE] [--copilot-home DIR] [--bin DIR]

Safe to re-run. Never touches repos/ or tasks/. Migrates the earlier bash-based install.
"""
import argparse
import json
import os
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = ("task-workspace", "cross-repo-change")
OLD_SKILLS = ("task-start", "task-switch", "task-status", "task-sync", "task-set-base", "task-commit", "task-finish")
BEGIN, END = "<!-- multirepo-tasks:begin -->", "<!-- multirepo-tasks:end -->"
WINDOWS = os.name == "nt"

INSTRUCTIONS = """{begin}
## Multi-repo task workspace ({ws})

When the current directory is inside `{ws}`:
- Work is organised in tasks: `tasks/<ID>/` holds `task.yaml` plus one git worktree per repository on branch `task/<ID>`. `repos/` holds canonical clones that are never edited directly.
- The active task is the one whose directory the session started in. Only edit, commit and push inside it.
- Use the task-workspace skill (`tw` CLI) for all git lifecycle work; the cross-repo-change skill for code changes spanning repos.
- Never change which branch a repository syncs with unless the user explicitly asks. Never edit `task.yaml` by hand.
{end}
"""


def ok(msg):
    print(f"✓ {msg}")


def warn(msg):
    print(f"! {msg}")


def python_cmd():
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if not in_venv:
        return sys.executable
    return "python" if WINDOWS else "python3"


def copy_skill(name, dest_root):
    src, dest = os.path.join(HERE, "skills", name), os.path.join(dest_root, name)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".workspace"))
    return dest


def write_shim(bin_dir, py, scripts):
    os.makedirs(bin_dir, exist_ok=True)
    tw = os.path.join(scripts, "tw.py")
    if WINDOWS:
        path = os.path.join(bin_dir, "tw.cmd")
        content = f'@"{py}" "{tw}" %*\r\n'
    else:
        path = os.path.join(bin_dir, "tw")
        if os.path.islink(path):
            os.unlink(path)  # old install symlinked to the bash script
        content = f'#!/bin/sh\nexec "{py}" "{tw}" "$@"\n'
    with open(path, "w", newline="") as f:
        f.write(content)
    if not WINDOWS:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def hook_entry(py, script, extra):
    return dict({
        "type": "command",
        "bash": f'"{py}" "{script}" || true',
        "powershell": f'& "{py}" "{script}"; exit 0',
    }, **extra)


def write_hooks(copilot_home, py, scripts):
    d = os.path.join(copilot_home, "hooks")
    os.makedirs(d, exist_ok=True)
    cfg = {"version": 1, "hooks": {
        "sessionStart": [hook_entry(py, os.path.join(scripts, "session_context.py"), {"timeoutSec": 20})],
        "preToolUse": [hook_entry(py, os.path.join(scripts, "guard.py"), {
            "matcher": "bash|shell|powershell|edit|create|str_replace_editor|str_replace|apply_patch|write",
            "timeoutSec": 10})],
    }}
    path = os.path.join(d, "multirepo-tasks.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return path


def write_instructions(copilot_home, ws):
    path = os.path.join(copilot_home, "copilot-instructions.md")
    text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
    if BEGIN in text and END in text:
        start, end = text.index(BEGIN), text.index(END) + len(END)
        text = text[:start] + text[end:]
    text = text.strip()
    block = INSTRUCTIONS.format(begin=BEGIN, end=END, ws=ws)
    with open(path, "w", encoding="utf-8") as f:
        f.write((text + "\n\n" if text else "") + block)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workspace", nargs="?", default=os.path.join(os.path.expanduser("~"), "workspace"))
    ap.add_argument("--copilot-home", default=os.environ.get("COPILOT_HOME") or os.path.join(os.path.expanduser("~"), ".copilot"))
    ap.add_argument("--bin", default=os.path.join(os.path.expanduser("~"), ".local", "bin"))
    a = ap.parse_args()

    if sys.version_info < (3, 8):
        sys.exit("Python 3.8+ is required")
    if not shutil.which("git"):
        sys.exit("git is required")

    ws = os.path.realpath(os.path.expanduser(a.workspace))
    for d in ("repos", "tasks"):
        os.makedirs(os.path.join(ws, d), exist_ok=True)
    open(os.path.join(ws, ".taskws"), "a").close()
    ok(f"workspace ready at {ws}")

    old_tools = os.path.join(ws, ".tools")
    if os.path.isfile(os.path.join(old_tools, "tw")):
        shutil.rmtree(old_tools)
        ok("removed previous bash install (<workspace>/.tools)")

    skills_root = os.path.join(a.copilot_home, "skills")
    os.makedirs(skills_root, exist_ok=True)
    for name in OLD_SKILLS:
        if os.path.isdir(os.path.join(skills_root, name)):
            shutil.rmtree(os.path.join(skills_root, name))
            ok(f"removed old skill {name}")
    for name in SKILLS:
        copy_skill(name, skills_root)
    ok(f"skills installed: {', '.join(SKILLS)} → {skills_root}")

    scripts = os.path.join(skills_root, "task-workspace", "scripts")
    with open(os.path.join(scripts, ".workspace"), "w", encoding="utf-8") as f:
        f.write(ws + "\n")
    if not WINDOWS:
        for fn in os.listdir(scripts):
            if fn.endswith(".py"):
                p = os.path.join(scripts, fn)
                os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    py = python_cmd()
    ok(f"tw command: {write_shim(a.bin, py, scripts)}")
    on_path = [os.path.normcase(os.path.realpath(p)) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if os.path.normcase(os.path.realpath(a.bin)) not in on_path:
        warn(f"{a.bin} is not on your PATH; add it so `tw` works in your shell")

    agents = os.path.join(a.copilot_home, "agents")
    os.makedirs(agents, exist_ok=True)
    shutil.copy2(os.path.join(HERE, "agents", "task-manager.agent.md"), agents)
    ok(f"agent installed → {agents}")

    ok(f"hooks → {write_hooks(a.copilot_home, py, scripts)}")
    ok(f"instructions block → {write_instructions(a.copilot_home, ws)}")

    print("\nNext:\n  tw clone <git-url>                  # once per repository\n"
          "  tw start PROJ-1 api web             # or ask Copilot at the workspace root\n"
          '  cd "$(tw path PROJ-1)" && copilot\n'
          "In an open Copilot session, run /skills reload.")


if __name__ == "__main__":
    main()
