from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import shlex
import subprocess
import tempfile
import uuid

from . import render


SCHEMA_VERSION = 1
# Bump when the SQLite projection changes shape; a mismatch triggers a replay.
PROJECTION_VERSION = 9
REPORT_KINDS = ("stalled", "slow")
REPORT_ACTIONS = ("stopped", "extended", "dismissed")  # what the top-level agent decides
REPORT_ENDED = "ended"  # the engine closes the open reports of an agent whose process ended by itself
REPORT_DEFAULT_SECONDS = 600
# Notes that count as durable knowledge and are handed to every new hypothesis.
KNOWLEDGE_KINDS = ("finding", "evidence", "decision", "surprise", "review")
# Who wrote a note. Agents that the engine starts read only the engine's notes, which state facts
# about the project such as a merge. Notes of the session, a worker or a reviewer stay in the record
# for the user and the session, so a fresh planner forms its plan from the findings alone.
NOTE_SOURCES = ("engine", "session", "worker", "reviewer", "user")
ENGINE_NOTE_PREFIXES = ("NOT MERGED:", "MERGED:")  # engine notes from before notes had a source
EXECUTABLE_KINDS = {"task", "investigation", "human", "validation"}
NODE_KINDS = EXECUTABLE_KINDS | {"group"}


SHELL_OPERATORS = ("&&", "||", ";", "|", ">", "<", "$", "`", "&")


def goal_check_text(argv: list[str]) -> str:
    """The check as the user wrote it: the shell string for sh -c, else the joined argv."""
    if len(argv) == 3 and argv[:2] == ["sh", "-c"]:
        return argv[2]
    return shlex.join(argv)


# Files in the state folder that belong to one machine. The events are the shared record.
STATE_IGNORE = ("state.sqlite3", "state.sqlite3-*", ".lock", "*.log", "workers/")


def ignore_local_files(state_dir: Path) -> None:
    """Add any missing STATE_IGNORE line to the state folder's .gitignore."""
    ignore = state_dir / ".gitignore"
    have = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    missing = [line for line in STATE_IGNORE if line not in have]
    if missing:
        state_dir.mkdir(parents=True, exist_ok=True)
        ignore.write_text("".join(f"{line}\n" for line in have + missing), encoding="utf-8")


class AgileError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode:
        raise AgileError(proc.stderr.strip() or "git reported an error")
    return proc.stdout


STATE_DIR = ".agentic-agile"


def _state_in(directory: Path) -> Path | None:
    """The state directory under ``directory``, if it exists."""
    path = directory / STATE_DIR
    return path if path.exists() else None


def discover_state(cwd: Path, explicit: str | None = None, create: bool = False) -> Path:
    """Locate the state directory for ``cwd``.

    Order: explicit path, ``AGENTIC_AGILE_STATE``, a checked-out ``meta``
    worktree, then the repository root (or ``cwd`` outside Git). With
    ``create`` the last location is returned for a new project too.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("AGENTIC_AGILE_STATE"):
        return Path(os.environ["AGENTIC_AGILE_STATE"]).expanduser().resolve()

    cwd = cwd.resolve()
    try:
        root = Path(_git(["rev-parse", "--show-toplevel"], cwd).strip())
        lines = _git(["worktree", "list", "--porcelain"], root).splitlines()
    except AgileError:
        found = _state_in(cwd)
        if found or create:
            return found or cwd / STATE_DIR
        raise AgileError(
            f"set a goal for {cwd} first: run 'agentic-agile init', or use --state PATH"
        )

    worktree: Path | None = None
    for line in lines:
        if line.startswith("worktree "):
            worktree = Path(line.removeprefix("worktree "))
        elif line == "branch refs/heads/meta" and worktree is not None:
            return (_state_in(worktree) or worktree / STATE_DIR).resolve()

    found = _state_in(root)
    if found or create:
        return (found or root / STATE_DIR).resolve()
    raise AgileError(
        f"set a goal for {root} first: run 'agentic-agile init', or use --state PATH"
    )


BIN = Path(__file__).resolve().parents[2] / "bin" / "agentic-agile"


def cli_command() -> str:
    """How an agent runs this tool: the name when it reaches this copy, else the full path."""
    found = shutil.which("agentic-agile")
    if found and (not BIN.exists() or Path(found).resolve() == BIN.resolve()):
        return "agentic-agile"
    return str(BIN) if BIN.exists() else "agentic-agile"


def runnable(text: str) -> str:
    """Rewrite the commands in the instructions so they run where the agent is.

    Only the instructions are rewritten; the goal and the findings above them
    are the user's and the agent's own words and stay exactly as written.
    """
    command = cli_command()
    if command == "agentic-agile" or "## Instructions" not in text:
        return text
    head, tail = text.split("## Instructions", 1)
    return head + "## Instructions" + re.sub(r"(?<![\w./-])agentic-agile(?= )", command, tail)


def _loop_value(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute("SELECT value FROM loop WHERE key=?", (key,)).fetchone()
    return int(row[0]) if row else default


def _set_loop_value(conn: sqlite3.Connection, key: str, value: int) -> None:
    conn.execute("INSERT OR REPLACE INTO loop VALUES (?,?)", (key, value))


class Store:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.events_dir = state_dir / "events"
        self.db_path = state_dir / "state.sqlite3"
        self.lock_path = state_dir / ".lock"

    @property
    def exists(self) -> bool:
        return self.events_dir.is_dir() and any(self.events_dir.glob("*.json"))

    def local_path(self, name: str) -> Path:
        """A path in the state folder for this machine only, such as a log or the worker worktrees.

        The state folder's .gitignore lists these paths, so git keeps the events and nothing else.
        """
        ignore_local_files(self.state_dir)
        return self.state_dir / name

    def initialize(self, outcome: str, done: str, constraints: str = "") -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        ignore_local_files(self.state_dir)
        self._ensure_db()
        if self.root() is not None:
            raise AgileError("a goal is already set")
        self.append(
            "aim.created",
            {
                "aim_id": "root",
                "outcome": outcome.strip(),
                "definition_of_done": done.strip(),
                "constraints": constraints.strip(),
                "version": 1,
            },
        )

    @contextlib.contextmanager
    def _locked(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def connect(self) -> sqlite3.Connection:
        self._ensure_db()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _replace_projection(self) -> None:
        """Build the index again from the events in a new file, then swap it in.

        Another process can hold the old file open, for example a run that
        started before an upgrade. The swap leaves that file whole for it, and
        its next connect reads the new one.
        """
        runtime: list[tuple[str, str]] = []
        if self.db_path.exists():
            with contextlib.suppress(sqlite3.Error):
                conn = sqlite3.connect(self.db_path)
                try:
                    runtime = [tuple(r) for r in conn.execute("SELECT key, value FROM runtime")]
                finally:
                    conn.close()
        # The name matches the state.sqlite3-* line of the state folder's .gitignore.
        fd, temp = tempfile.mkstemp(prefix=f"{self.db_path.name}-rebuild-", dir=self.state_dir)
        os.close(fd)
        try:
            conn = sqlite3.connect(temp)
            try:
                self._fill(conn, runtime)
            finally:
                conn.close()
            os.replace(temp, self.db_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise

    def _ensure_db(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            conn = sqlite3.connect(self.db_path)
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                has_tables = conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            finally:
                conn.close()
            if has_tables and version > PROJECTION_VERSION:
                raise AgileError(f"a newer Agentic Agile built the index of this state (index version {version}, "
                                 f"this copy reads version {PROJECTION_VERSION}). Use the newer copy: "
                                 f"stop this command or run, and start it again from the updated checkout.")
            if has_tables and version < PROJECTION_VERSION:
                self._replace_projection()  # older layout: replay the events into a new file
                return
        conn = sqlite3.connect(self.db_path)
        try:
            self._fill(conn)
        finally:
            conn.close()

    def _fill(self, conn: sqlite3.Connection, runtime: list[tuple[str, str]] | None = None) -> None:
        """Create the tables, apply each event the index lacks, and commit."""
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA user_version = {PROJECTION_VERSION}")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS event_log (
              event_id TEXT PRIMARY KEY,
              sequence INTEGER NOT NULL UNIQUE,
              event_type TEXT NOT NULL,
              recorded_at TEXT NOT NULL,
              payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS root_aim (
              singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
              aim_id TEXT NOT NULL UNIQUE,
              version INTEGER NOT NULL CHECK (version > 0),
              outcome TEXT NOT NULL CHECK (length(trim(outcome)) > 0),
              definition_of_done TEXT NOT NULL CHECK (length(trim(definition_of_done)) > 0),
              constraints_text TEXT NOT NULL,
              status TEXT NOT NULL CHECK (status IN ('active', 'complete')),
              completion_event TEXT
            );
            CREATE TABLE IF NOT EXISTS nodes (
              node_id TEXT PRIMARY KEY,
              parent_id TEXT REFERENCES nodes(node_id),
              kind TEXT NOT NULL CHECK (kind IN ('group','task','investigation','human','validation')),
              title TEXT NOT NULL CHECK (length(trim(title)) > 0),
              objective TEXT NOT NULL CHECK (length(trim(objective)) > 0),
              done_when TEXT NOT NULL CHECK (length(trim(done_when)) > 0),
              priority INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL CHECK (status IN ('pending','in_progress','delegated','complete')),
              created_event TEXT NOT NULL,
              started_event TEXT,
              completed_event TEXT,
              why TEXT NOT NULL DEFAULT '',
              picked_why TEXT NOT NULL DEFAULT '',
              learned TEXT NOT NULL DEFAULT '',
              delegated_to TEXT NOT NULL DEFAULT '',
              round INTEGER,
              expect TEXT NOT NULL DEFAULT '',
              alternatives TEXT NOT NULL DEFAULT '[]',
              understanding TEXT NOT NULL DEFAULT '',
              because TEXT NOT NULL DEFAULT '',
              rank INTEGER,
              rank_why TEXT NOT NULL DEFAULT '',
              minutes REAL
            );
            CREATE TABLE IF NOT EXISTS dependencies (
              node_id TEXT NOT NULL REFERENCES nodes(node_id),
              requires_id TEXT NOT NULL REFERENCES nodes(node_id),
              created_event TEXT NOT NULL,
              why TEXT NOT NULL DEFAULT '',
              PRIMARY KEY (node_id, requires_id),
              CHECK (node_id <> requires_id)
            );
            CREATE TABLE IF NOT EXISTS gates (
              gate_id TEXT PRIMARY KEY,
              node_id TEXT NOT NULL REFERENCES nodes(node_id),
              label TEXT NOT NULL,
              argv_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK (status IN ('pending','passed','failed')),
              result_event TEXT,
              exit_code INTEGER,
              output_digest TEXT
            );
            CREATE TABLE IF NOT EXISTS loop (
              key TEXT PRIMARY KEY,
              value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
              report_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL CHECK (kind IN ('stalled','slow')),
              node_id TEXT NOT NULL DEFAULT '',
              role TEXT NOT NULL DEFAULT '',
              host TEXT NOT NULL DEFAULT '',
              evidence TEXT NOT NULL DEFAULT '',
              opened TEXT NOT NULL,
              status TEXT NOT NULL CHECK (status IN ('open','stopped','extended','dismissed','ended')),
              extension INTEGER NOT NULL DEFAULT 0,
              resolved TEXT
            );
            CREATE TABLE IF NOT EXISTS notes (
              note_id TEXT PRIMARY KEY,
              node_id TEXT REFERENCES nodes(node_id),
              kind TEXT NOT NULL,
              body TEXT NOT NULL,
              created_event TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT 'session'
            );
            """
        )
        known = {r[0] for r in conn.execute("SELECT event_id FROM event_log")}
        for path in sorted(self.events_dir.glob("*.json")):
            event = json.loads(path.read_text(encoding="utf-8"))
            if event["event_id"] not in known:
                self._apply(conn, event)
        if runtime:
            conn.executemany("INSERT OR REPLACE INTO runtime VALUES (?,?)", runtime)
        conn.commit()

    def rebuild(self) -> None:
        self._replace_projection()

    def append(self, event_type: str, payload: dict) -> dict:
        with self._locked():
            self._ensure_db()
            paths = sorted(self.events_dir.glob("*.json"))
            sequence = int(paths[-1].name.split("_", 1)[0]) + 1 if paths else 1
            event = {
                "schema": SCHEMA_VERSION,
                "event_id": str(uuid.uuid4()),
                "sequence": sequence,
                "type": event_type,
                "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "payload": payload,
            }
            conn = self.connect()
            try:
                self._apply(conn, event)
                body = json.dumps(event, indent=2, sort_keys=True) + "\n"
                final = self.events_dir / f"{sequence:08d}_{event['event_id']}.json"
                fd, temp_name = tempfile.mkstemp(prefix="event-", dir=self.events_dir)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(body)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp_name, final)
                    conn.commit()
                except Exception:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(temp_name)
                    conn.rollback()
                    raise
            finally:
                conn.close()
            self._commit_event(final, event)
            return event

    def _commit_event(self, event_path: Path, event: dict) -> None:
        """Commit canonical events when state lives in a checked-out meta branch.

        On any other branch (or outside Git) events are left in the working
        tree for the user to version however they like.
        """
        try:
            worktree = Path(_git(["rev-parse", "--show-toplevel"], self.state_dir).strip())
            branch = _git(["branch", "--show-current"], worktree).strip()
        except AgileError:
            return
        if branch != "meta":
            return
        paths = [str(event_path.relative_to(worktree))]
        ignore = self.state_dir / ".gitignore"
        if ignore.exists():
            paths.append(str(ignore.relative_to(worktree)))
        _git(["add", "--", *paths], worktree)
        staged = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--cached", "--quiet", "--", *paths]
        )
        if staged.returncode == 0:
            return
        if staged.returncode != 1:
            raise AgileError("git reported an error on the staged event files")
        message = f"agentic-agile: {event['type']} #{event['sequence']}"
        _git(["commit", "-m", message, "--", *paths], worktree)

    def _apply(self, conn: sqlite3.Connection, event: dict) -> None:
        if event.get("schema") != SCHEMA_VERSION:
            raise AgileError(f"unsupported event schema: {event.get('schema')}")
        p = event["payload"]
        eid = event["event_id"]
        kind = event["type"]
        try:
            if kind == "aim.created":
                conn.execute(
                    "INSERT INTO root_aim VALUES (1,?,?,?,?,?,?,NULL)",
                    (p["aim_id"], p["version"], p["outcome"], p["definition_of_done"], p["constraints"], "active"),
                )
            elif kind == "node.added":
                conn.execute(
                    """INSERT INTO nodes (node_id, parent_id, kind, title, objective, done_when,
                                          priority, status, created_event, why, rank, rank_why, expect, minutes)
                       VALUES (?,?,?,?,?,?,?,'pending',?,?,?,?,?,?)""",
                    (p["node_id"], p.get("parent_id"), p["kind"], p["title"], p["objective"],
                     p["done_when"], p["priority"], eid, p.get("why", ""), p.get("rank"),
                     p.get("rank_why", ""), p.get("expect", ""), p.get("minutes")),
                )
            elif kind == "report.opened":
                conn.execute(
                    "INSERT INTO reports VALUES (?,?,?,?,?,?,?,'open',0,NULL)",
                    (p["report_id"], p["kind"], p.get("node_id", ""), p.get("role", ""), p.get("host", ""),
                     p.get("evidence", ""), event["recorded_at"]),
                )
            elif kind == "report.resolved":
                changed = conn.execute(
                    "UPDATE reports SET status=?, extension=?, resolved=? WHERE report_id=? AND status='open'",
                    (p["action"], int(p.get("extension", 0)), event["recorded_at"], p["report_id"]),
                ).rowcount
                if changed != 1:
                    raise AgileError(f"report {p['report_id']} is not open")
            elif kind == "dependency.added":
                conn.execute(
                    "INSERT INTO dependencies (node_id, requires_id, created_event, why) VALUES (?,?,?,?)",
                    (p["node_id"], p["requires_id"], eid, p.get("why", "")),
                )
            elif kind == "node.started":
                changed = conn.execute(
                    """UPDATE nodes SET status='in_progress', started_event=?, picked_why=?, round=?,
                              expect=CASE WHEN ?<>'' THEN ? ELSE expect END, alternatives=?
                       WHERE node_id=? AND status='pending'""",
                    (eid, p.get("why", ""), _loop_value(conn, "round", 1), p.get("expect", ""),
                     p.get("expect", ""), json.dumps(p.get("alternatives", [])), p["node_id"]),
                ).rowcount
                if changed != 1:
                    raise AgileError(f"task {p['node_id']} is already in progress or complete")
            elif kind == "node.delegated":
                changed = conn.execute(
                    """UPDATE nodes SET status='delegated', delegated_to=?,
                              started_event=COALESCE(started_event, ?),
                              picked_why=CASE WHEN ?<>'' THEN ? ELSE picked_why END,
                              expect=CASE WHEN ?<>'' THEN ? ELSE expect END,
                              alternatives=CASE WHEN status='pending' THEN ? ELSE alternatives END,
                              round=COALESCE(round, ?)
                       WHERE node_id=? AND status IN ('pending','in_progress')""",
                    (p["to"], eid, p.get("why", ""), p.get("why", ""), p.get("expect", ""), p.get("expect", ""),
                     json.dumps(p.get("alternatives", [])), _loop_value(conn, "round", 1), p["node_id"]),
                ).rowcount
                if changed != 1:
                    raise AgileError(f"task {p['node_id']} is already delegated or complete")
            elif kind == "settings.changed":
                if "max_parallel" in p:
                    _set_loop_value(conn, "max_parallel", int(p["max_parallel"]))
                # "goal_check" is read straight from the events (see goal_check()).
            elif kind == "gate.added":
                conn.execute(
                    "INSERT INTO gates VALUES (?,?,?,?,'pending',NULL,NULL,NULL)",
                    (p["gate_id"], p["node_id"], p["label"], json.dumps(p["argv"])),
                )
            elif kind == "gate.result":
                conn.execute(
                    "UPDATE gates SET status=?, result_event=?, exit_code=?, output_digest=? WHERE gate_id=?",
                    (p["status"], eid, p["exit_code"], p["output_digest"], p["gate_id"]),
                )
            elif kind == "node.completed":
                changed = conn.execute(
                    """UPDATE nodes SET status='complete', completed_event=?, learned=?,
                              understanding=?, because=?
                       WHERE node_id=? AND status <> 'complete'""",
                    (eid, p.get("learned", ""), p.get("understanding", ""), p.get("because", ""), p["node_id"]),
                ).rowcount
                if changed != 1:
                    raise AgileError(f"task {p['node_id']} is already complete")
                active = conn.execute(
                    "SELECT 1 FROM nodes WHERE status IN ('in_progress','delegated') LIMIT 1"
                ).fetchone()
                if not p.get("derived") and active is None:
                    # The last running task of the round is done: the next round starts.
                    _set_loop_value(conn, "round", _loop_value(conn, "round", 1) + 1)
            elif kind == "note.recorded":
                source = p.get("source") or ("engine" if p["body"].startswith(ENGINE_NOTE_PREFIXES) else "session")
                conn.execute(
                    "INSERT INTO notes VALUES (?,?,?,?,?,?)",
                    (p["note_id"], p.get("node_id"), p["kind"], p["body"], eid, source),
                )
            elif kind == "aim.constrained":
                # A constraint the user added to the goal while it runs. Every agent reads it.
                conn.execute("INSERT INTO notes VALUES (?,?,?,?,?,?)",
                             (eid, None, "constraint", p["text"], eid, "user"))
            elif kind == "hypothesis.discarded":
                # The remaining plan was a hypothesis; it leaves the projection.
                # The events keep it for audit, completed work stays.
                ids = list(p["node_ids"])
                marks = ",".join("?" * len(ids))
                if ids:
                    conn.execute(f"UPDATE nodes SET parent_id=NULL WHERE parent_id IN ({marks})", ids)
                    conn.execute(f"UPDATE notes SET node_id=NULL WHERE node_id IN ({marks})", ids)
                    conn.execute(f"DELETE FROM gates WHERE node_id IN ({marks})", ids)
                    conn.execute(
                        f"DELETE FROM dependencies WHERE node_id IN ({marks}) OR requires_id IN ({marks})",
                        ids + ids,
                    )
                    conn.execute(f"DELETE FROM nodes WHERE node_id IN ({marks}) AND status='pending'", ids)
            elif kind == "plan.reviewed":
                # An independent reviewer's verdict on whether the plan still holds.
                checked = "checked" if p.get("checked") else "unchecked"
                conn.execute(
                    "INSERT INTO notes VALUES (?,?,?,?,?,?)",
                    (eid, None, "review", f"{p['verdict']} ({checked}, {p.get('reviewer', '')}): {p.get('reason', '')}", eid,
                     "reviewer"),
                )
            elif kind == "aim.completed":
                conn.execute(
                    "UPDATE root_aim SET status='complete', completion_event=? WHERE singleton=1",
                    (eid,),
                )
            else:
                raise AgileError(f"unknown event type: {kind}")
            conn.execute(
                "INSERT INTO event_log VALUES (?,?,?,?,?)",
                (eid, event["sequence"], kind, event["recorded_at"], json.dumps(p, sort_keys=True)),
            )
        except sqlite3.IntegrityError as exc:
            raise AgileError(str(exc)) from exc

    def root(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM root_aim WHERE singleton=1").fetchone()

    def _assert_root_active(self) -> None:
        root = self.root()
        if root is None:
            raise AgileError("set a goal first: agentic-agile init --goal ... --done ...")
        if root["status"] != "active":
            raise AgileError("the goal is already met")

    def node(self, node_id: str) -> sqlite3.Row:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM nodes WHERE node_id=?", (node_id,)).fetchone()
        if row is None:
            raise AgileError(f"{node_id}: use the ID of a task in the plan (see 'agentic-agile tree')")
        return row

    def add_node(self, node_id: str, title: str, objective: str, done_when: str,
                 kind: str = "task", parent_id: str | None = None, priority: int = 0,
                 why: str = "", rank: int | None = None, rank_why: str = "", expect: str = "",
                 minutes: float | None = None) -> None:
        if kind not in NODE_KINDS:
            raise AgileError(f"{kind}: use one of the kinds group, task, investigation, human, validation")
        if minutes is not None and minutes <= 0:
            raise AgileError("the expected time is a number of minutes above 0")
        self._assert_root_active()
        if parent_id:
            parent = self.node(parent_id)
            if parent["status"] == "complete":
                raise AgileError("add the task below a task that is still open")
        self.append("node.added", {
            "node_id": node_id,
            "parent_id": parent_id,
            "kind": kind,
            "title": title.strip(),
            "objective": objective.strip(),
            "done_when": done_when.strip(),
            "priority": priority,
            "why": why.strip(),
            "rank": rank,
            "rank_why": rank_why.strip(),
            "expect": expect.strip(),
            **({"minutes": minutes} if minutes is not None else {}),
        })

    # -- reports: an agent that is stuck or slow, kept with its evidence ------

    def expected_seconds(self, node_id: str) -> int:
        """How long a task is expected to take: its own minutes, else the runtime key, else the planner timeout."""
        with self.connect() as conn:
            row = conn.execute("SELECT minutes FROM nodes WHERE node_id=?", (node_id,)).fetchone()
        if row is not None and row["minutes"]:
            return int(row["minutes"] * 60)
        default = self._runtime("task_minutes")
        if default:
            return int(float(default) * 60)
        return int(self._runtime("planner_timeout") or REPORT_DEFAULT_SECONDS)

    def set_task_minutes(self, minutes: float) -> None:
        if minutes <= 0:
            raise AgileError("the expected time is a number of minutes above 0")
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO runtime VALUES ('task_minutes', ?)", (str(minutes),))

    def open_report(self, kind: str, evidence: str, node_id: str = "", role: str = "", host: str = "") -> str:
        """Record that an agent is stalled or slow. Returns the report ID.

        One report stays open per agent and kind, so a monitor can call this on every check.
        """
        if kind not in REPORT_KINDS:
            raise AgileError(f"{kind}: use one of the report kinds stalled, slow")
        for r in self.reports(open_only=True):
            if r["kind"] == kind and r["node_id"] == node_id and r["role"] == role:
                return r["report_id"]
        number = len(self.reports()) + 1
        report_id = f"r{number}"
        self.append("report.opened", {"report_id": report_id, "kind": kind, "node_id": node_id, "role": role,
                                      "host": host, "evidence": evidence.strip()})
        return report_id

    def reports(self, open_only: bool = False) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reports" + (" WHERE status='open'" if open_only else "") + " ORDER BY rowid"
            ).fetchall()
        return [dict(r) for r in rows]

    def close_reports(self, report_ids) -> None:
        """Mark the open reports among `report_ids` as ended: their agent finished by itself."""
        for r in self.reports(open_only=True):
            if r["report_id"] in report_ids:
                try:
                    self.resolve_report(r["report_id"], REPORT_ENDED)
                except AgileError:
                    pass  # the top-level agent decided it at the same moment

    def dismissed_kinds(self, node_id: str, role: str = "worker") -> set[str]:
        """The report kinds already dismissed for this task and role, so a restart or a retry stays quiet."""
        return {r["kind"] for r in self.reports()
                if r["status"] == "dismissed" and r["node_id"] == node_id and r["role"] == role}

    def resolve_report(self, report_id: str, action: str, extension: int = 0) -> dict:
        """Close a report as stopped, extended (with seconds of extra time) or dismissed.
        The engine uses `ended` when the agent of the report ended by itself."""
        if action not in (*REPORT_ACTIONS, REPORT_ENDED):
            raise AgileError(f"{action}: use one of stopped, extended, dismissed")
        match = [r for r in self.reports() if r["report_id"] == report_id]
        if not match:
            raise AgileError(f"{report_id}: use the ID of a report (see 'agentic-agile report list')")
        if match[0]["status"] != "open":
            raise AgileError(f"report {report_id} is already {match[0]['status']}")
        if action == "extended" and extension <= 0:
            raise AgileError("give the extra time as a number of seconds above 0")
        self.append("report.resolved", {"report_id": report_id, "action": action,
                                        "extension": extension if action == "extended" else 0})
        return next(r for r in self.reports() if r["report_id"] == report_id)

    def add_dependency(self, node_id: str, requires_id: str, why: str = "") -> None:
        self._assert_root_active()
        node = self.node(node_id)
        self.node(requires_id)
        if node["status"] != "pending":
            raise AgileError("change the order of a task while it is still waiting")
        if node_id == requires_id:
            raise AgileError("a task depends on other tasks; give two different IDs")
        with self.connect() as conn:
            cycle = conn.execute(
                """
                WITH RECURSIVE reach(id) AS (
                  SELECT requires_id FROM dependencies WHERE node_id=?
                  UNION
                  SELECT d.requires_id FROM dependencies d JOIN reach r ON d.node_id=r.id
                ) SELECT 1 FROM reach WHERE id=?
                """,
                (requires_id, node_id),
            ).fetchone()
        if cycle:
            raise AgileError("these two tasks already have the opposite order; keep one direction")
        self.append("dependency.added", {"node_id": node_id, "requires_id": requires_id, "why": why.strip()})

    def add_gate(self, node_id: str, label: str, argv: list[str]) -> str:
        self._assert_root_active()
        node = self.node(node_id)
        if node["status"] == "complete":
            raise AgileError("add the check to a task that is still open")
        if not argv:
            raise AgileError("a check needs a command: gate-add TASK --label TEXT -- COMMAND")
        gate_id = f"gate-{uuid.uuid4().hex[:12]}"
        self.append("gate.added", {"gate_id": gate_id, "node_id": node_id, "label": label, "argv": argv})
        return gate_id

    def frontier(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT n.* FROM nodes n
                WHERE n.status='pending'
                  AND n.kind <> 'group'
                  AND NOT EXISTS (
                    SELECT 1 FROM dependencies d JOIN nodes r ON r.node_id=d.requires_id
                    WHERE d.node_id=n.node_id AND r.status <> 'complete'
                  )
                ORDER BY n.rank IS NULL, n.rank ASC, n.priority DESC, n.node_id ASC
                """
            ).fetchall()

    def current(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM nodes WHERE status='in_progress' ORDER BY rowid LIMIT 1"
            ).fetchone()

    @property
    def max_parallel(self) -> int:
        with self.connect() as conn:
            return _loop_value(conn, "max_parallel", 1)

    def set_max_parallel(self, value: int) -> None:
        if value < 1:
            raise AgileError("the parallel limit is a whole number of 1 or more")
        self._assert_root_active()
        self.append("settings.changed", {"max_parallel": value})

    def active(self) -> list[sqlite3.Row]:
        """Tasks that are running: the one in progress here, and the delegated ones."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM nodes WHERE status IN ('in_progress','delegated') ORDER BY rowid"
            ).fetchall()

    def delegated(self) -> list[sqlite3.Row]:
        return [row for row in self.active() if row["status"] == "delegated"]

    def round_closing(self) -> bool:
        """True when a task of this round is complete and others are still running."""
        with self.connect() as conn:
            current_round = _loop_value(conn, "round", 1)
            finished = conn.execute(
                "SELECT 1 FROM nodes WHERE round=? AND status='complete' AND kind <> 'group' LIMIT 1",
                (current_round,),
            ).fetchone()
        return bool(finished and self.active())

    def _assert_can_start(self) -> None:
        running = self.active()
        names = ", ".join(r["node_id"] for r in running)
        if running and self.round_closing():
            raise AgileError(
                f"this round finishes when {names} complete; the next plan is written after that"
            )
        if len(running) >= self.max_parallel:
            raise AgileError(
                f"{len(running)} of {self.max_parallel} task slots are in use ({names}); "
                "complete one, or raise the limit with 'agentic-agile parallel N'"
            )

    def claim_next(self, node_id: str | None = None, why: str = "", expect: str = "") -> sqlite3.Row | None:
        """Start one task in this session: the named ready task, or the top-priority one."""
        root = self.root()
        if root is None or root["status"] != "active":
            return None
        current = self.current()
        if current is not None:
            if node_id and node_id != current["node_id"]:
                raise AgileError(
                    f"{current['node_id']} is in progress here; complete it, or hand it to a "
                    f"background agent with 'agentic-agile delegate {current['node_id']}'"
                )
            return current
        ready = self.frontier()
        if node_id:
            if node_id not in {r["node_id"] for r in ready}:
                self.node(node_id)
                raise AgileError(f"{node_id} becomes ready when the tasks that it depends on are complete")
            chosen = node_id
        elif ready:
            chosen = ready[0]["node_id"]
        else:
            return None
        self._assert_can_start()
        alternatives = [r["node_id"] for r in ready if r["node_id"] != chosen]
        self.append("node.started", {"node_id": chosen, "why": why.strip(), "expect": expect.strip(),
                                     "alternatives": alternatives})
        return self.node(chosen)

    def delegate(self, node_id: str, to: str, why: str = "", expect: str = "") -> sqlite3.Row:
        """Hand a ready task, or the task in progress here, to a background agent."""
        self._assert_root_active()
        node = self.node(node_id)
        if not to.strip():
            raise AgileError("name the agent that does the task: --to NAME")
        if node["status"] == "pending":
            if node_id not in {r["node_id"] for r in self.frontier()}:
                raise AgileError(f"{node_id} becomes ready when the tasks that it depends on are complete")
            self._assert_can_start()
        elif node["status"] != "in_progress":
            raise AgileError(f"{node_id} is {node['status'].replace('_', ' ')}; delegate a ready task or the task in progress")
        alternatives = [r["node_id"] for r in self.frontier() if r["node_id"] != node_id]
        self.append("node.delegated", {"node_id": node_id, "to": to.strip(), "why": why.strip(),
                                       "expect": expect.strip(), "alternatives": alternatives})
        return self.node(node_id)

    def path(self, node_id: str) -> list[sqlite3.Row]:
        rows: list[sqlite3.Row] = []
        seen: set[str] = set()
        row = self.node(node_id)
        while row is not None:
            if row["node_id"] in seen:
                raise AgileError("the parent tasks make a cycle")
            seen.add(row["node_id"])
            rows.append(row)
            parent = row["parent_id"]
            row = self.node(parent) if parent else None
        return list(reversed(rows))

    def knowledge(self, limit: int = 12, independent: bool = False) -> list[dict]:
        """What finished blocks taught, oldest first: the part that outlives every plan.

        `independent` is the view of an agent that the engine starts: each finding without the
        result that an earlier planner expected, and only the engine's notes.
        """
        # An engine agent also reads the engine's blockers, such as the output of a failed goal check.
        kinds = (*KNOWLEDGE_KINDS, "blocker") if independent else KNOWLEDGE_KINDS
        marks = ",".join("?" * len(kinds))
        expected = "" if independent else "|| CASE WHEN n.expect <> '' THEN ' (expected: ' || n.expect || ')' ELSE '' END"
        sources = "AND t.source = 'engine'" if independent else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT e.sequence, 'finding' AS kind, n.node_id AS node, n.title AS title,
                           n.learned
                           {expected}
                           || CASE WHEN n.understanding <> '' THEN ' [understanding ' || n.understanding
                                   || CASE WHEN n.because <> '' THEN ': ' || n.because ELSE '' END || ']'
                              ELSE '' END AS body
                      FROM nodes n JOIN event_log e ON e.event_id = n.completed_event
                     WHERE n.status='complete' AND n.kind <> 'group' AND n.learned <> ''
                    UNION ALL
                    SELECT e.sequence, t.kind, t.node_id, NULL, t.body
                      FROM notes t JOIN event_log e ON e.event_id = t.created_event
                     WHERE t.kind IN ({marks}) {sources}
                    ORDER BY 1 DESC LIMIT ?""",
                (*kinds, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    RECENT_FINDINGS = 6
    FINDING_CHARS = 500

    def constraints_added(self) -> list[str]:
        """The constraints that the user added to the goal after it was set, oldest first."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT t.body FROM notes t JOIN event_log e ON e.event_id = t.created_event
                   WHERE t.kind='constraint' ORDER BY e.sequence""").fetchall()
        return [r["body"] for r in rows]

    def add_constraint(self, text: str) -> None:
        """Add a constraint from the user to the goal. The outcome and the done condition stay as set."""
        self._assert_root_active()
        if not text.strip():
            raise AgileError("give the constraint in the user's words")
        self.append("aim.constrained", {"text": text.strip()})

    def _aim_lines(self, heading: str, findings: str = "recent", independent: bool = False) -> list[str]:
        """The goal and the findings. 'full' gives every finding in full, for fresh planners and
        reviewers; 'recent' gives the latest few, shortened, for a session that already has them.
        `independent` gives the view of an agent that the engine starts (see `knowledge`)."""
        root = self.root()
        if root is None:
            raise AgileError("set a goal first: agentic-agile init --goal ... --done ...")
        lines = [heading, "", "## Goal (set by the user; keep it exactly as it is)", root["outcome"], "",
                 f"The goal is met when: {root['definition_of_done']}"]
        if root["constraints_text"]:
            lines.append(f"Constraints: {root['constraints_text']}")
        for text in self.constraints_added():
            lines.append(f"Constraint added by the user: {text}")
        known = self.knowledge(limit=1000 if findings == "full" else self.RECENT_FINDINGS + 1, independent=independent)
        if known:
            lines += ["", "## Findings so far"]
            if findings != "full" and len(known) > self.RECENT_FINDINGS:
                known = known[-self.RECENT_FINDINGS:]
                lines.append(f"(The latest {self.RECENT_FINDINGS}; run agentic-agile view for all of them.)")
            for item in known:
                where = f" `{item['node']}`" if item["node"] else ""
                body = item["body"]
                if findings != "full" and len(body) > self.FINDING_CHARS:
                    body = body[: self.FINDING_CHARS].rsplit(" ", 1)[0] + " [...]"
                lines.append(f"- {item['kind']}{where}: {body}")
        return lines

    def context(self, node_id: str, for_agent: bool = False) -> str:
        """The instructions for one task: what to do, how it is judged, and why.

        `for_agent` is the brief of a worker that the engine starts. It holds the planner's reason
        for the task, and leaves out the other tasks of the plan and the session's notes, so the
        worker's finding describes the project and the goal."""
        node = self.node(node_id)
        lineage = self.path(node_id)
        with self.connect() as conn:
            deps = conn.execute(
                """SELECT n.node_id,n.title,n.status,d.why FROM dependencies d
                   JOIN nodes n ON n.node_id=d.requires_id WHERE d.node_id=? ORDER BY n.node_id""",
                (node_id,),
            ).fetchall()
            gates = conn.execute(
                "SELECT gate_id,label,status,argv_json FROM gates WHERE node_id=? ORDER BY gate_id",
                (node_id,),
            ).fetchall()
        lines = self._aim_lines("# AGENTIC AGILE: DO THIS TASK", independent=for_agent)
        if len(lineage) > 1:
            lines += ["", "## Where this task fits"]
            for index, part in enumerate(lineage[:-1], 1):
                lines.append(f"{index}. {part['title']} (`{part['node_id']}`): {part['objective']}")
                if part["why"]:
                    lines.append(f"   Why: {part['why']}")
        lines += [
            "",
            "## Task",
            f"ID: `{node_id}` ({node['kind']})",
            f"What to do: {node['objective']}",
            f"Done when: {node['done_when']}",
        ]
        if node["why"]:
            lines.append(f"Why it matters: {node['why']}")
        if node["picked_why"] and not for_agent:
            lines.append(f"Why now: {node['picked_why']}")
        elif node["rank_why"]:
            lines.append(f"Why now (rank {node['rank']}): {node['rank_why']}")
        if node["expect"]:
            lines.append(f"Expected result: {node['expect']}")
        chosen_over = [] if for_agent else json.loads(node["alternatives"] or "[]")
        if chosen_over:
            lines.append(f"Chosen over: {', '.join(chosen_over)}")
        if deps:
            lines += ["", "This task depends on:"]
            for d in deps:
                reason = f" — {d['why']}" if d["why"] else ""
                lines.append(f"- `{d['node_id']}` {d['title']} ({d['status']}){reason}")
        if gates:
            lines += ["", "Checks that must pass:"] + [
                f"- {g['label']} ({g['status']}): `{' '.join(json.loads(g['argv_json']))}`" for g in gates
            ]
        others = [r for r in self.active() if r["node_id"] != node_id]
        if others:
            lines += ["", "## Also running in this round"] + [
                f"- `{r['node_id']}` {r['title']}" + (f" (delegated to {r['delegated_to']})" if r["delegated_to"] else "")
                for r in others
            ]
        lines += [
            "",
            "## Instructions",
            "Give this one task your full attention. Check how the result works, in addition to the tests.",
            "Put everything you learn that can change a later decision into the finding. A fresh planner",
            "reads the findings and nothing else from this session.",
            "When the task is done, run:",
            f"  agentic-agile complete {node_id} --learned \"what you learned, and the questions that are still open\" \\",
            "      --understanding changed|same --because \"why\"",
            "Say 'changed' when the finding changes what you think the work needs or its order. The plan is then",
            "cleared and a fresh one is written. Say 'same' when every remaining task still makes sense; a fresh",
            "reviewer then checks the remaining plan against the finding, and clears it if it disagrees.",
            *(["The decision is made when the other running tasks in this round are also complete."] if others else []),
        ]
        return runnable("\n".join(lines) + "\n")

    def choose_capsule(self) -> str:
        ready = self.frontier()
        lines = self._aim_lines("# AGENTIC AGILE: CHOOSE THE NEXT TASK")
        lines += ["", "## Tasks that are ready"]
        for node in ready:
            rank = f"rank {node['rank']}, " if node["rank"] is not None else ""
            lines.append(f"- `{node['node_id']}` ({rank}{node['kind']}) {node['title']}: {node['objective']}")
            if node["why"]:
                lines.append(f"  Why: {node['why']}")
            if node["rank_why"]:
                lines.append(f"  Rank: {node['rank_why']}")
            if node["expect"]:
                lines.append(f"  Expected result: {node['expect']}")
        limit = self.max_parallel
        lines += [
            "",
            "## Instructions",
            "Choose one task. Choose the task whose result can change what you build next.",
            "That task teaches the most, so it is the best use of the next step.",
            "Start the task and give the reason:",
            "  agentic-agile next TASK --why \"the decision this result can change, and the other ready tasks you compared\" --expect \"the result you expect\"",
        ]
        if limit > 1:
            lines += [
                "",
                f"Up to {limit} tasks can run at the same time. Ready tasks are independent of each other,",
                "so you can hand several of them to background agents and do one here:",
                "  agentic-agile delegate TASK --to AGENT-NAME --why \"the decision this result can change\" --expect \"the result you expect\"",
                "The round ends when every running task is complete. Then the plan is cleared and a new plan",
                "is written from all of their findings.",
            ]
        return runnable("\n".join(lines) + "\n")

    def graph_rows(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM nodes ORDER BY rowid").fetchall()

    def hypothesis_capsule(self, for_planner: bool = False) -> str:
        """The planning text. The old plan is absent: only the goal, findings and finished work."""
        with self.connect() as conn:
            done = conn.execute(
                """SELECT n.node_id, n.title FROM nodes n JOIN event_log e ON e.event_id = n.completed_event
                   WHERE n.status='complete' AND n.kind <> 'group' ORDER BY e.sequence"""
            ).fetchall()
        lines = self._aim_lines("# AGENTIC AGILE: WRITE THE PLAN", "full" if for_planner else "recent",
                                independent=for_planner)
        if done:
            lines += ["", "## Completed tasks"] + [f"- `{d['node_id']}` {d['title']}" for d in done]
        running = self.delegated()
        if running:
            lines += ["", "## Running elsewhere (keep these; new tasks may depend on them)"] + [
                f"- `{r['node_id']}` {r['title']} (delegated to {r['delegated_to']})" for r in running
            ]
        write = [
            "Read the goal, the project in its current state, and the findings.",
            "Then write the tasks that are necessary to meet the goal. Include as much detail as you can justify.",
            "",
            "  agentic-agile add ID --title TITLE --objective WHAT-TO-DO --done HOW-TO-TELL-IT-IS-DONE --why REASON \\",
            "      --rank N --rank-why \"what its result can change\" --expect \"the result you expect\" [--parent ID] [--kind KIND]",
            "Rank 1 is the task whose result can change the most about what is built next. Rank every task.",
            "  agentic-agile depends TASK REQUIRED-TASK --why REASON-FOR-THIS-ORDER",
            "",
            "Each task must say what to do, how to tell that it is done, and why it helps to meet the goal.",
            "Turn each open question into an `investigation` task. That keeps the plan honest about what you know.",
        ]
        choose = [
            "When the plan is written, start the task whose result can change what you build next:",
            "  agentic-agile next TASK --why \"the decision this result can change, and the other ready tasks you compared\" --expect \"the result you expect\"",
        ]
        lines += ["", "## Instructions"]
        if for_planner:
            lines += [
                "First decide whether the goal's condition already holds in the project. If it does, write no",
                "tasks, and give the evidence in your final message; the session checks it and records the goal",
                "as met.",
                "",
            ] + write + [
                "Start no task. The tasks are started in rank order as soon as their dependencies are complete.",
            ]
        else:
            if done:
                lines += [
                    "Every task is complete. First check the goal's condition yourself. When it holds, record it:",
                    "  agentic-agile root-complete --why \"the evidence that the condition holds\"",
                    "When work remains, plan it as below.",
                    "",
                ]
            lines += [
                ("The plan is clear. Write a fresh plan from the goal and the findings."
                 if done else "The plan is empty. Write the first plan."),
                "Have a fresh planner write it, so the new plan comes from the evidence alone:",
                "  agentic-agile replan",
                "It starts a separate agent that sees the goal, the findings and the project, and takes one to",
                "five minutes, so give the command a long time limit. If replan reports that it cannot run,",
                "write the plan here in the same way.",
                "",
            ] + write + choose
        return runnable("\n".join(lines) + "\n")

    def waiting_capsule(self) -> str:
        lines = self._aim_lines("# AGENTIC AGILE: WAITING FOR BACKGROUND AGENTS")
        lines += ["", "## Running elsewhere"] + [
            f"- `{r['node_id']}` {r['title']} (delegated to {r['delegated_to']})" for r in self.delegated()
        ]
        lines += [
            "",
            "## Instructions",
            "These tasks are with background agents. When an agent reports back, record its result:",
            "  agentic-agile complete TASK --learned \"what the agent found, and the questions that are still open\" \\",
            "      --understanding changed|same --because \"why\"",
            "When every running task is complete, the plan is cleared and a new plan is written from the findings.",
        ]
        if not self.round_closing() and len(self.active()) < self.max_parallel and self.frontier():
            lines.append("A slot is free, so you can also start a ready task: agentic-agile next TASK --why REASON --expect RESULT")
        return runnable("\n".join(lines) + "\n")

    def reminder(self, run_line: str | None = None) -> str | None:
        text = self._reminder(run_line)
        if text is None:
            return None
        block = render.report_lines(self.reports(open_only=True))
        if not block:
            return text
        # One Instructions section only: the report commands join the ones above.
        return runnable(text.rstrip("\n") + "\n\n" + "\n".join(block) + "\n")

    def _reminder(self, run_line: str | None = None) -> str | None:
        """The short text the hooks send. The engine runs the loop; this session keeps the goal.

        run_line is the one-line status of a run that is working now. Without a
        run the text tells the session to start one. Manual commands stay available.
        """
        root = self.root()
        if root is None or root["status"] != "active":
            return None
        lines = ["", f"Goal: {root['outcome']}"]
        state = self.status()["status"]
        if run_line:
            lines[0] = "# AGENTIC AGILE: THE RUN IS WORKING"
            lines += [run_line, "", "## Instructions", "The run plans, works and reviews with fresh subagents. Follow it:",
                      "  agentic-agile run --wait"]
            return runnable("\n".join(lines) + "\n")
        if state == "blocked":
            lines[0] = "# AGENTIC AGILE: A PERSON IS NEEDED"
            lines += ["No task can start: each waits for a person or for another task.", "", "## Instructions",
                      "Tell the user what is waiting (agentic-agile view), then wait for their answer."]
            return runnable("\n".join(lines) + "\n")
        lines[0] = "# AGENTIC AGILE: START THE RUN"
        current = self.current()
        if current is not None:
            lines.append(f"Task in progress: `{current['node_id']}` {current['title']}")
            others = [r["node_id"] for r in self.active() if r["node_id"] != current["node_id"]]
            if others:
                lines.append(f"Also running in this round: {', '.join(others)}")
        elif state == "awaiting_hypothesis" and self.root_eligible():
            if self._runtime("run_code") == "0":
                # The last run ended with the planner judging the goal met and the goal check passing.
                lines[0] = "# AGENTIC AGILE: RECORD THE GOAL"
                lines += ["The last run judged the goal met, and the goal check passes.", "", "## Instructions",
                          "Check the goal's done condition yourself with its evidence, then record it. Do not start the run again:",
                          "  agentic-agile root-complete --why \"the evidence\"",
                          "When the condition does not hold, run agentic-agile discard --why \"what is missing\" and start the run."]
                return runnable("\n".join(lines) + "\n")
            lines.append("Every task in the plan is complete. When the goal's condition holds, record it:")
            lines.append("  agentic-agile root-complete --why \"the evidence\"")
        elif state == "awaiting_hypothesis":
            lines.append("The plan is empty.")
        else:
            lines.append("Ready tasks: " + (", ".join(f"`{r['node_id']}`" for r in self.frontier()) or "none"))
        lines += ["", "## Instructions", "Start the run, then follow it until it stops:",
                  "  agentic-agile run --background",
                  "  agentic-agile run --wait        (repeat while it exits with 20)",
                  "Read its outcome and tell the user. Manual commands stay: agentic-agile context"]
        return runnable("\n".join(lines) + "\n")

    def capsule(self) -> str | None:
        """The text for the agent in the current state. None means the work is paused or done."""
        root = self.root()
        if root is None or root["status"] != "active":
            return None
        current = self.current()
        if current is not None:
            return self.context(current["node_id"])
        state = self.status()["status"]
        if state == "waiting":
            return self.waiting_capsule()
        if state == "planning":
            return runnable(
                "# AGENTIC AGILE: A FRESH PLANNER IS WRITING THE PLAN\n\n## Instructions\n"
                "Wait for it, then follow what it prints:\n  agentic-agile replan --wait\n"
            )
        if state == "active":
            return self.choose_capsule()
        if state == "awaiting_hypothesis":
            return self.hypothesis_capsule()
        return None  # blocked: waiting on a person or a dependency deadlock

    def run_gates(self, node_id: str, cwd: Path) -> None:
        with self.connect() as conn:
            gates = conn.execute(
                "SELECT * FROM gates WHERE node_id=? ORDER BY gate_id", (node_id,)
            ).fetchall()
        for gate in gates:
            argv = json.loads(gate["argv_json"])
            proc = subprocess.run(argv, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            output = proc.stdout or ""
            digest = hashlib.sha256(output.encode()).hexdigest()
            status = "passed" if proc.returncode == 0 else "failed"
            self.append("gate.result", {
                "gate_id": gate["gate_id"], "status": status,
                "exit_code": proc.returncode, "output_digest": digest,
            })
            if proc.returncode:
                raise AgileError(f"the check '{gate['label']}' needs to pass first. Its output:\n{output.rstrip()}")

    def _assert_completable(self, node_id: str, check_gates: bool = True) -> None:
        node = self.node(node_id)
        if node["status"] not in ("in_progress", "delegated"):
            raise AgileError(f"start task {node_id} first: agentic-agile next {node_id}")
        with self.connect() as conn:
            incomplete_deps = conn.execute(
                """SELECT n.node_id FROM dependencies d JOIN nodes n ON n.node_id=d.requires_id
                   WHERE d.node_id=? AND n.status <> 'complete'""", (node_id,)
            ).fetchall()
            incomplete_children = conn.execute(
                "SELECT node_id FROM nodes WHERE parent_id=? AND status <> 'complete'", (node_id,)
            ).fetchall()
            bad_gates = conn.execute(
                "SELECT label FROM gates WHERE node_id=? AND status <> 'passed'", (node_id,)
            ).fetchall()
        if incomplete_deps:
            raise AgileError("these tasks must be complete first: " + ", ".join(r[0] for r in incomplete_deps))
        if incomplete_children:
            raise AgileError("these tasks below it must be complete first: " + ", ".join(r[0] for r in incomplete_children))
        if check_gates and bad_gates:
            raise AgileError("these checks need to pass first: " + ", ".join(r[0] for r in bad_gates))

    def complete(self, node_id: str, cwd: Path, learned: str = "",
                 understanding: str = "changed", because: str = "") -> list[str]:
        """Finish a task and keep its finding. Returns the ids of cleared tasks.

        The agent declares whether the finding changed its understanding. While
        other tasks of the round are still running, the plan stays. When the
        round ends, a declared change clears the plan at once; a round that
        declared no change keeps it, and the caller asks an independent
        reviewer (see review_needed and record_review).
        """
        if understanding not in ("changed", "same"):
            raise AgileError("declare the understanding as changed or same")
        self._assert_completable(node_id, check_gates=False)
        self.run_gates(node_id, cwd)
        self._assert_completable(node_id)
        round_number = self.node(node_id)["round"]
        self.append("node.completed", {"node_id": node_id, "derived": False, "learned": learned.strip(),
                                       "understanding": understanding, "because": because.strip()})
        self._close_ancestors(self.node(node_id)["parent_id"])
        if self.active():
            return []
        if self._round_declared_change(round_number):
            return self.discard(f"task '{node_id}' changed the understanding", after=node_id)
        return []

    def _round_declared_change(self, round_number: int | None) -> bool:
        with self.connect() as conn:
            if round_number is None:
                return True
            row = conn.execute(
                "SELECT 1 FROM nodes WHERE round=? AND status='complete' AND understanding <> 'same' LIMIT 1",
                (round_number,),
            ).fetchone()
        return row is not None

    def review_needed(self) -> bool:
        """True when a round ended with 'same' and open tasks remain to be checked."""
        if self.active():
            return False
        with self.connect() as conn:
            pending = conn.execute("SELECT 1 FROM nodes WHERE status='pending' LIMIT 1").fetchone()
            last = conn.execute(
                """SELECT n.understanding, e.sequence FROM nodes n JOIN event_log e ON e.event_id=n.completed_event
                   WHERE n.kind <> 'group' ORDER BY e.sequence DESC LIMIT 1"""
            ).fetchone()
            reviewed = conn.execute(
                "SELECT max(sequence) FROM event_log WHERE event_type IN ('plan.reviewed','hypothesis.discarded')"
            ).fetchone()[0]
        return bool(pending and last and last["understanding"] == "same" and (reviewed or 0) < last["sequence"])

    def review_text(self) -> str:
        """What the reviewer sees: the goal, the remaining plan with reasons, and the round's findings."""
        with self.connect() as conn:
            last_round = conn.execute(
                """SELECT n.round FROM nodes n JOIN event_log e ON e.event_id=n.completed_event
                   WHERE n.kind <> 'group' ORDER BY e.sequence DESC LIMIT 1"""
            ).fetchone()[0]
            finished = conn.execute(
                "SELECT * FROM nodes WHERE round=? AND status='complete' ORDER BY rowid", (last_round,)
            ).fetchall()
            pending = conn.execute("SELECT * FROM nodes WHERE status='pending' ORDER BY rowid").fetchall()
            deps = conn.execute("SELECT node_id, requires_id, why FROM dependencies ORDER BY node_id").fetchall()
        lines = self._aim_lines("# AGENTIC AGILE: DOES THE PLAN STILL HOLD?", "full", independent=True)
        lines += ["", "## Just finished"]
        for n in finished:
            lines.append(f"- `{n['node_id']}` {n['title']}")
            if n["expect"]:
                lines.append(f"  Expected: {n['expect']}")
            lines.append(f"  Found: {n['learned']}")
            lines.append(f"  The agent says its understanding is the same, because: {n['because']}")
        lines += ["", "## The remaining plan"]
        for n in pending:
            lines.append(f"- `{n['node_id']}` {n['title']}: {n['objective']}")
            lines.append(f"  Why: {n['why']}")
            for d in deps:
                if d["node_id"] == n["node_id"]:
                    lines.append(f"  After `{d['requires_id']}`" + (f": {d['why']}" if d["why"] else ""))
        lines += [
            "",
            "## Your answer",
            "Your one question: do the findings just listed change what the remaining plan should be? They do",
            "when one of them contradicts a remaining task's reason or order, makes a task unnecessary, or shows",
            "that work is missing. Judge only the effect of these new findings. A weakness in the plan that",
            "these findings did not cause is outside this question; mention it in your reason if it matters.",
            "Answer on the first line with exactly HOLDS or CHANGES. On the next line give the reason in one",
            "or two sentences, naming the new finding and the task involved.",
        ]
        return runnable("\n".join(lines) + "\n")

    def record_review(self, verdict: str, reason: str, reviewer: str, checked: bool) -> list[str]:
        """Record the reviewer's verdict; a verdict of 'changes' clears the plan."""
        self.append("plan.reviewed", {"verdict": verdict, "reason": reason.strip(), "reviewer": reviewer,
                                      "checked": checked})
        if verdict == "changes":
            return self.discard(f"reviewer: {reason.strip()}")
        return []

    def discard(self, why: str, after: str | None = None) -> list[str]:
        """Clear every waiting task. Completed tasks and findings stay."""
        self._assert_root_active()
        with self.connect() as conn:
            ids = [r[0] for r in conn.execute("SELECT node_id FROM nodes WHERE status='pending' ORDER BY node_id")]
        if ids:
            self.append("hypothesis.discarded", {"node_ids": ids, "why": why.strip(), "after": after})
        return ids

    def _close_ancestors(self, node_id: str | None) -> None:
        while node_id:
            node = self.node(node_id)
            with self.connect() as conn:
                open_children = conn.execute(
                    "SELECT 1 FROM nodes WHERE parent_id=? AND status <> 'complete'", (node_id,)
                ).fetchone()
                bad_gates = conn.execute(
                    "SELECT 1 FROM gates WHERE node_id=? AND status <> 'passed'", (node_id,)
                ).fetchone()
            if node["kind"] != "group" or open_children or bad_gates:
                return
            self.append("node.completed", {"node_id": node_id, "derived": True})
            node_id = node["parent_id"]

    def record_note(self, kind: str, body: str, node_id: str | None = None, source: str = "session") -> str:
        if node_id:
            self.node(node_id)
        if source not in NOTE_SOURCES:
            raise AgileError(f"{source}: a note comes from one of {', '.join(NOTE_SOURCES)}")
        note_id = f"note-{uuid.uuid4().hex[:12]}"
        self.append("note.recorded", {"note_id": note_id, "node_id": node_id, "kind": kind, "body": body,
                                      "source": source})
        return note_id

    def graph(self) -> list[dict]:
        """Every node in refinement-tree order with depth, readiness, and requires."""
        with self.connect() as conn:
            # Finished blocks first, in the order they finished; then the live hypothesis.
            nodes = conn.execute(
                """SELECT n.* FROM nodes n LEFT JOIN event_log e ON e.event_id = n.completed_event
                   ORDER BY e.sequence IS NULL, e.sequence, n.priority DESC, n.node_id"""
            ).fetchall()
            deps: dict[str, list[str]] = {}
            for row in conn.execute("SELECT node_id, requires_id FROM dependencies ORDER BY requires_id"):
                deps.setdefault(row[0], []).append(row[1])
        children: dict[str | None, list[sqlite3.Row]] = {}
        for node in nodes:
            children.setdefault(node["parent_id"], []).append(node)
        ready = {r["node_id"] for r in self.frontier()}
        out: list[dict] = []

        def walk(parent: str | None, depth: int, last_flags: tuple[bool, ...]) -> None:
            rows = children.get(parent, [])
            for index, node in enumerate(rows):
                flags = (*last_flags, index == len(rows) - 1)
                out.append({
                    "id": node["node_id"], "title": node["title"], "kind": node["kind"],
                    "status": node["status"], "depth": depth, "last": list(flags),
                    "ready": node["node_id"] in ready, "requires": deps.get(node["node_id"], []),
                    "delegated_to": node["delegated_to"], "rank": node["rank"],
                })
                walk(node["node_id"], depth + 1, flags)

        walk(None, 0, ())
        return out

    # -- goal: whether the host should keep pursuing the current node ---------

    def _runtime(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM runtime WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def planner_running(self) -> bool:
        """True while a background planner process is writing the plan."""
        pid = self._runtime("planner_pid")
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @property
    def goal_paused(self) -> bool:
        return self._runtime("goal_paused") == "1"

    def set_goal_paused(self, paused: bool) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO runtime VALUES ('goal_paused', ?)", ("1" if paused else "0",))

    def view(self) -> dict:
        """One snapshot for every display surface: active node, progress, goal."""
        root = self.root()
        if root is None:
            return {"initialized": False}
        status = self.status()
        current = self.current()
        with self.connect() as conn:
            total = conn.execute("SELECT count(*) FROM nodes WHERE kind <> 'group'").fetchone()[0]
            done = conn.execute(
                "SELECT count(*) FROM nodes WHERE kind <> 'group' AND status='complete'"
            ).fetchone()[0]
            gates = conn.execute(
                "SELECT label, status FROM gates WHERE node_id=? ORDER BY gate_id",
                (current["node_id"] if current else "",),
            ).fetchall()
            finished = conn.execute("SELECT count(*) FROM nodes WHERE status='complete'").fetchone()[0]
        node = None
        if current is not None:
            node = {
                "id": current["node_id"], "title": current["title"], "kind": current["kind"], "rank": current["rank"],
                "objective": current["objective"], "done_when": current["done_when"],
                "why": current["why"], "picked_why": current["picked_why"],
                "expect": current["expect"], "alternatives": json.loads(current["alternatives"] or "[]"),
                "path": [{"id": r["node_id"], "title": r["title"], "kind": r["kind"]}
                         for r in self.path(current["node_id"])],
                "gates": [{"label": g["label"], "status": g["status"]} for g in gates],
            }
        paused = self.goal_paused
        # The goal is active whenever there is something to do: a block in progress, a block
        # to choose, or a hypothesis to write.
        if status["status"] == "complete":
            goal = "complete"
        elif status["status"] == "blocked":
            goal = "blocked"
        elif paused:
            goal = "paused"
        elif node is not None:
            goal = "pursuing"
        elif status["status"] == "waiting":
            goal = "waiting"
        elif status["status"] == "planning":
            goal = "planning"
        elif status["status"] == "active":
            goal = "choosing"
        else:
            goal = "hypothesizing"
        return {
            "initialized": True,
            "state": status["status"],
            "root": {
                "outcome": root["outcome"], "definition_of_done": root["definition_of_done"],
                "constraints": root["constraints_text"], "status": root["status"],
            },
            "node": node,
            "ready": status["ready"],
            "progress": {"done": done, "total": total},
            "goal": {"state": goal, "active": goal in {"pursuing", "choosing", "hypothesizing"}},
            "known": [{"kind": k["kind"], "body": k["body"], "node": k["node"]} for k in self.knowledge(6)],
            "running": [
                {"id": r["node_id"], "title": r["title"], "status": r["status"], "delegated_to": r["delegated_to"]}
                for r in self.active()
            ],
            "parallel": self.max_parallel,
            "round_closing": self.round_closing(),
            "finished": finished,
            "root_eligible": status["root_eligible"],
            "reports": [render.report_entry(r) for r in self.reports(open_only=True)],
        }

    def root_eligible(self) -> bool:
        root = self.root()
        if root is None or root["status"] == "complete":
            return False
        with self.connect() as conn:
            any_nodes = conn.execute("SELECT 1 FROM nodes LIMIT 1").fetchone()
            open_nodes = conn.execute("SELECT 1 FROM nodes WHERE status <> 'complete' LIMIT 1").fetchone()
            bad_gates = conn.execute("SELECT 1 FROM gates WHERE status <> 'passed' LIMIT 1").fetchone()
        return bool(any_nodes and not open_nodes and not bad_gates)

    def goal_check(self) -> list[str] | None:
        """The command that must pass before the goal is recorded, or None.

        Old event files have none, so their goals complete as before.
        """
        found = None
        for path in sorted(self.events_dir.glob("*.json")):
            event = json.loads(path.read_text(encoding="utf-8"))
            if event.get("type") == "settings.changed" and "goal_check" in event["payload"]:
                found = event["payload"]["goal_check"] or None
        return found

    def set_goal_check(self, command: str) -> None:
        """Store the check. A command with shell operators (&&, ||, ;, |, redirects,
        $, backticks) runs through ``sh -c``; a plain command runs as argv."""
        argv = shlex.split(command)
        if not argv:
            raise AgileError("the goal check needs a command")
        if any(token in command for token in SHELL_OPERATORS):
            argv = ["sh", "-c", command.strip()]
        self._assert_root_active()
        self.append("settings.changed", {"goal_check": argv})

    def run_goal_check(self, cwd: Path | None = None) -> tuple[list[str], int, str] | None:
        """Run the goal's check in the project folder; None when the goal has none."""
        argv = self.goal_check()
        if not argv:
            return None
        try:
            proc = subprocess.run(argv, cwd=cwd or Path.cwd(), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as error:
            raise AgileError(f"the goal check '{goal_check_text(argv)}' could not run: {error}") from error
        return argv, proc.returncode, proc.stdout or ""

    def complete_root(self, why: str = "", cwd: Path | None = None) -> dict:
        """Certify the aim: work is done and the rebuilt hypothesis is empty.

        When the goal has a check, it runs first and must pass.
        """
        if not self.root_eligible():
            raise AgileError("complete or clear the open tasks first; then record the goal as met")
        payload = {"frontier_digest": self.frontier_digest(), "why": why.strip()}
        ran = self.run_goal_check(cwd)
        if ran:
            argv, code, output = ran
            if code:
                raise AgileError(f"the goal check '{goal_check_text(argv)}' needs to pass first. Its output:\n{output.rstrip()}")
            payload["check"] = {"argv": argv, "exit_code": 0,
                                "output_digest": hashlib.sha256(output.encode()).hexdigest()}
        return self.append("aim.completed", payload)

    def frontier_digest(self) -> str:
        hashes = []
        for path in sorted(self.events_dir.glob("*.json")):
            hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        return hashlib.sha256("".join(hashes).encode()).hexdigest()

    def status(self) -> dict:
        root = self.root()
        if root is None:
            return {"status": "uninitialized"}
        with self.connect() as conn:
            counts = {r[0]: r[1] for r in conn.execute("SELECT status,count(*) FROM nodes GROUP BY status")}
            total = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
        current = self.current()
        ready = self.frontier()
        delegated = self.delegated()
        if root["status"] == "complete":
            state = "complete"
        elif current:
            state = "active"
        elif delegated:
            # Background agents hold the round; the session waits for their findings.
            state = "waiting"
        elif ready:
            state = "active"
        elif total and counts.get("complete", 0) != total:
            state = "blocked"
        elif self.planner_running():
            state = "planning"
        else:
            state = "awaiting_hypothesis"
        return {
            "status": state,
            "root_status": root["status"],
            "root_aim": root["outcome"],
            "current": current["node_id"] if current else None,
            "delegated": [r["node_id"] for r in delegated],
            "ready": [r["node_id"] for r in ready],
            "counts": counts,
            "root_eligible": self.root_eligible(),
        }
