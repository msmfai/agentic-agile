"""Text renderings of the current state: status line, tree, and dashboard."""

from __future__ import annotations

import os
import shutil
import sys

RESET = "\033[0m"
STYLES = {
    "bold": "\033[1m", "dim": "\033[2m", "green": "\033[32m", "yellow": "\033[33m",
    "blue": "\033[34m", "magenta": "\033[35m", "cyan": "\033[36m", "red": "\033[31m",
}
MARKS = {"complete": ("✔", "green"), "in_progress": ("▶", "cyan"), "delegated": ("◐", "cyan"),
         "ready": ("◇", "yellow"), "pending": ("○", "dim")}
# goal state -> (symbol, goal label, color, the activity shown between tasks)
GOAL = {
    "pursuing": ("●", "goal active", "green", ""),
    "choosing": ("●", "goal active", "green", "choosing the next task"),
    "hypothesizing": ("●", "goal active", "green", "writing the plan"),
    "waiting": ("◐", "background agents working", "cyan", "waiting for"),
    "planning": ("◐", "fresh planner working", "cyan", "a fresh planner is writing the plan"),
    "paused": ("⏸", "goal paused", "yellow", "paused"),
    "blocked": ("✖", "blocked", "red", "waiting for a person or another task"),
    "complete": ("✔", "goal met", "green", "done"),
}


# Tones are host-neutral color roles; each UI maps them to its own theme.
TONES = {"green": "success", "yellow": "warning", "red": "error", "dim": "muted", "cyan": "accent"}


def display(view: dict) -> dict:
    """The words and symbols every UI draws, decided here and nowhere else.

    Change a symbol or a label in MARKS or GOAL above and the terminal views,
    the Claude Code band and pane, and the OpenCode sidebar all follow.
    """
    progress = view["progress"]
    return {
        "node": node_label(view),
        "goal": goal_label(view),
        "tone": TONES[GOAL[view["goal"]["state"]][2]],
        "bar": bar(progress["done"], progress["total"], 5),
        "bar_wide": bar(progress["done"], progress["total"], 16),
        "count": f"{progress['done']}/{progress['total']}",
        "line": statusline(view),
        "reports": reports_label(view.get("reports") or []),
    }


def reports_label(reports: list[dict]) -> str:
    """One line for the band and the sidebar. Empty when no report is open."""
    if not reports:
        return ""
    first = reports[0]["line"]
    if len(reports) == 1:
        return f"1 report open: {first}"
    return f"{len(reports)} reports open: {first} and more"


def decorate(graph: list[dict]) -> list[dict]:
    """Add each node's mark, tone, and tree prefix for UIs that draw the graph."""
    out = []
    for node in graph:
        mark, style = MARKS["ready" if node["ready"] else node["status"]]
        prefix = "".join("  " if last else "│ " for last in node["last"][:-1])
        prefix += "└ " if node["last"][-1] else "├ "
        out.append({**node, "mark": mark, "tone": TONES[style], "prefix": prefix})
    return out


def want_color(flag: str = "auto", stream=None) -> bool:
    if flag == "always":
        return True
    if flag == "never" or os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


class Painter:
    def __init__(self, color: bool):
        self.color = color

    def __call__(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        return "".join(STYLES[s] for s in styles) + text + RESET


def bar(done: int, total: int, width: int = 10) -> str:
    filled = round(width * done / total) if total else 0
    return "▰" * filled + "▱" * (width - filled)


def node_label(view: dict) -> str:
    """The path of the current task, or the current activity between tasks."""
    node = view.get("node")
    if node:
        return " › ".join(part["id"] for part in node["path"])
    state = view["goal"]["state"]
    label = GOAL[state][3]
    if state == "choosing":
        label += f" ({len(view['ready'])} ready)"
    if state == "waiting":
        label += " " + ", ".join(r["id"] for r in view.get("running", []))
    return label


def goal_label(view: dict) -> str:
    symbol, text = GOAL[view["goal"]["state"]][:2]
    return f"{symbol} {text}"


def statusline(view: dict, color: bool = False) -> str:
    """One line: which node is active and whether a goal is pursuing it."""
    if not view.get("initialized"):
        return ""
    paint = Painter(color)
    progress = view["progress"]
    goal_color = GOAL[view["goal"]["state"]][2]
    parts = [
        paint("◆ Agentic Agile", "magenta", "bold"),
        paint(node_label(view), "cyan", "bold") if view.get("node") else paint(node_label(view), "yellow"),
        f"{paint(bar(progress['done'], progress['total'], 5), 'green')} {progress['done']}/{progress['total']}",
        paint(goal_label(view), goal_color),
    ]
    return paint(" · ", "dim").join(parts)


def tree_lines(view: dict, graph: list[dict], color: bool = False) -> list[str]:
    paint = Painter(color)
    root_done = view["root"]["status"] == "complete"
    lines = [f"{paint('✔', 'green') if root_done else paint('◆', 'magenta')} {paint(view['root']['outcome'], 'bold')}"]
    for node in graph:
        prefix = "".join("   " if last else "│  " for last in node["last"][:-1])
        prefix += "└─ " if node["last"][-1] else "├─ "
        key = "ready" if node["ready"] else node["status"]
        mark, style = MARKS[key]
        title_styles = {"complete": ("dim",), "in_progress": ("cyan", "bold"), "delegated": ("cyan",)}.get(node["status"], ())
        rank = paint(f"#{node['rank']} ", "dim") if node.get("rank") is not None and node["status"] == "pending" else ""
        line = f"{paint(prefix, 'dim')}{paint(mark, style)} {rank}{paint(node['id'], *title_styles)}"
        line += paint(f" ({node['kind']})", "dim") + f" {paint(node['title'], *title_styles)}"
        if node["ready"]:
            line += paint("  ready", "yellow")
        if node.get("delegated_to"):
            line += paint(f"  with {node['delegated_to']}", "cyan")
        if node["requires"]:
            line += paint(f"  requires {', '.join(node['requires'])}", "dim")
        lines.append(line)
    return lines


def dashboard(view: dict, graph: list[dict], color: bool = False, width: int | None = None) -> str:
    if not view.get("initialized"):
        return "Set a goal to start: agentic-agile init --goal ... --done ...\n"
    paint = Painter(color)
    width = width or shutil.get_terminal_size((80, 24)).columns
    progress = view["progress"]
    rule = paint("─" * min(width, 100), "dim")
    goal_color = GOAL[view["goal"]["state"]][2]
    lines = [
        f"{paint('◆ AGENTIC AGILE', 'magenta', 'bold')}   {paint(goal_label(view), goal_color, 'bold')}",
        rule,
        f"{paint('Goal', 'dim')}      {paint(view['root']['outcome'], 'bold')}",
        f"{paint('Done when', 'dim')} {view['root']['definition_of_done']}",
    ]
    if view["root"]["constraints"]:
        lines.append(f"{paint('Within', 'dim')}    {view['root']['constraints']}")
    lines += [
        f"{paint('Progress', 'dim')}  {paint(bar(progress['done'], progress['total'], 24), 'green')} "
        f"{progress['done']} done, {progress['total'] - progress['done']} in the current plan",
        "",
    ]
    lines += tree_lines(view, graph, color) if graph else [paint("(the plan is empty)", "dim")]
    node = view.get("node")
    state = view["goal"]["state"]
    lines.append("")
    if node:
        lines += [
            f"{paint('▶ Current task', 'cyan', 'bold')}  {paint(node_label(view), 'cyan')}",
            f"  {paint('What', 'dim')}       {node['objective']}",
            f"  {paint('Done when', 'dim')}  {node['done_when']}",
        ]
        if node["why"]:
            lines.append(f"  {paint('Why', 'dim')}        {node['why']}")
        if node["picked_why"]:
            lines.append(f"  {paint('Why now', 'dim')}    {node['picked_why']}")
        if node.get("expect"):
            lines.append(f"  {paint('Expect', 'dim')}     {node['expect']}")
        for gate in node["gates"]:
            mark, style = {"passed": ("✔", "green"), "failed": ("✖", "red")}.get(gate["status"], ("○", "dim"))
            lines.append(f"  {paint(mark, style)} gate: {gate['label']} {paint('(' + gate['status'] + ')', 'dim')}")
    elif state == "choosing":
        lines.append(f"{paint('Choose the next task.', 'yellow')} Ready: {', '.join(view['ready'])}. "
                     "Run 'agentic-agile next TASK --why ...'.")
    elif state == "hypothesizing":
        lines.append(paint("The plan is clear. Write a new one from the findings.", "yellow"))
    others = [r for r in view.get("running", []) if r["status"] == "delegated"]
    if others:
        lines += ["", paint("Running with background agents", "cyan")]
        for r in others:
            lines.append(f"  {paint('◐', 'cyan')} {r['id']} {r['title']} {paint('with ' + r['delegated_to'], 'dim')}")
        if view.get("round_closing"):
            lines.append(paint("  The plan is cleared when these are complete.", "dim"))
    if view["known"]:
        lines += ["", paint("Findings (permanent)", "dim")]
        for item in view["known"]:
            where = f" [{item['node']}]" if item["node"] else ""
            lines.append(f"  {paint(item['kind'], 'yellow')}{paint(where, 'dim')} {item['body']}")
    return "\n".join(lines) + "\n"


# -- reports: an agent that is stuck or slow -------------------------------

REPORT_TITLES = {"stalled": "sent no output and ran no command", "slow": "ran past its expected time"}
REPORT_ACTIONS = {"stopped": "stopped", "extended": "given more time", "dismissed": "dismissed",
                  "ended": "closed when the agent ended"}


def report_entry(report: dict) -> dict:
    """One report as every UI shows it, with its words decided here."""
    who = report["node_id"] or report["role"] or "an agent"
    if report["role"] and report["node_id"]:
        who = f"{report['role']} on {report['node_id']}"
    return {
        "id": report["report_id"], "kind": report["kind"], "task": report["node_id"],
        "role": report["role"], "host": report["host"], "evidence": report["evidence"],
        "opened": report["opened"], "status": report["status"], "extension": report["extension"],
        "line": f"{who} {REPORT_TITLES[report['kind']]}",
    }


def report_lines(reports: list[dict]) -> list[str]:
    """The text that tells the top-level agent about open reports. Empty when none are open."""
    if not reports:
        return []
    lines = ["## Agents that need a decision"]
    for report in reports:
        entry = report_entry(report)
        lines.append(f"- {entry['id']}: {entry['line']} ({entry['evidence']})")
    lines += ["", "## Instructions", "The engine stops no agent on its own. Tell the user each report and its evidence, "
              "then end your turn and wait for their decision. For each report, stop the task, give it more time, "
              "or dismiss the report:",
              "  agentic-agile report ID stop",
              "  agentic-agile report ID extend --seconds N",
              "  agentic-agile report ID dismiss"]
    return lines


def report_block(reports: list[dict], command: str = "agentic-agile", again: str = "run --wait") -> str:
    """The text that `run --wait` and `replan --wait` print when reports are open: each report with
    its evidence and the commands that decide it. Empty when none are open."""
    if not reports:
        return ""
    lines = ["Agents need a decision. The run keeps going until you act."]
    for report in reports:
        entry = report_entry(report)
        lines += ["", f"Report {entry['id']}: {entry['line']}", f"  Evidence: {entry['evidence']}",
                  f"  Stop the task:   {command} report {entry['id']} stop",
                  f"  Give more time:  {command} report {entry['id']} extend --seconds N",
                  f"  Dismiss:         {command} report {entry['id']} dismiss"]
    lines += ["", f"Decide with the user, then wait again with {command} {again}."]
    return "\n".join(lines)
