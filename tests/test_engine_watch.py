"""The engine feeds each worker's stream to a monitor and opens reports; it stops nothing."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_agile import monitor
from agentic_agile.core import Store
from agentic_agile.engine import Engine, Worker

BASH = json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "make slow"}}]}})
PARTIAL = json.dumps({"type": "stream_event", "event": {"type": "content_block_delta"}})
HEARTBEAT = json.dumps({"type": "tool_progress", "heartbeat": True})


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / ".agentic-agile")
        self.store.initialize("goal", "done")
        self.store.add_node("t", "T", "do t", "t done", why="w", minutes=10)
        self.engine = Engine(self.store, root, host="claude", say=lambda *_: None)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
            p.stdout.close()
        self.temp.cleanup()

    def worker(self, lines):
        """A worker whose process prints `lines` and then stays silent and alive."""
        code = "import sys,time\nfor l in sys.argv[1:]:\n print(l,flush=True)\ntime.sleep(60)"
        p = subprocess.Popen([sys.executable, "-c", code, *lines], stdout=subprocess.PIPE, text=True)
        self.procs.append(p)
        w = Worker("t", p, Path(self.temp.name), "claude")
        w.monitor = monitor.Monitor("claude", p.pid, "worker", "t",
                                    expected=self.store.expected_seconds("t"), start=w.started)
        w.reader = threading.Thread(target=w.read_output, daemon=True)
        w.reader.start()
        self.engine.workers["t"] = w
        deadline = time.monotonic() + 10
        while w.monitor.events + (len(lines) and 0) < len([l for l in lines if "stream_event" not in l
                                                           and "heartbeat" not in l]) and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.1)
        return w

    def kinds(self):
        return [(r["kind"], r["status"]) for r in self.store.reports()]

    def test_silent_worker_opens_one_stalled_report_and_keeps_running(self):
        w = self.worker([PARTIAL])
        t = w.started
        self.engine._watch(t + 60)
        self.assertEqual(self.kinds(), [])
        self.engine._watch(t + 125)
        self.engine._watch(t + 130)  # a repeated check opens no second report
        self.assertEqual(self.kinds(), [("stalled", "open")])
        self.assertIn("worker t", self.store.reports()[0]["evidence"])
        self.assertIsNone(w.process.poll())
        self.assertIn("t", self.engine.workers)

    def test_worker_past_its_expected_time_opens_one_slow_report(self):
        w = self.worker([BASH])
        t = w.started
        self.engine._watch(t + 590)
        self.assertEqual(self.kinds(), [])
        self.engine._watch(t + 601)
        self.engine._watch(t + 700)
        self.assertEqual(self.kinds(), [("slow", "open")])
        self.assertIn("make slow", self.store.reports()[0]["evidence"])
        self.assertIsNone(w.process.poll())

    def test_busy_command_opens_no_stalled_report(self):
        w = self.worker([BASH])
        self.engine._watch(w.started + 555)
        self.assertEqual(self.kinds(), [])

    def test_heartbeats_do_not_count_as_output(self):
        w = self.worker([HEARTBEAT])
        self.assertEqual(w.monitor.events, 0)
        self.engine._watch(w.started + 125)
        self.assertEqual(self.kinds(), [("stalled", "open")])

    def test_an_extension_moves_the_slow_limit(self):
        w = self.worker([BASH])
        t = w.started
        self.engine._watch(t + 601)
        rid = self.store.reports()[0]["report_id"]
        self.store.resolve_report(rid, "extended", 120)
        self.engine._watch(t + 650)
        self.assertEqual(w.monitor.extension, 120)
        self.assertEqual(self.kinds(), [("slow", "extended")])
        self.engine._watch(t + 800)
        self.assertEqual(self.kinds(), [("slow", "extended"), ("slow", "open")])

    def open_slow(self, w):
        self.engine._watch(w.started + 601)
        return self.store.reports()[-1]["report_id"]

    def test_stop_kills_the_worker_and_records_the_task(self):
        self.store.delegate("t", "claude worker")
        w = self.worker([BASH])
        rid = self.open_slow(w)
        self.assertIsNone(w.process.poll())
        self.store.resolve_report(rid, "stopped")
        self.engine._watch(w.started + 610)
        self.assertIsNotNone(w.process.poll())
        self.assertNotIn("t", self.engine.workers)
        node = self.store.node("t")
        self.assertEqual(node["status"], "complete")
        self.assertIn(rid, node["learned"])
        self.assertIn("stopped", node["learned"])
        self.assertEqual(self.engine.steps, 0)

    def test_stop_leaves_no_open_report_for_the_stopped_worker(self):
        self.store.delegate("t", "claude worker")
        w = self.worker([BASH])
        rid = self.open_slow(w)
        self.store.resolve_report(rid, "stopped")
        # A later pass must not open a second report for the worker that is being stopped.
        self.engine._watch(w.started + 700)
        self.assertEqual(self.store.reports(open_only=True), [])
        self.assertEqual([r["status"] for r in self.store.reports()], ["stopped"])

    def test_stop_closes_other_open_reports_of_the_worker(self):
        self.store.delegate("t", "claude worker")
        w = self.worker([BASH])
        rid = self.open_slow(w)
        other = self.store.open_report("stalled", "silent", "t", "worker", "claude")
        w.reported.add(other)
        self.store.resolve_report(rid, "stopped")
        self.engine._watch(w.started + 610)
        self.assertEqual(self.store.reports(open_only=True), [])

    def test_stop_kills_the_children_of_the_worker(self):
        self.store.delegate("t", "claude worker")
        code = ("import subprocess,sys,time\n"
                "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                "print(c.pid,flush=True)\nprint(sys.argv[1],flush=True)\ntime.sleep(60)")
        p = subprocess.Popen([sys.executable, "-c", code, BASH], stdout=subprocess.PIPE, text=True,
                             start_new_session=True)
        self.procs.append(p)
        child = int(p.stdout.readline())
        w = Worker("t", p, Path(self.temp.name), "claude")
        w.monitor = monitor.Monitor("claude", p.pid, "worker", "t", expected=600, start=w.started)
        w.monitor.feed(BASH, w.started)
        self.engine.workers["t"] = w
        rid = self.open_slow(w)
        self.store.resolve_report(rid, "stopped")
        self.engine._watch(w.started + 610)
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_dismissed_report_is_not_opened_again(self):
        w = self.worker([BASH])
        rid = self.open_slow(w)
        self.store.resolve_report(rid, "dismissed")
        self.engine._watch(w.started + 700)
        self.engine._watch(w.started + 900)
        self.assertEqual(self.kinds(), [("slow", "dismissed")])
        self.assertIsNone(w.process.poll())

    def test_dismissing_one_kind_leaves_the_other_kind_open_to_report(self):
        w = self.worker([PARTIAL])
        self.engine._watch(w.started + 125)
        self.store.resolve_report(self.store.reports()[0]["report_id"], "dismissed")
        self.engine._watch(w.started + 700)
        self.assertEqual(self.kinds(), [("stalled", "dismissed"), ("slow", "open")])

    def test_an_extension_is_read_again_on_each_pass(self):
        w = self.worker([BASH])
        rid = self.open_slow(w)
        self.store.resolve_report(rid, "extended", 100)
        self.engine._watch(w.started + 650)
        self.assertEqual(w.monitor.extension, 100)
        self.engine._watch(w.started + 790)  # past 700: a new slow report
        self.assertEqual(self.kinds(), [("slow", "extended"), ("slow", "open")])
        self.store.resolve_report("r2", "extended", 300)
        self.engine._watch(w.started + 800)
        self.assertEqual(w.monitor.extension, 400)

    def test_a_dismissal_holds_for_a_retry_of_the_same_task(self):
        w = self.worker([BASH])
        rid = self.open_slow(w)
        self.store.resolve_report(rid, "dismissed")
        self.assertIn(rid, w.reported)
        w2 = self.worker([BASH])  # a retry is a new Worker for the same node
        self.engine._watch(w2.started + 601)
        self.assertEqual(self.kinds(), [("slow", "dismissed")])

    def test_a_dismissal_holds_after_the_engine_restarts(self):
        w = self.worker([BASH])
        self.store.resolve_report(self.open_slow(w), "dismissed")
        root = Path(self.temp.name)
        engine2 = Engine(Store(root / ".agentic-agile"), root, host="claude", say=lambda *_: None)
        w2 = self.worker([BASH])
        engine2.workers["t"] = w2
        engine2._watch(w2.started + 601)
        engine2._watch(w2.started + 900)
        self.assertEqual(self.kinds(), [("slow", "dismissed")])

    def test_a_dismissal_of_another_task_or_kind_does_not_hide_a_report(self):
        self.store.add_node("u", "U", "do u", "u done", why="w", minutes=10)
        self.store.open_report("slow", "e", "u", "worker", "claude")
        self.store.resolve_report("r1", "dismissed")
        self.assertEqual(self.store.dismissed_kinds("t"), set())
        self.assertEqual(self.store.dismissed_kinds("u"), {"slow"})
        w = self.worker([BASH])
        self.engine._watch(w.started + 601)
        self.assertEqual(self.kinds(), [("slow", "dismissed"), ("slow", "open")])
        self.engine._close_reports(w)
        self.assertEqual(self.store.reports(open_only=True), [])
        self.assertEqual(self.kinds(), [("slow", "dismissed"), ("slow", "ended")])
        self.assertEqual(self.store.dismissed_kinds("t"), set())  # a retry of t reports again

    def test_a_task_without_minutes_still_has_an_expected_time(self):
        self.store.add_node("u", "U", "do u", "u done", why="w")
        self.assertGreater(self.store.expected_seconds("u"), 0)


if __name__ == "__main__":
    unittest.main()
