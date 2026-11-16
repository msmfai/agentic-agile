from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentic_agile.procs import parse_etime, parse_ps

FIX = ROOT / "tests" / "fixtures"
DAY = 86400


def rows(name):
    text = (FIX / name).read_text()
    got = parse_ps(text)
    by_pid = {r.pid: r for r in got}
    return text, got, by_pid


class EtimeValues(unittest.TestCase):
    def test_procps_and_macos(self):
        for text, secs in [("00:03", 3), ("59:59", 3599), ("01:00:00", 3600), ("21:47:10", 78430),
                           ("1-00:00:00", DAY), ("4-23:08:03", 4 * DAY + 23 * 3600 + 8 * 60 + 3),
                           ("123-01:02:03", 123 * DAY + 3723), ("  00:01", 1)]:
            self.assertEqual(parse_etime(text), secs, text)

    def test_busybox(self):
        for text, secs in [("0:04", 4), ("9:59", 599), ("59:59", 3599), ("1h00", 3600), ("3h07", 3 * 3600 + 420),
                           ("23h59", 23 * 3600 + 59 * 60), ("1d00", DAY), ("4d23", 4 * DAY + 23 * 3600),
                           ("123d05", 123 * DAY + 5 * 3600)]:
            self.assertEqual(parse_etime(text), secs, text)

    def test_rejects_other_shapes(self):
        for text in ["", "-", "12", "1:2:3:4", "4d", "h07", "3x07", "Sat"]:
            with self.assertRaises(ValueError, msg=text):
                parse_etime(text)


class ProcpsFixture(unittest.TestCase):  # Debian bookworm, procps-ng 4.0.2, host pid namespace of a VM up 4.96 days
    def test_rows(self):
        text, got, by_pid = rows("ps-procps-4.0.2.txt")
        self.assertEqual(len(got), len([l for l in text.splitlines() if l.strip()]))
        init = by_pid[1]
        self.assertEqual((init.ppid, init.stat, init.args), (0, "Ss", "/sbin/init"))
        self.assertEqual(init.elapsed, 4 * DAY + 23 * 3600 + 8 * 60 + 3)  # days
        self.assertEqual(by_pid[838602].elapsed, 16 * 3600 + 31 * 60 + 6)  # hours, age over 1 hour and under 1 day
        self.assertEqual(by_pid[838602].args, "[kworker/1:2-inet_frag_wq]")  # bracketed kernel thread
        self.assertTrue(any(r.elapsed < 60 for r in got))  # minutes and seconds only

    def test_parenthesised_and_zombie(self):
        _, got, by_pid = rows("ps-procps-4.0.2.txt")
        self.assertEqual(by_pid[495].args, "(sd-pam)")
        self.assertEqual(by_pid[495].elapsed, 4 * DAY + 23 * 3600 + 8 * 60)
        zombie = by_pid[975032]
        self.assertEqual((zombie.stat, zombie.args, zombie.ppid), ("Z", "[sh] <defunct>", 975028))
        self.assertEqual(zombie.elapsed, 1)
        self.assertEqual(by_pid[975028].args, "sleep 300")  # args with a space stay whole


class BusyboxFixture(unittest.TestCase):  # Alpine 3.22, BusyBox 1.37, same host
    def test_rows(self):
        text, got, by_pid = rows("ps-busybox-1.37.txt")
        self.assertEqual(len(got), len([l for l in text.splitlines() if l.strip()]))
        self.assertEqual(by_pid[1].elapsed, 4 * DAY + 23 * 3600)  # 4d23
        self.assertEqual(by_pid[1].args, "{systemd} /sbin/init")
        self.assertEqual(by_pid[918329].elapsed, 6 * 3600 + 44 * 60)  # 6h44, age over 1 hour and under 1 day
        self.assertEqual(by_pid[972346].elapsed, 9 * 60 + 59)  # 9:59
        self.assertEqual(by_pid[975122].elapsed, 2)  # 0:02

    def test_parenthesised_and_zombie(self):
        _, _, by_pid = rows("ps-busybox-1.37.txt")
        self.assertEqual(by_pid[495].args, "(sd-pam)")
        zombie = by_pid[975126]
        self.assertEqual((zombie.stat, zombie.args, zombie.ppid), ("Z", "[sh]", 975122))  # no <defunct> marker
        self.assertEqual(zombie.elapsed, 2)


class MacosFixture(unittest.TestCase):
    def test_rows(self):
        text, got, by_pid = rows("ps-macos.txt")
        self.assertEqual(len(got), len([l for l in text.splitlines() if l.strip()]))
        self.assertEqual(by_pid[1].args, "/sbin/launchd")
        self.assertGreater(by_pid[1].elapsed, DAY)


class NoiseLines(unittest.TestCase):
    def test_skips_non_rows(self):
        got = parse_ps("ps: unrecognized option: V\n  PID  PPID STAT ELAPSED COMMAND\n  7  1 S 0:04 x y\n  8 1 S bogus z\n")
        self.assertEqual([(r.pid, r.elapsed, r.args) for r in got], [(7, 4, "x y")])


if __name__ == "__main__":
    unittest.main()
