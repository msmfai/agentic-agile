"""A fresh planner, reviewer or worker gets the goal, the findings and the project, and nothing
that links it to the session that drives the run or to an earlier plan."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentic_agile import engine, isolation, planner
from agentic_agile.core import AgileError, Store
from test_core import run, clear_worker_environment


class Base(unittest.TestCase):
    def setUp(self):
        clear_worker_environment(self)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / ".agentic-agile"
        self.store = Store(self.state)
        self.store.initialize("Ship the parser", "The parser reads every file", "Use the standard library")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, node_id, **kw):
        kw.setdefault("why", f"{node_id} serves the goal")
        self.store.add_node(node_id, node_id.title(), f"Do {node_id}", f"{node_id} done", **kw)

    def finish(self, node_id, learned, expect="it works"):
        self.add(node_id, rank=1, rank_why="decides the format", expect=expect)
        self.store.delegate(node_id, "claude worker", "rank 1: decides the format", expect)
        self.store.complete(node_id, self.root, learned, "changed", "the format is different")

    def env(self, role, **extra):
        return {**os.environ, planner.ROLE_VARIABLE: role, "AGENTIC_AGILE_STATE": str(self.state), **extra}


class PlannerText(Base):
    def test_a_fresh_planner_reads_the_findings_the_engine_notes_and_the_users_constraints(self):
        self.finish("probe", "The files use tabs, not commas", expect="the files use commas")
        self.store.record_note("decision", "SESSION-IDEA: do the writer first")
        self.store.record_note("surprise", "WORKER-ASIDE: the old plan had a writer task", source="worker")
        self.store.record_note("decision", "MERGED: the changes of probe are now in the project", source="engine")
        self.store.add_constraint("Leave the CSV writer out of this goal")
        self.store.record_review("holds", "REVIEW-TEXT: the writer task still holds", "claude", True)
        text = planner.prompt(self.store)
        self.assertIn("The files use tabs, not commas [understanding changed: the format is different]", text)
        self.assertIn("MERGED: the changes of probe", text)
        self.assertIn("Constraint added by the user: Leave the CSV writer out of this goal", text)
        for hidden in ("SESSION-IDEA", "WORKER-ASIDE", "REVIEW-TEXT", "expected: the files use commas"):
            self.assertNotIn(hidden, text)
        # The session and the user still see the whole record.
        known = " ".join(k["body"] for k in self.store.view()["known"])
        for kept in ("SESSION-IDEA", "WORKER-ASIDE", "REVIEW-TEXT"):
            self.assertIn(kept, known)

    def test_the_reviewer_text_leaves_out_the_session_and_earlier_reviews(self):
        self.finish("probe", "The files use tabs")
        self.add("next-step", rank=1, rank_why="builds on probe")
        self.store.record_note("decision", "SESSION-IDEA: keep next-step")
        self.store.record_review("holds", "REVIEW-TEXT: an earlier verdict", "claude", True)
        text = self.store.review_text()
        self.assertIn("The files use tabs", text)
        self.assertNotIn("SESSION-IDEA", text)
        self.assertNotIn("REVIEW-TEXT", text)

    def test_notes_from_before_sources_keep_the_engine_ones(self):
        self.store.append("note.recorded", {"note_id": "n1", "node_id": None, "kind": "decision",
                                            "body": "MERGED: an old merge"})
        self.store.append("note.recorded", {"note_id": "n2", "node_id": None, "kind": "decision",
                                            "body": "OLD-SESSION: an old idea"})
        self.store.rebuild()
        text = planner.prompt(self.store)
        self.assertIn("MERGED: an old merge", text)
        self.assertNotIn("OLD-SESSION", text)

    def test_the_constraint_command_records_the_users_words(self):
        done = run("--state", str(self.state), "goal", "constrain", "Target macOS only")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.store.constraints_added(), ["Target macOS only"])
        self.assertIn("Constraint added by the user: Target macOS only", self.store.capsule() or "")
        self.assertNotEqual(run("--state", str(self.state), "goal", "constrain").returncode, 0)
        self.assertNotEqual(run("--state", str(self.state), "goal", "pause", "extra").returncode, 0)
        with self.assertRaises(AgileError):
            self.store.add_constraint("  ")


class WorkerBrief(Base):
    def test_the_brief_holds_the_planners_reason_and_leaves_out_the_other_tasks(self):
        self.add("a", rank=1, rank_why="decides the format", expect="it works")
        self.add("b", rank=2, rank_why="OTHER-TASK reason")
        self.store.record_note("decision", "SESSION-IDEA: do b next")
        self.store.add_constraint("Target macOS only")
        self.store.delegate("a", "claude worker", "SESSION-WHY: chosen over b", "it works")
        brief = planner.brief(self.store, "a")
        self.assertIn("Why now (rank 1): decides the format", brief)
        self.assertIn("Constraint added by the user: Target macOS only", brief)
        for hidden in ("Chosen over", "SESSION-WHY", "SESSION-IDEA", "OTHER-TASK"):
            self.assertNotIn(hidden, brief)
        self.assertIn("facts about the project and the goal", brief)
        # The session's own view of the task keeps the record of the choice.
        self.assertIn("Chosen over: b", self.store.context("a"))


class RoleCommands(Base):
    def test_a_planner_runs_only_the_commands_that_write_the_plan(self):
        env = self.env("planner")
        add = run("add", "t", "--title", "T", "--objective", "o", "--done", "d", "--why", "w", env=env)
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertEqual(run("tree", env=env).returncode, 0)
        for command in (["view"], ["context", "t"], ["frontier"], ["status"], ["note", "decision", "x"],
                        ["statusline"], ["discard", "--why", "x"], ["report", "list"], ["goal", "constrain", "x"]):
            refused = run(*command, env=env)
            self.assertNotEqual(refused.returncode, 0, command)
            self.assertIn("this is a planner", refused.stderr)

    def test_a_reviewer_runs_no_command(self):
        env = self.env("reviewer")
        for command in (["tree"], ["view"], ["note", "decision", "x"]):
            self.assertNotEqual(run(*command, env=env).returncode, 0, command)
        self.assertEqual(run("hook-stop", env=env).returncode, 0)

    def test_a_worker_reads_its_own_task_in_the_run_state(self):
        self.add("a")
        self.add("b")
        self.store.delegate("a", "claude worker", "w", "")
        env = {**os.environ, planner.ROLE_VARIABLE: "worker", "AGENTIC_AGILE_RUN_STATE": str(self.state),
               "AGENTIC_AGILE_TASK": "a"}
        state = ["--state", str(self.state)]
        self.assertEqual(run(*state, "context", "a", env=env).returncode, 0)
        self.assertEqual(run(*state, "status", env=env).returncode, 0)
        for command in (["context", "b"], ["view"], ["tree"], ["frontier"]):
            self.assertNotEqual(run(*state, *command, env=env).returncode, 0, command)
        note = run(*state, "note", "evidence", "seen", "--node", "a", env=env)
        self.assertEqual(note.returncode, 0, note.stderr)
        self.assertEqual(self.store.view()["known"][-1]["body"], "seen")
        self.assertNotIn("- evidence `a`: seen", planner.prompt(self.store))  # a worker's note stays out


class Environment(unittest.TestCase):
    def test_the_agent_environment_drops_every_link_to_the_session(self):
        base = {"PATH": "/bin", "HOME": "/home/u", "SSH_AUTH_SOCK": "/tmp/ssh", "ANTHROPIC_API_KEY": "k",
                "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CONFIG_DIR": "/home/u/.claude",
                "CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s", "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/m.sock",
                "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_ENTRYPOINT": "cli",
                "CLAUDE_PID": "1", "CMUX_SOCKET_PATH": "/tmp/cmux", "CMUX_SURFACE_ID": "x", "TMUX": "/tmp/tmux,1,0",
                "TMUX_PANE": "%1", "VSCODE_IPC_HOOK_CLI": "/tmp/v", "CODEX_THREAD_ID": "c",
                "AGENTIC_AGILE_STATE": "/elsewhere"}
        env = isolation.agent_env({"AGENTIC_AGILE_ROLE": "planner"}, base)
        for kept in ("PATH", "HOME", "SSH_AUTH_SOCK", "ANTHROPIC_API_KEY", "CLAUDE_CODE_USE_BEDROCK",
                     "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "CLAUDE_CONFIG_DIR"):
            self.assertEqual(env[kept], base[kept])
        for gone in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                     "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PID", "CMUX_SOCKET_PATH",
                     "CMUX_SURFACE_ID", "TMUX", "TMUX_PANE", "VSCODE_IPC_HOOK_CLI", "CODEX_THREAD_ID",
                     "AGENTIC_AGILE_STATE"):
            self.assertNotIn(gone, env)
        self.assertEqual((env["CMUX_CLAUDE_HOOKS_DISABLED"], env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"]), ("1", "1"))
        self.assertEqual(env["AGENTIC_AGILE_ROLE"], "planner")

    def test_the_file_tools_are_closed_to_the_state_and_the_host_folders(self):
        state = Path(tempfile.gettempdir()) / "proj" / "meta-state"
        argv, _ = planner.command_for("claude", "T", "", Path("/tmp"), state_dir=state)
        deny = json.loads(argv[argv.index("--settings") + 1])["permissions"]["deny"]
        self.assertIn(f"Read(/{state.resolve()}/**)", deny)
        self.assertIn("Read(**/.agentic-agile/**)", deny)
        self.assertIn("Grep(~/.claude/**)", deny)
        worker, _ = engine.worker_command("claude", "T", "", Path("/tmp"), state_dir=state)
        deny = json.loads(worker[worker.index("--settings") + 1])["permissions"]["deny"]
        self.assertIn("Read(./.agentic-agile/**)", deny)
        self.assertIn("Write(~/.claude/**)", deny)  # a worker writes no memory for later sessions
        self.assertNotIn("Read(**/.agentic-agile/**)", deny)
        self.assertEqual(worker[worker.index("--tools") + 1], "Bash,Edit,Write,Read,Glob,Grep,Task")


if __name__ == "__main__":
    unittest.main()
