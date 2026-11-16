"""Reads from agent hosts. Each read returns a safe default on any error."""

from __future__ import annotations

import json
import os
import select
from pathlib import Path
import sqlite3
import sys
import time


def read_hook_input(timeout: float = 1.0) -> dict:
    """The JSON a host pipes to a hook on stdin; {} for an empty stdin.

    Never blocks for long: a hook that hangs on an open, silent stdin would
    freeze the host, so reading stops at EOF, at the first complete JSON
    document, or after ``timeout`` seconds.
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        fd = sys.stdin.fileno()
        deadline = time.monotonic() + timeout
        raw = b""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            raw += chunk
            try:
                json.loads(raw)
                break
            except ValueError:
                continue
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def codex_goal_status(thread_id: str | None) -> str | None:
    """Status of a Codex thread's native goal ('active', 'paused', ...), if any.

    Codex keeps thread goals in an undocumented SQLite file, so this is a
    best-effort read: any surprise means "unknown" and callers fall back to
    Agentic Agile's own Stop hook.
    """
    if not thread_id:
        return None
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    for db in sorted(home.glob("goals_*.sqlite"), reverse=True):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1)
            try:
                row = conn.execute(
                    "SELECT status FROM thread_goals WHERE thread_id=?", (thread_id,)
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        if row:
            return str(row[0])
    return None
