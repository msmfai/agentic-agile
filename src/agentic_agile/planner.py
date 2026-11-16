"""Write each new plan in a fresh context.

`agentic-agile replan` starts a separate headless agent (Claude Code, Codex,
OpenCode, or a command you choose). That agent sees only the planning text and
the project: the goal, the findings, the completed tasks, and the tasks running
elsewhere. The old plan is absent from all of it. The planner writes the new
plan with agentic-agile commands, ranks the tasks, and exits.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from . import isolation, monitor
from .core import AgileError, Store

HOSTS = ("claude", "codex", "opencode")
ROLE_VARIABLE = "AGENTIC_AGILE_ROLE"
DEFAULT_TIMEOUT = 600
REPORT_POLL_SECONDS = 1.0  # how often a headless run is checked for a report
DEFAULT_MODELS = {"claude": "sonnet"}
BIN_DIR = Path(__file__).resolve().parents[2] / "bin"


def tail_text(text: str, limit: int) -> str:
    """The end of `text` within `limit` characters, starting at a word so no word is cut."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    first = re.search(r"\s", cut)
    return "..." + (cut[first.end():] if first else cut).lstrip()


def in_planner() -> bool:
    """True inside a planner, reviewer or worker process. Goal hooks stay silent there."""
    return os.environ.get(ROLE_VARIABLE) in ("planner", "reviewer", "worker")


def brief(store: Store, node_id: str) -> str:
    """Instructions for an agent that does one task, runnable from any folder or worktree."""
    from .core import BIN
    task = store.context(node_id, for_agent=True).split("## Instructions")[0].rstrip()
    command = f"{BIN} --state {store.state_dir}" if BIN.exists() else f"agentic-agile --state {store.state_dir}"
    return (
        task.replace("# AGENTIC AGILE: DO THIS TASK", "# AGENTIC AGILE: A TASK FOR YOU", 1)
        + "\n\n## Instructions\n"
        + "Do this one task. Check how the result works, in addition to the tests.\n"
        + "When it is done, record the finding with this exact command (it works from any folder):\n"
        + f"  {command} complete {node_id} --learned \"what you found, and the questions that are still open\" "
        + "--understanding changed|same --because \"why\"\n"
        + "Write the finding as facts about the project and the goal, with the evidence for each.\n"
        + "Say changed when the finding changes what the work needs or its order, and same otherwise.\n"
        + "If the command cannot run, put the finding in your final report instead.\n"
    )


def settings(store: Store) -> dict:
    """This machine's planner settings. They live outside the event history."""
    return {
        "host": store._runtime("planner_host") or "",
        "model": store._runtime("planner_model") or "",
        "timeout": int(store._runtime("planner_timeout") or DEFAULT_TIMEOUT),
        "command": store._runtime("planner_command") or "",
    }


def save_settings(store: Store, **values: str | int | None) -> None:
    with store.connect() as conn:
        for key, value in values.items():
            if value is None:
                continue
            if value == "":
                conn.execute("DELETE FROM runtime WHERE key=?", (f"planner_{key}",))
            else:
                conn.execute("INSERT OR REPLACE INTO runtime VALUES (?,?)", (f"planner_{key}", str(value)))


def prompt(store: Store) -> str:
    """The whole input of the planner: its role, then the planning text."""
    role = (
        "You are the planner for an Agentic Agile project. You start with no history. Everything you "
        "need is the text below and the files in this folder.\n\n"
        "Read the project files to learn its current state. The text below holds the goal and every "
        "finding so far. Change no project "
        "file and do no task. Write the plan with the agentic-agile commands below (agentic-agile is "
        "on your PATH and points at the right state). Start every shell call with the word agentic-agile "
        "and write the name in full in each command, because the tool permissions check each command by "
        "its name. You can join several agentic-agile commands with &&. Rank every task as the text "
        "describes, start none, then run agentic-agile tree and stop."
    )
    # The planner's PATH starts with this tool's bin folder, so the plain name always works there.
    return role + "\n\n" + store.hypothesis_capsule(for_planner=True).replace(str(BIN_DIR / 'agentic-agile'), 'agentic-agile')


def resolve_host(requested: str = "") -> str:
    """The planner host: the one asked for, the agent running now, or the first one installed."""
    if requested:
        if requested != "command" and requested not in HOSTS:
            raise AgileError(f"{requested}: use claude, codex, opencode, or command")
        return requested
    from .install import detect_self

    running = detect_self()
    if running and shutil.which(running):
        return running
    for host in HOSTS:
        if shutil.which(host):
            return host
    raise AgileError("install claude, codex, or opencode, or set a planner command: agentic-agile planner --command CMD")


def command_for(host: str, text: str, model: str, cwd: Path, custom: str = "",
                state_dir: Path | None = None) -> tuple[list[str], str | None]:
    """The argv for one planner or reviewer run, and the text to send on stdin (custom commands only).

    The agent starts with no link to this session and no view of earlier plans: see isolation.py.
    Hooks are off, because a hook that waits for a person would look like a stall that the engine
    caused itself. `--include-hook-events` stays on so a hook that still runs shows in the stream.
    """
    if host == "command":
        if not custom:
            raise AgileError("set the command first: agentic-agile planner --command CMD")
        return shlex.split(custom), text
    if host == "claude":
        argv = ["claude", "-p", text, *isolation.claude_planner_flags(state_dir), "--no-session-persistence",
                "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--allowedTools", "Bash(agile:*)", "Bash(agile *)", "Bash(agentic-agile:*)", "Bash(agentic-agile *)",
                "Read", "Glob", "Grep",
                "--disallowedTools", "Edit", "Write"]
        return argv + (["--model", model] if model else []), None
    if host == "codex":
        argv = ["codex", "exec", *isolation.CODEX_FLAGS,
                "--skip-git-repo-check", "-s", "workspace-write", "--json", "-C", str(cwd)]
        return argv + (["-m", model] if model else []) + [text], None
    argv = ["opencode", "run", *isolation.OPENCODE_FLAGS, "--auto", "--format", "json", "--dir", str(cwd)]
    return argv + (["-m", model] if model else []) + [text], None


def _descendants(pid: int) -> list[str]:
    """The commands of every live process below `pid`, oldest first."""
    return [r.args for r in monitor.live_commands(monitor.sample_ps(time.monotonic()), pid)]


def is_partial(line: str) -> bool:
    """A claude partial-message line (type stream_event): it shows the agent is alive and is not kept."""
    return line.startswith("{") and '"type":"stream_event"' in line[:60].replace(" ", "")


def final_result(host: str, out: str) -> str:
    """For claude, the closing result event of the stream; the other hosts' output is left as it is."""
    if host != "claude":
        return out
    for line in reversed(out.splitlines()):
        if line.startswith("{") and '"type":"result"' in line.replace(" ", ""):
            return line
    return out


def run_headless(store: Store, role: str, host: str, argv: list[str], stdin: str | None, env: dict, cwd: Path,
                 expected: int, errors=None) -> subprocess.CompletedProcess:
    """Run one headless agent and watch it. The engine stops nothing on its own.

    A Monitor reads each output line. An agent that sends no output while it runs no
    command, or that runs past `expected` seconds, gets a report from Store.open_report.
    The run then goes on until the process ends or the top-level agent resolves one of its
    reports as stopped, which ends the process group. More time given with `report extend`
    moves the expected time. The output so far is saved in `<state>/<role>.log` on a stop.
    """
    sink = errors if errors is not None else subprocess.DEVNULL
    proc = subprocess.Popen(argv, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=sink, env=env,
                            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            start_new_session=True)
    started = time.monotonic()
    watch = monitor.Monitor(host, proc.pid, role, "", expected=expected, start=started)
    chunks: list[str] = []
    lock = threading.Lock()

    def pump() -> None:
        if stdin is not None:
            try:
                proc.stdin.write(stdin)
                proc.stdin.close()
            except OSError:
                pass
        for line in proc.stdout:
            with lock:
                watch.feed(line, time.monotonic())
            if not is_partial(line):
                chunks.append(line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    opened: set[str] = set()
    stopped = ""
    last_poll = 0.0
    while reader.is_alive():
        reader.join(0.5)
        now = time.monotonic()
        if now - last_poll < REPORT_POLL_SECONDS:
            continue
        last_poll = now
        try:
            watch.extension = monitor.extension_seconds(store, "")
            with lock:
                findings = watch.check(now)
            seen = store.reports()
            # A report the top-level agent dismissed stays dismissed for this agent.
            dismissed = {r["kind"] for r in seen if r["report_id"] in opened and r["status"] == "dismissed"}
            for finding in findings:
                if finding.kind not in dismissed:
                    opened.add(store.open_report(finding.kind, finding.evidence, "", role, host))
            stopped = next((r["report_id"] for r in seen
                            if r["report_id"] in opened and r["status"] == "stopped"), "")
        except (AgileError, OSError):
            continue
        if stopped:
            waiting = _descendants(proc.pid)
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            reader.join(10)
            break
    proc.wait()
    proc.stdout.close()
    out = "".join(chunks)
    try:
        store.close_reports(opened)
    except (AgileError, OSError):
        pass
    if not stopped:
        return subprocess.CompletedProcess(argv, proc.returncode, final_result(host, out), "")
    seconds = int(time.monotonic() - started)
    try:
        store.local_path(f"{role}.log").write_text(
            f"{role} on {host} stopped by the top-level agent (report {stopped}) after {seconds} s\n"
            f"processes: {waiting}\n\n{out}", encoding="utf-8")
    except OSError:
        pass
    raise AgileError(f"the {host} {role} was stopped after {seconds} s on report {stopped}; details in {role}.log")


def _cost(host: str, stdout: str) -> str:
    """A short cost note from the planner's own output, when it reports one."""
    try:
        if host == "claude":
            data = json.loads(stdout)
            return f"${float(data.get('total_cost_usd') or 0):.2f}"
        total, tokens = 0.0, 0
        for line in stdout.splitlines():
            event = json.loads(line) if line.startswith("{") else {}
            part = event.get("part") or {}
            total += float(part.get("cost") or 0)
            usage = event.get("usage") or {}
            tokens += int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        if total:
            return f"${total:.2f}" if total >= 0.01 else f"${total:.4f}"
        return f"{tokens} tokens" if tokens else ""
    except (ValueError, TypeError, AttributeError):
        return ""


def replan(store: Store, cwd: Path, host: str = "", model: str = "", timeout: int | None = None) -> dict:
    """Run one fresh planner. Returns what it added and started.

    Raises AgileError with the reason when the planner cannot run or finish, so
    the caller can fall back to writing the plan in the current session.
    """
    if os.environ.get(ROLE_VARIABLE) in ("planner", "reviewer"):
        raise AgileError("this is already a planner session; write the plan with agentic-agile add")
    running = store._runtime("planner_pid")
    if running and int(running) != os.getpid() and store.planner_running():
        raise AgileError("a fresh planner is already writing the plan; wait for it with: agentic-agile replan --wait")
    state = store.status()["status"]
    if state not in ("awaiting_hypothesis", "planning"):
        raise AgileError("the plan still has open tasks; clear them first with: agentic-agile discard --why TEXT")
    config = settings(store)
    host = resolve_host(host or config["host"])
    model = model or config["model"] or DEFAULT_MODELS.get(host, "")
    timeout = timeout or config["timeout"]
    before = {row["node_id"] for row in store.graph_rows()}
    text = prompt(store)
    argv, stdin = command_for(host, text, model, cwd, config["command"], store.state_dir)
    env = isolation.agent_env({ROLE_VARIABLE: "planner", "AGENTIC_AGILE_STATE": str(store.state_dir),
                               "PATH": f"{BIN_DIR}{os.pathsep}{os.environ.get('PATH', '')}"})
    with tempfile.TemporaryFile("w+") as errors:
        try:
            proc = run_headless(store, "planner", host, argv, stdin, env, cwd, timeout, errors)
        except FileNotFoundError:
            raise AgileError(f"{argv[0]} is not installed on this machine")
        errors.seek(0)
        error_text = errors.read().strip()
    added = [row for row in store.graph_rows() if row["node_id"] not in before]
    if proc.returncode != 0:
        detail = (error_text or proc.stdout.strip()).splitlines()[-1:] or ["no output"]
        raise AgileError(f"the {host} planner wrote no plan (exit {proc.returncode}): {detail[0][:300]}")
    if not added:
        answer = _answer_text(host, proc.stdout).strip() or "no reason given"
        return {"host": host, "model": model, "added": [], "started": None, "delegated": [],
                "cost": _cost(host, proc.stdout), "goal_met_reason": tail_text(answer, 600)}
    current = store.current()
    return {
        "host": host,
        "model": model,
        "added": [row["node_id"] for row in added],
        "started": current["node_id"] if current else None,
        "delegated": [row["node_id"] for row in store.delegated()],
        "cost": _cost(host, proc.stdout),
    }


def _answer_text(host: str, stdout: str) -> str:
    """The reviewer's final text from each host's output format."""
    if host == "command":
        return stdout
    if host == "claude":
        try:
            return str(json.loads(stdout).get("result", ""))
        except ValueError:
            return stdout
    texts = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item") or {}
        if item.get("type") == "agent_message":
            texts.append(str(item.get("text", "")))
        part = event.get("part") or {}
        if part.get("type") == "text":
            texts.append(str(part.get("text", "")))
    return "\n".join(texts)


def parse_verdict(text: str) -> tuple[str, str]:
    """('holds' | 'changes', reason) from an answer whose first word is HOLDS or CHANGES."""
    lines = [line.strip().strip("*#`> ") for line in text.strip().splitlines() if line.strip()]
    for index, line in enumerate(lines):
        word = line.split()[0].upper().strip(".:") if line.split() else ""
        if word in ("HOLDS", "CHANGES"):
            rest = line[len(line.split()[0]):].strip(" .:-")
            reason = rest or " ".join(lines[index + 1:index + 3])
            return ("holds" if word == "HOLDS" else "changes"), reason
    raise AgileError("the reviewer's answer did not start with HOLDS or CHANGES")


def review(store: Store, cwd: Path, host: str = "", model: str = "", timeout: int | None = None) -> tuple[str, str, str]:
    """Ask a fresh reviewer whether the remaining plan still holds. Returns (verdict, reason, reviewer)."""
    config = settings(store)
    host = resolve_host(host or config["host"])
    model = model or config["model"] or DEFAULT_MODELS.get(host, "")
    timeout = timeout or min(config["timeout"], 300)
    argv, stdin = command_for(host, store.review_text(), model, cwd, config["command"], store.state_dir)
    env = isolation.agent_env({ROLE_VARIABLE: "reviewer", "AGENTIC_AGILE_STATE": str(store.state_dir),
                               "PATH": f"{BIN_DIR}{os.pathsep}{os.environ.get('PATH', '')}"})
    try:
        proc = run_headless(store, "reviewer", host, argv, stdin, env, cwd, timeout, None)
    except FileNotFoundError:
        raise AgileError(f"{argv[0]} is not installed on this machine")
    if proc.returncode != 0:
        raise AgileError(f"the {host} reviewer stopped with exit {proc.returncode}")
    verdict, reason = parse_verdict(_answer_text(host, proc.stdout))
    return verdict, reason, f"{host}{'/' + model if model else ''}"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_planner(store: Store) -> int | None:
    """The pid of a background planner that is still writing the plan, if any."""
    pid = store._runtime("planner_pid")
    if not pid:
        return None
    if _alive(int(pid)):
        return int(pid)
    save_settings(store, pid="")
    return None


def start_background(store: Store, cwd: Path, argv: list[str]) -> tuple[int, Path]:
    """Start `replan` as a detached child that outlives the caller's time limit."""
    if running_planner(store):
        raise AgileError("a fresh planner is already writing the plan; wait for it with: agentic-agile replan --wait")
    if store.status()["status"] != "awaiting_hypothesis":
        raise AgileError("the plan still has open tasks; clear them first with: agentic-agile discard --why TEXT")
    log = store.local_path("planner.log")
    with open(log, "w") as handle:
        child = subprocess.Popen(argv, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
    save_settings(store, pid=child.pid, log=str(log))
    return child.pid, log


def wait(store: Store, timeout: int) -> tuple[bool, str]:
    """Wait for a background planner. Returns (still running, its log)."""
    import time
    deadline = time.monotonic() + timeout
    while running_planner(store) and time.monotonic() < deadline and not store.reports(open_only=True):
        time.sleep(1)
    log = store._runtime("planner_log") or ""
    text = Path(log).read_text() if log and Path(log).exists() else ""
    return bool(running_planner(store)), text
