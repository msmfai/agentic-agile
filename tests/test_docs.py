from __future__ import annotations

import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, str(ROOT / "bin" / "agentic-agile")]
DOCS = [ROOT / "skills" / "agentic-agile" / "SKILL.md", ROOT / "docs" / "commands.md", ROOT / "docs" / "protocol.md"]


class DocCommands(unittest.TestCase):
    def test_every_report_command_in_the_docs_runs(self):
        found = set()
        for path in DOCS:
            for code in re.findall(r"`([^`]*report [^`]*)`", path.read_text()):
                code = code.replace("agentic-agile ", "").strip()
                if not re.match(r"^report (list|ID (stop|extend|dismiss))", code):
                    continue
                found.add(code)
        self.assertTrue(any("stop" in c for c in found) and any("extend" in c for c in found)
                        and any("dismiss" in c for c in found) and any("list" in c for c in found))
        with tempfile.TemporaryDirectory() as tmp:
            for code in sorted(found):
                argv = shlex.split(re.sub(r"\[[^\]]*\]", "", code.replace("\\|", "|")))
                argv = [("r1" if a == "ID" else "30" if a == "N" else a) for a in argv]
                if "extend" in argv and "--seconds" not in argv:
                    continue
                out = subprocess.run(CLI + ["--state", tmp] + argv, capture_output=True, text=True)
                # A parser error is exit 2 with a usage line. A missing report is a plain refusal.
                self.assertNotIn("usage:", out.stderr, f"{code} does not parse: {out.stderr}")

    def test_the_docs_teach_each_behaviour(self):
        text = "\n".join(p.read_text() for p in DOCS)
        for phrase in ("report ID stop", "report ID extend --seconds N", "report ID dismiss", "`21`",
                       "hook-stop", "stays delegated", "restart", "long generation", "--variant lite"):
            self.assertIn(phrase, text)

    def test_public_docs_use_no_colons_in_prose(self):
        for path in DOCS[1:]:
            for line in path.read_text().splitlines():
                if line.startswith(("|", "    ", "```")) or "http" in line or "`" in line.split(":")[0]:
                    continue
                self.assertNotRegex(line, r"\w: ", f"{path.name}: {line}")


if __name__ == "__main__":
    unittest.main()
