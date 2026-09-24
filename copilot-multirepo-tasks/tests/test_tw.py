"""End-to-end tests for tw and the hooks, against real (local, bare) git remotes.

Run:  python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "skills", "task-workspace", "scripts")
sys.path.insert(0, SCRIPTS)

from workspace import Task, TwError, yaml_load, yaml_scalar  # noqa: E402

TW = [sys.executable, os.path.join(SCRIPTS, "tw.py")]
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t", NO_COLOR="1", TASK_WORKSPACE="")


def sh(args, cwd, check=True, stdin=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=ENV, input=stdin)
    if check and p.returncode != 0:
        raise AssertionError(f"{args} failed ({p.returncode}):\n{p.stdout}\n{p.stderr}")
    return p


class YamlTests(unittest.TestCase):
    def test_roundtrip_keeps_strings(self):
        t = Task("PROJ-1", 'Fix "quotes": yes', "rebase", "2026-09-24",
                 {"api": "main", "lib": "1.10", "web": "release/2.3", "cfg": "yes"})
        data = yaml_load(t.dump())
        self.assertEqual(data["title"], 'Fix "quotes": yes')
        self.assertEqual(data["repos"], {"api": "main", "lib": "1.10", "web": "release/2.3", "cfg": "yes"})

    def test_hand_written_variants(self):
        text = "# c\nid: X-1\ntitle: 'It''s here'  # comment\nstrategy: merge\nrepos:\n  api: main # c\n  lib: \"dev\"\n"
        d = yaml_load(text)
        self.assertEqual(d["title"], "It's here")
        self.assertEqual(d["repos"], {"api": "main", "lib": "dev"})

    def test_errors(self):
        with self.assertRaises(TwError):
            yaml_load("repos:\n\tapi: main\n")
        with self.assertRaises(TwError):
            yaml_load("  api: main\n")

    def test_scalar_quoting(self):
        self.assertEqual(yaml_scalar("main"), "main")
        self.assertEqual(yaml_scalar("1.10"), '"1.10"')
        self.assertEqual(yaml_scalar("no"), '"no"')


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tw-test-")
        self.remotes = os.path.join(self.tmp, "remotes")
        self.ws = os.path.join(self.tmp, "ws")
        for repo in ("api", "lib"):
            bare = os.path.join(self.remotes, f"{repo}.git")
            sh(["git", "init", "-q", "--bare", "-b", "main", bare], self.tmp)
            seed = os.path.join(self.tmp, f"seed-{repo}")
            sh(["git", "clone", "-q", bare, seed], self.tmp)
            self._commit(seed, "f.txt", "v1", "init")
            sh(["git", "push", "-q", "origin", "HEAD:main"], seed)
            sh(["git", "checkout", "-qb", "release/2.3"], seed)
            self._commit(seed, "rel.txt", "rel", "release")
            sh(["git", "push", "-q", "origin", "release/2.3"], seed)
            sh(["git", "checkout", "-q", "main"], seed)
        os.makedirs(self.ws)
        self.tw("init")
        self.tw("clone", os.path.join(self.remotes, "api.git"))
        self.tw("clone", os.path.join(self.remotes, "lib.git"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _commit(self, repo, name, content, msg):
        with open(os.path.join(repo, name), "a") as f:
            f.write(content + "\n")
        sh(["git", "add", "."], repo)
        sh(["git", "commit", "-qm", msg], repo)

    def tw(self, *args, cwd=None, check=True):
        return sh(TW + list(args), cwd or self.ws, check=check)

    def tj(self, *args, cwd=None, check=True):
        return json.loads(self.tw("--json", *args, cwd=cwd, check=check).stdout)

    def tdir(self, tid):
        return os.path.join(self.ws, "tasks", tid)

    def upstream_commit(self, repo):
        seed = os.path.join(self.tmp, f"seed-{repo}")
        self._commit(seed, "up.txt", "up", "upstream change")
        sh(["git", "push", "-q", "origin", "main"], seed)

    # ------------------------------------------------------------------ tw

    def test_start_uses_latest_default_and_override(self):
        r = self.tj("start", "P-1", "api", "lib", "-b", "lib=release/2.3", "-t", "Rate limit")
        self.assertEqual({x["repo"]: x["base"] for x in r["repos"]}, {"api": "main", "lib": "release/2.3"})
        t = self.tj("list")["tasks"][0]
        self.assertEqual(t["repos"], {"api": "main", "lib": "release/2.3"})
        self.assertTrue(os.path.isfile(os.path.join(self.tdir("P-1"), "AGENTS.md")))

    def test_start_fails_cleanly_for_unknown_repo(self):
        p = self.tw("start", "P-9", "nope", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(os.path.exists(self.tdir("P-9")))

    def test_same_repo_in_two_tasks_is_isolated(self):
        self.tw("start", "P-1", "api")
        self.tw("start", "P-2", "api")
        with open(os.path.join(self.tdir("P-2"), "api", "other.txt"), "w") as f:
            f.write("x")
        with open(os.path.join(self.tdir("P-1"), "api", "f.txt"), "a") as f:
            f.write("mine")
        self.tw("commit", "-m", "work", cwd=self.tdir("P-1"))
        st2 = self.tj("status", "P-2")["repos"][0]
        self.assertEqual(st2["changed"], 1)
        self.assertEqual(st2["ahead"], 0)
        st1 = self.tj("status", cwd=self.tdir("P-1"))["repos"][0]
        self.assertEqual((st1["ahead"], st1["changed"]), (1, 0))

    def test_commit_prefix_push_sync_and_force(self):
        self.tw("start", "P-1", "api")
        d = self.tdir("P-1")
        with open(os.path.join(d, "api", "f.txt"), "a") as f:
            f.write("change")
        c = self.tj("commit", "-m", "add limit", cwd=d)
        self.assertIn("[P-1] add limit", c["committed"][0]["commit"])
        self.assertEqual(self.tj("push", cwd=d)["results"][0]["result"], "pushed")
        self.assertEqual(self.tj("push", cwd=d)["results"], [])  # nothing new

        self.upstream_commit("api")
        st = self.tj("status", "--fetch", cwd=d)["repos"][0]
        self.assertEqual(st["behind"], 1)
        s = self.tj("sync", cwd=d)
        self.assertEqual(s["results"][0]["result"], "synced")

        p = self.tw("--json", "push", cwd=d, check=False)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout)["results"][0]["result"], "rejected")
        self.assertEqual(self.tj("push", "--force-with-lease", cwd=d)["results"][0]["result"], "pushed")

    def test_sync_conflict_reported(self):
        self.tw("start", "P-1", "api")
        d = self.tdir("P-1")
        with open(os.path.join(d, "api", "f.txt"), "w") as f:
            f.write("mine\n")
        self.tw("commit", "-m", "conflicting", cwd=d)
        seed = os.path.join(self.tmp, "seed-api")
        with open(os.path.join(seed, "f.txt"), "w") as f:
            f.write("theirs\n")
        sh(["git", "commit", "-qam", "theirs"], seed)
        sh(["git", "push", "-q", "origin", "main"], seed)
        p = self.tw("--json", "sync", cwd=d, check=False)
        self.assertEqual(p.returncode, 2)
        res = json.loads(p.stdout)["results"][0]
        self.assertEqual((res["result"], res["files"]), ("conflict", ["f.txt"]))
        self.assertEqual(self.tj("status", cwd=d)["repos"][0]["in_progress"], "rebase")

    def test_set_base_moves_only_task_commits(self):
        self.tw("start", "P-1", "api")
        d = self.tdir("P-1")
        self.upstream_commit("api")
        self.tw("sync", cwd=d)  # branch now contains the main-only upstream commit
        with open(os.path.join(d, "api", "feature.txt"), "w") as f:
            f.write("x")
        self.tw("commit", "-m", "feature", cwd=d)
        self.tw("set-base", "P-1", "api", "release/2.3", cwd=d)
        log = sh(["git", "log", "--format=%s"], os.path.join(d, "api")).stdout.split("\n")
        self.assertIn("[P-1] feature", log)
        self.assertIn("release", log)
        self.assertNotIn("upstream change", log)
        self.assertEqual(Task.load(__import__("workspace").Workspace(self.ws), "P-1").repos["api"], "release/2.3")

    def test_finish_refuses_unpushed_then_archives(self):
        self.tw("start", "P-1", "api")
        d = self.tdir("P-1")
        with open(os.path.join(d, "api", "f.txt"), "a") as f:
            f.write("x")
        self.tw("commit", "-m", "x", cwd=d)
        p = self.tw("finish", "P-1", check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("never pushed", p.stderr)
        self.tw("push", cwd=d)
        self.tw("finish", "P-1")
        self.assertFalse(os.path.exists(d))
        self.assertTrue(os.listdir(os.path.join(self.ws, "tasks", ".archive")))
        branches = sh(["git", "branch"], os.path.join(self.ws, "repos", "api")).stdout
        self.assertNotIn("task/P-1", branches)

    def test_resume_existing_remote_branch(self):
        self.tw("start", "P-1", "api")
        d = self.tdir("P-1")
        with open(os.path.join(d, "api", "f.txt"), "a") as f:
            f.write("x")
        self.tw("commit", "-m", "x", cwd=d)
        self.tw("push", cwd=d)
        self.tw("finish", "P-1")
        r = self.tj("start", "P-1", "api")
        self.assertIn("resumed", r["repos"][0]["how"])

    # ------------------------------------------------------------------ hooks

    def hook(self, script, payload):
        p = sh([sys.executable, os.path.join(SCRIPTS, script)], self.ws, stdin=json.dumps(payload))
        return json.loads(p.stdout) if p.stdout.strip() else None

    def guard(self, tool, cwd, **args):
        r = self.hook("guard.py", {"toolName": tool, "cwd": cwd, "toolArgs": args})
        return r["permissionDecision"] if r else None

    def test_guard(self):
        self.tw("start", "P-1", "api")
        self.tw("start", "P-2", "api")
        d = self.tdir("P-1")
        cases = [
            (("edit", d), {"path": "api/f.txt"}, None),
            (("edit", d), {"path": "../P-2/api/f.txt"}, "deny"),
            (("create", d), {"path": os.path.join(self.ws, "repos", "api", "x")}, "deny"),
            (("edit", d), {"path": "task.yaml"}, "deny"),
            (("edit", d), {"path": os.path.join(self.tmp, "scratch.txt")}, None),
            (("bash", d), {"command": "git -C api commit -am 'fix a/b'"}, None),
            (("bash", d), {"command": "cd ../P-2/api && git commit -am x"}, "deny"),
            (("bash", d), {"command": "git -C api push -f"}, "deny"),
            (("bash", d), {"command": "tw push P-2"}, "deny"),
            (("bash", d), {"command": "tw --json push"}, None),
            (("bash", d), {"command": "tw set-base P-1 api release/2.3"}, "ask"),
            (("bash", d), {"command": "tw push --force-with-lease"}, "ask"),
            (("bash", d), {"command": "sed -i s/main/dev/ task.yaml"}, "deny"),
            (("bash", d), {"command": "ls ../P-2"}, None),
        ]
        for (tool, cwd), args, expected in cases:
            with self.subTest(tool=tool, args=args):
                self.assertEqual(self.guard(tool, cwd, **args), expected)
        # outside any workspace the hook stays silent
        self.assertIsNone(self.guard("bash", self.tmp, command="git push -f"))

    def test_session_context(self):
        self.tw("start", "P-1", "api", "-t", "Rate limit")
        ctx = self.hook("session_context.py", {"cwd": self.tdir("P-1")})["additionalContext"]
        self.assertIn("ACTIVE TASK: P-1", ctx)
        self.assertIn("api: main", ctx)
        root_ctx = self.hook("session_context.py", {"cwd": self.ws})["additionalContext"]
        self.assertIn("P-1: Rate limit", root_ctx)
        self.assertIsNone(self.hook("session_context.py", {"cwd": self.tmp}))


if __name__ == "__main__":
    unittest.main()
