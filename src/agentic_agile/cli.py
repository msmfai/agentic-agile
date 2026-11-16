from __future__ import annotations

import argparse
import json
import shlex
import os
from pathlib import Path
import sys
import time

from . import __version__
from . import core, hosts, planner, render
from .core import AgileError, Store, discover_state, goal_check_text


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentic-agile",
        description="Set a goal. The agent writes a plan, does one task, records what it learned, "
                    "deletes the rest of the plan, and writes a new plan.",
    )
    p.add_argument("--version", action="version", version=f"agentic-agile {__version__}")
    p.add_argument("--state", help="explicit .agentic-agile state directory")
    p.add_argument("--cwd", default=".", help="project working directory (default: current directory)")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="set the goal")
    init.add_argument("--goal", "--aim", dest="aim", required=True, help="the result that you want")
    init.add_argument("--done", required=True, help="how to tell that the goal is met")
    init.add_argument("--constraints", default="")
    init.add_argument("--check", help="a command that must pass before root-complete records the goal")
    init.add_argument("--parallel", type=int, default=1,
                      help="how many tasks can run at the same time (default 1)")

    add = sub.add_parser("add", help="add a task to the plan")
    add.add_argument("node_id")
    add.add_argument("--parent", help="refinement parent; omit for root")
    add.add_argument("--kind", choices=["group", "task", "investigation", "human", "validation"], default="task")
    add.add_argument("--title", required=True)
    add.add_argument("--objective", required=True, help="what to do")
    add.add_argument("--done", required=True, help="how to tell that the task is done")
    add.add_argument("--why", required=True, help="why the task helps to meet the goal")
    add.add_argument("--priority", type=int, default=0)
    add.add_argument("--rank", type=int, help="1 for the task whose result can change the most")
    add.add_argument("--rank-why", default="", help="what this task's result can change")
    add.add_argument("--expect", default="", help="the result you expect from this task")
    add.add_argument("--minutes", type=float, help="the expected time of this task in minutes (default from the planner timeout)")

    dep = sub.add_parser("depends", help="make one task wait for a different task")
    dep.add_argument("node_id")
    dep.add_argument("requires_id")
    dep.add_argument("--why", default="", help="why this order")

    gate = sub.add_parser("gate-add", help="add an argv-based preflight gate")
    gate.add_argument("node_id")
    gate.add_argument("--label", required=True)
    gate.add_argument("argv", nargs="*", metavar="COMMAND", help="command and arguments; put them after --")

    sub.add_parser("frontier", help="show ready nodes").add_argument("--json", action="store_true")
    nxt = sub.add_parser("next", help="start one ready task and print its instructions")
    nxt.add_argument("node_id", nargs="?", help="the ready task to start (default: highest priority)")
    nxt.add_argument("--why", default="", help="why this task now: the decision its result can change, and the other ready tasks you compared")
    nxt.add_argument("--expect", default="", help="the result you expect, written before the task runs")
    nxt.add_argument("--json", action="store_true")
    context = sub.add_parser("context", help="print a root-to-current-node goal capsule")
    context.add_argument("node_id", nargs="?")

    complete = sub.add_parser("complete", help="run the checks, complete the task, and clear the rest of the plan")
    complete.add_argument("node_id", nargs="?")
    complete.add_argument("--understanding", choices=["changed", "same"],
                          help="did this finding change your understanding of the work? changed clears the plan; "
                               "same keeps it after an independent review")
    complete.add_argument("--because", default="", help="why the understanding changed or stayed the same")
    complete.add_argument("--learned", required=True,
                          help="what you learned, and the questions that are still open")
    delegate = sub.add_parser("delegate", help="hand a ready task, or the task in progress, to a background agent")
    delegate.add_argument("node_id")
    delegate.add_argument("--to", required=True, help="the agent that does the task")
    delegate.add_argument("--why", default="", help="why this task now: the decision that its result can change")
    delegate.add_argument("--expect", default="", help="the result you expect from the agent")
    replan = sub.add_parser("replan", help="have a fresh planner write the next plan")
    replan.add_argument("--host", choices=["claude", "codex", "opencode", "command"], help="the agent that plans")
    replan.add_argument("--model", help="the model for the planner")
    replan.add_argument("--timeout", type=int, help="seconds after which a slow planner is reported")
    replan.add_argument("--background", action="store_true",
                        help="start the planner and return at once; use this when your shell stops long commands")
    replan.add_argument("--wait", action="store_true", help="wait for a background planner and print its result")
    replan.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    run_cmd = sub.add_parser("run", help="run the loop with fresh subagents until a person is needed")
    run_cmd.add_argument("--host", choices=["claude", "codex", "opencode", "command"], help="the agent for planners, workers and reviewers")
    run_cmd.add_argument("--model", help="the model for the subagents")
    run_cmd.add_argument("--max-steps", type=int, default=50, help="planner runs and task starts before the run stops (default 50)")
    run_cmd.add_argument("--max-minutes", type=float, default=120, help="minutes before the run stops starting work (default 120)")
    run_cmd.add_argument("--background", action="store_true",
                         help="start the run detached and return at once; use this when your shell stops long commands")
    run_cmd.add_argument("--wait", action="store_true",
                         help="follow a background run; exit 20 while it works, else the outcome's exit code")
    run_cmd.add_argument("--status", action="store_true", help="one line: step, running tasks, last outcome")
    run_cmd.add_argument("--timeout", type=int, default=540, help="seconds that --wait follows the run (default 540)")
    run_cmd.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    planner_cfg = sub.add_parser("planner", help="show or set the planner for this machine")
    planner_cfg.add_argument("--host", choices=["claude", "codex", "opencode", "command", ""])
    planner_cfg.add_argument("--model")
    planner_cfg.add_argument("--timeout", type=int)
    planner_cfg.add_argument("--command", dest="planner_command", help="a command that reads the planning text on stdin")
    parallel = sub.add_parser("parallel", help="show or set how many tasks can run at the same time")
    parallel.add_argument("limit", nargs="?", type=int)
    discard = sub.add_parser("discard", help="clear the tasks that are still waiting")
    discard.add_argument("--why", required=True, help="what changed")

    note = sub.add_parser("note", help="record a finding, a surprise, evidence, a decision, or a blocker")
    note.add_argument("kind", choices=["finding", "surprise", "evidence", "decision", "blocker"])
    note.add_argument("body")
    note.add_argument("--node")

    check = sub.add_parser("goal-check", help="show, set, or run the check that must pass before the goal is met")
    check.add_argument("command_line", nargs="?", metavar="COMMAND", help="the new check; leave out to show the current one")
    check.add_argument("--run", action="store_true", help="run the check now in the project folder")
    done = sub.add_parser("root-complete", help="record that the goal is met")
    done.add_argument("--why", required=True, help="the evidence that the goal is met")
    sub.add_parser("status", help="show root and scheduler status").add_argument("--json", action="store_true")
    tree = sub.add_parser("tree", help="show the whole graph with statuses and dependencies")
    view = sub.add_parser("view", help="machine-readable snapshot: active node, progress, goal")
    view.add_argument("--json", action="store_true", help="accepted for symmetry; output is always JSON")
    view.add_argument("--graph", action="store_true", help="include every node in tree order")
    line = sub.add_parser("statusline", help="one line: active node, progress, and goal state")
    watch = sub.add_parser("watch", help="live dashboard for a side pane")
    watch.add_argument("--once", action="store_true", help="print the dashboard once and exit")
    watch.add_argument("--interval", type=float, default=1.0, help="seconds between checks (default 1)")
    for display in (tree, line, watch):
        display.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    goal = sub.add_parser("goal", help="show, pause, or resume the goal, or add a constraint from the user",
                          usage="agentic-agile goal [status | pause | resume | constrain TEXT]")
    goal.add_argument("action", nargs="?", choices=["status", "pause", "resume", "constrain"], default="status")
    goal.add_argument("text", nargs="?", help="with constrain, the constraint in the user's words")
    for name, summary in (("install", "install Agentic Agile into an agent host"),
                          ("uninstall", "remove what install added")):
        setup = sub.add_parser(name, help=summary)
        setup.add_argument("host", choices=["claude", "codex", "opencode", "all", "self"],
                           help="'self' is the agent running this command")
        setup.add_argument("--scope", choices=["global", "project"], default="global",
                           help="all projects (default) or the one project at --project")
        setup.add_argument("--project", help="project directory for --scope project (default: --cwd)")
        setup.add_argument("--print", dest="dry_run", action="store_true", help="preview the steps")
        if name == "install":
            setup.add_argument("--variant", choices=["full", "lite"], default="full",
                               help="full adds the host UI; lite installs the skill and goal hooks as plain files")
            setup.add_argument("--source", help="a checkout path or owner/repo (default: this checkout)")
    report = sub.add_parser("report", help="list the reports of stuck or slow agents, or stop, extend or dismiss one",
                            usage="agentic-agile report list [--json] | report ID stop | report ID extend --seconds N | report ID dismiss")
    report.add_argument("target", metavar="list|ID", help="list, or the ID of a report such as r1")
    report.add_argument("action", nargs="?", choices=["stop", "extend", "dismiss"], help="what to do with the report")
    report.add_argument("--seconds", type=int, help="with extend, the extra time for the task in seconds")
    report.add_argument("--json", action="store_true", help="with list, print the reports as JSON")
    report.add_argument("--all", action="store_true", help="with list, include reports that are resolved")
    sub.add_parser("rebuild", help="rebuild SQLite from immutable events")
    for name in ("hook-context", "hook-stop"):
        hook = sub.add_parser(name, help=argparse.SUPPRESS)
        hook.add_argument("--host", choices=["claude", "codex", "opencode"], default="claude")
    return p


def _store(args: argparse.Namespace, allow_missing: bool = False) -> Store | None:
    try:
        state = discover_state(Path(args.cwd), args.state, create=args.command == "init")
    except AgileError:
        if allow_missing:
            return None
        raise
    store = Store(state)
    if args.command != "init" and not store.exists:
        if allow_missing:
            return None
        raise AgileError(f"set a goal first: run 'agentic-agile init' (state folder: {state})")
    return store


def _print_status(status: dict) -> None:
    print(f"Agentic Agile: {status['status']}")
    if status.get("root_aim"):
        print(f"Goal: {status['root_aim']}")
        print(f"Current: {status['current'] or '-'}")
        print(f"Ready: {', '.join(status['ready']) or '-'}")
        print(f"Nodes: {status['counts']}")
        print(f"Root eligible: {status['root_eligible']}")


# A worker reads its own task and the run's status line in the run's state. The plan and the
# session's notes stay out of its view, so its finding describes the project and the goal.
WORKER_READS = {"status", "statusline"}
# A planner writes the plan and shows it; a reviewer answers in its final message and runs nothing.
ROLE_COMMANDS = {"planner": {"add", "depends", "gate-add", "tree", "hook-context", "hook-stop"},
                 "reviewer": {"hook-context", "hook-stop"}}


def _guard_role(args: argparse.Namespace) -> None:
    """A planner or reviewer starts with the goal and the findings only. The commands that show more
    of the state (earlier reasons, notes, other plans) are refused there."""
    role = os.environ.get(planner.ROLE_VARIABLE, "")
    allowed = ROLE_COMMANDS.get(role)
    if allowed is not None and args.command not in allowed:
        shown = ", ".join(sorted(allowed - {"hook-context", "hook-stop"})) or "no commands"
        raise AgileError(f"this is a {role}; it runs {shown}. Its input is the text it was given and the project files.")


def _guard_worker(store: Store, args: argparse.Namespace) -> None:
    """Inside a worker, the run's own state accepts only that worker's own result."""
    run_state = os.environ.get("AGENTIC_AGILE_RUN_STATE")
    if os.environ.get(planner.ROLE_VARIABLE) != "worker" or not run_state:
        return
    task = os.environ.get("AGENTIC_AGILE_TASK", "")
    if Path(store.state_dir).resolve() != Path(run_state).resolve() or args.command in WORKER_READS:
        return
    if args.command == "context" and args.node_id in (task, None):
        return
    if args.command == "complete" and args.node_id == task:
        return
    if args.command == "note" and args.node in (task, None):
        return
    raise AgileError(
        f"this is a worker for task {task}; in this run's state it records only its own result. "
        "For experiments, use a separate state folder, for example: agentic-agile --state /tmp/trial/.agentic-agile ..."
    )


def engine_module_code() -> int:
    from .engine import REPORT_OPEN
    return REPORT_OPEN


def _report(store: Store, args: argparse.Namespace) -> int:
    """List the reports, or close one. Only the top-level agent decides, so agents of a run are refused."""
    if planner.in_planner():
        raise AgileError("reports are for the top-level agent and the user; an agent of the run does not decide on them")
    if args.target == "list":
        if args.action or args.seconds is not None:
            raise AgileError("report list takes only --json and --all")
        found = store.reports(open_only=not args.all)
        if args.json:
            print(json.dumps([render.report_entry(r) for r in found], indent=2))
        elif not found:
            print("No open reports.")
        else:
            for r in found:
                entry = render.report_entry(r)
                done = "" if r["status"] == "open" else f" [{render.REPORT_ACTIONS[r['status']]}]"
                print(f"{entry['id']}: {entry['line']} ({entry['evidence']}){done}")
        return 0
    if not args.action:
        raise AgileError(f"say what to do with {args.target}: stop, extend --seconds N, or dismiss")
    if args.json or args.all:
        raise AgileError("--json and --all belong to report list")
    if args.action == "extend":
        if args.seconds is None or args.seconds <= 0:
            raise AgileError("extend needs --seconds N, the extra time in seconds, above 0")
    elif args.seconds is not None:
        raise AgileError("--seconds belongs to extend")
    action = {"stop": "stopped", "extend": "extended", "dismiss": "dismissed"}[args.action]
    done = store.resolve_report(args.target, action, args.seconds or 0)
    more = f" by {done['extension']} s" if action == "extended" else ""
    print(f"Report {args.target} {render.REPORT_ACTIONS[action]}{more}.")
    return 0


def _delegate_brief(store: Store, node_id: str) -> str:
    return planner.brief(store, node_id)


def _replan_foreground(store: Store, cwd: Path, args: argparse.Namespace) -> int:
    print("Starting a fresh planner. It sees the goal, the findings and the project.", flush=True)
    try:
        result = planner.replan(store, cwd, args.host or "", args.model or "", args.timeout)
    except AgileError as exc:
        print(f"The fresh planner could not write the plan: {exc}")
        print("Write the plan in this session instead.")
        print()
        print(store.hypothesis_capsule(for_planner=True), end="")
        return 3
    model = f", {result['model']}" if result["model"] else ""
    cost = f", {result['cost']}" if result["cost"] else ""
    if not result["added"]:
        print(f"The {result['host']} planner{model}{cost} wrote no tasks. It judges the goal met:")
        print(result["goal_met_reason"])
        ran = store.run_goal_check(cwd)
        if ran and ran[1]:
            print()
            print(f"The goal check '{goal_check_text(ran[0])}' fails with exit {ran[1]}. Its output:")
            print(ran[2].rstrip())
            print("Run discard with what is missing, then replan.")
            return 19
        print()
        print("Check the goal's condition yourself. When it holds, record it:")
        print(f"  {core.cli_command()} root-complete --why \"the evidence\"")
        return 0
    print(f"The {result['host']} planner{model}{cost} wrote {len(result['added'])} tasks: {', '.join(result['added'])}.")
    print()
    print(store.capsule() or "", end="")
    return 0


def _hook(args: argparse.Namespace) -> int:
    """Host hooks. SessionStart injects the operative goal; Stop is the goal hook."""
    payload = hosts.read_hook_input()
    stop = args.command == "hook-stop"
    if planner.in_planner():
        # A planner session writes one plan and ends; the goal hooks stay out of it.
        if stop:
            print("{}")
        return 0
    store = _store(args, allow_missing=True)
    if store is None:
        if stop:
            print("{}")
        return 0
    from . import engine as engine_module
    paused = store.goal_paused
    run_active = engine_module.running_pid(store) is not None
    run_line = engine_module.status_line(store) if run_active else None
    # With a run working, or no run and a session to prompt, the hooks send the short text.
    # While only a fresh planner or background agents work, the full text stays.
    shortened = run_active or store.status()["status"] in {"active", "awaiting_hypothesis", "blocked"}
    capsule = store.reminder(run_line) if shortened else store.capsule()
    if not stop:
        if capsule:
            print(capsule, end="")
            if paused:
                print("\nThe goal is paused. Wait for the user to resume it.")
        return 0
    # Stop: keep the session going while this session has something to do: a
    # task in progress here, a task to choose, or a plan to write. While only
    # background agents are working, the session rests; the host wakes it when
    # an agent reports back. Codex and Claude both accept this decision shape
    # and feed `reason` back as the next prompt.
    view = store.view()
    if view["reports"] and not paused:
        # An open report waits for the top-level agent. The hook holds the turn once so the agent
        # tells the user; the next stop ends the turn, so the session waits for the user's decision.
        label = render.reports_label(view["reports"])
        if payload.get("stop_hook_active"):
            print(json.dumps({"systemMessage": label}))
        else:
            print(json.dumps({"decision": "block", "reason": store.reminder(run_line) or capsule,
                              "systemMessage": label}))
        return 0
    if run_active and not paused:
        # A run drives the loop; the session rests and shows where the run is.
        print(json.dumps({"systemMessage": run_line}))
        return 0
    if paused or view["goal"]["state"] not in {"pursuing", "choosing", "hypothesizing"}:
        if view["goal"]["state"] == "waiting" and not paused:
            print(json.dumps({"systemMessage": render.statusline(view)}))
        else:
            print("{}")
        return 0
    decision: dict = {"decision": "block", "reason": store.reminder() or capsule}
    if args.host == "codex":
        line = render.statusline(store.view())
        if hosts.codex_goal_status(payload.get("session_id")) == "active":
            # A native Codex goal is already pursuing this thread; let it drive.
            decision = {}
        decision["systemMessage"] = line
    print(json.dumps(decision))
    return 0


def _watch(args: argparse.Namespace, color: bool) -> int:
    def snapshot() -> tuple[str, tuple]:
        store = _store(args, allow_missing=True)
        if store is None:
            return render.dashboard({"initialized": False}, [], color), ()
        stamps = [p.stat().st_mtime_ns for p in (store.events_dir, store.db_path) if p.exists()]
        return render.dashboard(store.view(), store.graph(), color), tuple(stamps)

    text, signature = snapshot()
    if args.once:
        print(text, end="")
        return 0
    try:
        previous = None
        while True:
            if signature != previous:
                sys.stdout.write("\033[H\033[2J" + text)
                sys.stdout.flush()
                previous = signature
            time.sleep(max(args.interval, 0.1))
            text, signature = snapshot()
    except KeyboardInterrupt:
        return 0


def run(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    _guard_role(args)
    if args.command == "init":
        store = _store(args)
        assert store
        _guard_worker(store, args)
        store.initialize(args.aim, args.done, args.constraints)
        if args.parallel != 1:
            store.set_max_parallel(args.parallel)
        if args.check:
            store.set_goal_check(args.check)
        print(f"Goal set. State is in {store.state_dir}")
        return 0

    if args.command in {"install", "uninstall"}:
        from .install import install
        return install(
            args.host, getattr(args, "source", None), args.dry_run, args.scope,
            getattr(args, "variant", "full"), args.project or args.cwd, remove=args.command == "uninstall",
        )

    if args.command in {"hook-context", "hook-stop"}:
        return _hook(args)

    if args.command in {"statusline", "view", "watch"}:
        # Display surfaces show output in projects that have a goal.
        store = _store(args, allow_missing=True)
        if store:
            _guard_worker(store, args)
        if args.command == "view":
            snapshot = store.view() if store else {"initialized": False}
            if store:
                snapshot["display"] = render.display(snapshot)
            if store and args.graph:
                snapshot["graph"] = render.decorate(store.graph())
            print(json.dumps(snapshot, indent=2, sort_keys=True))
            return 0
        color = render.want_color(args.color)
        if args.command == "statusline":
            line = render.statusline(store.view(), color) if store else ""
            if line:
                print(line)
            return 0
        return _watch(args, color)

    store = _store(args)
    assert store
    _guard_worker(store, args)
    if args.command == "add":
        store.add_node(args.node_id, args.title, args.objective, args.done, args.kind, args.parent,
                       args.priority, args.why, args.rank, args.rank_why, args.expect, args.minutes)
        print(f"Added {args.node_id}")
    elif args.command == "depends":
        store.add_dependency(args.node_id, args.requires_id, args.why)
        print(f"{args.node_id} now requires {args.requires_id}")
    elif args.command == "gate-add":
        argv = args.argv
        gate_id = store.add_gate(args.node_id, args.label, argv)
        print(f"Added {gate_id}")
    elif args.command == "frontier":
        rows = store.frontier()
        if args.json:
            print(json.dumps([dict(r) for r in rows], indent=2))
        else:
            for row in rows:
                print(f"{row['node_id']}\t{row['kind']}\tpriority={row['priority']}\t{row['title']}")
    elif args.command == "next":
        if not store.current() and store.frontier():
            target = store.node(args.node_id) if args.node_id else store.frontier()[0]
            why = args.why.strip() or (f"rank {target['rank']}: {target['rank_why']}" if target["rank_why"] else "")
            if not why or not (args.expect.strip() or target["expect"]):
                raise AgileError("give the reason and the expected result: "
                                 "agentic-agile next TASK --why REASON --expect RESULT")
            args.why = why
        node = store.claim_next(args.node_id, args.why, args.expect)
        if node is None:
            print(store.capsule() or "All tasks wait for a person or for a different task.\n", end="")
            _print_status(store.status())
            return 2
        if args.json:
            print(json.dumps({"node": dict(node), "context": store.context(node["node_id"])}, indent=2))
        else:
            print(store.context(node["node_id"]), end="")
    elif args.command == "context":
        if args.node_id:
            print(store.context(args.node_id), end="")
        else:
            print(store.capsule() or "The work is paused or done.\n", end="")
    elif args.command == "complete":
        node_id = args.node_id or (store.current()["node_id"] if store.current() else None)
        if not node_id:
            raise AgileError("start a task first: agentic-agile next")
        if not args.understanding or not args.because.strip():
            raise AgileError("say whether the finding changed your understanding, and why: "
                             "--understanding changed|same --because REASON")
        expected = store.node(node_id)["expect"]
        dropped = store.complete(node_id, cwd, args.learned, args.understanding, args.because)
        print(f"Completed {node_id}. Finding saved.")
        if expected:
            print(f"  Expected: {expected}")
            print(f"  Found:    {args.learned.strip()}")
        running = store.active()
        if running:
            names = ", ".join(r["node_id"] for r in running)
            print(f"Still running in this round: {names}. The plan is cleared when they are complete.")
        elif dropped:
            print(f"Cleared the rest of the plan ({len(dropped)} tasks). Write a new plan with: agentic-agile replan")
        elif store.review_needed() and os.environ.get(planner.ROLE_VARIABLE) == "worker":
            print("The engine checks the remaining plan when this worker exits.")
        elif store.review_needed():
            print("You said your understanding is the same. A fresh reviewer is checking the remaining plan.", flush=True)
            try:
                verdict, reason, reviewer = planner.review(store, cwd)
                checked = True
            except AgileError as exc:
                verdict, reason, reviewer, checked = "holds", f"not checked: {exc}", "none", False
            dropped = store.record_review(verdict, reason, reviewer, checked)
            if dropped:
                print(f"The reviewer ({reviewer}) found that the plan changes: {reason}")
                print(f"Cleared the rest of the plan ({len(dropped)} tasks). Write a new plan with: agentic-agile replan")
            elif checked:
                print(f"The reviewer ({reviewer}) agrees the plan holds: {reason}")
            else:
                print(f"The plan stays. The review could not run ({reason}); the declaration is recorded as unchecked.")
        print()
        print(store.capsule() or "", end="")
    elif args.command == "delegate":
        node = store.delegate(args.node_id, args.to, args.why, args.expect)
        print(f"Delegated {node['node_id']} to {node['delegated_to']}. Give the agent everything between the lines:")
        print("-" * 72)
        print(_delegate_brief(store, node["node_id"]), end="")
        print("-" * 72)
        print(f"When the agent reports back, run: {core.cli_command()} complete {node['node_id']} "
              "--learned \"...\" --understanding changed|same --because \"...\"")
    elif args.command == "replan" and args.background:
        argv = [sys.executable, str(core.BIN), "--state", str(store.state_dir), "--cwd", str(cwd), "replan", "--child"]
        for flag, value in (("--host", args.host), ("--model", args.model), ("--timeout", args.timeout)):
            if value:
                argv += [flag, str(value)]
        pid, log = planner.start_background(store, cwd, argv)
        print(f"A fresh planner is writing the plan in the background (process {pid}, log {log}).")
        print(f"It takes one to ten minutes. Wait for it with: {core.cli_command()} replan --wait")
    elif args.command == "replan" and args.wait:
        running, log = planner.wait(store, args.timeout or 540)
        open_reports = store.reports(open_only=True) if running else []
        if open_reports:
            print(render.report_block(open_reports, core.cli_command(), "replan --wait"))
            return engine_module_code()
        if running:
            print(f"The planner is still writing. Wait again with: {core.cli_command()} replan --wait")
            return 0
        print(log, end="")
        return 0 if store.status()["status"] != "awaiting_hypothesis" else 3
    elif args.command == "replan":
        if args.child:
            planner.save_settings(store, pid=os.getpid())
        try:
            return _replan_foreground(store, cwd, args)
        finally:
            if args.child:
                planner.save_settings(store, pid="")
    elif args.command == "run":
        from . import engine as engine_module
        from .engine import Engine
        if args.status:
            print(engine_module.status_line(store))
            return 0
        if args.wait:
            return engine_module.wait(store, args.timeout, lambda text: print(text, flush=True))
        if args.background:
            argv = [sys.executable, str(core.BIN), "--state", str(store.state_dir), "--cwd", str(cwd), "run", "--child",
                    "--max-steps", str(args.max_steps), "--max-minutes", str(args.max_minutes)]
            for flag, value in (("--host", args.host), ("--model", args.model)):
                if value:
                    argv += [flag, value]
            pid, log = engine_module.start_background(store, cwd, argv)
            print(f"The run works in the background (process {pid}, log {log}).")
            print(f"Follow it with: {core.cli_command()} run --wait   (one line: run --status)")
            return 0
        engine_module.claim(store)
        engine = None

        def say(line: str) -> None:
            print(line, flush=True)
            if engine is not None:
                engine_module.progress(store, engine.steps, line)

        outcome = None
        try:
            say("Running the loop. Each step uses a fresh subagent; this session keeps the goal.")
            engine = Engine(store, cwd, args.host or "", args.model or "", args.max_steps, args.max_minutes, say=say)
            outcome = engine.run()
        except BaseException as exc:
            engine_module.finish(store, None, str(exc))
            raise
        engine_module.finish(store, outcome)
        print(f"[run] stopped: {outcome.message()}")
        if engine.cost_line():
            print(f"[run] reported cost of planners and workers: {engine.cost_line()}")
        return outcome.code
    elif args.command == "planner":
        if any(v is not None for v in (args.host, args.model, args.timeout, args.planner_command)):
            planner.save_settings(store, host=args.host, model=args.model, timeout=args.timeout,
                                  command=args.planner_command)
        cfg = planner.settings(store)
        print(f"host: {cfg['host'] or 'the agent running now, else the first one installed'}")
        print(f"model: {cfg['model'] or 'the host default (sonnet for claude)'}")
        print(f"timeout: {cfg['timeout']} s")
        if cfg["command"]:
            print(f"command: {cfg['command']}")
    elif args.command == "parallel":
        if args.limit is not None:
            store.set_max_parallel(args.limit)
        print(f"Up to {store.max_parallel} tasks can run at the same time.")
    elif args.command == "discard":
        dropped = store.discard(args.why)
        print(f"Cleared {len(dropped)} tasks. Completed tasks and findings are kept.")
        print()
        print(store.capsule() or "", end="")
    elif args.command == "report":
        return _report(store, args)
    elif args.command == "note":
        source = "worker" if os.environ.get(planner.ROLE_VARIABLE) == "worker" else "session"
        print(store.record_note(args.kind, args.body, args.node, source))
    elif args.command == "goal-check":
        if args.command_line:
            _guard_worker(store, args)
            store.set_goal_check(args.command_line)
        argv = store.goal_check()
        if not argv:
            print("The goal has no check.")
        elif args.run:
            ran = store.run_goal_check(cwd)
            print(ran[2].rstrip())
            print(f"The goal check '{goal_check_text(argv)}' {'passes' if not ran[1] else 'fails with exit ' + str(ran[1])}.")
            return 1 if ran[1] else 0
        else:
            print(f"The goal check is: {goal_check_text(argv)}")
    elif args.command == "root-complete":
        event = store.complete_root(args.why, cwd)
        print("The goal is met. Recorded.")
    elif args.command == "status":
        status = store.status()
        if args.json:
            print(json.dumps(status, indent=2, sort_keys=True))
        else:
            _print_status(status)
    elif args.command == "tree":
        print("\n".join(render.tree_lines(store.view(), store.graph(), render.want_color(args.color))))
    elif args.command == "goal":
        if args.action == "constrain":
            store.add_constraint(args.text or "")
            print("Constraint added. Every planner, reviewer and worker from now on reads it with the goal.")
            return 0
        if args.text:
            raise AgileError("only goal constrain takes text")
        if args.action != "status":
            store.set_goal_paused(args.action == "pause")
        view = store.view()
        print(f"{render.goal_label(view)} · node: {render.node_label(view)}")
    elif args.command == "rebuild":
        store.rebuild()
        print(f"Rebuilt {store.db_path} from {len(list(store.events_dir.glob('*.json')))} events")
    return 0


def _parse(argv: list[str]) -> argparse.Namespace:
    """Parse argv; for gate-add, everything after the first ``--`` is the gate command."""
    p = parser()
    if "--" in argv:
        split = argv.index("--")
        args = p.parse_args(argv[:split])
        if args.command == "gate-add":
            args.argv = [*args.argv, *argv[split + 1:]]
            return args
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        return run(_parse(sys.argv[1:] if argv is None else list(argv)))
    except AgileError as exc:
        print(f"agentic-agile: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
