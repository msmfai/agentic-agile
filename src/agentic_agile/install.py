"""Install Agentic Agile into agent hosts: Claude Code, Codex, and OpenCode.

Two variants, two scopes:

* ``full`` adds the host's UI (Claude Code band and pane, OpenCode sidebar and
  indicator) on top of the skill and goal hooks.
* ``lite`` installs the skill and goal hooks as plain files in the host's own
  config, where they are easy to read and edit.
* ``global`` installs for every project; ``project`` writes into one project.

Everything written here is tagged so ``uninstall`` removes exactly what
``install`` added.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

REPO = "msmfai/agentic-agile"
HOSTS = ("claude", "codex", "opencode")
MARKER = ".agentic-agile-install"  # dropped in copied skill folders we own
# Environment variables each host sets for the commands it runs.
SELF_ENV = {
    "claude": ("CLAUDECODE",),
    "opencode": ("OPENCODE",),
    "codex": ("CODEX_THREAD_ID", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED"),
}


class Plan:
    """Collects steps so --print and a real run share one code path."""

    def __init__(self, dry_run: bool):
        self.dry_run = dry_run
        self.ok = True

    def say(self, text: str) -> None:
        print("  " + text, flush=True)

    def run(self, argv: list[str], cwd: Path | None = None, required: bool = True) -> None:
        self.say("$ " + " ".join(argv))
        if self.dry_run:
            return
        try:
            proc = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            if required:
                self.fail(f"install {argv[0]} or add it to PATH, then run this again")
            return
        if proc.returncode and required:
            self.fail("this step needs attention: " + (proc.stdout.strip().splitlines() or [" ".join(argv)])[-1])

    def fail(self, text: str) -> None:
        self.ok = False
        self.say("! " + text)


def checkout() -> Path | None:
    """The plugin checkout this module runs from, if it is one."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".claude-plugin" / "plugin.json").exists() else None


def detect_self() -> str | None:
    """The host whose shell this command is running in, if it says so."""
    for host, names in SELF_ENV.items():
        if any(os.environ.get(name) for name in names):
            return host
    return None


# -- locations ----------------------------------------------------------------

def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def claude_dir(scope: str, project: Path) -> Path:
    if scope == "project":
        return project / ".claude"
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or _home() / ".claude")


def codex_hooks_file(scope: str, project: Path) -> Path:
    if scope == "project":
        return project / ".codex" / "hooks.json"
    return Path(os.environ.get("CODEX_HOME") or _home() / ".codex") / "hooks.json"


def agents_skills_dir(scope: str, project: Path) -> Path:
    return (project if scope == "project" else _home()) / ".agents" / "skills"


def opencode_dir(scope: str, project: Path) -> Path:
    if scope == "project":
        return project / ".opencode"
    if os.environ.get("OPENCODE_CONFIG_DIR"):
        return Path(os.environ["OPENCODE_CONFIG_DIR"]).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config") / "opencode"


# -- plain-file pieces shared by the lite installs -----------------------------

def _read_json(path: Path, plan: Plan) -> dict | None:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        data = None
    if not isinstance(data, dict):
        plan.fail(f"{path} has comments or custom syntax; add the entry by hand (see INSTALL.md)")
        return None
    return data


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _is_ours(group: dict) -> bool:
    return any("agentic-agile" in str(h.get("command", "")) and " hook-" in str(h.get("command", ""))
               for h in group.get("hooks", []))


def hook_groups(cli: Path, host: str) -> dict:
    """SessionStart and Stop entries in the shape Claude Code and Codex share."""
    base = f'"{cli}" hook-{{}} --host {host}'
    return {
        "SessionStart": {
            "matcher": "startup|resume|compact|clear",
            "hooks": [{"type": "command", "command": base.format("context"),
                       "statusMessage": "Loading the Agentic Agile operative goal", "timeout": 10}],
        },
        "Stop": {
            "hooks": [{"type": "command", "command": base.format("stop"),
                       "statusMessage": "Checking the Agentic Agile goal", "timeout": 10}],
        },
    }


def set_hooks(path: Path, groups: dict | None, plan: Plan) -> None:
    """Replace our entries in a hooks-bearing JSON file; ``None`` removes them."""
    verb = "merge goal hooks into" if groups else "remove goal hooks from"
    if groups is None and not (path.exists() and " hook-" in path.read_text(encoding="utf-8", errors="replace")):
        return
    plan.say(f"{verb} {path}")
    if plan.dry_run:
        return
    data = _read_json(path, plan)
    if data is None:
        return
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    for event in ("SessionStart", "Stop"):
        kept = [g for g in hooks.get(event, []) if not _is_ours(g)]
        if groups:
            kept.append(groups[event])
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    _write_json(path, data)


def set_skill(root: Path | None, target: Path, plan: Plan, install: bool) -> None:
    """Copy the skill folder with the CLI path filled in, or remove our copy."""
    owned = (target / MARKER).exists()
    if not install:
        if owned:
            plan.say(f"remove {target}")
            if not plan.dry_run:
                shutil.rmtree(target)
        return
    if target.exists() and not owned:
        plan.fail(f"{target} is your own folder; move it, then run this again")
        return
    plan.say(f"copy skill to {target}")
    if plan.dry_run or root is None:
        return
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(root / "skills" / "agentic-agile", target)
    skill = target / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    cli = root / "bin" / "agentic-agile"
    skill.write_text(text.replace('"${CLAUDE_PLUGIN_ROOT}/bin/agentic-agile"', f'"{cli}"'),
                     encoding="utf-8")
    (target / MARKER).write_text(f"source: {root}\n", encoding="utf-8")


# -- hosts --------------------------------------------------------------------

def _claude(plan: Plan, root: Path | None, source: str, scope: str, variant: str, project: Path, install: bool) -> None:
    config = claude_dir(scope, project)
    plugin_scope = "user" if scope == "global" else "project"
    cwd = project if scope == "project" else None
    settings = config / "settings.json"
    skill = config / "skills" / "agentic-agile"
    if install and variant == "full":
        # The plugin carries its own skill and hooks; drop lite copies first.
        set_hooks(settings, None, plan)
        set_skill(root, skill, plan, install=False)
        plan.run(["claude", "plugin", "marketplace", "add", source, "--scope", plugin_scope], cwd)
        plan.run(["claude", "plugin", "install", "agentic-agile@agentic-agile", "--scope", plugin_scope], cwd)
        plan.say("Restart Claude Code: band above the prompt, /agile-graph for the pane.")
        return
    plan.run(["claude", "plugin", "uninstall", "agentic-agile@agentic-agile", "--scope", plugin_scope], cwd, required=False)
    if not install:
        set_hooks(settings, None, plan)
        set_skill(root, skill, plan, install=False)
        return
    if root is None:
        plan.fail(f"lite installs from a checkout: git clone https://github.com/{REPO}")
        return
    set_hooks(settings, hook_groups(root / "bin" / "agentic-agile", "claude"), plan)
    set_skill(root, skill, plan, install=True)
    plan.say("Restart Claude Code. For the view, run 'agentic-agile statusline' or 'agentic-agile watch'.")


def _codex(plan: Plan, root: Path | None, source: str, scope: str, variant: str, project: Path, install: bool) -> None:
    hooks = codex_hooks_file(scope, project)
    skill = agents_skills_dir(scope, project) / "agentic-agile"
    as_plugin = install and variant == "full" and scope == "global"
    if as_plugin:
        set_hooks(hooks, None, plan)
        set_skill(root, skill, plan, install=False)
        plan.run(["codex", "plugin", "marketplace", "add", source])
        plan.run(["codex", "plugin", "add", "agentic-agile@agentic-agile"])
    else:
        if scope == "global":
            plan.run(["codex", "plugin", "remove", "agentic-agile@agentic-agile"], required=False)
        if not install:
            set_hooks(hooks, None, plan)
            set_skill(root, skill, plan, install=False)
            return
        if root is None:
            plan.fail(f"this installs from a checkout: git clone https://github.com/{REPO}")
            return
        if variant == "full":
            plan.say("Codex plugins are global, so a project install uses plain files.")
        set_hooks(hooks, hook_groups(root / "bin" / "agentic-agile", "codex"), plan)
        set_skill(root, skill, plan, install=True)
    plan.say("In Codex, run /hooks and trust the two Agentic Agile hooks (SessionStart, Stop).")
    plan.say("For the live view in Codex, run 'agentic-agile watch' in a side pane.")


def _opencode(plan: Plan, root: Path | None, source: str, scope: str, variant: str, project: Path, install: bool) -> None:
    config = opencode_dir(scope, project)
    server = config / "plugins" / "agentic-agile.js"
    skill = config / "skills" / "agentic-agile"
    tui_json = config / "tui.json"
    wants_tui = install and variant == "full"
    if install and root is None:
        plan.fail(f"OpenCode loads plugins from files, so this installs from a checkout: git clone https://github.com/{REPO}")
        return

    if install:
        plan.say(f"write {server}")
        if not plan.dry_run:
            target = json.dumps(str(root / "opencode" / "agentic-agile.js"))
            options = json.dumps({"ui": variant == "full"})
            server.parent.mkdir(parents=True, exist_ok=True)
            server.write_text(
                "// Installed by `agentic-agile install opencode`. Edit the options here, or the\n"
                "// plugin itself at the path below.\n"
                f"import {{ AgenticAgilePlugin as plugin }} from {target};\n\n"
                f"export const AgenticAgilePlugin = (ctx) => plugin(ctx, {options});\n",
                encoding="utf-8",
            )
        set_skill(root, skill, plan, install=True)
    else:
        if server.exists() and "agentic-agile" in server.read_text(encoding="utf-8", errors="replace"):
            plan.say(f"remove {server}")
            if not plan.dry_run:
                server.unlink()
        set_skill(root, skill, plan, install=False)

    if not wants_tui and not tui_json.exists():
        return
    tui_plugin = str(root / "opencode" / "agentic-agile-tui.tsx") if root else ""
    plan.say(("add the sidebar plugin to " if wants_tui else "remove the sidebar plugin from ") + str(tui_json))
    if plan.dry_run:
        return
    settings = _read_json(tui_json, plan)
    if settings is None:
        if wants_tui:
            plan.say(f'  add this to its "plugin" list: {tui_plugin}')
        return
    plugins = [p for p in settings.get("plugin", []) if not str(p).endswith("agentic-agile-tui.tsx")]
    if wants_tui:
        settings.setdefault("$schema", "https://opencode.ai/tui.json")
        plugins.append(tui_plugin)
    if plugins:
        settings["plugin"] = plugins
    else:
        settings.pop("plugin", None)
    if settings.keys() - {"$schema"}:
        _write_json(tui_json, settings)
    else:
        tui_json.unlink()
    if install:
        plan.say("Restart OpenCode.")


STEPS = {"claude": _claude, "codex": _codex, "opencode": _opencode}


def install(host: str, source: str | None = None, dry_run: bool = False, scope: str = "global",
            variant: str = "full", project: str | None = None, remove: bool = False) -> int:
    root = checkout()
    source = source or (str(root) if root else REPO)
    if source != REPO and Path(source).expanduser().exists():
        root = Path(source).expanduser().resolve()
    project_dir = Path(project or ".").expanduser().resolve()
    if host == "self":
        detected = detect_self()
        if detected is None:
            print("Name the agent that runs this command: claude, codex, or opencode.")
            return 2
        host = detected
    targets = HOSTS if host == "all" else (host,)
    failed = False
    for name in targets:
        if shutil.which(name) is None and host == "all":
            print(f"{name}: install it and run this again to add it", flush=True)
            continue
        what = "uninstall" if remove else f"{variant} install"
        where = f"project {project_dir}" if scope == "project" else "global"
        print(f"{name}: {what}, {where}", flush=True)
        plan = Plan(dry_run)
        STEPS[name](plan, root, source, scope, variant, project_dir, not remove)
        failed = failed or not plan.ok
    return 1 if failed else 0
