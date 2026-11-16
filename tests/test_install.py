from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    """Lite installs are plain files, so these tests run on any machine."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.project = Path(self.temp.name) / "project"
        self.home.mkdir()
        self.project.mkdir()
        # With an empty PATH, lite installs still work: they use plain files.
        self.env = {"HOME": str(self.home), "PATH": "/nonexistent"}

    def tearDown(self):
        self.temp.cleanup()

    def cli(self, *args):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "agentic-agile"), *args],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self.env, stdin=subprocess.DEVNULL,
        )
        return proc.returncode, proc.stdout

    def test_lite_project_install_writes_plain_files_and_uninstall_removes_exactly_ours(self):
        settings = self.project / ".claude" / "settings.json"
        settings.parent.mkdir()
        mine = {"hooks": [{"type": "command", "command": "/usr/local/bin/my-own-stop-hook"}]}
        settings.write_text(json.dumps({"model": "x", "hooks": {"Stop": [mine]}}))

        for host in ("claude", "codex", "opencode"):
            code, out = self.cli("install", host, "--scope", "project", "--variant", "lite", "--project", str(self.project))
            self.assertEqual(code, 0, out)

        data = json.loads(settings.read_text())
        self.assertEqual(data["model"], "x")
        stop = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop[0], "/usr/local/bin/my-own-stop-hook")
        self.assertIn("hook-stop --host claude", stop[1])
        self.assertIn("SessionStart", data["hooks"])
        codex = json.loads((self.project / ".codex" / "hooks.json").read_text())
        self.assertIn("hook-stop --host codex", codex["hooks"]["Stop"][0]["hooks"][0]["command"])
        for skill in (".claude/skills/agentic-agile", ".agents/skills/agentic-agile", ".opencode/skills/agentic-agile"):
            text = (self.project / skill / "SKILL.md").read_text()
            self.assertNotIn("CLAUDE_PLUGIN_ROOT", text)
            self.assertIn(str(ROOT / "bin" / "agentic-agile"), text)
        shim = (self.project / ".opencode" / "plugins" / "agentic-agile.js").read_text()
        self.assertIn('{"ui": false}', shim)
        self.assertFalse((self.project / ".opencode" / "tui.json").exists())
        for name in (".claude", ".codex", ".agents", ".config"):
            self.assertFalse((self.home / name).exists(), f"project scope keeps all files in the project")

        # A second install keeps one copy of each hook.
        self.cli("install", "claude", "--scope", "project", "--variant", "lite", "--project", str(self.project))
        self.assertEqual(len(json.loads(settings.read_text())["hooks"]["Stop"]), 2)

        for host in ("claude", "codex", "opencode"):
            code, out = self.cli("uninstall", host, "--scope", "project", "--project", str(self.project))
            self.assertEqual(code, 0, out)
        data = json.loads(settings.read_text())
        self.assertEqual(data, {"model": "x", "hooks": {"Stop": [mine]}})
        self.assertNotIn("hooks", json.loads((self.project / ".codex" / "hooks.json").read_text()))
        for gone in (".claude/skills/agentic-agile", ".agents/skills/agentic-agile", ".opencode/skills/agentic-agile",
                     ".opencode/plugins/agentic-agile.js"):
            self.assertFalse((self.project / gone).exists(), gone)

    def test_global_lite_and_opencode_full_tui_entry(self):
        for host in ("claude", "codex"):
            self.assertEqual(self.cli("install", host, "--variant", "lite")[0], 0)
        self.assertTrue((self.home / ".claude" / "settings.json").exists())
        self.assertTrue((self.home / ".codex" / "hooks.json").exists())
        self.assertTrue((self.home / ".agents" / "skills" / "agentic-agile" / "SKILL.md").exists())

        tui = self.home / ".config" / "opencode" / "tui.json"
        tui.parent.mkdir(parents=True)
        tui.write_text(json.dumps({"theme": "dark", "plugin": ["other-plugin"]}))
        self.assertEqual(self.cli("install", "opencode")[0], 0)
        plugins = json.loads(tui.read_text())["plugin"]
        self.assertEqual(plugins[0], "other-plugin")
        self.assertTrue(plugins[1].endswith("opencode/agentic-agile-tui.tsx"))
        self.assertIn('{"ui": true}', (tui.parent / "plugins" / "agentic-agile.js").read_text())
        # Switching to lite drops the UI and keeps the person's own settings.
        self.assertEqual(self.cli("install", "opencode", "--variant", "lite")[0], 0)
        after = json.loads(tui.read_text())
        self.assertEqual((after["theme"], after["plugin"]), ("dark", ["other-plugin"]))

    def test_the_users_own_skill_folder_is_kept(self):
        theirs = self.project / ".claude" / "skills" / "agentic-agile"
        theirs.mkdir(parents=True)
        (theirs / "SKILL.md").write_text("hand-written")
        code, out = self.cli("install", "claude", "--scope", "project", "--variant", "lite", "--project", str(self.project))
        self.assertEqual(code, 1)
        self.assertIn("is your own folder", out)
        self.assertEqual((theirs / "SKILL.md").read_text(), "hand-written")

    def test_self_detects_the_host_from_its_environment(self):
        self.assertEqual(self.cli("install", "self", "--print")[0], 2)
        self.env["OPENCODE"] = "1"
        code, out = self.cli("install", "self", "--print")
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith("opencode: full install, global"))


if __name__ == "__main__":
    unittest.main()
