"""Decide whether an agent in a run is stuck or slow, and keep the evidence.

A Monitor is fed each stream line of one agent with its time, and `check(now)`
yields findings. It stops nothing. The caller passes each finding to
`Store.open_report`, which is idempotent, so `check` may run on every poll.

Time is a float of seconds on any one clock (the engine uses time.monotonic).
Only the opencode detector reads the process table, through `ps`, which tests
replace with a fixture.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field

STALL_SECONDS = 120
# codex streams nothing while the model reasons or writes; 307 s was measured (inv-codex-silence)
CODEX_STALL_SECONDS = 600
SLACK = 1.0  # seconds of error in a start time computed from ps etime
PS_COMMAND = ["ps", "-A", "-o", "pid=,ppid=,stat=,etime=,args="]
IGNORED = re.compile(r"node_repl|cua_node|cua-repl|cmux|/codex-[0-9.]+/bin/|ChatGPT\.app")
CODEX_QUIET_TYPES = {"agent_message", "reasoning", "error"}
CLAUDE_QUIET_TOOLS = {"Agent", "Task"}

CAVEATS = {
    "claude": "claude streams every event, so silence with no command means the model or the network did not answer",
    "codex": "codex streams nothing while the model reasons or writes, so silence can be a long generation (307 s was measured)",
    "opencode": "opencode is silent between a step and its text, so silence can be a long generation",
}


def one_line(text: str) -> str:
    """A command or label on one line, so the evidence of a report stays one line in every display."""
    return " ".join(str(text).split())


@dataclass
class Proc:
    pid: int
    ppid: int
    stat: str
    start: float  # on the clock of the sample
    args: str


@dataclass
class Finding:
    kind: str  # stalled or slow
    evidence: str
    node_id: str = ""
    role: str = ""
    host: str = ""


def parse_etime(text: str) -> int | None:
    """Seconds from ps etime, [[dd-]hh:]mm:ss."""
    match = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", text.strip())
    if not match:
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def parse_ps(text: str, now: float) -> list[Proc]:
    """The rows of `ps -A -o pid=,ppid=,stat=,etime=,args=` sampled at `now`."""
    rows = []
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        elapsed = parse_etime(parts[3])
        if elapsed is None:
            continue
        rows.append(Proc(int(parts[0]), int(parts[1]), parts[2], now - elapsed, parts[4] if len(parts) > 4 else ""))
    return rows


def sample_ps(now: float) -> list[Proc]:
    try:
        out = subprocess.run(PS_COMMAND, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_ps(out, now)


def descendants(rows: list[Proc], pid: int) -> list[Proc]:
    """Every process below `pid`, oldest pid first."""
    below, found = {pid}, []
    for row in sorted(rows, key=lambda r: r.pid):
        if row.ppid in below:
            below.add(row.pid)
            found.append(row)
    return found


def live_commands(rows: list[Proc], pid: int) -> list[Proc]:
    """The descendants of `pid` that are running a command: not defunct, not ignored helpers."""
    return [r for r in descendants(rows, pid)
            if "Z" not in r.stat and "<defunct>" not in r.args and not r.args.startswith("(")
            and not IGNORED.search(r.args)]


def stall_limit(host: str) -> int:
    override = os.environ.get("AGENTIC_AGILE_STALL")
    if override and override.isdigit() and int(override) > 0:
        return int(override)
    return CODEX_STALL_SECONDS if host == "codex" else STALL_SECONDS


def extension_seconds(store, node_id: str) -> int:
    """Extra time the top-level agent gave this task by extending slow reports."""
    return sum(int(r["extension"]) for r in store.reports()
               if r["node_id"] == node_id and r["kind"] == "slow" and r["status"] == "extended")


@dataclass
class Monitor:
    host: str
    pid: int
    role: str = ""
    node_id: str = ""
    expected: int = 0  # Store.expected_seconds; 0 means no slow report
    extension: int = 0
    start: float = 0.0
    ps: object = None  # callable(now) -> list[Proc]; defaults to the real process table
    stall: int = 0
    last_line: float = 0.0
    events: int = 0
    last_type: str = ""
    step_start: float | None = None
    open_items: dict = field(default_factory=dict)  # id -> (label, start time)
    subagents: dict = field(default_factory=dict)  # Agent call id -> (description, start time)
    inside: dict = field(default_factory=dict)  # open item id -> Agent call id it runs under
    _children: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.last_line = self.start
        self.stall = self.stall or stall_limit(self.host)
        self.ps = self.ps or sample_ps

    # -- feeding ------------------------------------------------------------

    def feed(self, line: str, now: float) -> None:
        """Take one stream line of the agent, received at `now`."""
        before, self.last_line = self.last_line, now
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        kind = str(event.get("type", ""))
        if kind == "tool_progress" and event.get("heartbeat"):
            # claude sends one every 30 s for an open Agent call, whether or not the subagent works
            self.last_line = before
            return
        if kind == "stream_event":  # a partial message: alive, no state change
            return
        self.events += 1
        self.last_type = kind
        if self.host == "codex":
            self._codex(event, kind, now)
        elif self.host == "claude":
            self._claude(event, kind, now)
        elif self.host == "opencode" and kind == "step_start":
            self.step_start = now

    def _codex(self, event: dict, kind: str, now: float) -> None:
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        if kind == "item.started" and item.get("type") not in CODEX_QUIET_TYPES:
            label = item.get("command") or item.get("type") or "item"
            self.open_items[item.get("id", len(self.open_items))] = (str(label), now)
        elif kind == "item.completed":
            self.open_items.pop(item.get("id"), None)
        elif kind == "turn.completed":
            self.open_items.clear()

    def _claude(self, event: dict, kind: str, now: float) -> None:
        if kind == "result":
            self.open_items.clear()
            self.subagents.clear()
            self.inside.clear()
            return
        if kind == "system":
            sub, task = event.get("subtype"), event.get("task_id")
            if sub == "task_started" and event.get("task_type") == "local_bash":
                self.open_items[f"task:{task}"] = (str(event.get("description") or "background command"), now)
            elif sub == "task_notification":
                self.open_items.pop(f"task:{task}", None)
                self.subagents.pop(event.get("tool_use_id"), None)
            return
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            args = block.get("input") if isinstance(block.get("input"), dict) else {}
            if kind == "assistant" and block.get("type") == "tool_use" and block.get("name") in CLAUDE_QUIET_TOOLS:
                self.subagents[block.get("id")] = (str(args.get("description") or args.get("subagent_type") or "subagent"), now)
            elif kind == "assistant" and block.get("type") == "tool_use":
                label = args.get("command") or args.get("file_path") or args.get("pattern") or block.get("name")
                self.open_items[block.get("id")] = (str(label), now)
                if event.get("parent_tool_use_id"):
                    self.inside[block.get("id")] = event["parent_tool_use_id"]
            elif kind == "user" and block.get("type") == "tool_result":
                self.open_items.pop(block.get("tool_use_id"), None)
                self.inside.pop(block.get("tool_use_id"), None)

    # -- checking -----------------------------------------------------------

    def running(self, now: float) -> list[tuple[str, float]]:
        """The commands the agent runs now, as (command, start time), oldest first."""
        if self.host == "opencode":
            if self.step_start is None:
                return []
            cutoff = self.step_start - SLACK
            return sorted(((r.args, r.start) for r in live_commands(self.ps(now), self.pid) if r.start >= cutoff),
                          key=lambda c: c[1])
        return sorted(self.open_items.values(), key=lambda c: c[1])

    def check(self, now: float) -> list[Finding]:
        busy = self.running(now)
        found = []
        silence = now - self.last_line
        if not busy and silence > self.stall:
            found.append(self._finding("stalled", now, silence, busy,
                                       f"sent no output for {int(silence)} s and ran no command"))
        elapsed = now - self.start
        limit = self.expected + self.extension
        if self.expected and elapsed > limit:
            found.append(self._finding("slow", now, silence, busy,
                                       f"ran {int(elapsed)} s, past the expected {limit} s"))
        return found

    def _finding(self, kind: str, now: float, silence: float, busy: list, headline: str) -> Finding:
        who = f"{self.role or 'agent'}{' ' + self.node_id if self.node_id else ''} on {self.host}"
        parts = [f"{who} {headline}",
                 f"silence {int(silence)} s"]
        if busy:
            command, began = busy[0]
            parts.append(f"open command {one_line(command)[:200]} for {int(now - began)} s")
            under = next((self.subagents[self.inside[k]][0] for k, v in self.open_items.items()
                          if v == busy[0] and self.inside.get(k) in self.subagents), "")
            if under:
                parts.append(f"inside subagent {one_line(under)[:80]} of this {self.role or 'agent'} run")
            if len(busy) > 1:
                parts.append(f"{len(busy)} commands open")
        else:
            parts.append("no command open")
        for description, began in sorted(self.subagents.values(), key=lambda c: c[1])[:1]:
            parts.append(f"subagent {one_line(description)[:80]} of this {self.role or 'agent'} run open for {int(now - began)} s")
        parts.append(f"{self.events} events, last {self.last_type or 'none'}")
        if not busy:
            # The caveat explains silence; an open command already explains it.
            parts.append(CAVEATS.get(self.host, ""))
        return Finding(kind, "; ".join(p for p in parts if p), self.node_id, self.role, self.host)
