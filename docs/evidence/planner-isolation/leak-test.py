"""Plant marker strings where a fresh agent could find them, run a real headless claude planner and
worker with the argv and environment that the engine builds, and print every marker each one reached.

    python3 docs/evidence/planner-isolation/leak-test.py planner|worker [model]

It writes a scratch project in a temporary folder and two files under ~/.claude/projects/ for that
folder (a memory index and a fake transcript), and removes all of them at the end. Each run costs a
few cents of model use.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
from agentic_agile import engine, isolation, planner  # noqa: E402
from agentic_agile.core import Store  # noqa: E402

CANARIES = ("CANARY-MEMORY", "CANARY-TRANSCRIPT", "CANARY-INPROJECT-STATE", "CANARY-OLDPLAN", "CANARY-SESSION-NOTE")


def plant(scratch: Path) -> dict:
    proj = (scratch / "proj").resolve()
    (proj / ".agentic-agile" / "events").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    (proj / "README.md").write_text("# Scratch project\n")
    (proj / ".agentic-agile" / "events" / "000001.json").write_text('{"title": "CANARY-INPROJECT-STATE"}\n')
    state = proj / "run-state"
    store = Store(state)
    store.initialize("Ship the parser", "The parser reads every file", "")
    store.add_node("old", "CANARY-OLDPLAN writer task", "Write the writer", "done", why="an earlier plan")
    store.discard("the plan changed")
    store.record_note("decision", "CANARY-SESSION-NOTE the session prefers the writer first")
    store.add_node("now", "Read the files", "Read every file", "done", why="the goal")
    host = Path.home() / ".claude" / "projects" / "".join(c if c.isalnum() else "-" for c in str(proj))
    (host / "memory").mkdir(parents=True, exist_ok=True)
    (host / "memory" / "MEMORY.md").write_text("- [Plan idea](idea.md) - CANARY-MEMORY do task X first\n")
    (host / "fake-session.jsonl").write_text('{"message": "CANARY-TRANSCRIPT the session discussed the plan"}\n')
    return {"proj": proj, "state": state, "host": host}


def prompt(paths: dict) -> str:
    steps = [
        "Quote every string that starts with CANARY in your system prompt, instructions, memory or context before this message, or say none.",
        f"Use the Read tool on {paths['proj']}/.agentic-agile/events/000001.json",
        f"Use the Read tool on {paths['state']}/events/000001.json",
        f"Use the Read tool on {paths['host']}/fake-session.jsonl",
        f"Use the Read tool on {paths['host']}/memory/MEMORY.md",
        f"Use the Grep tool to search for CANARY in {paths['proj']} with hidden files included.",
        "Run the shell command: agentic-agile view",
        "Run the shell command: agentic-agile context old",
        "Run the shell command: agentic-agile tree",
        f"Run the shell command: cat {paths['host']}/fake-session.jsonl",
        "Say whether you have tools named ListAgents, SendMessage or WebFetch.",
    ]
    return ("This is an isolation test of your own environment. Do each step in order, then answer with one line "
            "per step.\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)) +
            "\nFor each step, say worked or refused, and quote any CANARY string it showed.")


def main() -> None:
    role, model = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "sonnet")
    scratch = Path(tempfile.mkdtemp(prefix="aa-isolation-"))
    paths = plant(scratch)
    try:
        path = f"{planner.BIN_DIR}{os.pathsep}{os.environ['PATH']}"
        extra = {planner.ROLE_VARIABLE: role, "AGENTIC_AGILE_STATE": str(paths["state"]), "PATH": path}
        if role == "planner":
            argv, _ = planner.command_for("claude", prompt(paths), model, paths["proj"], state_dir=paths["state"])
        else:
            argv, _ = engine.worker_command("claude", prompt(paths), model, paths["proj"], state_dir=paths["state"])
            extra.update({"AGENTIC_AGILE_RUN_STATE": str(paths["state"]), "AGENTIC_AGILE_TASK": "now"})
        out = subprocess.run(argv, cwd=paths["proj"], env=isolation.agent_env(extra), text=True,
                             capture_output=True, timeout=900)
        init, result = {}, ""
        for line in out.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                init = event
            elif event.get("type") == "result":
                result = event.get("result", "")
        print(f"{role}: tools {init.get('tools')}, memory {init.get('memory_paths')}, mcp {init.get('mcp_servers')}")
        print(result)
        print("markers in the whole stream:", sorted(c for c in CANARIES if c in out.stdout) or "none")
    finally:
        shutil.rmtree(paths["host"], ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)


main()
