import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_agile import monitor
from agentic_agile.core import Store
from agentic_agile.monitor import Monitor, parse_etime, parse_ps


def j(**kw):
    return json.dumps(kw)


def feed_all(m, lines):
    for t, line in lines:
        m.feed(line, t)


class Etime(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_etime("05:07"), 307)
        self.assertEqual(parse_etime("01:02:03"), 3723)
        self.assertEqual(parse_etime("2-00:00:01"), 172801)
        self.assertIsNone(parse_etime("junk"))

    def test_parse_ps_keeps_args_with_spaces(self):
        rows = parse_ps("  10     1 S      00:30 claude -p hello world\nbad line\n", 1000)
        self.assertEqual((rows[0].pid, rows[0].start, rows[0].args), (10, 970, "claude -p hello world"))


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("AGENTIC_AGILE_STALL", None)

    def use(self, i, name="Bash"):
        return j(type="assistant", message={"content": [{"type": "tool_use", "id": i, "name": name,
                                                          "input": {"command": "sleep 300"}}]})

    def result(self, i):
        return j(type="user", message={"content": [{"type": "tool_result", "tool_use_id": i}]})

    def test_command_running_under_limit_makes_no_report(self):
        m = Monitor("claude", 1, "worker", "t", start=0)
        feed_all(m, [(1, j(type="system", subtype="init")), (2, self.use("a"))])
        self.assertEqual(m.check(1000), [])  # silent for 998 s but a command is open
        m.feed(self.result("a"), 1001)
        self.assertEqual(m.check(1100), [])

    def test_stalled_after_command_finishes(self):
        m = Monitor("claude", 1, "worker", "t", start=0)
        feed_all(m, [(2, self.use("a")), (5, self.result("a"))])
        self.assertEqual(m.check(100), [])
        f = m.check(126)
        self.assertEqual([x.kind for x in f], ["stalled"])
        self.assertIn("silence 121 s", f[0].evidence)
        self.assertIn("2 events, last user", f[0].evidence)
        self.assertIn("no command open", f[0].evidence)
        self.assertEqual((f[0].node_id, f[0].role, f[0].host), ("t", "worker", "claude"))

    def test_the_evidence_is_one_line_and_the_caveat_explains_only_silence(self):
        m = Monitor("claude", 1, "worker", "t", expected=60, start=0)
        m.feed(j(type="assistant", message={"content": [{"type": "tool_use", "id": "a", "name": "Bash",
                                                          "input": {"command": "run one &\nrun two\n  sleep 9"}}]}), 1)
        slow = m.check(61)
        self.assertEqual([x.kind for x in slow], ["slow"])
        self.assertNotIn("\n", slow[0].evidence)
        self.assertIn("open command run one & run two sleep 9 for 60 s", slow[0].evidence)
        self.assertNotIn(monitor.CAVEATS["claude"], slow[0].evidence)
        m.feed(self.result("a"), 62)
        stalled = [x for x in m.check(200) if x.kind == "stalled"]
        self.assertIn(monitor.CAVEATS["claude"], stalled[0].evidence)

    def test_agent_tool_is_not_a_command_and_result_clears(self):
        m = Monitor("claude", 1, start=0)
        feed_all(m, [(1, self.use("a", "Agent")), (2, self.use("b", "Task"))])
        self.assertEqual(len(m.check(500)), 1)
        m.feed(self.use("c"), 600)
        m.feed(j(type="result"), 601)
        self.assertEqual(m.running(602), [])

    def test_background_bash_task(self):
        m = Monitor("claude", 1, start=0)
        m.feed(j(type="system", subtype="task_started", task_type="local_bash", task_id="x", description="build"), 1)
        self.assertEqual(m.check(900), [])
        m.feed(j(type="system", subtype="task_notification", task_id="x"), 901)
        self.assertEqual(m.check(901), [])
        self.assertEqual(len(m.check(1100)), 1)

    def replay(self):
        path = Path(__file__).parent / "fixtures" / "claude-stream-2.1.325.jsonl"
        for n, line in enumerate(path.read_text().splitlines()):
            yield n, line, json.loads(line)

    def test_real_recording_foreground_background_and_agent(self):
        # Real claude 2.1.325 stream: foreground Bash, run_in_background Bash, Agent subagent, hooks.
        m = Monitor("claude", 1, start=0)
        seen = set()
        for n, line, e in self.replay():
            m.feed(line, n)
            busy = [k for k in m.open_items]
            blocks = (e.get("message") or {}).get("content") if isinstance(e.get("message"), dict) else None
            if e.get("type") == "assistant" and isinstance(blocks, list) and blocks[0].get("name") == "Bash":
                seen.add("fg_open")
                self.assertIn(blocks[0]["id"], busy)
            if e.get("subtype") == "task_started" and e.get("task_type") == "local_bash":
                self.assertIn("task:" + e["task_id"], m.open_items)
                seen.add("bg_task")
            if e.get("subtype") == "task_started" and e.get("task_type") == "local_agent":
                self.assertNotIn("task:" + e["task_id"], m.open_items)  # a subagent is not a command
                seen.add("agent")
            if e.get("subtype") in ("hook_started", "hook_response"):
                seen.add("hook")
            if e.get("type") == "assistant" and isinstance(blocks, list) and blocks[0].get("name") == "Agent":
                self.assertNotIn(blocks[0]["id"], busy)
        self.assertEqual(seen, {"fg_open", "bg_task", "agent", "hook"})
        self.assertEqual(m.open_items, {})  # result line clears everything
        self.assertEqual(m.events, sum(1 for _, _, e in self.replay() if e.get("type") != "stream_event"))

    def test_real_recording_background_task_outlives_its_tool_result(self):
        m = Monitor("claude", 1, start=0)
        for n, line, e in self.replay():
            m.feed(line, n)
            if e.get("type") == "user" and e["message"]["content"][0].get("type") == "tool_result" \
                    and any(k.startswith("task:") for k in m.open_items):
                self.assertEqual(m.check(10000), [])  # the tool_result came, the task still runs
                return
        self.fail("no tool_result while a background task was open")

    def test_partial_lines_keep_it_alive(self):
        m = Monitor("claude", 1, start=0)
        m.feed(j(type="stream_event", event={}), 100)
        self.assertEqual(m.check(200), [])
        self.assertEqual(m.events, 0)

    def test_stall_override(self):
        os.environ["AGENTIC_AGILE_STALL"] = "10"
        self.addCleanup(os.environ.pop, "AGENTIC_AGILE_STALL")
        self.assertEqual(len(Monitor("claude", 1, start=0).check(11)), 1)

    def test_slow_with_extension_and_busy_command_evidence(self):
        m = Monitor("claude", 1, "worker", "t", expected=600, start=0)
        m.feed(self.use("a"), 10)
        self.assertEqual(m.check(600), [])
        f = m.check(700)
        self.assertEqual([x.kind for x in f], ["slow"])
        self.assertIn("sleep 300 for 690 s", f[0].evidence)
        m.extension = 300
        self.assertEqual(m.check(700), [])
        self.assertEqual(len(m.check(901)), 1)


class CodexTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop("AGENTIC_AGILE_STALL", None)

    def started(self, i, kind="command_execution"):
        return j(type="item.started", item={"id": i, "type": kind, "command": "make test"})

    def test_open_item_is_busy_and_completion_clears(self):
        m = Monitor("codex", 1, start=0)
        feed_all(m, [(1, j(type="thread.started")), (2, self.started("i1"))])
        self.assertEqual(m.check(2000), [])
        m.feed(j(type="item.completed", item={"id": "i1"}), 2001)
        self.assertEqual(m.running(2002), [])

    def test_quiet_types_are_not_commands(self):
        m = Monitor("codex", 1, start=0)
        for i, kind in enumerate(["agent_message", "reasoning", "error"]):
            m.feed(self.started(f"q{i}", kind), 1)
        self.assertEqual(m.running(2), [])

    def test_turn_completed_clears(self):
        m = Monitor("codex", 1, start=0)
        m.feed(self.started("i1", "mcp_tool_call"), 1)
        m.feed(j(type="turn.completed"), 2)
        self.assertEqual(m.running(3), [])

    def test_longer_limit_and_caveat(self):
        m = Monitor("codex", 1, "planner", start=0)
        m.feed(j(type="turn.started"), 1)
        self.assertEqual(m.check(310), [])  # a 307 s essay does not report
        f = m.check(700)
        self.assertEqual(f[0].kind, "stalled")
        self.assertIn("long generation", f[0].evidence)

    def test_override_applies_to_codex(self):
        os.environ["AGENTIC_AGILE_STALL"] = "120"
        self.addCleanup(os.environ.pop, "AGENTIC_AGILE_STALL")
        self.assertEqual(len(Monitor("codex", 1, start=0).check(130)), 1)


class OpencodeTests(unittest.TestCase):
    """ps rows are given as (pid, ppid, stat, start time on the test clock, args)."""

    def setUp(self):
        os.environ.pop("AGENTIC_AGILE_STALL", None)

    def monitor(self, rows, **kw):
        def ps(now):
            text = "".join(f"{p} {pp} {st} {int((now - a) // 60):02d}:{int((now - a) % 60):02d} {args}\n"
                           for p, pp, st, a, args in rows)
            return parse_ps(text, now)
        return Monitor("opencode", 100, "worker", "t", ps=ps, **kw)

    def test_parallel_tools_all_count_after_first_tool_use(self):
        rows = [(101, 100, "S", 10, "sleep 5"), (102, 100, "S", 10, "sleep 200"), (103, 102, "S", 10, "sleep 200")]
        m = self.monitor(rows, start=0)
        m.feed(j(type="step_start"), 10)
        m.feed(j(type="tool_use"), 15)  # a later tool_use must not move the cutoff
        self.assertEqual(m.check(200), [])
        self.assertEqual(len(m.running(200)), 3)

    def test_child_from_before_step_start_is_ignored(self):
        rows = [(101, 100, "S", 10, "typescript-language-server --stdio")]
        m = self.monitor(rows, start=0)
        m.feed(j(type="step_start"), 100)  # the child started at 10 s
        f = m.check(300)
        self.assertEqual([x.kind for x in f], ["stalled"])
        self.assertIn("long generation", f[0].evidence)

    def test_late_lsp_like_child_counts_until_next_step(self):
        rows = [(101, 100, "S", 50, "typescript-language-server --stdio")]
        m = self.monitor(rows, start=0)
        m.feed(j(type="step_start"), 10)
        self.assertEqual(m.check(200), [])  # started at 50 s, after the step began
        m.feed(j(type="step_start"), 190)  # the next step: the server predates it
        self.assertEqual(m.check(400)[0].kind, "stalled")

    def test_defunct_parenthesised_and_ignored_rows(self):
        rows = [(101, 100, "Z", 110, "sleep 9"), (102, 100, "S", 110, "(sleep)"),
                (103, 100, "S", 110, "/x/codex-0.1.2/bin/helper"), (104, 100, "S", 110, "node_repl"),
                (105, 100, "S", 110, "/Applications/ChatGPT.app/x"), (106, 100, "S", 110, "cmux ping"),
                (107, 100, "S", 110, "cua-repl"), (108, 1, "S", 110, "sleep unrelated"),
                (109, 100, "S", 110, "sleep 7 <defunct>")]
        m = self.monitor(rows, start=0)
        m.feed(j(type="step_start"), 100)
        self.assertEqual(m.running(120), [])
        self.assertEqual(len(m.check(300)), 1)

    def test_no_step_start_means_idle(self):
        m = self.monitor([(101, 100, "S", 10, "sleep 9")], start=0)
        self.assertEqual(m.running(60), [])

    def test_slack(self):
        rows = [(101, 100, "S", 99, "sleep 400")]  # started 1 s before step_start 100
        m = self.monitor(rows, start=0)
        m.feed(j(type="step_start"), 100)
        self.assertEqual(len(m.running(200)), 1)


class SubagentRecordings(unittest.TestCase):
    """Real claude 2.1.325 runs where an Agent subagent runs a long Bash and where it writes silently."""

    def lines(self, name):
        path = Path(__file__).parent / "fixtures" / f"claude-subagent-{name}-2.1.325.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()]

    def monitor(self):
        return Monitor("claude", 1, "worker", "task-1", start=0)

    def test_busy_subagent_command_is_open_and_never_stalls(self):
        m = self.monitor()
        under = None
        for row in self.lines("busy"):
            m.feed(row["line"], row["t"])
            e = json.loads(row["line"])
            if e.get("type") == "tool_progress":  # 30 s heartbeat while the subagent's command runs
                self.assertEqual(m.check(row["t"] + 1), [])
            if e["type"] == "assistant" and e.get("parent_tool_use_id"):
                under = e["parent_tool_use_id"]
                self.assertIn(under, m.subagents)
        self.assertTrue(under)
        # no line for 3 h while the command is open: busy, not stalled
        m2 = self.monitor()
        for row in self.lines("busy"):
            if row["t"] > 30:
                break
            m2.feed(row["line"], row["t"])
        self.assertEqual(m2.check(10800), [])
        self.assertEqual(m.open_items, {})
        self.assertEqual(m.subagents, {})

    def test_busy_report_text_names_run_and_subagent(self):
        m = self.monitor()
        for row in self.lines("busy"):
            if row["t"] > 30:
                break
            m.feed(row["line"], row["t"])
        m.expected = 10
        found = m.check(300)
        self.assertEqual([f.kind for f in found], ["slow"])
        text = found[0].evidence
        self.assertIn("worker task-1 on claude", text)
        self.assertIn("time.sleep(40)", text)
        self.assertIn("inside subagent", text)
        self.assertIn("of this worker run", text)

    def test_silent_subagent_stalls_despite_heartbeats(self):
        m = self.monitor()
        for row in self.lines("silent"):
            if row["t"] > 63:  # up to the second heartbeat; the last real line came at 3 s
                break
            m.feed(row["line"], row["t"])
        self.assertEqual(m.check(100), [])  # silent for 97 s
        found = m.check(125)  # silent for 122 s although heartbeats came at 33 s and 63 s
        self.assertEqual([f.kind for f in found], ["stalled"])
        self.assertIn("worker task-1 on claude", found[0].evidence)
        self.assertIn("no command open", found[0].evidence)
        self.assertIn("subagent Write tides essay of this worker run open for", found[0].evidence)

    def test_hung_subagent_command_is_busy_then_overrun(self):
        """Real run: the subagent's `time.sleep(600)` never ends; heartbeats come every 30 s for 180 s."""
        m = self.monitor()
        beats = 0
        for row in self.lines("hang"):
            m.feed(row["line"], row["t"])
            if json.loads(row["line"]).get("type") == "tool_progress":
                beats += 1
                self.assertEqual(m.check(row["t"] + 1), [])  # busy, never stalled
        self.assertEqual(beats, 6)
        self.assertEqual(len(m.open_items), 2)  # the Bash call and its task entry
        self.assertEqual(m.check(10800), [])  # no expected time: a hung command stays unreported
        m.expected = 120
        found = m.check(200)
        self.assertEqual([f.kind for f in found], ["slow"])
        self.assertIn("time.sleep(600)", found[0].evidence)
        self.assertIn("inside subagent", found[0].evidence)
        self.assertIn("hang test", found[0].evidence)

    def test_heartbeat_is_not_output(self):
        m = self.monitor()
        m.feed(j(type="tool_progress", heartbeat=True), 100)
        self.assertEqual(m.last_line, 0)
        self.assertEqual(m.events, 0)
        m.feed(j(type="tool_progress", tool_name="Bash"), 100)  # a real progress line counts
        self.assertEqual(m.last_line, 100)

    def test_finished_subagent_leaves_no_trace(self):
        m = self.monitor()
        for row in self.lines("silent"):
            m.feed(row["line"], row["t"])
        self.assertEqual(m.subagents, {})
        self.assertEqual(m.open_items, {})


class StoreTests(unittest.TestCase):
    def test_findings_open_idempotent_reports_and_extension(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / ".agentic-agile")
            store.initialize("g", "d", "c")
            store.add_node("t", "T", "o", "d", why="w", minutes=1)
            m = Monitor("claude", 1, "worker", "t", expected=store.expected_seconds("t"), start=0)
            for _ in range(2):
                for f in m.check(300):
                    store.open_report(f.kind, f.evidence, f.node_id, f.role, f.host)
            self.assertEqual(sorted(r["kind"] for r in store.reports(open_only=True)), ["slow", "stalled"])
            slow = [r for r in store.reports() if r["kind"] == "slow"][0]
            store.resolve_report(slow["report_id"], "extended", 120)
            self.assertEqual(monitor.extension_seconds(store, "t"), 120)


if __name__ == "__main__":
    unittest.main()
