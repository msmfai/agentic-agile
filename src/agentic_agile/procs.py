"""Read `ps -A -o pid=,ppid=,stat=,etime=,args=` on macOS, Debian procps and Alpine BusyBox."""
from __future__ import annotations

import re
from typing import NamedTuple

# procps and macOS: [[dd-]hh:]mm:ss.  BusyBox: m:ss, NhMM (hours and minutes), NdHH (days and hours).
_COLON = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d\d)$")
_BUSYBOX = re.compile(r"^(\d+)([dh])(\d\d)$")
_ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)(?:\s+(.*))?$")


class Row(NamedTuple):
    pid: int
    ppid: int
    stat: str
    elapsed: int  # seconds, floored (BusyBox floors to the minute above 1 hour and to the hour above 1 day)
    args: str


def parse_etime(text: str) -> int:
    """Elapsed seconds from a ps etime value. Raises ValueError for any other shape."""
    text = text.strip()
    m = _COLON.match(text)
    if m:
        days, hours, minutes, seconds = (int(v or 0) for v in m.groups())
        return ((days * 24 + hours) * 60 + minutes) * 60 + seconds
    m = _BUSYBOX.match(text)
    if m:
        big, unit, small = int(m[1]), m[2], int(m[3])
        return big * 86400 + small * 3600 if unit == "d" else big * 3600 + small * 60
    raise ValueError(f"unrecognised etime {text!r}")


def parse_ps(text: str) -> list[Row]:
    """Rows of the five column ps call. Lines that are not rows, and rows with an unreadable etime, are skipped."""
    rows = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        try:
            elapsed = parse_etime(m[4])
        except ValueError:
            continue
        rows.append(Row(int(m[1]), int(m[2]), m[3], elapsed, (m[5] or "").strip()))
    return rows
