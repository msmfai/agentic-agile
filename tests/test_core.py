from __future__ import annotations

import json
import re
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentic_agile import core, render
from agentic_agile.core import PROJECTION_VERSION, AgileError, Store, goal_check_text

CLI = [sys.executable, str(ROOT / "bin" / "agentic-agile")]


def run(*args, stdin="", env=None, cwd=None):
    return subprocess.run([*CLI, *args], input=stdin, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, cwd=cwd)


WORKER_VARIABLES = ("AGENTIC_AGILE_ROLE", "AGENTIC_AGILE_STATE", "AGENTIC_AGILE_RUN_STATE",
                    "AGENTIC_AGILE_TASK", "CLAUDECODE")


def clear_worker_environment(case):
    """Remove the variables a worker or host session sets, and restore them after the test."""
    saved = {name: os.environ.pop(name) for name in WORKER_VARIABLES if name in os.environ}
    case.addCleanup(os.environ.update, saved)


class Base(unittest.TestCase):
    def setUp(self):
        clear_worker_environment(self)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "meta" / ".agentic-agile"
        self.store = Store(self.state)
        self.store.initialize("Ship an explainable planner", "The planner executes a verified DAG",
                              "Keep human intent authoritative")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, node_id, **kw):
        kw.setdefault("why", f"{node_id} serves the aim")
        self.store.add_node(node_id, kw.pop("title", node_id.title()), kw.pop("objective", f"Do {node_id}"),
                            kw.pop("done_when", f"{node_id} done"), **kw)

    def events(self):
        return [json.loads(p.read_text())["type"] for p in sorted(self.store.events_dir.glob("*.json"))]


class LoopTests(Base):
    """One block, keep what it taught, discard the rest, rebuild."""

    def test_completing_a_block_keeps_the_finding_and_discards_the_hypothesis(self):
        self.add("trial", kind="investigation")
        self.add("build")
        self.add("ship")
        self.store.add_dependency("ship", "build", "nothing to ship before it is built")
        self.store.add_gate("ship", "smoke", [sys.executable, "-c", "pass"])
        self.store.claim_next("trial", "its result decides the design")
        dropped = self.store.complete("trial", self.root, "The simple design is enough")

        self.assertEqual(dropped, ["build", "ship"])
        self.assertEqual(self.events()[-2:], ["node.completed", "hypothesis.discarded"])
        with self.store.connect() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT node_id FROM nodes")], ["trial"])
            self.assertEqual(conn.execute("SELECT count(*) FROM dependencies").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM gates").fetchone()[0], 0)
        self.assertEqual(self.store.status()["status"], "awaiting_hypothesis")
        self.assertTrue(self.store.knowledge()[-1]["body"].startswith("The simple design is enough"))
        # A discarded id is free again: the next hypothesis may reuse it.
        self.add("build")
        self.assertEqual(self.store.node("build")["status"], "pending")

    def test_the_block_capsule_explains_what_how_and_why(self):
        self.add("phase", kind="group", why="The planner needs an engine before anything else")
        self.add("engine", parent_id="phase", objective="Implement the event projector",
                 done_when="Projector passes tests", why="Replay is what makes the plan disposable")
        self.add("unrelated", title="Write launch copy")
        self.store.claim_next("engine", "Nothing else can be judged without it")
        capsule = self.store.context("engine")
        for expected in (
            "Ship an explainable planner",
            "Why: The planner needs an engine before anything else",
            "What to do: Implement the event projector",
            "Done when: Projector passes tests",
            "Why it matters: Replay is what makes the plan disposable",
            "Why now: Nothing else can be judged without it",
            "--learned",
        ):
            self.assertIn(expected, capsule)
        self.assertNotIn("Write launch copy", capsule)
        self.assertLess(capsule.index("`phase`"), capsule.index("`engine`"))

    def test_capsule_follows_the_phase_of_the_loop(self):
        self.assertIn("WRITE THE PLAN", self.store.capsule())
        self.add("a")
        self.add("b")
        choose = self.store.capsule()
        self.assertIn("CHOOSE THE NEXT TASK", choose)
        self.assertIn("can change what you build next", choose)
        self.store.claim_next("b")
        self.assertIn("DO THIS TASK", self.store.capsule())
        self.store.complete("b", self.root, "b taught something")
        rebuilt = self.store.capsule()
        self.assertIn("WRITE THE PLAN", rebuilt)
        self.assertIn("finding `b`: b taught something", rebuilt)
        self.assertIn("Write a fresh plan from the goal and the findings", rebuilt)
        self.assertNotIn("`a`", rebuilt)

    def test_one_ready_task_is_in_progress_at_a_time(self):
        self.add("discover", kind="investigation")
        self.add("implement")
        self.store.add_dependency("implement", "discover")
        self.assertEqual([r["node_id"] for r in self.store.frontier()], ["discover"])
        with self.assertRaisesRegex(AgileError, "becomes ready when"):
            self.store.claim_next("implement")
        with self.assertRaisesRegex(AgileError, "opposite order"):
            self.store.add_dependency("discover", "implement")
        self.store.claim_next("discover")
        with self.assertRaisesRegex(AgileError, "in progress"):
            self.store.claim_next("implement")
        # Resuming returns the same block.
        self.assertEqual(Store(self.state).claim_next()["node_id"], "discover")

    def test_gate_blocks_then_allows_completion(self):
        self.add("build")
        marker = self.root / "gate-ready"
        check = "import pathlib,sys;sys.exit(not pathlib.Path(sys.argv[1]).exists())"
        self.store.add_gate("build", "state-dependent check", [sys.executable, "-c", check, str(marker)])
        self.store.claim_next()
        with self.assertRaisesRegex(AgileError, "needs to pass first"):
            self.store.complete("build", self.root, "x")
        self.assertEqual(self.store.node("build")["status"], "in_progress")
        marker.write_text("ready", encoding="utf-8")
        self.store.complete("build", self.root, "x")
        self.assertEqual(self.store.node("build")["status"], "complete")

    def test_checks_run_after_the_task_is_started(self):
        self.add("build")
        self.store.add_gate("build", "always", [sys.executable, "-c", "pass"])
        before = len(self.events())
        with self.assertRaisesRegex(AgileError, "start task build first"):
            self.store.complete("build", self.root, "x")
        self.assertEqual(len(self.events()), before)

    def test_goal_check_must_pass_before_the_goal_is_recorded(self):
        flag = self.root / "ok.flag"
        self.store.set_goal_check(f"test -f {flag}")
        self.add("only")
        self.store.claim_next()
        self.store.complete("only", self.root, "done")
        with self.assertRaisesRegex(AgileError, "goal check .* needs to pass first"):
            self.store.complete_root("claimed", self.root)
        self.assertEqual(self.store.root()["status"], "active")
        flag.write_text("x")
        self.store.complete_root("check passes", self.root)
        self.assertEqual(self.store.root()["status"], "complete")
        last = json.loads(sorted(self.store.events_dir.glob("*.json"))[-1].read_text())
        self.assertEqual(last["payload"]["check"]["exit_code"], 0)

    def test_goal_check_with_shell_operators_runs_through_sh(self):
        self.store.set_goal_check("test -f a.txt && test -f b.txt")
        self.assertEqual(self.store.goal_check()[:2], ["sh", "-c"])
        self.assertEqual(self.store.run_goal_check(self.root)[1], 1)
        (self.root / "a.txt").write_text("x")
        self.assertEqual(self.store.run_goal_check(self.root)[1], 1)
        (self.root / "b.txt").write_text("x")
        argv, code, _ = self.store.run_goal_check(self.root)
        self.assertEqual(code, 0)
        self.assertEqual(goal_check_text(argv), "test -f a.txt && test -f b.txt")

    def test_plain_goal_check_stays_argv_and_old_events_load(self):
        self.store.set_goal_check("test -f a.txt")
        self.assertEqual(self.store.goal_check(), ["test", "-f", "a.txt"])
        self.assertEqual(goal_check_text(self.store.goal_check()), "test -f a.txt")
        self.store.append("settings.changed", {"goal_check": ["grep", "-q", "x", "f"]})
        self.assertEqual(self.store.goal_check(), ["grep", "-q", "x", "f"])

    def test_goal_check_unittest_and_file_passes_and_fails(self):
        (self.root / "t.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\nunittest.main()\n")
        self.store.set_goal_check("python3 t.py && test -f out.txt")
        self.assertNotEqual(self.store.run_goal_check(self.root)[1], 0)
        (self.root / "out.txt").write_text("x")
        self.assertEqual(self.store.run_goal_check(self.root)[1], 0)

    def test_goal_without_a_check_completes_as_before(self):
        self.assertIsNone(self.store.goal_check())
        self.add("only")
        self.store.claim_next()
        self.store.complete("only", self.root, "done")
        self.store.complete_root("no check set", self.root)
        last = json.loads(sorted(self.store.events_dir.glob("*.json"))[-1].read_text())
        self.assertNotIn("check", last["payload"])

    def test_goal_is_recorded_as_met_when_the_plan_is_empty(self):
        self.add("only")
        self.assertFalse(self.store.root_eligible())
        self.store.claim_next()
        self.store.complete("only", self.root, "done")
        self.assertTrue(self.store.root_eligible())
        self.assertEqual(self.store.root()["status"], "active")
        self.add("more")
        self.assertFalse(self.store.root_eligible())
        with self.assertRaisesRegex(AgileError, "complete or clear the open tasks first"):
            self.store.complete_root("too early")
        self.store.discard("not needed after all")
        self.store.complete_root("definition of done verified")
        self.assertEqual(self.store.root()["status"], "complete")
        self.assertIsNone(self.store.capsule())

    def test_discard_keeps_the_block_in_progress(self):
        self.add("a")
        self.add("b")
        self.store.claim_next("a")
        self.assertEqual(self.store.discard("b was a wrong guess"), ["b"])
        self.assertEqual(self.store.current()["node_id"], "a")


class PersistenceTests(Base):
    def test_git_keeps_the_events_and_ignores_the_files_of_this_machine(self):
        ignore = self.state / ".gitignore"
        self.assertEqual(set(ignore.read_text().splitlines()), set(core.STATE_IGNORE))
        # A state made before logs and workers existed gains the missing lines when a log is written.
        ignore.write_text("state.sqlite3\nstate.sqlite3-*\n.lock\nmine.txt\n")
        self.store.local_path("planner.log").write_text("output")
        lines = ignore.read_text().splitlines()
        self.assertIn("mine.txt", lines)
        self.assertIn("*.log", lines)
        self.assertIn("workers/", lines)
        self.assertEqual(len(lines), len(set(lines)))

    def test_rebuild_replays_events_including_discards(self):
        self.add("one")
        self.add("two")
        self.store.claim_next("one")
        self.store.complete("one", self.root, "one taught this")
        self.store.set_goal_paused(True)
        count = len(self.events())
        self.store.rebuild()
        self.assertEqual(self.store.node("one")["learned"], "one taught this")
        with self.assertRaises(AgileError):
            self.store.node("two")
        self.assertTrue(self.store.goal_paused)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM event_log").fetchone()[0], count)

    def test_an_older_projection_is_replaced_by_a_replay(self):
        self.add("one")
        conn = sqlite3.connect(self.store.db_path)
        conn.execute("PRAGMA user_version = 1")
        conn.execute("ALTER TABLE nodes DROP COLUMN why")
        conn.commit()
        conn.close()
        self.assertEqual(Store(self.state).node("one")["why"], "one serves the aim")

    def test_a_process_that_reads_the_old_index_keeps_it_through_an_upgrade(self):
        # A run started before an upgrade holds the old file open while a newer command rebuilds the index.
        self.add("one")
        conn = sqlite3.connect(self.store.db_path)
        conn.execute(f"PRAGMA user_version = {PROJECTION_VERSION - 1}")
        conn.commit()
        self.add("two")
        self.assertEqual(conn.execute("SELECT count(*) FROM nodes").fetchone()[0], 1)
        conn.close()
        with self.store.connect() as fresh:
            self.assertEqual(fresh.execute("PRAGMA user_version").fetchone()[0], PROJECTION_VERSION)
            self.assertEqual(fresh.execute("SELECT count(*) FROM nodes").fetchone()[0], 2)
        self.assertEqual([p.name for p in self.state.glob("state.sqlite3-rebuild-*")], [])

    def test_an_older_copy_leaves_a_newer_index_alone(self):
        self.add("one")
        conn = sqlite3.connect(self.store.db_path)
        conn.execute(f"PRAGMA user_version = {PROJECTION_VERSION + 1}")
        conn.commit()
        conn.close()
        with self.assertRaises(AgileError) as caught:
            self.store.node("one")
        self.assertIn("a newer Agentic Agile built the index", str(caught.exception))
        conn = sqlite3.connect(self.store.db_path)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], PROJECTION_VERSION + 1)
        self.assertEqual(conn.execute("SELECT count(*) FROM nodes").fetchone()[0], 1)
        conn.close()

    def test_older_events_load_with_empty_reasons(self):
        legacy = {"schema": 1, "event_id": "legacy-1", "sequence": len(self.events()) + 1, "type": "node.added",
                  "recorded_at": "2026-01-01T00:00:00+00:00",
                  "payload": {"node_id": "old", "parent_id": None, "kind": "task", "title": "Old",
                              "objective": "From before reasons existed", "done_when": "Done", "priority": 0}}
        (self.store.events_dir / f"{legacy['sequence']:08d}_legacy-1.json").write_text(json.dumps(legacy))
        self.assertEqual(self.store.node("old")["why"], "")
        self.assertIn("From before reasons existed", self.store.context("old"))

    def test_meta_worktree_auto_commits_events(self):
        repo, meta = self.root / "repo", self.root / "git-meta"
        repo.mkdir()
        git = lambda *a, cwd=repo: subprocess.run(
            ["git", "-c", "user.name=Agentic Agile Test", "-c", "user.email=agentic-agile@example.invalid", *a],
            cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        git("init", "-b", "main")
        git("config", "user.name", "Agentic Agile Test")
        git("config", "user.email", "agentic-agile@example.invalid")
        (repo / "README").write_text("main\n", encoding="utf-8")
        git("add", "README")
        git("commit", "-m", "main")
        git("switch", "--orphan", "meta")
        (repo / "META").write_text("meta\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-m", "meta")
        git("switch", "main")
        git("worktree", "add", str(meta), "meta")
        proc = run("--cwd", str(repo), "init", "--aim", "Durable aim", "--done", "Events are committed")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(any((meta / ".agentic-agile" / "events").glob("*.json")))
        self.assertEqual(git("log", "-1", "--format=%s", cwd=meta).stdout.strip(), "agentic-agile: aim.created #1")

    def test_on_an_ordinary_branch_state_is_local_and_you_control_commits(self):
        repo = self.root / "plain"
        (repo / "pkg").mkdir(parents=True)
        git = lambda *a: subprocess.run(
            ["git", "-c", "user.name=Agentic Agile Test", "-c", "user.email=agentic-agile@example.invalid", *a],
            cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        git("init", "-b", "main")
        (repo / "README").write_text("main\n", encoding="utf-8")
        git("add", "README")
        git("commit", "-m", "main")
        self.assertEqual(run("--cwd", str(repo / "pkg"), "init", "--aim", "Local", "--done", "Works").returncode, 0)
        self.assertTrue(any((repo / ".agentic-agile" / "events").glob("*.json")))
        self.assertEqual(git("rev-list", "--count", "HEAD").stdout.strip(), "1")
        status = git("status", "--porcelain", "--untracked-files=all").stdout
        self.assertIn(".agentic-agile/events/", status)
        self.assertNotIn("state.sqlite3", status)

    def test_outside_git_state_lives_in_the_working_directory(self):
        plain = self.root / "nogit"
        plain.mkdir()
        self.assertEqual(run("--cwd", str(plain), "init", "--aim", "No git", "--done", "Works").returncode, 0)
        self.assertTrue((plain / ".agentic-agile" / "events").is_dir())


class CliTests(Base):
    def cli(self, *args, **kw):
        return run("--state", str(self.state), *args, **kw)

    def test_the_whole_loop_from_the_command_line(self):
        self.assertNotEqual(self.cli("add", "a", "--title", "A", "--objective", "Do a", "--done", "Done").returncode, 0,
                            "each task needs a why")
        for node in ("a", "b"):
            proc = self.cli("add", node, "--title", node, "--objective", f"Do {node}", "--done", "Done",
                            "--why", f"{node} matters")
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.cli("depends", "b", "a", "--why", "b builds on a").returncode, 0)
        started = self.cli("next", "a", "--why", "a decides the design", "--expect", "a works")
        self.assertIn("Why now: a decides the design", started.stdout)
        self.assertNotEqual(self.cli("complete", "a").returncode, 0, "complete takes --learned")
        done = self.cli("complete", "a", "--learned", "a works", "--understanding", "changed", "--because", "new facts")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Cleared the rest of the plan (1 tasks)", done.stdout)
        self.assertIn("WRITE THE PLAN", done.stdout)
        self.assertNotEqual(self.cli("root-complete").returncode, 0, "root-complete takes --why")
        self.assertEqual(self.cli("root-complete", "--why", "verified by hand").returncode, 0)
        self.assertEqual(json.loads(self.cli("status", "--json").stdout)["status"], "complete")

    def test_gate_add_accepts_either_argument_order(self):
        self.add("build")
        for order in (["build", "--label", "after"], ["--label", "before", "build"]):
            proc = self.cli("gate-add", *order, "--", sys.executable, "-c", "pass", "--flag")
            self.assertEqual(proc.returncode, 0, proc.stderr)
        with self.store.connect() as conn:
            gates = [json.loads(r[0]) for r in conn.execute("SELECT argv_json FROM gates")]
        self.assertEqual(gates, [[sys.executable, "-c", "pass", "--flag"]] * 2)

    def test_hooks_stay_silent_before_a_goal_is_set(self):
        missing = str(self.root / "missing")
        self.assertEqual(run("--state", missing, "hook-stop").stdout.strip(), "{}")
        self.assertEqual(run("--state", missing, "hook-context").stdout, "")
        self.assertEqual(run("--state", missing, "statusline").stdout, "")

    def test_goal_hook_holds_through_every_state_and_the_agent_chooses_the_task(self):
        hold = lambda: json.loads(self.cli("hook-stop").stdout)
        self.assertIn("The plan is empty", hold()["reason"])
        self.add("one")
        self.assertIn("Ready tasks: `one`", hold()["reason"])
        self.assertIsNone(self.store.current(), "the agent chooses the task")
        self.store.claim_next("one")
        self.assertIn("Task in progress: `one`", hold()["reason"])
        self.assertIn("START THE RUN", self.cli("hook-context").stdout)
        self.assertNotIn("DO THIS TASK", self.cli("hook-context").stdout)

    def test_paused_goal_releases_the_hook(self):
        self.add("one")
        self.store.claim_next()
        self.store.set_goal_paused(True)
        self.assertEqual(self.store.view()["goal"], {"state": "paused", "active": False})
        self.assertEqual(json.loads(self.cli("hook-stop").stdout), {})
        self.assertIn("paused", self.cli("hook-context").stdout)
        self.store.set_goal_paused(False)
        self.assertEqual(json.loads(self.cli("hook-stop").stdout)["decision"], "block")

    def test_a_blocked_graph_releases_the_hook(self):
        # `a` waits for an empty group, so every open task waits.
        self.add("empty", kind="group")
        self.add("a")
        self.store.add_dependency("a", "empty")
        self.assertEqual(self.store.status()["status"], "blocked")
        self.assertEqual(self.store.view()["goal"], {"state": "blocked", "active": False})
        self.assertEqual(json.loads(self.cli("hook-stop").stdout), {})

    def test_codex_stop_hook_yields_to_an_active_native_goal(self):
        self.add("one")
        self.store.claim_next()
        home = self.root / "codex-home"
        home.mkdir()
        conn = sqlite3.connect(home / "goals_1.sqlite")
        conn.execute("CREATE TABLE thread_goals (thread_id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO thread_goals VALUES ('t-active', 'active'), ('t-paused', 'paused')")
        conn.commit()
        conn.close()
        env = {**os.environ, "CODEX_HOME": str(home)}
        ask = lambda sid: json.loads(self.cli("hook-stop", "--host", "codex",
                                              stdin=json.dumps({"session_id": sid}), env=env).stdout)
        native = ask("t-active")
        self.assertNotIn("decision", native)
        self.assertIn("one", native["systemMessage"])
        for sid in ("t-paused", "t-unknown"):
            held = ask(sid)
            self.assertEqual(held["decision"], "block")
            self.assertIn("goal active", held["systemMessage"])


class DisplayTests(Base):
    def test_one_line_says_what_is_being_pursued_in_every_phase(self):
        line = lambda: render.statusline(self.store.view())
        self.assertIn("writing the plan", line())
        self.add("phase", kind="group")
        self.add("a", parent_id="phase")
        self.add("b")
        self.assertIn("choosing the next task (2 ready)", line())
        self.store.claim_next("a")
        view = self.store.view()
        self.assertEqual([p["id"] for p in view["node"]["path"]], ["phase", "a"])
        self.assertEqual(view["progress"], {"done": 0, "total": 2})
        self.assertIn("phase › a", line())
        self.assertIn("● goal active", line())
        self.assertNotIn("\033", line())
        self.assertIn("\033", render.statusline(view, color=True))

    def test_display_block_carries_the_labels_the_uis_draw(self):
        self.add("a", why="a matters")
        self.store.claim_next("a", "now")
        snapshot = json.loads(run("--state", str(self.state), "view", "--graph").stdout)
        self.assertEqual(snapshot["display"]["node"], "a")
        self.assertEqual(snapshot["display"]["goal"], "● goal active")
        self.assertEqual(snapshot["display"]["tone"], "success")
        self.assertEqual(snapshot["node"]["why"], "a matters")
        self.assertEqual(snapshot["graph"][0]["mark"], "▶")

    def test_tree_and_dashboard(self):
        self.add("phase", kind="group")
        self.add("a", title="Alpha", parent_id="phase")
        self.add("b", title="Beta", parent_id="phase")
        self.store.add_dependency("b", "a")
        self.store.claim_next("a", "first")
        tree = "\n".join(render.tree_lines(self.store.view(), self.store.graph()))
        self.assertIn("◆ Ship an explainable planner", tree)
        self.assertIn("▶ a (task) Alpha", tree)
        self.assertIn("○ b (task) Beta  requires a", tree)
        board = render.dashboard(self.store.view(), self.store.graph(), width=80)
        self.assertIn("Current task", board)
        self.assertIn("Why now", board)
        self.store.complete("a", self.root, "Alpha taught this")
        board = render.dashboard(self.store.view(), self.store.graph(), width=80)
        self.assertIn("Write a new one from the findings", board)
        self.assertIn("Alpha taught this", board)


class ParallelTests(Base):
    """Background agents, the parallel limit, and the gate at the end of a round."""

    def cli(self, *args, **kw):
        return run("--state", str(self.state), *args, **kw)

    def test_delegate_a_ready_task_and_the_session_rests(self):
        self.add("a")
        self.add("b")
        self.store.delegate("a", "agent-1", "its result decides the design")
        node = self.store.node("a")
        self.assertEqual((node["status"], node["delegated_to"]), ("delegated", "agent-1"))
        self.assertIsNone(self.store.current())
        self.assertEqual(self.store.view()["goal"]["state"], "waiting")
        released = json.loads(self.cli("hook-stop").stdout)
        self.assertNotIn("decision", released)
        self.assertIn("waiting for a", released["systemMessage"])
        self.assertIn("WAITING FOR BACKGROUND AGENTS", self.store.capsule())

    def test_the_task_in_progress_can_be_handed_off(self):
        self.add("a")
        self.store.claim_next("a", "first")
        self.store.delegate("a", "agent-1")
        self.assertEqual(self.store.node("a")["status"], "delegated")
        self.assertEqual(self.store.node("a")["picked_why"], "first")
        self.assertIsNone(self.store.current())

    def test_the_default_limit_is_one_running_task(self):
        self.add("a")
        self.add("b")
        self.store.delegate("a", "agent-1")
        with self.assertRaisesRegex(AgileError, "1 of 1 task slots are in use"):
            self.store.claim_next("b")
        with self.assertRaisesRegex(AgileError, "task slots are in use"):
            self.store.delegate("b", "agent-2")

    def test_n_tasks_run_together_and_the_plan_waits_for_all_of_them(self):
        self.store.set_max_parallel(3)
        for node in ("a", "b", "c", "later"):
            self.add(node)
        self.store.add_dependency("later", "a")
        self.store.delegate("a", "agent-1")
        self.store.delegate("b", "agent-2")
        self.store.claim_next("c", "done here")
        self.assertEqual({r["node_id"] for r in self.store.active()}, {"a", "b", "c"})

        self.assertEqual(self.store.complete("a", self.root, "a found X"), [])
        self.assertEqual(self.store.node("later")["status"], "pending")
        self.assertTrue(self.store.round_closing())
        with self.assertRaisesRegex(AgileError, "this round finishes when"):
            self.store.delegate("later", "agent-3")
        self.assertIn("Also running in this round", self.store.context("c"))

        self.assertEqual(self.store.complete("c", self.root, "c found Y"), [])
        self.assertEqual(self.store.view()["goal"]["state"], "waiting")

        self.assertEqual(self.store.complete("b", self.root, "b found Z"), ["later"])
        self.assertEqual(self.store.active(), [])
        self.assertFalse(self.store.round_closing())
        rebuilt = self.store.capsule()
        self.assertIn("WRITE THE PLAN", rebuilt)
        for finding in ("a found X", "b found Z", "c found Y"):
            self.assertIn(finding, rebuilt)

    def test_a_new_round_starts_fresh_after_the_gate(self):
        self.store.set_max_parallel(2)
        self.add("a")
        self.store.delegate("a", "agent-1")
        self.store.complete("a", self.root, "done")
        self.add("b")
        self.add("c")
        self.store.delegate("b", "agent-1")
        self.store.delegate("c", "agent-2")
        self.assertEqual(len(self.store.active()), 2)

    def test_clearing_keeps_delegated_tasks_and_the_goal_waits_for_them(self):
        self.store.set_max_parallel(2)
        self.add("a")
        self.add("b")
        self.store.delegate("a", "agent-1")
        self.assertEqual(self.store.discard("new evidence"), ["b"])
        self.assertEqual(self.store.node("a")["status"], "delegated")
        self.assertFalse(self.store.root_eligible())

    def test_session_task_and_background_task_together(self):
        self.store.set_max_parallel(2)
        self.add("a")
        self.add("b")
        self.store.claim_next("a", "here")
        self.store.delegate("b", "agent-1")
        held = json.loads(self.cli("hook-stop").stdout)
        self.assertEqual(held["decision"], "block")
        self.assertIn("Task in progress: `a`", held["reason"])
        self.assertIn("Also running in this round: b", held["reason"])

    def test_cli_delegate_parallel_and_gated_complete(self):
        self.assertIn("Up to 1 tasks", self.cli("parallel").stdout)
        self.assertIn("Up to 2 tasks", self.cli("parallel", "2").stdout)
        self.add("a")
        self.add("b")
        out = self.cli("delegate", "a", "--to", "agent-1", "--why", "decides X").stdout
        self.assertIn("Delegated a to agent-1", out)
        self.assertIn("ID: `a`", out)
        self.cli("delegate", "b", "--to", "agent-2")
        done = self.cli("complete", "a", "--learned", "a works", "--understanding", "changed", "--because", "new facts")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Still running in this round: b", done.stdout)
        self.assertIn("◐ b", "\n".join(render.tree_lines(self.store.view(), render.decorate(self.store.graph()))))

    def test_parallel_limit_and_delegations_survive_a_rebuild(self):
        self.store.set_max_parallel(3)
        self.add("a")
        self.store.delegate("a", "agent-1")
        self.store.rebuild()
        self.assertEqual(self.store.max_parallel, 3)
        self.assertEqual(self.store.node("a")["delegated_to"], "agent-1")

    def test_a_version_2_index_is_rebuilt_from_the_events(self):
        self.add("a")
        self.store.claim_next("a")
        conn = sqlite3.connect(self.store.db_path)
        conn.execute("PRAGMA user_version = 2")
        conn.execute("DROP TABLE loop")
        conn.commit()
        conn.close()
        fresh = Store(self.state)
        self.assertEqual(fresh.node("a")["status"], "in_progress")
        self.assertEqual(fresh.max_parallel, 1)
        with fresh.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], PROJECTION_VERSION)


FAKE_PLANNER = """
import os, subprocess, sys
prompt = sys.stdin.read()
open(os.environ["FAKE_PROMPT_OUT"], "w").write(prompt)
env = dict(os.environ)
assert env.get("AGENTIC_AGILE_ROLE") == "planner"
if os.environ.get("FAKE_MODE") == "fail":
    sys.exit(4)
if os.environ.get("FAKE_MODE") == "met":
    print("The goal holds: all tests pass.")
    sys.exit(0)
if os.environ.get("FAKE_MODE") == "slow":
    import time; time.sleep(5)
if os.environ.get("FAKE_MODE") == "blocked":
    print("started the plan")
    sys.stdout.flush()
    subprocess.run(["sleep", "30"])
run = lambda *a: subprocess.run(["agile", *a], check=True, stdout=subprocess.DEVNULL, env=env)
run("add", "fresh-2", "--title", "Fresh two", "--objective", "Do it", "--done", "Done", "--why", "From the finding",
    "--rank", "2", "--rank-why", "Builds on fresh-1", "--expect", "It builds")
run("add", "fresh-1", "--title", "Fresh one", "--objective", "Do it", "--done", "Done", "--why", "From the finding",
    "--rank", "1", "--rank-why", "Decides the rest", "--expect", "It works")
hook = subprocess.run(["agile", "hook-stop"], stdout=subprocess.PIPE, text=True, env=env, stdin=subprocess.DEVNULL)
open(os.environ["FAKE_PROMPT_OUT"] + ".hook", "w").write(hook.stdout)
"""


class ReplanTests(Base):
    """The next plan is written by a separate planner that never sees the old plan."""

    def setUp(self):
        super().setUp()
        self.fake = self.root / "fake_planner.py"
        self.fake.write_text(FAKE_PLANNER)
        self.prompt_out = self.root / "prompt.txt"
        self.add("probe", kind="investigation")
        self.add("stale-guess", title="A stale guess from the first plan")
        self.store.claim_next("probe", "decides the rest")
        self.store.complete("probe", self.root, "The probe found the real cause")

    def replan(self, mode="ok", *extra):
        env = {**os.environ, "FAKE_PROMPT_OUT": str(self.prompt_out), "FAKE_MODE": mode}
        run("--state", str(self.state), "planner", "--host", "command",
            "--command", f"{sys.executable} {self.fake}")
        return run("--state", str(self.state), "--cwd", str(self.root), "replan", *extra, env=env)

    def test_the_planner_sees_the_findings_and_none_of_the_old_plan(self):
        done = self.replan()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        sent = self.prompt_out.read_text()
        self.assertIn("Ship an explainable planner", sent)
        self.assertIn("The probe found the real cause", sent)
        self.assertIn("`probe`", sent)
        self.assertNotIn("stale", sent)
        self.assertIn("You are the planner", sent)
        self.assertIn("Rank every task", sent)
        self.assertIn("Start no task", sent)
        self.assertNotIn("agentic-agile replan", sent)
        # Each command must match the planner's tool permissions by name.
        self.assertIn("Start every shell call with the word agentic-agile", sent)

    def test_the_planner_writes_a_ranked_plan_and_starts_nothing(self):
        done = self.replan()
        self.assertIn("wrote 2 tasks: fresh-2, fresh-1", done.stdout)
        self.assertIn("CHOOSE THE NEXT TASK", done.stdout)
        self.assertIsNone(self.store.current())
        self.assertEqual([r["node_id"] for r in self.store.frontier()], ["fresh-1", "fresh-2"])
        self.assertIn("`fresh-1` (rank 1", done.stdout)
        # Starting the top-ranked task reuses the planned reason and expectation.
        started = run("--state", str(self.state), "next")
        self.assertEqual(started.returncode, 0, started.stderr)
        node = self.store.node("fresh-1")
        self.assertEqual((node["status"], node["expect"]), ("in_progress", "It works"))
        self.assertIn("rank 1: Decides the rest", node["picked_why"])
        # Inside the planner, the goal hook stays silent.
        self.assertEqual(json.loads(Path(str(self.prompt_out) + ".hook").read_text()), {})

    def test_a_planner_that_judges_the_goal_met_writes_nothing_and_says_why(self):
        done = self.replan("met")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("wrote no tasks. It judges the goal met", done.stdout)
        self.assertIn("The goal holds: all tests pass.", done.stdout)
        self.assertIn("root-complete", done.stdout)
        self.assertEqual(self.store.root()["status"], "active")

    def test_the_planner_is_told_to_leave_certification_to_the_session(self):
        from agentic_agile import planner
        text = planner.prompt(self.store).split("## Instructions", 1)[1]
        self.assertIn("write no", text)
        self.assertNotIn("root-complete", text)

    def test_when_every_task_is_done_the_session_checks_the_goal_first(self):
        text = self.store.hypothesis_capsule().split("## Instructions", 1)[1]
        self.assertLess(text.index("root-complete"), text.index("replan"))
        reminder = self.store.reminder()
        self.assertLess(reminder.index("root-complete"), reminder.index("run --background"))

    def test_a_failed_planner_hands_the_plan_back_to_the_session(self):
        done = self.replan("fail")
        self.assertEqual(done.returncode, 3)
        self.assertIn("could not write the plan", done.stdout)
        self.assertIn("wrote no plan (exit 4)", done.stdout)
        self.assertIn("WRITE THE PLAN", done.stdout)
        self.assertEqual(self.store.status()["status"], "awaiting_hypothesis")

    def test_a_slow_planner_gets_a_report_and_is_left_to_finish(self):
        done = self.replan("slow", "--timeout", "1")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("wrote 2 tasks", done.stdout)
        slow = [r for r in self.store.reports() if r["kind"] == "slow"]
        self.assertEqual(len(slow), 1)
        # The planner finished by itself, so its report closes as ended and keeps its evidence.
        self.assertEqual((slow[0]["role"], slow[0]["status"]), ("planner", "ended"))
        self.assertIn("past the expected 1 s", slow[0]["evidence"])
        self.assertFalse((self.state / "planner.log").exists())

    def _stream(self, script, expected=30):
        import sys
        from agentic_agile import planner
        return planner.run_headless(self.store, "planner", "claude", [sys.executable, "-c", script], None,
                                    dict(os.environ), self.root, expected, None)

    def test_a_silent_run_gets_a_report_and_the_process_stays_alive(self):
        import threading, time
        os.environ["AGENTIC_AGILE_STALL"] = "1"
        self.addCleanup(os.environ.pop, "AGENTIC_AGILE_STALL", None)
        seen = []
        script = ('import time;print(\'{"type":"system"}\',flush=True);time.sleep(4);'
                  'print(\'{"type":"result","result":"ok"}\',flush=True)')
        thread = threading.Thread(target=lambda: seen.append(self._stream(script)))
        started = time.monotonic()
        thread.start()
        time.sleep(3)
        open_now = self.store.reports(open_only=True)
        self.assertEqual([(r["kind"], r["role"]) for r in open_now], [("stalled", "planner")])
        self.assertIn("no command", open_now[0]["evidence"])
        self.assertTrue(thread.is_alive())
        thread.join(20)
        self.assertEqual(seen[0].returncode, 0)
        self.assertGreaterEqual(time.monotonic() - started, 4)

    def test_a_slow_reviewer_gets_a_report_and_is_not_stopped(self):
        import sys
        from agentic_agile import planner
        done = planner.run_headless(self.store, "reviewer", "claude", [sys.executable, "-c",
                                    'import time;time.sleep(3);print(\'{"type":"result","result":"HOLDS"}\')'],
                                    None, dict(os.environ), self.root, 1, None)
        self.assertEqual(done.returncode, 0)
        report = self.store.reports()[0]
        self.assertEqual((report["kind"], report["role"], report["status"]), ("slow", "reviewer", "ended"))

    def test_extending_the_report_moves_the_expected_time(self):
        import threading, time
        def extend():
            for _ in range(40):
                found = self.store.reports(open_only=True)
                if found:
                    self.store.resolve_report(found[0]["report_id"], "extended", 100)
                    return
                time.sleep(0.1)
        threading.Timer(1.5, extend).start()
        self._stream('import time;time.sleep(4)', 1)
        self.assertEqual(len(self.store.reports()), 1)

    def test_a_stop_on_the_report_ends_the_run_and_saves_the_log(self):
        import threading, time
        def stop():
            time.sleep(2)
            self.store.resolve_report(self.store.reports()[0]["report_id"], "stopped")
        threading.Thread(target=stop).start()
        started = time.monotonic()
        with self.assertRaises(AgileError) as caught:
            self._stream('import time;print(\'{"type":"system"}\',flush=True);time.sleep(30)', 1)
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("stopped", str(caught.exception))
        self.assertIn("planner.log", str(caught.exception))
        self.assertIn("by the top-level agent", (self.state / "planner.log").read_text())

    def test_a_claude_run_waiting_on_a_command_is_not_reported_as_silent(self):
        os.environ["AGENTIC_AGILE_STALL"] = "1"
        self.addCleanup(os.environ.pop, "AGENTIC_AGILE_STALL", None)
        tool = '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"sleep 3"}}]}}'
        done = self._stream(f'import time;print({tool!r},flush=True);time.sleep(3);print(\'{{"type":"result","result":"ok"}}\')', 0)
        self.assertEqual(done.returncode, 0)
        self.assertEqual([r for r in self.store.reports() if r["kind"] == "stalled"], [])

    def test_the_stream_is_reduced_to_its_result_for_text_and_cost(self):
        from agentic_agile import planner
        stream = '{"type":"system"}\n{"type":"assistant"}\n{"type": "result","result":"HOLDS fine","total_cost_usd":0.5}\n'
        reduced = planner.final_result("claude", stream)
        self.assertEqual(planner._cost("claude", reduced), "$0.50")
        self.assertEqual(planner._answer_text("claude", reduced), "HOLDS fine")
        self.assertEqual(planner.final_result("codex", stream), stream)

    def test_replan_waits_for_an_empty_plan(self):
        self.add("open-task")
        done = self.replan()
        self.assertEqual(done.returncode, 3)
        self.assertIn("the plan still has open tasks", done.stdout)

    def test_the_session_text_points_at_replan(self):
        self.assertIn("agentic-agile replan", self.store.capsule())

    def test_planner_settings_are_kept_for_this_machine(self):
        out = run("--state", str(self.state), "planner", "--host", "opencode", "--model", "deepseek/deepseek-v4-pro").stdout
        self.assertIn("host: opencode", out)
        self.assertIn("model: deepseek/deepseek-v4-pro", out)
        self.store.rebuild()
        self.assertIn("host: opencode", run("--state", str(self.state), "planner").stdout)

    def test_host_commands_keep_the_planner_isolated(self):
        from agentic_agile import planner
        claude, _ = planner.command_for("claude", "TEXT", "sonnet", self.root, state_dir=self.state)
        self.assertTrue({"--safe-mode", "--restricted", "--strict-mcp-config"} <= set(claude))
        self.assertEqual(claude[claude.index("--tools") + 1], "Bash,Read,Glob,Grep")
        self.assertEqual(claude[claude.index("--output-format") + 1:][:2], ["stream-json", "--verbose"])
        self.assertIn("Write", claude[claude.index("--disallowedTools"):])
        codex, _ = planner.command_for("codex", "TEXT", "", self.root)
        self.assertTrue({"hooks", "plugins", "memories", "apps", "--ignore-user-config", "--ephemeral"} <= set(codex))
        opencode, _ = planner.command_for("opencode", "TEXT", "m", self.root)
        self.assertIn("--pure", opencode)

    def test_every_host_argv_carries_its_hook_switch(self):
        from agentic_agile import engine, planner
        for build in (lambda h: planner.command_for(h, "T", "m", self.root)[0],
                      lambda h: engine.worker_command(h, "T", "m", self.root)[0]):
            claude = build("claude")
            settings = json.loads(claude[claude.index("--settings") + 1])
            self.assertEqual((settings["disableAllHooks"], settings["autoMemoryEnabled"]), (True, False))
            self.assertIn("--include-hook-events", claude)
            self.assertIn("--strict-mcp-config", claude)
            codex = build("codex")
            self.assertEqual(codex[codex.index("--disable") + 1], "hooks")
            self.assertIn("--pure", build("opencode"))
        worker = engine.worker_command("claude", "T", "m", self.root)[0]
        self.assertEqual(worker[worker.index("--setting-sources") + 1], "project")  # the project's CLAUDE.md stays


FIXTURES = {
    "claude": ['{"type":"system","subtype":"init"}',
               '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"text":"par"}}}',
               '{"type": "stream_event", "event": {"type": "content_block_delta"}}',
               '{"type":"assistant","message":{"content":[{"type":"text","text":"all done"}]}}',
               '{"type":"result","subtype":"success","result":"all done","total_cost_usd":0.25}'],
    "codex": ['{"type":"item.started","item":{"id":"i1","type":"command_execution"}}',
              '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"all done"}}',
              '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":20}}'],
    "opencode": ['{"type":"step_start","part":{"type":"step-start"}}',
                 '{"type":"text","part":{"type":"text","text":"all done"}}',
                 '{"type":"step_finish","part":{"type":"step-finish","cost":0.5}}'],
}
EXPECTED = {"claude": ("$0.25", "all done"), "codex": ("120 tokens", "all done"), "opencode": ("$0.50", "all done")}


class WorkerStreamTests(Base):
    """Worker output is read live on a thread; its line times move while it runs."""

    def _worker(self, host, gap):
        import json as _json
        from agentic_agile import engine
        lines = _json.dumps([l + "\n" for l in FIXTURES[host]])
        script = f"import json,sys,time\nfor l in json.loads({lines!r}):\n    sys.stdout.write(l);sys.stdout.flush();time.sleep({gap})\n"
        process = subprocess.Popen([sys.executable, "-c", script], text=True, stdout=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL)
        worker = engine.Worker("t", process, self.root, host)
        import threading
        worker.reader = threading.Thread(target=worker.read_output, daemon=True)
        worker.reader.start()
        return worker

    def test_line_times_update_while_the_worker_runs_and_results_match(self):
        import time
        from agentic_agile import planner
        for host, (cost, answer) in EXPECTED.items():
            with self.subTest(host=host):
                worker = self._worker(host, 0.4)
                time.sleep(0.2)
                first = worker.last_line
                time.sleep(0.6)
                self.assertGreater(worker.last_line, first, "the last-line time moves before the worker exits")
                self.assertIsNone(worker.process.poll())
                worker.process.wait()
                worker.reader.join(10)
                out = planner.final_result(host, "".join(worker.lines))
                self.assertEqual(planner._cost(host, out), cost)
                self.assertEqual(planner._answer_text(host, out), answer)

    def test_partial_message_lines_stamp_the_time_and_are_not_stored(self):
        worker = self._worker("claude", 0)
        worker.process.wait()
        worker.reader.join(10)
        self.assertEqual(len(worker.lines), 3)
        self.assertFalse(any("stream_event" in line for line in worker.lines))

    def test_the_claude_commands_stream_with_partial_messages(self):
        from agentic_agile import engine, planner
        worker, _ = engine.worker_command("claude", "T", "", self.root)
        planner_argv, _ = planner.command_for("claude", "T", "", self.root)
        for argv in (worker, planner_argv):
            self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
            self.assertIn("--verbose", argv)
            self.assertIn("--include-partial-messages", argv)


class StartRecordTests(Base):
    """Choosing records the alternatives and a prediction; completing shows it beside the finding."""

    def cli(self, *args, **kw):
        return run("--state", str(self.state), *args, **kw)

    def test_next_stores_the_alternatives_and_the_expected_result(self):
        for node in ("a", "b", "c"):
            self.add(node)
        self.store.claim_next("b", "b decides the design", "b shows X")
        node = self.store.node("b")
        self.assertEqual(node["expect"], "b shows X")
        self.assertEqual(json.loads(node["alternatives"]), ["a", "c"])
        text = self.store.context("b")
        self.assertIn("Expected result: b shows X", text)
        self.assertIn("Chosen over: a, c", text)
        self.assertEqual(self.store.view()["node"]["expect"], "b shows X")

    def test_the_finding_is_shown_beside_the_expected_result(self):
        self.add("a")
        self.cli("next", "a", "--why", "it decides", "--expect", "a shows X")
        done = self.cli("complete", "a", "--learned", "a showed Y", "--understanding", "changed", "--because", "Y not X")
        self.assertIn("Expected: a shows X", done.stdout)
        self.assertIn("Found:    a showed Y", done.stdout)
        self.assertIn("a showed Y (expected: a shows X)", self.store.hypothesis_capsule())

    def test_next_asks_for_the_reason_and_the_expected_result(self):
        self.add("a")
        missing = self.cli("next", "a", "--why", "it decides")
        self.assertEqual(missing.returncode, 2)
        self.assertIn("--expect RESULT", missing.stderr)
        self.assertIsNone(self.store.current())

    def test_delegation_records_them_too(self):
        self.add("a")
        self.add("b")
        self.store.delegate("a", "agent-1", "it decides", "a shows X")
        self.assertEqual(self.store.node("a")["expect"], "a shows X")
        self.assertEqual(json.loads(self.store.node("a")["alternatives"]), ["b"])

    def test_older_start_events_load_without_them(self):
        self.add("a")
        legacy = {"schema": 1, "event_id": "legacy-start", "sequence": len(self.events()) + 1,
                  "type": "node.started", "recorded_at": "2026-01-01T00:00:00+00:00",
                  "payload": {"node_id": "a", "why": "old"}}
        (self.store.events_dir / f"{legacy['sequence']:08d}_legacy-start.json").write_text(json.dumps(legacy))
        self.store.rebuild()
        node = self.store.node("a")
        self.assertEqual((node["status"], node["expect"], node["alternatives"]), ("in_progress", "", "[]"))


FAKE_REVIEWER = """
import os, sys
text = sys.stdin.read()
open(os.environ["FAKE_REVIEW_OUT"], "w").write(text)
assert os.environ.get("AGENTIC_AGILE_ROLE") == "reviewer"
if os.environ.get("FAKE_VERDICT") == "broken":
    sys.exit(5)
print(os.environ.get("FAKE_VERDICT", "HOLDS") + ": the remaining reasons still stand")
"""


class DeclareTests(Base):
    """Each completion declares whether understanding changed; 'same' is checked by a fresh reviewer."""

    def setUp(self):
        super().setUp()
        self.fake = self.root / "fake_reviewer.py"
        self.fake.write_text(FAKE_REVIEWER)
        self.review_out = self.root / "review.txt"
        run("--state", str(self.state), "planner", "--host", "command", "--command", f"{sys.executable} {self.fake}")

    def complete(self, node, understanding, verdict="HOLDS", because="a reason"):
        env = {**os.environ, "FAKE_REVIEW_OUT": str(self.review_out), "FAKE_VERDICT": verdict}
        return run("--state", str(self.state), "--cwd", str(self.root), "complete", node, "--learned",
                   f"{node} finding", "--understanding", understanding, "--because", because, env=env)

    def start(self, node):
        self.store.claim_next(node, "it decides", "it works")

    def test_changed_clears_the_plan(self):
        self.add("a")
        self.add("b")
        self.start("a")
        out = self.complete("a", "changed").stdout
        self.assertIn("Cleared the rest of the plan (1 tasks)", out)
        self.assertFalse(self.review_out.exists())

    def test_same_keeps_the_plan_when_the_reviewer_agrees(self):
        self.add("a", why="a reason for a")
        self.add("b", why="b still needed because of X")
        self.start("a")
        out = self.complete("a", "same", because="a confirmed X").stdout
        self.assertIn("agrees the plan holds", out)
        self.assertEqual(self.store.node("b")["status"], "pending")
        self.assertEqual(self.store.view()["goal"]["state"], "choosing")
        sent = self.review_out.read_text()
        for expected in ("DOES THE PLAN STILL HOLD", "a finding", "a confirmed X", "Expected: it works",
                         "b still needed because of X", "HOLDS or CHANGES", "Judge only the effect of these new findings"):
            self.assertIn(expected, sent)
        self.assertIn("holds (checked", self.store.hypothesis_capsule())

    def test_same_is_overruled_when_the_reviewer_disagrees(self):
        self.add("a")
        self.add("b")
        self.start("a")
        out = self.complete("a", "same", verdict="CHANGES").stdout
        self.assertIn("found that the plan changes", out)
        self.assertIn("Cleared the rest of the plan (1 tasks)", out)
        self.assertEqual(self.store.status()["status"], "awaiting_hypothesis")

    def test_without_a_reviewer_the_plan_stays_and_the_check_is_marked_unchecked(self):
        self.add("a")
        self.add("b")
        self.start("a")
        out = self.complete("a", "same", verdict="broken").stdout
        self.assertIn("recorded as unchecked", out)
        self.assertEqual(self.store.node("b")["status"], "pending")
        self.assertIn("holds (unchecked", self.store.hypothesis_capsule())

    def test_a_parallel_round_with_one_change_clears_at_the_end(self):
        self.store.set_max_parallel(2)
        for node in ("a", "b", "c"):
            self.add(node)
        self.store.delegate("a", "agent-1")
        self.store.delegate("b", "agent-2")
        self.assertIn("Still running in this round: b", self.complete("a", "changed").stdout)
        self.assertEqual(self.store.node("c")["status"], "pending")
        out = self.complete("b", "same").stdout
        self.assertIn("Cleared the rest of the plan (1 tasks)", out)
        self.assertFalse(self.review_out.exists())

    def test_a_parallel_round_that_stays_the_same_is_reviewed_once(self):
        self.store.set_max_parallel(2)
        for node in ("a", "b", "c"):
            self.add(node)
        self.store.delegate("a", "agent-1")
        self.store.delegate("b", "agent-2")
        self.complete("a", "same")
        self.assertFalse(self.review_out.exists())
        out = self.complete("b", "same").stdout
        self.assertIn("agrees the plan holds", out)
        sent = self.review_out.read_text()
        self.assertIn("`a`", sent)
        self.assertIn("`b`", sent)

    def test_complete_asks_for_the_declaration(self):
        self.add("a")
        self.start("a")
        out = run("--state", str(self.state), "complete", "a", "--learned", "x")
        self.assertEqual(out.returncode, 2)
        self.assertIn("--understanding changed|same --because REASON", out.stderr)

    def test_verdict_parsing(self):
        from agentic_agile.planner import parse_verdict
        self.assertEqual(parse_verdict("HOLDS: fine")[0], "holds")
        self.assertEqual(parse_verdict("**CHANGES**\nTask b is no longer needed.")[0], "changes")
        self.assertEqual(parse_verdict("CHANGES\nTask b is no longer needed.")[1], "Task b is no longer needed.")
        with self.assertRaises(AgileError):
            parse_verdict("I think it is fine")


class RunnableCommandTests(Base):
    """Every command an agent is told to run must run where that agent is."""

    BARE_AA = re.compile(r"(^|[\\s`\"'(])aa\\s")

    def texts(self):
        from agentic_agile import planner
        from agentic_agile.cli import _delegate_brief
        self.store.set_max_parallel(2)
        for node in ("a", "b", "c"):
            self.add(node)
        out = {"choose": self.store.choose_capsule()}
        self.store.claim_next("a", "decides", "works")
        self.store.delegate("b", "agent-1")
        out["task"] = self.store.context("a")
        out["brief"] = _delegate_brief(self.store, "b")
        self.store.complete("a", self.root, "a done", "same", "fine")
        out["waiting"] = self.store.waiting_capsule()
        self.store.complete("b", self.root, "b done", "changed", "new facts")
        out["plan"] = self.store.hypothesis_capsule()
        out["planner"] = planner.prompt(self.store)
        out["skill"] = (ROOT / "skills" / "agentic-agile" / "SKILL.md").read_text()
        out["readme"] = (ROOT / "README.md").read_text()
        return out

    def test_no_text_tells_an_agent_to_run_bare_aa(self):
        for name, text in self.texts().items():
            self.assertIsNone(self.BARE_AA.search(text), f"{name} tells an agent to run bare aa")

    def test_commands_use_the_full_path_when_the_name_is_not_on_path(self):
        from agentic_agile import core
        texts = self.texts()
        if core.cli_command() == "agentic-agile":
            self.skipTest("agentic-agile is on PATH here")
        for name in ("choose", "task", "waiting", "plan"):
            self.assertIn(f"{core.BIN} ", texts[name], name)
        self.assertIn(f"{core.BIN} --state {self.state} complete b", texts["brief"])
        self.assertIn("agentic-agile add", texts["planner"])
        self.assertIn("finding `a`: a done", texts["plan"])
        findings = texts["plan"].split("## Instructions")[0]
        self.assertNotIn(str(core.BIN), findings)
        self.assertNotIn(str(core.BIN), texts["planner"])

    def test_the_brief_works_from_another_worktree(self):
        texts = self.texts()
        command = texts["brief"].split("(it works from any folder):\n  ", 1)[1].split("\n")[0]
        self.assertIn("--state", command)
        self.assertIn("--understanding changed|same", command)


class BackgroundReplanTests(ReplanTests):
    """A slow planner runs detached, so a shell time limit cannot stop it."""

    def background(self, mode="ok"):
        env = {**os.environ, "FAKE_PROMPT_OUT": str(self.prompt_out), "FAKE_MODE": mode}
        run("--state", str(self.state), "planner", "--host", "command",
            "--command", f"{sys.executable} {self.fake}")
        started = time.monotonic()
        out = run("--state", str(self.state), "--cwd", str(self.root), "replan", "--background", env=env)
        return out, time.monotonic() - started

    def test_background_replan_returns_at_once_and_the_session_rests(self):
        out, seconds = self.background("slow")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertLess(seconds, 4)
        self.assertIn("in the background", out.stdout)
        self.assertEqual(self.store.view()["goal"]["state"], "planning")
        self.assertEqual(json.loads(run("--state", str(self.state), "hook-stop").stdout).get("decision"), None)
        done = run("--state", str(self.state), "replan", "--wait", "--timeout", "30")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("wrote 2 tasks", done.stdout)
        self.assertEqual(self.store.frontier()[0]["node_id"], "fresh-1")
        self.assertFalse(self.store.planner_running())

    def test_replan_wait_returns_21_with_the_report_of_a_slow_planner(self):
        env = {**os.environ, "FAKE_PROMPT_OUT": str(self.prompt_out), "FAKE_MODE": "slow"}
        run("--state", str(self.state), "planner", "--host", "command", "--command", f"{sys.executable} {self.fake}")
        run("--state", str(self.state), "--cwd", str(self.root), "replan", "--background", "--timeout", "1", env=env)
        out = run("--state", str(self.state), "replan", "--wait", "--timeout", "4")
        self.assertEqual(out.returncode, 21, out.stdout + out.stderr)
        self.assertIn("planner", out.stdout)
        self.assertIn("past the expected 1 s", out.stdout)
        self.assertIn("report r1 dismiss", out.stdout)
        self.assertIn("replan --wait", out.stdout)
        run("--state", str(self.state), "report", "r1", "dismiss")
        done = run("--state", str(self.state), "replan", "--wait", "--timeout", "30")
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("wrote 2 tasks", done.stdout)

    def test_a_failed_background_planner_hands_the_plan_back(self):
        self.background("fail")
        done = run("--state", str(self.state), "replan", "--wait", "--timeout", "30")
        self.assertEqual(done.returncode, 3)
        self.assertIn("could not write the plan", done.stdout)
        self.assertEqual(self.store.status()["status"], "awaiting_hypothesis")

    def test_one_background_planner_at_a_time(self):
        self.background("slow")
        again, _ = self.background("slow")
        self.assertIn("already writing the plan", again.stderr)
        run("--state", str(self.state), "replan", "--wait", "--timeout", "30")


TWO_LOOP_PLANNER = """
import os, subprocess, sys
prompt = sys.stdin.read()
loop = 1 if "finding `t1`" not in prompt else 2
open(os.environ["LOOP_PROMPTS"] + f".{loop}", "w").write(prompt)
run = lambda *a: subprocess.run(["agentic-agile", *a], check=True, stdout=subprocess.DEVNULL)
if loop == 1:
    run("add", "t1", "--title", "Probe", "--objective", "Probe it", "--done", "Known", "--why", "Decides the design",
        "--rank", "1", "--rank-why", "Decides the design over t2", "--expect", "Design A works")
    run("add", "t2", "--title", "Guess from loop one", "--objective", "Build it", "--done", "Built", "--why", "Assumed",
        "--rank", "2", "--rank-why", "Depends on the design", "--expect", "It builds")
else:
    run("add", "u1", "--title", "Build design B", "--objective", "Build B", "--done", "Built", "--why", "t1 showed B",
        "--rank", "1", "--rank-why", "The only task", "--expect", "B builds")
"""


class TwoLoopTests(Base):
    """The whole loop of media/loop.svg, twice, through the command line."""

    def test_two_full_loops(self):
        planner_script = self.root / "two_loop_planner.py"
        planner_script.write_text(TWO_LOOP_PLANNER)
        env = {**os.environ, "LOOP_PROMPTS": str(self.root / "prompt")}
        cli = lambda *a: run("--state", str(self.state), "--cwd", str(self.root), *a, env=env)
        cli("planner", "--host", "command", "--command", f"{sys.executable} {planner_script}")

        # Loop 1: write the plan in a fresh context, choose with a reason, do one task.
        first = cli("replan")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        cli("next")
        self.assertEqual(self.store.current()["node_id"], "t1")
        self.assertEqual(json.loads(self.store.node("t1")["alternatives"]), ["t2"])
        done = cli("complete", "t1", "--learned", "Design A fails; design B works",
                   "--understanding", "changed", "--because", "the plan assumed design A")
        self.assertIn("Expected: Design A works", done.stdout)
        self.assertIn("Cleared the rest of the plan (1 tasks)", done.stdout)

        # Loop 2: a fresh planner sees the finding and none of the old plan.
        second = cli("replan")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        prompt_two = (self.root / "prompt.2").read_text()
        self.assertIn("Design A fails; design B works [understanding changed", prompt_two)
        self.assertNotIn("expected: Design A works", prompt_two)
        self.assertNotIn("Guess from loop one", prompt_two)
        cli("next")
        self.assertEqual(self.store.current()["node_id"], "u1")
        cli("complete", "u1", "--learned", "B built", "--understanding", "same", "--because", "as planned")
        self.assertTrue(self.store.root_eligible())
        cli("root-complete", "--why", "B built and verified")
        self.assertEqual(self.store.root()["status"], "complete")
        self.assertEqual(self.events().count("hypothesis.discarded"), 1)


class HookSizeTests(Base):
    """The Stop hook sends a short reminder; planners and reviewers get every finding."""

    def setUp(self):
        super().setUp()
        for index in range(12):
            self.store.record_note("evidence", f"finding number {index} " + "detail " * 200, source="engine")
        self.add("a", objective="Do a carefully")
        self.add("b")

    def test_the_stop_reminder_is_short_in_every_state(self):
        sizes = {}
        hold = lambda: json.loads(run("--state", str(self.state), "hook-stop").stdout)["reason"]
        sizes["choose"] = hold()
        self.store.claim_next("a", "decides", "a works")
        sizes["task"] = hold()
        self.store.complete("a", self.root, "done", "changed", "new facts")
        sizes["plan"] = hold()
        for name, text in sizes.items():
            self.assertLess(len(text), 1000, name)
            self.assertIn("Ship an explainable planner", text, name)
        self.assertIn("Task in progress: `a`", sizes["task"])
        self.assertIn("`b`", sizes["choose"])
        for name, text in sizes.items():
            self.assertIn("run --background", text, name)

    def test_session_text_shows_the_latest_findings_shortened(self):
        text = self.store.choose_capsule()
        self.assertIn("finding number 11", text)
        self.assertNotIn("finding number 5 ", text)
        self.assertIn("run agentic-agile view for all of them", text)
        self.assertLess(len(text), 6000)

    def test_planner_and_reviewer_get_every_finding_in_full(self):
        from agentic_agile import planner
        self.store.discard("make room")
        text = planner.prompt(self.store)
        for index in range(12):
            self.assertIn(f"finding number {index} ", text)
        self.assertNotIn("[...]", text)


class PlannerPicksTests(ReplanTests):
    """The fresh planner chooses the first task; the session then does it."""

    @staticmethod
    def instructions(text):
        return text.split("## Instructions", 1)[1].lower()

    def test_the_planner_ranks_every_task_and_starts_none(self):
        from agentic_agile import planner
        tail = self.instructions(planner.prompt(self.store))
        self.assertIn("rank every task", tail)
        self.assertIn("--expect", tail)
        self.assertIn("start no task", tail)
        self.assertNotIn("agentic-agile next", tail)

    def test_after_replan_the_session_sees_the_ranked_tasks(self):
        self.replan()
        text = self.store.capsule()
        self.assertIn("CHOOSE THE NEXT TASK", text)
        self.assertLess(text.index("`fresh-1`"), text.index("`fresh-2`"))
        self.assertIn("Expected result: It works", text)

    def test_the_fallback_text_still_writes_and_starts_in_the_session(self):
        tail = self.instructions(self.store.hypothesis_capsule())
        self.assertIn("agentic-agile replan", tail)
        self.assertIn("write the plan here", tail)
        self.assertIn("start the task whose result can change", tail)

    def test_the_planner_sees_why_each_plan_was_cleared(self):
        from agentic_agile import planner
        self.replan()
        self.store.claim_next("fresh-1", "rank 1")
        self.store.complete("fresh-1", self.root, "fresh-1 found Z", "changed", "fresh-2 is no longer needed")
        text = planner.prompt(self.store)
        # The worker's finding and its reason reach the planner; the earlier planner's expectation stays out.
        self.assertIn("fresh-1 found Z [understanding changed: fresh-2 is no longer needed]", text)
        self.assertNotIn("expected: It works", text)


class RankTests(Base):
    """The planner ranks tasks; ready tasks come back in rank order."""

    def test_add_stores_rank_reason_and_expectation(self):
        self.store.add_node("b", "B", "Do b", "b done", why="needed", rank=2, rank_why="after a", expect="b works")
        self.store.add_node("a", "A", "Do a", "a done", why="needed", rank=1, rank_why="decides b", expect="a works")
        self.store.add_node("z", "Z", "Do z", "z done", why="needed")
        self.assertEqual([r["node_id"] for r in self.store.frontier()], ["a", "b", "z"])
        node = self.store.node("a")
        self.assertEqual((node["rank"], node["rank_why"], node["expect"]), (1, "decides b", "a works"))
        self.assertIn("Why now (rank 1): decides b", self.store.context("a"))
        tree = "\n".join(render.tree_lines(self.store.view(), render.decorate(self.store.graph())))
        self.assertIn("#1 a", tree)

    def test_an_explicit_expectation_at_start_replaces_the_planned_one(self):
        self.store.add_node("a", "A", "Do a", "a done", why="needed", rank=1, expect="planned")
        self.store.claim_next("a", "now", "revised")
        self.assertEqual(self.store.node("a")["expect"], "revised")

    def test_older_add_events_load_without_ranks(self):
        legacy = {"schema": 1, "event_id": "legacy-add", "sequence": len(self.events()) + 1, "type": "node.added",
                  "recorded_at": "2026-01-01T00:00:00+00:00",
                  "payload": {"node_id": "old", "parent_id": None, "kind": "task", "title": "Old",
                              "objective": "Old", "done_when": "Done", "priority": 0, "why": "w"}}
        (self.store.events_dir / f"{legacy['sequence']:08d}_legacy-add.json").write_text(json.dumps(legacy))
        conn = sqlite3.connect(self.store.db_path)
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        conn.close()
        fresh = Store(self.state)
        self.assertEqual((fresh.node("old")["rank"], fresh.node("old")["rank_why"]), (None, ""))


class ReportModel(Base):
    def test_a_report_is_appended_projected_listed_and_resolved(self):
        self.store.add_node("a", "A", "Do a", "a done", why="needed")
        rid = self.store.open_report("stalled", "no output for 120 s, no command", "a", "worker", "claude")
        (row,) = self.store.reports(open_only=True)
        self.assertEqual((row["report_id"], row["kind"], row["node_id"], row["role"], row["host"], row["status"]),
                         (rid, "stalled", "a", "worker", "claude", "open"))
        self.assertIn("no output for 120 s", row["evidence"])
        # the same agent and kind do not open a second report
        self.assertEqual(self.store.open_report("stalled", "again", "a", "worker", "claude"), rid)
        self.assertEqual(len(self.store.reports()), 1)
        done = self.store.resolve_report(rid, "extended", 300)
        self.assertEqual((done["status"], done["extension"]), ("extended", 300))
        self.assertEqual(self.store.reports(open_only=True), [])
        self.assertEqual(len(self.store.reports()), 1)
        with self.assertRaises(AgileError):
            self.store.resolve_report(rid, "dismissed")
        with self.assertRaises(AgileError):
            self.store.open_report("loud", "x")

    def test_reports_survive_an_index_rebuild(self):
        a = self.store.open_report("slow", "ran 11 minutes of 10", "", "planner", "codex")
        b = self.store.open_report("stalled", "silent", "", "reviewer", "claude")
        self.store.resolve_report(a, "stopped")
        before = self.store.reports()
        self.store.rebuild()
        self.assertEqual(Store(self.state).reports(), before)
        self.assertEqual([r["report_id"] for r in Store(self.state).reports(open_only=True)], [b])

    def test_view_and_the_hook_text_carry_open_reports(self):
        self.store.add_node("a", "A", "Do a", "a done", why="needed")
        self.assertEqual(self.store.view()["reports"], [])
        rid = self.store.open_report("slow", "ran 700 s of 600 s", "a", "worker", "claude")
        (entry,) = self.store.view()["reports"]
        self.assertEqual((entry["id"], entry["kind"], entry["task"], entry["status"]), (rid, "slow", "a", "open"))
        self.assertIn("ran 700 s", entry["evidence"])
        text = self.store.reminder("run is working")
        self.assertIn(rid, text)
        self.assertIn("ran past its expected time", text)
        self.store.resolve_report(rid, "dismissed")
        self.assertEqual(self.store.view()["reports"], [])
        self.assertNotIn("Agents that need a decision", self.store.reminder("run is working"))

    def test_the_stop_hook_holds_the_turn_while_a_report_is_open(self):
        self.store.add_node("a", "A", "Do a", "a done", why="needed")
        rid = self.store.open_report("slow", "ran 700 s of 600 s", "a", "worker", "claude")
        ctx = run("--state", str(self.state), "hook-context").stdout
        self.assertIn(rid, ctx)
        for command in ("stop", "extend --seconds N", "dismiss"):
            self.assertIn(f"report ID {command}", ctx)
        held = json.loads(run("--state", str(self.state), "hook-stop").stdout)
        self.assertEqual(held["decision"], "block")
        self.assertIn(rid, held["reason"])
        self.assertIn("report ID dismiss", held["reason"])
        self.assertIn("1 report open", held["systemMessage"])
        # The second stop in a row ends the turn, so the session waits for the user's decision.
        rest = json.loads(run("--state", str(self.state), "hook-stop", stdin='{"stop_hook_active": true}').stdout)
        self.assertNotIn("decision", rest)
        self.assertIn("1 report open", rest["systemMessage"])
        shown = json.loads(run("--state", str(self.state), "view").stdout)
        self.assertIn("1 report open", shown["display"]["reports"])
        self.store.resolve_report(rid, "dismissed")
        shown = json.loads(run("--state", str(self.state), "view").stdout)
        self.assertEqual(shown["display"]["reports"], "")
        self.assertNotIn(rid, run("--state", str(self.state), "hook-context").stdout)

    def test_a_report_of_an_agent_that_ended_leaves_the_retry_free_to_report(self):
        self.store.add_node("a", "A", "Do a", "a done", why="needed")
        first = self.store.open_report("stalled", "silence 130 s", "a", "worker", "claude")
        self.store.close_reports({first})
        self.assertEqual(self.store.reports()[0]["status"], "ended")
        self.assertEqual(self.store.dismissed_kinds("a"), set())
        self.assertNotEqual(self.store.open_report("stalled", "silence 125 s", "a", "worker", "claude"), first)
        self.store.close_reports({first})  # already closed: nothing changes
        with self.assertRaises(AgileError):
            self.store.resolve_report(first, "dismissed")
        listed = run("--state", str(self.state), "report", "list", "--all").stdout
        self.assertIn("closed when the agent ended", listed)

    def test_expected_time_comes_from_the_task_the_runtime_key_or_the_planner_timeout(self):
        self.store.add_node("a", "A", "Do a", "a done", why="needed", minutes=3)
        self.store.add_node("b", "B", "Do b", "b done", why="needed")
        self.assertEqual(self.store.expected_seconds("a"), 180)
        self.assertEqual(self.store.expected_seconds("b"), 600)
        self.store.set_task_minutes(7)
        self.assertEqual(self.store.expected_seconds("b"), 420)
        with self.assertRaises(AgileError):
            self.store.add_node("c", "C", "Do c", "c done", why="x", minutes=0)

    def test_the_add_command_takes_minutes(self):
        env = {**os.environ, "AGENTIC_AGILE_STATE": str(self.state)}
        done = run("add", "m", "--title", "M", "--objective", "o", "--done", "d", "--why", "w", "--minutes", "2.5", env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(Store(self.state).expected_seconds("m"), 150)

    def test_an_old_event_file_without_reports_or_minutes_loads(self):
        self.store.add_node("a", "A", "Do a", "a done", why="needed")
        conn = sqlite3.connect(self.store.db_path)
        conn.execute("PRAGMA user_version = 6")
        conn.execute("DROP TABLE reports")
        conn.commit()
        conn.close()
        fresh = Store(self.state)
        self.assertEqual(fresh.reports(), [])
        self.assertEqual(fresh.view()["reports"], [])
        self.assertEqual(fresh.expected_seconds("a"), 600)


STUB_AGENT = """
import os, re, subprocess, sys, time
text = sys.stdin.read()
role = os.environ["AGENTIC_AGILE_ROLE"]
mode = os.environ.get("STUB_MODE", "ok")
aa = lambda *a: subprocess.run(["agentic-agile", *a], check=True, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
if role == "planner" and mode == "review-changes":
    if "finding `a`" in text:
        print("The goal holds after a.")
        sys.exit(0)
    aa("add", "a", "--title", "A", "--objective", "make a", "--done", "a.txt", "--why", "w",
       "--rank", "1", "--rank-why", "first", "--expect", "a.txt exists")
    aa("add", "b", "--title", "B", "--objective", "make b", "--done", "b.txt", "--why", "w",
       "--rank", "2", "--rank-why", "after a", "--expect", "b.txt exists")
    aa("depends", "b", "a")
elif role == "planner":
    if "finding `a`" in text:
        print("The goal holds: a and b are done.")
        sys.exit(0)
    kind = "human" if mode == "human" else "task"
    aa("add", "a", "--title", "A", "--objective", "make a", "--done", "a.txt", "--why", "w", "--kind", kind,
       "--rank", "1", "--rank-why", "first", "--expect", "a.txt exists")
    if mode != "human":
        aa("add", "b", "--title", "B", "--objective", "make b", "--done", "b.txt", "--why", "w",
           "--rank", "2", "--rank-why", "second", "--expect", "b.txt exists")
    if mode.startswith("linger"):
        aa("add", "c", "--title", "C", "--objective", "make c", "--done", "c.txt", "--why", "w",
           "--rank", "3", "--rank-why", "third", "--expect", "c.txt exists")
elif role == "worker":
    task = re.search(r"ID: `([^`]+)`", text).group(1)
    # Like a real worker: record the result with the exact command the brief gives.
    state = re.search(r"--state (\\S+) complete", text).group(1)
    aa = lambda *a: subprocess.run(["agentic-agile", "--state", state, *a], check=True,
                                   stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if mode == "silent":
        sys.exit(0)
    if mode == "meddle":
        rogue = subprocess.run(["agentic-agile", "add", "rogue", "--title", "R", "--objective", "o", "--done", "d",
                                "--why", "w"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        open(task + ".guard", "w").write(rogue.stderr)
    if mode == "report":
        print(task + " was made, but I did not record it")
        sys.exit(0)
    if mode == "slow":
        time.sleep(2)
    if mode == "bytecode":
        os.makedirs("__pycache__", exist_ok=True)
        open("__pycache__/base.cpython-314.pyc", "w").write(task)
        open("__pycache__/" + task + ".cpython-314.pyc", "w").write(task)
    name = "shared.txt" if mode == "conflict" else task + ".txt"
    open(name, "w").write(task + "\\n")
    aa("complete", task, "--learned", task + " made", "--understanding", "same", "--because", "as planned")
    if mode == "linger-all" or (mode == "linger" and task == "b"):
        time.sleep(2)
elif role == "reviewer":
    print(("CHANGES: b is no longer needed" if mode == "review-changes" else "HOLDS: the remaining task still stands"))
"""


class ReportCommandTests(Base):
    def cli(self, *args, **kw):
        return run("--state", str(self.state), *args, **kw)

    def open(self, kind="stalled", node="a"):
        return self.store.open_report(kind, "no output for 120 s", node, "worker", "claude")

    def test_list_shows_open_reports_as_text_and_json(self):
        self.assertIn("No open reports", self.cli("report", "list").stdout)
        rid = self.open()
        text = self.cli("report", "list").stdout
        self.assertIn(f"{rid}: worker on a sent no output and ran no command", text)
        (entry,) = json.loads(self.cli("report", "list", "--json").stdout)
        self.assertEqual((entry["id"], entry["kind"], entry["status"]), (rid, "stalled", "open"))

    def test_stop_extend_and_dismiss_record_events_and_clear_the_view(self):
        ids = [self.open("stalled", n) for n in ("a", "b", "c")]
        self.assertEqual(len(self.store.view()["reports"]), 3)
        before = len(self.events())
        self.assertEqual(self.cli("report", ids[0], "stop").returncode, 0)
        out = self.cli("report", ids[1], "extend", "--seconds", "300")
        self.assertIn("more time by 300 s", out.stdout)
        self.assertEqual(self.cli("report", ids[2], "dismiss").returncode, 0)
        self.assertEqual(self.events()[before:], ["report.resolved"] * 3)
        store = Store(self.state)
        self.assertEqual([(r["status"], r["extension"]) for r in store.reports()],
                         [("stopped", 0), ("extended", 300), ("dismissed", 0)])
        self.assertEqual(store.view()["reports"], [])
        self.assertIn(ids[0], self.cli("report", "list", "--all", "--json").stdout)
        self.assertIn("No open reports", self.cli("report", "list").stdout)

    def test_bad_use_is_refused_and_records_nothing(self):
        rid = self.open()
        before = len(self.events())
        for args in (("extend",), ("extend", "--seconds", "0"), ("stop", "--seconds", "5"), ("bogus",)):
            self.assertNotEqual(self.cli("report", rid, *args).returncode, 0, args)
        self.assertNotEqual(self.cli("report", "r99", "stop").returncode, 0)
        self.assertNotEqual(self.cli("report", rid).returncode, 0)
        self.cli("report", rid, "dismiss")
        again = self.cli("report", rid, "stop")
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("already dismissed", again.stderr)
        self.assertEqual(len(self.events()), before + 1)

    def test_agents_of_a_run_are_refused(self):
        rid = self.open()
        before = len(self.events())
        for role in ("planner", "reviewer", "worker"):
            env = {**os.environ, "AGENTIC_AGILE_ROLE": role, "AGENTIC_AGILE_RUN_STATE": str(self.state),
                   "AGENTIC_AGILE_TASK": "a"}
            for args in (("report", rid, "dismiss"), ("report", "list")):
                out = self.cli(*args, env=env)
                self.assertNotEqual(out.returncode, 0, (role, args))
        self.assertEqual(len(self.events()), before)
        self.assertEqual(len(self.store.reports(open_only=True)), 1)

    def test_the_hook_text_names_commands_that_exist(self):
        rid = self.open()
        text = self.store.reminder() or ""
        self.assertIn("agentic-agile report ID stop", text)
        for line in ("report ID stop", "report ID extend --seconds N", "report ID dismiss"):
            self.assertIn(line, text)
            args = line.replace("ID", rid).replace("N", "60").split()
            self.assertEqual(self.cli(*args).returncode, 0, line)
            rid = self.open("slow", "z" + line[-3:])
        help_text = self.cli("report", "--help").stdout
        for word in ("list", "stop", "extend", "dismiss"):
            self.assertIn(word, help_text)


class EngineTests(Base):
    """agentic-agile run drives the loop with fresh subagents and returns only when a person is needed."""

    def setUp(self):
        super().setUp()
        self.project = self.root / "project"
        self.project.mkdir()
        stub = self.root / "stub_agent.py"
        stub.write_text(STUB_AGENT)
        self.state = self.project / ".agentic-agile"
        self.store = Store(self.state)
        self.store.initialize("Make a and b", "a.txt and b.txt exist")
        run("--state", str(self.state), "planner", "--host", "command", "--command", f"{sys.executable} {stub}")

    def engine(self, mode="ok", *extra):
        env = {**os.environ, "STUB_MODE": mode}
        return run("--state", str(self.state), "--cwd", str(self.project), "run", *extra, env=env)

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                              cwd=self.project, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_run_drives_the_goal_from_an_empty_plan_to_goal_met(self):
        out = self.engine()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("the planner judges the goal met", out.stdout)
        self.assertTrue((self.project / "a.txt").exists() and (self.project / "b.txt").exists())
        events = self.events()
        self.assertEqual(events.count("node.delegated"), 2)
        self.assertEqual(events.count("node.completed"), 2)
        self.assertEqual(events.count("plan.reviewed"), 1)
        self.assertLess(events.index("node.completed"), events.index("plan.reviewed"))
        self.assertIn("started a with a command worker", out.stdout)
        self.assertEqual(self.store.root()["status"], "active", "the top-level agent certifies the goal")

    def test_run_with_a_failing_goal_check_exits_19_and_keeps_the_output(self):
        self.store.set_goal_check("sh -c 'echo missing-c; exit 3'")
        out = self.engine()
        self.assertEqual(out.returncode, 19, out.stdout + out.stderr)
        self.assertIn("goal check fails", out.stdout)
        self.assertIn("missing-c", out.stdout)
        self.assertIn("note.recorded", self.events())
        self.assertEqual(self.store.root()["status"], "active")

    def test_run_with_a_passing_goal_check_returns_goal_met(self):
        self.store.set_goal_check("test -f a.txt")
        out = self.engine()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("goal check passes", out.stdout)

    def test_run_without_a_goal_check_returns_goal_met(self):
        self.assertIsNone(self.store.goal_check())
        out = self.engine()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_goal_check_command_sets_shows_and_runs_the_check(self):
        base = ["--state", str(self.state), "--cwd", str(self.project)]
        self.assertIn("no check", run(*base, "goal-check").stdout)
        self.assertEqual(run(*base, "goal-check", "test -f a.txt").returncode, 0)
        self.assertIn("test -f a.txt", run(*base, "goal-check").stdout)
        failing = run(*base, "goal-check", "--run")
        self.assertEqual(failing.returncode, 1)
        self.assertIn("fails with exit 1", failing.stdout)
        (self.project / "a.txt").write_text("x")
        self.assertEqual(run(*base, "goal-check", "--run").returncode, 0)

    def test_foreground_replan_exits_19_when_the_check_fails(self):
        self.assertEqual(self.engine().returncode, 0)
        self.store.set_goal_check("false")
        out = run("--state", str(self.state), "--cwd", str(self.project), "replan",
                  env={**os.environ, "STUB_MODE": "ok"})
        self.assertEqual(out.returncode, 19, out.stdout + out.stderr)
        self.assertIn("goal check 'false' fails", out.stdout)

    def test_parallel_workers_run_in_worktrees_and_merge_back(self):
        self.git("init", "-q", "-b", "main")
        (self.project / ".gitignore").write_text(".agentic-agile/\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.store.set_max_parallel(2)
        out = self.engine()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("in its own worktree", out.stdout)
        self.assertTrue((self.project / "a.txt").exists() and (self.project / "b.txt").exists())
        self.assertEqual(self.git("branch", "--list", "agile/*").stdout.strip(), "")
        self.assertIn("agile: merge", self.git("log", "--oneline").stdout)

    def lingering_run(self, mode):
        self.git("init", "-q", "-b", "main")
        (self.project / ".gitignore").write_text(".agentic-agile/\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.store.set_max_parallel(2)
        out = self.engine(mode)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        events = self.events()
        self.assertEqual(events.count("node.delegated"), 3)
        self.assertEqual(events.count("plan.reviewed"), 1)
        self.assertLess(events.index("plan.reviewed"), len(events) - 1 - events[::-1].index("node.delegated"),
                        "the reviewer checks the round before c starts")
        text = out.stdout
        self.assertLess(text.index("merged the changes of a"), text.index("reviewer: holds"))
        self.assertLess(text.index("merged the changes of b"), text.index("reviewer: holds"),
                        "the reviewer reads the project after every worker of the round is merged")
        self.assertLess(text.index("reviewer: holds"), text.index("started c"))
        self.assertTrue(all((self.project / f"{t}.txt").exists() for t in "abc"))

    def test_the_next_round_waits_for_a_worker_that_recorded_but_has_not_exited(self):
        self.lingering_run("linger")

    def test_the_reviewer_waits_until_every_worker_of_the_round_is_merged(self):
        self.lingering_run("linger-all")

    def test_bytecode_from_two_workers_does_not_conflict_or_enter_the_history(self):
        self.git("init", "-q", "-b", "main")
        (self.project / ".gitignore").write_text(".agentic-agile/\n")
        (self.project / "__pycache__").mkdir()
        (self.project / "__pycache__" / "base.cpython-314.pyc").write_text("base")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.store.set_max_parallel(2)
        out = self.engine("bytecode")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.git("ls-files", "__pycache__").stdout.split(), ["__pycache__/base.cpython-314.pyc"])
        self.assertTrue((self.project / "a.txt").exists() and (self.project / "b.txt").exists())

    def test_the_run_totals_dollars_and_tokens_the_hosts_report(self):
        from agentic_agile import planner
        from agentic_agile.engine import Engine
        engine = Engine(self.store, self.project, host="command")
        codex = "\n".join(['{"type":"turn.started"}',
                           '{"type":"turn.completed","usage":{"input_tokens":1000,"output_tokens":50}}'])
        opencode = '{"type":"step_finish","part":{"cost":0.00123,"tokens":{"total":9}}}'
        engine._spend(planner._cost("codex", codex))
        engine._spend(planner._cost("codex", codex))
        engine._spend(planner._cost("opencode", opencode))
        self.assertEqual(engine.cost_line(), "$0.0012 and 2,100 tokens")

    def test_a_merge_conflict_returns_with_the_branch_kept(self):
        self.git("init", "-q", "-b", "main")
        (self.project / ".gitignore").write_text(".agentic-agile/\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.store.set_max_parallel(2)
        out = self.engine("conflict")
        self.assertEqual(out.returncode, 12, out.stdout + out.stderr)
        self.assertIn("its changes are on branch agile/", out.stdout)
        self.assertEqual(self.git("status", "--porcelain").stdout.strip(), "")

    def test_a_conflict_is_visible_to_the_next_planner_and_the_resume_records_the_merge(self):
        from agentic_agile import planner
        self.git("init", "-q", "-b", "main")
        (self.project / ".gitignore").write_text(".agentic-agile/\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.store.set_max_parallel(2)
        self.assertEqual(self.engine("conflict").returncode, 12)
        branch = self.git("branch", "--list", "agile/*", "--format=%(refname:short)").stdout.split()[0]
        text = planner.prompt(Store(self.state))
        self.assertIn("NOT MERGED", text)
        self.assertIn(branch, text)
        # The top-level agent resolves the conflict: both sides of shared.txt stay.
        merge = self.git("merge", "--no-ff", "-m", "resolve", branch)
        self.assertNotEqual(merge.returncode, 0)
        (self.project / "shared.txt").write_text("a\nb\n")
        self.git("add", "shared.txt")
        self.assertEqual(self.git("commit", "-qm", "resolve").returncode, 0)
        out = self.engine("conflict")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("are merged into the project", out.stdout)
        self.assertIn("MERGED: the changes of", planner.prompt(Store(self.state)))
        self.assertEqual(self.git("branch", "--list", "agile/*").stdout.strip(), "")

    def test_a_worker_report_is_used_when_it_records_nothing(self):
        out = self.engine("report", "--max-steps", "2")
        self.assertIn("using its final report", out.stdout)
        self.assertIn("a was made, but I did not record it", self.store.node("a")["learned"])
        self.assertEqual(self.store.node("a")["understanding"], "changed")

    def test_a_silent_worker_is_retried_once_then_returns(self):
        out = self.engine("silent")
        self.assertEqual(out.returncode, 11, out.stdout + out.stderr)
        self.assertIn("trying a fresh worker once more", out.stdout)

    def test_a_human_task_returns_to_the_top_level_agent(self):
        out = self.engine("human")
        self.assertEqual(out.returncode, 10, out.stdout + out.stderr)
        self.assertIn("a: make a", out.stdout)

    def test_a_reviewer_verdict_is_applied_before_the_next_task_starts(self):
        out = self.engine("review-changes")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("reviewer: changes", out.stdout)
        events = self.events()
        self.assertEqual(events.count("node.delegated"), 1, "b must be cleared before it can start")
        self.assertLess(events.index("plan.reviewed"), events.index("hypothesis.discarded"))
        self.assertFalse((self.project / "b.txt").exists())

    def test_a_worker_can_record_only_its_own_task_in_the_run_state(self):
        out = self.engine("meddle")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertNotIn("rogue", [r["node_id"] for r in self.store.graph_rows()])
        self.assertIn("records only its own result", (self.project / "a.guard").read_text())
        self.assertTrue((self.project / "a.txt").exists())

    def test_tasks_left_by_a_stopped_run_get_a_fresh_worker(self):
        self.store.add_node("a", "A", "make a", "a.txt", why="w", rank=1, expect="a.txt exists")
        self.store.delegate("a", "command worker", "rank 1")
        out = self.engine()
        self.assertIn("a was left by an earlier run", out.stdout)
        self.assertEqual(self.store.node("a")["status"], "complete")

    def test_limits_and_pause_return(self):
        self.assertEqual(self.engine("ok", "--max-steps", "1").returncode, 13)
        self.store.set_goal_paused(True)
        self.assertEqual(self.engine().returncode, 16)

    def background(self, mode="slow", *extra):
        env = {**os.environ, "STUB_MODE": mode}
        return run("--state", str(self.state), "--cwd", str(self.project), "run", "--background", *extra, env=env)

    def wait(self, *extra):
        return run("--state", str(self.state), "--cwd", str(self.project), "run", "--wait", *extra)

    def status(self):
        return run("--state", str(self.state), "--cwd", str(self.project), "run", "--status").stdout.strip()

    def test_background_run_returns_at_once_and_wait_gives_the_foreground_outcome(self):
        self.assertEqual(self.status(), "no run yet")
        started = self.background()
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertIn("in the background", started.stdout)
        self.assertTrue((self.state / "run.log").exists())
        out = self.wait("--timeout", "60")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("the planner judges the goal met", out.stdout + self.status())
        self.assertTrue((self.project / "a.txt").exists() and (self.project / "b.txt").exists())
        self.assertIn("not running; last outcome (exit 0)", self.status())

    def test_status_shows_the_step_and_running_tasks_while_it_works(self):
        self.background()
        seen = ""
        deadline = time.time() + 30
        while time.time() < deadline and "tasks running: a" not in seen:
            seen = self.status()
            time.sleep(0.2)
        self.assertRegex(seen, r"^running \(process \d+\): step \d+, tasks running: a; now: ")
        self.wait("--timeout", "60")

    def test_a_second_run_on_the_same_state_is_refused(self):
        self.background()
        for second in (self.background(), self.engine("slow")):
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already working on this state", second.stdout + second.stderr)
        self.wait("--timeout", "60")
        self.assertEqual(self.engine().returncode, 0, "a finished run frees the state")

    def test_wait_returns_20_while_the_run_works_and_other_outcomes_keep_their_codes(self):
        self.background()
        short = self.wait("--timeout", "1")
        self.assertEqual(short.returncode, 20, short.stdout + short.stderr)
        self.assertIn("Wait again with", short.stdout)
        self.assertEqual(self.wait("--timeout", "60").returncode, 0)
        self.store.set_goal_paused(True)
        self.background("ok")
        self.assertEqual(self.wait("--timeout", "60").returncode, 16)

    def test_wait_returns_21_with_the_report_and_continues_after_dismiss(self):
        self.background()
        rid = self.store.open_report("slow", "ran 700 s of 600 s; open command: make slow", "a", "worker", "claude")
        out = self.wait("--timeout", "60")
        self.assertEqual(out.returncode, 21, out.stdout + out.stderr)
        for text in (f"Report {rid}: worker on a", "Evidence: ran 700 s of 600 s; open command: make slow",
                     f"report {rid} stop", f"report {rid} extend --seconds N", f"report {rid} dismiss"):
            self.assertIn(text, out.stdout)
        self.assertIn("run --wait", out.stdout)
        self.assertEqual(self.wait("--timeout", "60").returncode, 21, "the report stays until it is decided")
        self.assertTrue(self.status().startswith("running"), self.status())
        dismissed = run("--state", str(self.state), "report", rid, "dismiss")
        self.assertEqual(dismissed.returncode, 0, dismissed.stderr)
        again = self.wait("--timeout", "60")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertNotIn("Agents need a decision", again.stdout)

    def test_wait_returns_21_for_a_planner_report(self):
        self.background()
        rid = self.store.open_report("stalled", "no output for 120 s", "", "planner", "claude")
        out = self.wait("--timeout", "60")
        self.assertEqual(out.returncode, 21, out.stdout)
        self.assertIn(f"Report {rid}: planner", out.stdout)
        self.assertIn("no output for 120 s", out.stdout)
        run("--state", str(self.state), "report", rid, "dismiss")
        self.assertEqual(self.wait("--timeout", "60").returncode, 0)

    def test_the_run_survives_the_caller_and_a_killed_run_is_reported(self):
        self.background()
        pid = int(re.search(r"process (\d+)", self.status()).group(1))
        os.kill(pid, 9)
        time.sleep(0.3)
        out = self.wait("--timeout", "5")
        self.assertEqual(out.returncode, 18, out.stdout + out.stderr)
        self.assertIn("without recording an outcome", out.stdout)
        self.assertIn("not running", self.status())


if __name__ == "__main__":
    unittest.main()


class RunHookTests(Base):
    """The hooks say one short thing in each state; the run keeps the loop."""

    def cli(self, *args, **kw):
        return run("--state", str(self.state), *args, **kw)

    def start_run(self):
        from agentic_agile import engine
        engine._set(self.store, pid=os.getpid(), step=2, activity="worker on a")

    def stop(self):
        return json.loads(self.cli("hook-stop").stdout)

    def test_no_plan_and_no_run_sends_the_session_to_start_the_run(self):
        out = self.cli("hook-context").stdout
        self.assertIn("START THE RUN", out)
        self.assertIn("run --background", out)
        self.assertNotIn("DO THIS TASK", out)
        held = self.stop()
        self.assertEqual(held["decision"], "block")
        self.assertIn("The plan is empty", held["reason"])
        self.assertLess(len(held["reason"]), 1000)

    def test_an_active_run_shows_one_status_line_and_lets_the_session_rest(self):
        self.add("a")
        self.store.claim_next("a", "decides", "a works")
        self.start_run()
        out = self.cli("hook-context").stdout
        self.assertIn("THE RUN IS WORKING", out)
        self.assertIn("running (process", out)
        self.assertNotIn("DO THIS TASK", out)
        rest = self.stop()
        self.assertNotIn("decision", rest)
        self.assertIn("running (process", rest["systemMessage"])
        self.assertLess(len(rest["systemMessage"]), 1000)

    def test_the_last_outcome_is_one_short_line_after_a_long_multiline_reason(self):
        from agentic_agile import engine
        reason = "The goal is met.\n\n- first finding " + "word " * 120 + "\n- last finding"
        engine._set(self.store, code=0, outcome=reason)
        line = engine.status_line(self.store)
        self.assertNotIn("\n", line)
        self.assertLess(len(line), 400)
        self.assertTrue(line.startswith("not running; last outcome (exit 0): The goal is met."))

    def test_a_long_goal_met_reason_keeps_its_end_and_starts_at_a_word(self):
        from agentic_agile import planner
        text = " ".join(f"w{i}" for i in range(400))
        out = planner.tail_text(text, 600)
        self.assertTrue(out.startswith("...w"))
        self.assertTrue(out.endswith("w399"))
        self.assertLessEqual(len(out), 604)
        self.assertEqual(planner.tail_text("short answer", 600), "short answer")

    def test_after_a_run_judged_the_goal_met_the_hold_says_to_record_it_not_to_start_the_run(self):
        from agentic_agile import engine
        self.add("a")
        self.store.claim_next("a", "decides")
        self.store.complete("a", self.root, "done", "same", "nothing changed")
        held = self.stop()
        self.assertIn("START THE RUN", held["reason"])
        engine._set(self.store, code=0, outcome="the planner judges the goal met")
        held = self.stop()
        self.assertEqual(held["decision"], "block")
        self.assertIn("RECORD THE GOAL", held["reason"])
        self.assertIn("root-complete", held["reason"])
        self.assertNotIn("run --background", held["reason"])
        self.assertLess(len(held["reason"]), 1000)

    def test_an_active_run_rests_the_session_while_no_plan_exists(self):
        self.start_run()
        self.assertNotIn("decision", self.stop())

    def test_waiting_for_a_person_names_the_person_and_does_not_hold_the_session(self):
        self.add("empty", kind="group")
        self.add("a")
        self.store.add_dependency("a", "empty")
        out = self.cli("hook-context").stdout
        self.assertIn("A PERSON IS NEEDED", out)
        self.assertLess(len(out), 1000)
        self.assertEqual(self.stop(), {})

    def test_goal_met_is_silent(self):
        self.add("a")
        self.store.claim_next("a", "decides")
        self.store.complete("a", self.root, "done", "same", "nothing changed")
        self.store.complete_root("the evidence")
        self.assertEqual(self.cli("hook-context").stdout, "")
        self.assertEqual(self.stop(), {})

    def test_hooks_exit_zero_quickly_and_stay_silent_in_a_worker(self):
        env = {**os.environ, "AGENTIC_AGILE_ROLE": "worker"}
        began = time.monotonic()
        done = self.cli("hook-context", env=env)
        self.assertEqual((done.returncode, done.stdout), (0, ""))
        self.assertEqual(json.loads(self.cli("hook-stop", env=env).stdout), {})
        self.assertLess(time.monotonic() - began, 5)
