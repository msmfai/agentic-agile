"""The engine: a deterministic state machine that runs the loop with fresh subagents.

`agentic-agile run` reads the state and takes the next step itself. An empty
plan starts a fresh planner. Ready tasks start in rank order, up to the
parallel limit, each with a fresh worker that records its own finding. A round
that ends with 'same' is checked by a fresh reviewer. The engine returns to the
top-level agent only when a person is needed: a human task, a failure it cannot
resolve, a merge conflict, a limit, or a planner that judges the goal met.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import shlex
import signal
from pathlib import Path
import subprocess
import threading
import time

from . import core, isolation, monitor, planner, render
from .core import AgileError, Store, goal_check_text

# Why the engine handed control back, and the exit code for each.
RETURNS = {
    "goal_met": (0, "the planner judges the goal met and the goal check passes (or the goal has none); check the goal's condition and run root-complete"),
    "goal_recorded": (0, "the goal is recorded as met"),
    "human_task": (10, "a task needs a person"),
    "worker_failed": (11, "a worker did not finish its task"),
    "merge_conflict": (12, "a worker's changes conflict with the project"),
    "limit": (13, "the run reached its step or time limit"),
    "blocked": (14, "every open task waits for another task"),
    "planner_failed": (15, "the planner could not write the plan"),
    "paused": (16, "the goal is paused"),
    "waiting": (17, "the running tasks belong to agents outside this run"),
    "crashed": (18, "the run stopped without recording an outcome"),
    "goal_check_failed": (19, "the planner judges the goal met and the goal check fails"),
}
STILL_RUNNING = 20  # `run --wait` exit code while the background run works; every other code is an outcome

REPORT_OPEN = 21  # `run --wait` and `replan --wait` exit code while a report waits for the top-level agent

# Paths that a worker's commit leaves out: bytecode and cache folders, and the system's own files.
ARTIFACTS = ("**/__pycache__/**", "**/*.pyc", "**/.DS_Store", "**/node_modules/**", "**/.pytest_cache/**")

RUN_STATE_VARIABLE = "AGENTIC_AGILE_RUN_STATE"
TASK_VARIABLE = "AGENTIC_AGILE_TASK"

WORKER_ROLE = (
    "You are a worker for an Agentic Agile project. You start with no history. Do exactly the task "
    "below in this folder: edit files and run commands as needed, and check how the result works. "
    "When the task is done, record the result with the exact command given at the end. That command "
    "is the last thing you do. Leave the .agentic-agile folder and its git history as they are. For any "
    "experiment with Agentic Agile itself, give it a separate state folder with --state. Name tasks and "
    "plans only in your finding, so that the project files describe the project."
)


@dataclass
class Worker:
    node_id: str
    process: subprocess.Popen
    cwd: Path
    host: str
    branch: str = ""
    attempt: int = 1
    output: str = ""
    started: float = field(default_factory=time.monotonic)
    lines: list = field(default_factory=list)
    last_line: float = field(default_factory=time.monotonic)
    reader: "threading.Thread | None" = None
    monitor: "monitor.Monitor | None" = None
    guard: threading.Lock = field(default_factory=threading.Lock)
    reported: set = field(default_factory=set)  # IDs of the reports this attempt opened or joined

    def read_output(self) -> None:
        """Reader thread: feed every line to the monitor with its arrival time and keep the lines.
        Partial-message events count as life for the monitor but are not kept."""
        for line in self.process.stdout:
            now = time.monotonic()
            self.last_line = now
            if self.monitor:
                with self.guard:
                    self.monitor.feed(line, now)
            if not planner.is_partial(line):
                self.lines.append(line)

    def check(self, now: float, extension: int) -> list:
        """The findings of the monitor now. The engine only reports them and stops nothing."""
        if not self.monitor:
            return []
        with self.guard:
            self.monitor.extension = extension
            return self.monitor.check(now)


@dataclass
class Outcome:
    reason: str
    detail: str = ""

    @property
    def code(self) -> int:
        return RETURNS[self.reason][0]

    def message(self) -> str:
        text = RETURNS[self.reason][1]
        return f"{text}: {self.detail}" if self.detail else text


def worker_command(host: str, text: str, model: str, cwd: Path, custom: str = "",
                   state_dir: Path | None = None) -> tuple[list[str], str | None]:
    """The argv for one worker: edit and shell tools, no prompts, no hooks, no link to this session."""
    if host == "command":
        if not custom:
            raise AgileError("set the command first: agentic-agile planner --command CMD")
        import shlex
        return shlex.split(custom), text
    if host == "claude":
        argv = ["claude", "-p", text, *isolation.claude_worker_flags(state_dir), "--no-session-persistence",
                "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--allowedTools", "Bash", "Edit", "Write", "Read", "Glob", "Grep"]
        return argv + (["--model", model] if model else []), None
    if host == "codex":
        argv = ["codex", "exec", *isolation.CODEX_FLAGS,
                "--skip-git-repo-check", "-s", "workspace-write", "--json", "-C", str(cwd)]
        return argv + (["-m", model] if model else []) + [text], None
    argv = ["opencode", "run", *isolation.OPENCODE_FLAGS, "--auto", "--format", "json", "--dir", str(cwd)]
    return argv + (["-m", model] if model else []) + [text], None


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=Agentic Agile", "-c", "user.email=agentic-agile@localhost",
                           "-C", str(cwd), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class Engine:
    def __init__(self, store: Store, cwd: Path, host: str = "", model: str = "",
                 max_steps: int = 50, max_minutes: float = 120, say=print):
        self.store = store
        self.cwd = cwd
        config = planner.settings(store)
        self.host = planner.resolve_host(host or config["host"])
        self.model = model or config["model"] or planner.DEFAULT_MODELS.get(self.host, "")
        self.custom = config["command"]
        self.max_steps = max_steps
        self.deadline = time.monotonic() + max_minutes * 60
        self.steps = 0
        self.say = say
        self.workers: dict[str, Worker] = {}
        self.cost = 0.0
        self.tokens = 0  # for hosts that report tokens and no dollars

    # -- isolation -----------------------------------------------------------------

    def _parallel(self) -> int:
        """The parallel limit, lowered to 1 when worktrees cannot isolate the workers."""
        limit = self.store.max_parallel
        if limit <= 1:
            return 1
        inside = _git(self.cwd, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0:
            self.say(f"[run] the parallel limit is {limit}, but this project is not a git repository; running one task at a time")
            return 1
        dirty = [line for line in _git(self.cwd, "status", "--porcelain").stdout.splitlines()
                 if not line[3:].startswith(".agentic-agile")]
        if dirty:
            self.say(f"[run] the parallel limit is {limit}, but the working tree has changes; running one task at a time")
            return 1
        return limit

    def _workspace(self, node_id: str, isolated: bool) -> tuple[Path, str]:
        if not isolated:
            return self.cwd, ""
        # A worktree left by a run that stopped early is replaced.
        _git(self.cwd, "worktree", "remove", "--force", str(self.store.state_dir / "workers" / node_id))
        _git(self.cwd, "branch", "-q", "-D", f"agile/{node_id}")
        workers = self.store.local_path("workers")
        workers.mkdir(parents=True, exist_ok=True)
        path, branch = workers / node_id, f"agile/{node_id}"
        made = _git(self.cwd, "worktree", "add", "-q", str(path), "-b", branch)
        if made.returncode != 0:
            raise AgileError(f"could not create a worktree for {node_id}: {made.stderr.strip()}")
        return path, branch

    def _merge(self, worker: Worker) -> Outcome | None:
        """Bring an isolated worker's changes into the project; a conflict returns to the top agent."""
        if not worker.branch:
            return None
        # Build output is the worker's side effect, not its work; two workers that each commit
        # their own .pyc files would conflict on every merge.
        _git(worker.cwd, "add", "-A", "--", ".", *(f":(exclude,glob){g}" for g in ARTIFACTS))
        if _git(worker.cwd, "diff", "--cached", "--quiet").returncode != 0:
            _git(worker.cwd, "commit", "-q", "-m", f"agile: {worker.node_id}")
        merged = _git(self.cwd, "merge", "-q", "--no-ff", "-m", f"agile: merge {worker.node_id}", worker.branch)
        _git(self.cwd, "worktree", "remove", "--force", str(worker.cwd))
        if merged.returncode != 0:
            _git(self.cwd, "merge", "--abort")
            # A surprise reaches every fresh planner; the finding of this task describes work that is not in the project yet.
            self.store.record_note("surprise", f"NOT MERGED: the changes of {worker.node_id} conflict with the project, so "
                                               f"the finding of {worker.node_id} describes work that is absent from it. "
                                               f"The changes are kept on branch {worker.branch}.", worker.node_id, source="engine")
            return Outcome("merge_conflict", f"{worker.node_id}; its changes are on branch {worker.branch}")
        _git(self.cwd, "branch", "-q", "-D", worker.branch)
        self.say(f"[run] merged the changes of {worker.node_id}")
        return None

    # -- one step at a time ----------------------------------------------------------

    def _plan(self) -> Outcome | None:
        self.steps += 1
        self.say("[run] the plan is empty; a fresh planner is writing it")
        try:
            result = planner.replan(self.store, self.cwd, self.host, self.model)
        except AgileError as exc:
            return Outcome("planner_failed", str(exc))
        self._spend(result.get("cost", ""))
        if not result["added"]:
            reason = result.get("goal_met_reason", "")
            try:
                ran = self.store.run_goal_check(self.cwd)
            except AgileError as exc:
                ran = (["?"], 1, str(exc))
            if ran and ran[1]:
                argv, _, output = ran
                detail = f"'{goal_check_text(argv)}' exits {ran[1]}. Its output:\n{output.strip()[-800:]}"
                self.store.record_note("blocker", f"The planner judged the goal met, and the goal check fails. {detail}", source="engine")
                return Outcome("goal_check_failed", detail)
            return Outcome("goal_met", reason)
        self.say(f"[run] the planner wrote {len(result['added'])} tasks: {', '.join(result['added'])}")
        return None

    def _start(self, node, isolated: bool, attempt: int = 1) -> None:
        self.steps += 1
        why = f"rank {node['rank']}: {node['rank_why']}" if node["rank_why"] else "next ready task in rank order"
        if node["status"] == "pending":
            self.store.delegate(node["node_id"], f"{self.host} worker", why, node["expect"])
        cwd, branch = self._workspace(node["node_id"], isolated)
        text = WORKER_ROLE + "\n\n" + planner.brief(self.store, node["node_id"])
        argv, stdin = worker_command(self.host, text, self.model, cwd, self.custom, self.store.state_dir)
        # The worker gets the run's state only through the --state in its brief. The guard in the
        # CLI then lets it record its own task and nothing else in that state.
        env = isolation.agent_env({planner.ROLE_VARIABLE: "worker",
                                   RUN_STATE_VARIABLE: str(self.store.state_dir.resolve()),
                                   TASK_VARIABLE: node["node_id"],
                                   "PATH": f"{planner.BIN_DIR}{os.pathsep}{os.environ.get('PATH', '')}"})
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, start_new_session=True,
                                       stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL)
        except FileNotFoundError:
            raise AgileError(f"{argv[0]} is not installed on this machine")
        if stdin is not None:
            process.stdin.write(stdin)
            process.stdin.close()
        worker = Worker(node["node_id"], process, cwd, self.host, branch, attempt)
        # Every task has an expected time (Store.expected_seconds falls back to a default); it is the
        # only guard against a hung command inside a claude subagent, whose heartbeats never stop.
        worker.monitor = monitor.Monitor(
            self.host, process.pid, "worker", node["node_id"],
            expected=self.store.expected_seconds(node["node_id"]),
            extension=monitor.extension_seconds(self.store, node["node_id"]), start=worker.started)
        worker.reader = threading.Thread(target=worker.read_output, daemon=True)
        worker.reader.start()
        self.workers[node["node_id"]] = worker
        where = f" in its own worktree" if branch else ""
        self.say(f"[run] started {node['node_id']} with a {self.host} worker{where}")

    def _watch(self, now: float | None = None) -> None:
        """Report each stuck or slow worker, and obey the top-level agent's decision on those reports.

        Extra time is read again on every pass. A kind that was dismissed for the task is read from the
        store, so it is not opened again in this attempt, in a retry, or after an engine restart. A report resolved as stopped ends that worker.
        """
        now = time.monotonic() if now is None else now
        for worker in list(self.workers.values()):
            extension = monitor.extension_seconds(self.store, worker.node_id)
            dismissed = self.store.dismissed_kinds(worker.node_id, "worker")
            stopped = next((r["report_id"] for r in self.store.reports()
                            if r["report_id"] in worker.reported and r["status"] == "stopped"), "")
            if stopped:
                # Decided already: open nothing new for a worker that is about to end.
                self._stop(worker, stopped)
                continue
            for found in worker.check(now, extension):
                if found.kind in dismissed:
                    continue
                before = len(self.store.reports())
                worker.reported.add(self.store.open_report(found.kind, found.evidence, found.node_id,
                                                           found.role, found.host))
                if len(self.store.reports()) > before:
                    self.say(f"[run] report: {found.kind} {found.node_id}: {found.evidence.split(';')[0]}")

    def _stop(self, worker: Worker, report_id: str) -> None:
        """The top-level agent stopped this worker: end its process group, keep its work on its branch
        without merging, and record the task as complete with a finding that names the report."""
        try:
            os.killpg(worker.process.pid, signal.SIGKILL)
        except OSError:
            worker.process.kill()
        worker.process.wait()
        if worker.reader:
            worker.reader.join(10)
        if worker.process.stdout:
            worker.process.stdout.close()
        self._close_reports(worker)
        self.workers.pop(worker.node_id, None)
        worker.output = planner.final_result(worker.host, "".join(worker.lines))
        self._spend(planner._cost(worker.host, worker.output))
        if worker.branch:
            _git(worker.cwd, "add", "-A", "--", ".", *(f":(exclude,glob){g}" for g in ARTIFACTS))
            if _git(worker.cwd, "diff", "--cached", "--quiet").returncode != 0:
                _git(worker.cwd, "commit", "-q", "-m", f"agile: {worker.node_id} (stopped)")
            _git(self.cwd, "worktree", "remove", "--force", str(worker.cwd))
        seconds = int(time.monotonic() - worker.started)
        kept = f" Its changes are kept on branch {worker.branch} and are not merged." if worker.branch else ""
        reason = (f"The top-level agent stopped the worker of {worker.node_id} after {seconds} s on report "
                  f"{report_id}; the task did not finish.{kept}")
        self.say(f"[run] {worker.node_id}: stopped on report {report_id}")
        try:
            self.store.complete(worker.node_id, worker.cwd, reason, "changed",
                                "the task was stopped before it finished, so the plan is rebuilt")
        except AgileError as exc:
            self.store.record_note("blocker", f"{reason} It could not be recorded as complete: {exc}", worker.node_id, source="engine")

    def _close_reports(self, worker: Worker) -> None:
        """The worker ended, so reports that are still open for this attempt no longer describe a live agent.
        They close as ended, which leaves a retry free to report the same kind again."""
        self.store.close_reports(worker.reported)

    def _finish(self, worker: Worker) -> Outcome | None:
        """A worker exited: check its task, fall back to its report, retry once, then return."""
        worker.process.wait()
        if worker.reader:
            worker.reader.join(10)
        if worker.process.stdout:
            worker.process.stdout.close()
        self._close_reports(worker)
        worker.output = planner.final_result(worker.host, "".join(worker.lines))
        self._spend(planner._cost(worker.host, worker.output))
        node = self.store.node(worker.node_id)
        if node["status"] != "complete":
            report = planner._answer_text(worker.host, worker.output).strip()
            if report:
                self.say(f"[run] {worker.node_id}: the worker did not record its finding; using its final report")
                try:
                    self.store.complete(worker.node_id, worker.cwd, report[-1500:], "changed",
                                        "the worker did not record its own finding, so the plan is rebuilt from its report")
                except AgileError as exc:
                    report = ""
                    self.say(f"[run] {worker.node_id}: its report could not complete the task: {exc}")
            if not report:
                if worker.attempt == 1:
                    self.say(f"[run] {worker.node_id}: the worker finished without a result; trying a fresh worker once more")
                    if worker.branch:
                        _git(self.cwd, "worktree", "remove", "--force", str(worker.cwd))
                        _git(self.cwd, "branch", "-q", "-D", worker.branch)
                    self._start(node, bool(worker.branch), attempt=2)
                    return None
                self.store.record_note("blocker", f"Two workers finished {worker.node_id} without a result.", worker.node_id, source="engine")
                return Outcome("worker_failed", worker.node_id)
        node = self.store.node(worker.node_id)
        self.say(f"[run] {worker.node_id} is complete; understanding {node['understanding'] or 'not declared'}")
        conflict = self._merge(worker)
        if conflict:
            return conflict
        # The reviewer reads the project once every worker of the round is merged.
        if not self.workers and not self.store.active() and self.store.review_needed():
            self.say("[run] the round ended with 'same'; a fresh reviewer is checking the plan")
            try:
                verdict, reason, reviewer = planner.review(self.store, self.cwd)
                checked = True
            except AgileError as exc:
                verdict, reason, reviewer, checked = "holds", f"not checked: {exc}", "none", False
            self.store.record_review(verdict, reason, reviewer, checked)
            self.say(f"[run] reviewer: {verdict}")
        return None

    def _spend(self, note: str) -> None:
        if note.startswith("$"):
            self.cost += float(note[1:])
        elif note.endswith(" tokens"):
            self.tokens += int(note.split()[0])

    def cost_line(self) -> str:
        """What the hosts reported: dollars, tokens, or both."""
        parts = []
        if self.cost:
            parts.append(f"${self.cost:.2f}" if self.cost >= 0.01 else f"${self.cost:.4f}")
        if self.tokens:
            parts.append(f"{self.tokens:,} tokens")
        return " and ".join(parts)

    def _settle_branches(self) -> None:
        """Branches kept after a conflict: record the ones now merged, and name the ones still waiting."""
        listed = _git(self.cwd, "branch", "--list", "agile/*", "--format=%(refname:short)")
        for branch in listed.stdout.split():
            node_id = branch.removeprefix("agile/")
            try:
                node = self.store.node(node_id)
            except AgileError:
                continue
            if node["status"] != "complete":
                continue
            if _git(self.cwd, "merge-base", "--is-ancestor", branch, "HEAD").returncode == 0:
                _git(self.cwd, "branch", "-q", "-D", branch)
                self.store.record_note("decision", f"MERGED: the changes of {node_id} are now in the project; "
                                                   f"its finding describes work that is present.", node_id, source="engine")
                self.say(f"[run] the changes of {node_id} are merged into the project")
            else:
                self.say(f"[run] the changes of {node_id} are still on branch {branch}; merge it with git merge {branch}")

    def run(self) -> Outcome:
        limit = self._parallel()
        isolated = limit > 1
        self._settle_branches()
        # Tasks still held by the workers of a run that stopped early get a fresh worker.
        for node in self.store.delegated():
            if node["delegated_to"].endswith(" worker"):
                self.say(f"[run] {node['node_id']} was left by an earlier run; starting a fresh worker")
                self._start(node, isolated)
        while True:
            root = self.store.root()
            if root is not None and root["status"] == "complete":
                return Outcome("goal_recorded")
            if self.store.goal_paused:
                return Outcome("paused")
            if self.steps >= self.max_steps or time.monotonic() > self.deadline:
                if not self.workers:
                    return Outcome("limit", f"{self.steps} steps")
            # Collect finished workers first.
            for node_id, worker in list(self.workers.items()):
                if worker.process.poll() is not None:
                    del self.workers[node_id]
                    outcome = self._finish(worker)
                    if outcome:
                        return outcome
            state = self.store.status()["status"]
            if not self.workers:
                if state == "awaiting_hypothesis":
                    outcome = self._plan()
                    if outcome:
                        return outcome
                    continue
                if state == "blocked":
                    return Outcome("blocked")
            # Start ready tasks in rank order, up to the limit, while the round is open.
            # A worker that has recorded its finding but has not exited is collected and merged first,
            # so the next round starts from the merged project and after the reviewer's check.
            recorded = [w for w in self.workers if self.store.node(w)["status"] == "complete"]
            can_start = (self.steps < self.max_steps and time.monotonic() <= self.deadline
                         and not self.store.round_closing() and not recorded)
            if can_start:
                ready = [r for r in self.store.frontier() if r["kind"] != "human"]
                humans = [r for r in self.store.frontier() if r["kind"] == "human"]
                running = {r["node_id"] for r in self.store.active()} | set(self.workers)
                free = limit - len(running)
                for node in ready[:max(free, 0)]:
                    self._start(node, isolated)
                if not self.workers and not ready and humans:
                    return Outcome("human_task", f"{humans[0]['node_id']}: {humans[0]['objective']}")
            if self.workers:
                self._watch()
                time.sleep(0.5)
                continue
            others = self.store.active()
            if others:
                return Outcome("waiting", ", ".join(r["node_id"] for r in others))
            if self.steps >= self.max_steps or time.monotonic() > self.deadline:
                return Outcome("limit", f"{self.steps} steps")


# -- the run in the background ---------------------------------------------------------
# The state lives in the runtime table (outside the event history), under run_* keys.

def _set(store: Store, **values: str | int) -> None:
    with store.connect() as conn:
        for key, value in values.items():
            if value == "":
                conn.execute("DELETE FROM runtime WHERE key=?", (f"run_{key}",))
            else:
                conn.execute("INSERT OR REPLACE INTO runtime VALUES (?,?)", (f"run_{key}", str(value)))


def _get(store: Store, key: str) -> str:
    return store._runtime(f"run_{key}") or ""


def running_pid(store: Store) -> int | None:
    """The pid of a run that is working on this state, if any."""
    pid = _get(store, "pid")
    if pid and planner._alive(int(pid)):
        return int(pid)
    return None


def claim(store: Store) -> None:
    """Take the run for this process; a second run on the same state is refused."""
    other = running_pid(store)
    if other and other != os.getpid():
        raise AgileError(f"a run is already working on this state (process {other}); "
                         "follow it with: agentic-agile run --status, or run --wait")
    _set(store, pid=os.getpid(), code="", outcome="", step=0, activity="starting")


def finish(store: Store, outcome: Outcome | None, detail: str = "") -> None:
    if outcome:
        _set(store, code=outcome.code, outcome=outcome.message(), pid="")
    else:
        _set(store, code=RETURNS["crashed"][0], outcome=f"{RETURNS['crashed'][1]}: {detail}".rstrip(": "), pid="")


def progress(store: Store, step: int, line: str) -> None:
    _set(store, step=step, activity=line.removeprefix("[run] "))


def start_background(store: Store, cwd: Path, argv: list[str]) -> tuple[int, Path]:
    """Start `run --child` detached in its own session, so it outlives the caller."""
    other = running_pid(store)
    if other:
        raise AgileError(f"a run is already working on this state (process {other}); "
                         "follow it with: agentic-agile run --status, or run --wait")
    log = store.local_path("run.log")
    with open(log, "w") as handle:
        child = subprocess.Popen(argv, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
    _set(store, pid=child.pid, log=str(log), code="", outcome="", step=0, activity="starting")
    return child.pid, log


def status_line(store: Store) -> str:
    pid = running_pid(store)
    if pid:
        tasks = ", ".join(r["node_id"] for r in store.active()) or "none"
        return (f"running (process {pid}): step {_get(store, 'step') or 0}, tasks running: {tasks}; "
                f"now: {_get(store, 'activity') or 'starting'}")
    code, outcome = _get(store, "code"), _get(store, "outcome")
    if code:
        one_line = " ".join(str(outcome).split())
        if len(one_line) > 300:
            one_line = one_line[:300].rsplit(" ", 1)[0] + " ..."
        return f"not running; last outcome (exit {code}): {one_line}"
    if _get(store, "log"):
        return f"not running; the last run recorded no outcome (see {_get(store, 'log')})"
    return "no run yet"


def wait(store: Store, timeout: float, say) -> int:
    """Print the log as it grows until the run ends or the time is up. Returns an exit code."""
    log = _get(store, "log")
    offset = 0
    deadline = time.monotonic() + timeout

    def drain() -> None:
        nonlocal offset
        if log and Path(log).exists():
            with open(log) as handle:
                handle.seek(offset)
                text = handle.read()
                offset = handle.tell()
            if text:
                say(text.rstrip("\n"))

    while running_pid(store) and time.monotonic() < deadline:
        drain()
        if store.reports(open_only=True):
            break
        time.sleep(1)
    drain()
    if running_pid(store):
        open_reports = store.reports(open_only=True)
        if open_reports:
            say(render.report_block(open_reports, core.cli_command(), "run --wait"))
            return REPORT_OPEN
        say(f"The run is still working: {status_line(store)}\nWait again with: agentic-agile run --wait")
        return STILL_RUNNING
    code = _get(store, "code")
    if not code:
        say("The run ended without recording an outcome. Its log is " + (log or "missing"))
        return RETURNS["crashed"][0]
    if not (log and Path(log).exists()):
        say(f"The run ended: {status_line(store)}")  # a foreground run writes its log to its own terminal
    return int(code)
