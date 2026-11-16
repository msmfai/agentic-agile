# Agentic Agile protocol

This document gives the exact rules. In the stored data, a task is called a
`node` and the goal is called the `aim`. The event names use those words.

## 1. Three kinds of data

| | Who controls it | How long it stays |
| --- | --- | --- |
| **Goal**, which is the result, the done condition, and the constraints | The user | Until the goal is met |
| **Plan**, which is the tasks that are still open | The agent. It is an estimate of the necessary work and its order. | Until the next task is completed |
| **Findings**, which is what completed tasks showed, and the engine's notes about the project | The record. Each finding is written one time and kept. | Permanent |

Each plan is written fresh from the goal, the project, and the findings. So
each task in a plan is justified by the current findings. Notes from the
session sit in the record beside the findings, for the session and the user.

## 2. Two relations between tasks

1. **Parent.** Each task has one parent, which is a different task or the goal. The
   line of parents from a task to the goal explains why the task is there.
2. **Depends on.** A task can depend on other tasks. These links control which
   tasks are ready. The links always give the tasks a clear order, and the
   tool checks this when you add a link. A link can record the reason for the
   order.

## 3. What a task records

- `objective` is what to do.
- `done_when` tells how to know that the task is done.
- `why` tells why the task helps to meet the goal. New tasks need it.
- `picked_why` tells why the agent chose it at that time. It is recorded when the task starts, together with the ready alternatives.
- `expected` is the result that the agent expects. It is recorded before the task starts and shown beside the finding.
- `learned` is the finding, recorded when the task is completed.

The kinds are `task`, `investigation`, `human`, `validation`, and `group`. A
`group` gives one reason to the tasks below it. It is complete when those
tasks are complete.

## 4. The loop

One task is in progress at a time. The loop is in one of these states.

| State | Condition | What the agent does |
| --- | --- | --- |
| writing the plan | the plan is empty | when every task is complete, check the goal's condition and run `root-complete` if it holds; otherwise start a fresh planner with `replan` |
| planning | a background planner is writing the plan | wait for it with `replan --wait` |
| choosing | tasks are ready to start | start the ready task that teaches the most |
| working | one task is in progress | complete that task |
| blocked | all open tasks wait for a person or for a different task | ask the person, or correct the order |
| complete | the goal is recorded as met | the work is done |

A goal can carry a check, set with `init --check COMMAND`. `root-complete` runs it in the project folder and records the goal only when it exits 0. The event keeps the command and the digest of its output. `goal-check COMMAND` sets or changes the check on an existing goal, and `goal-check --run` runs it. The run runs the same check when a fresh planner judges the goal met. When the check fails, the run records a blocker note with the output and exits 19, so the planner's judgment alone never ends a run. Goals set without a check complete as before, so old event files load unchanged.

A task is ready when all the tasks that it depends on are complete. A task is
completed when it is in progress, the tasks that it depends on and the tasks
below it are complete, and each check on it passes in that run.

Up to N tasks run at the same time (`agentic-agile parallel N`, default 1). At
most one in progress in the session, the others delegated to background
agents. The tasks started since the plan was last written form a round. While
a round has a completed task and others still running, new tasks wait.

`complete` records `node.completed` with the finding and the agent's
declaration, which is `--understanding changed` or `same`, with a reason. When all the
tasks below a `group` are complete, it closes the group. When the last running
task of the round is complete, the round ends.

- If any task in the round declared `changed`, Agentic Agile records
  `hypothesis.discarded` with the IDs of the tasks that are still waiting.
  Those tasks, their links, and their checks leave the index, and their IDs
  are free to use again. The next plan is written in a fresh context with
  `agentic-agile replan`.
- If every task declared `same`, a fresh reviewer receives the goal, the
  findings, the round's results with their expectations and reasons, and the
  remaining tasks with their reasons and order. It answers HOLDS or CHANGES.
  Agentic Agile records `plan.reviewed`. CHANGES clears the plan as above;
  HOLDS keeps it, and the agent chooses the next task. When no reviewer can
  run, the plan stays and the review is recorded as unchecked.

`discard` clears the waiting tasks directly and keeps the tasks that are
running.

The goal is recorded as met when one task or more is complete and the plan is
empty. This is a separate command, and it records the evidence.

## How each step of the loop is tested

| Step in `media/loop.svg` | Test in `tests/test_core.py` |
| --- | --- |
| Write the plan in a fresh context | `ReplanTests.test_the_planner_sees_the_findings_and_none_of_the_old_plan`, `BackgroundReplanTests` |
| Choose the ready task that teaches the most | `StartRecordTests.test_next_stores_the_alternatives_and_the_expected_result`, `LoopTests.test_one_ready_task_is_in_progress_at_a_time` |
| Do one task and check how the result works | `LoopTests.test_gate_blocks_then_allows_completion`, `StartRecordTests.test_the_finding_is_shown_beside_the_expected_result` |
| Keep the finding and clear the plan | `LoopTests.test_completing_a_block_keeps_the_finding_and_discards_the_hypothesis`, `DeclareTests` |
| Tasks with background agents | `ParallelTests` |
| Write a new plan, or finish when the goal is met | `TwoLoopTests.test_two_full_loops`, `LoopTests.test_goal_is_recorded_as_met_when_the_plan_is_empty` |

## 5. What the agent is told

One function, `Store.capsule()`, selects the text for the current state. Each
agent host sends that exact text.

- **Writing the plan.** The text has the goal, the findings, the completed tasks, and the
  instruction to write a new plan from them.
- **Choosing.** The text has the goal, the findings, the ready tasks with their reasons, and
  the instruction to choose the task whose result can change what is built next.
- **Working.** The text has the goal, the findings, the parent tasks with their reasons, and
  the task, which is what to do, the done condition, the reason, why it was chosen, the
  tasks that it depends on, and its checks.

The text for a task contains that task and its parents, so the agent's
attention stays on one task.

The planner gets every finding in full, so its text grows with each completed task. Reconsider how the findings are given to it when the text nears about 100,000 characters. Until then the full text keeps each plan justified by the whole record.

## The run

`agentic-agile run` (also `agile run`) is the engine that repeats the loop with fresh agents. A fresh planner writes each plan from the goal, the project and the findings. The engine starts the ready task with its alternatives and the expected result, gives the task to a fresh worker in its own worktree and branch, completes it with the worker's finding, and ends the round with a fresh reviewer when every task said `same`. The planner judges the goal met when its plan is empty. The goal check must then pass before the run reports the goal as met.

A worker's changes are committed on its branch `agile/TASK`. The commit leaves out build output (`__pycache__`, `*.pyc`, `.DS_Store`, `node_modules`, `.pytest_cache`) because the commit holds the worker's work and build output is a side effect of running it. The engine then merges the branch into the project.

A merge conflict ends the run with exit 12. The engine keeps the branch, aborts the merge, and records a surprise that starts with NOT MERGED. Every later planner sees it, so it knows that the finding describes work that is absent. After you merge the branch, the next `run` finds it in the project, deletes the branch, and records a decision that starts with MERGED.

When the planner judges the goal met and the goal check fails, the run records a blocker note with the output of the check and exits 19. When the check passes, the run exits 0. The session then records the goal with `root-complete`.

## What a fresh agent can see

Each plan comes from the evidence. So each planner starts with the goal, the
findings and the project, and with no link to the session that drives the run
or to an earlier plan. The same holds for each reviewer and worker, because
their output reaches the next planner.

| Agent | Text it gets | What it can reach |
| --- | --- | --- |
| Planner | The goal and its constraints, the completed tasks by name, every finding, and the engine's notes | The project folder with Read, Glob and Grep, and the commands `add`, `depends`, `gate-add` and `tree` |
| Reviewer | The goal, every finding, the engine's notes, the round's results with their expected results, and the remaining plan | The project folder with Read, Glob and Grep. It answers in its final message. |
| Worker | The goal, the recent findings, the engine's notes, its task with the planner's reason, and the tasks that it depends on | Its worktree with every tool. In the run's state it reads its own task and the status, and it records its own finding and notes. |

Each finding reaches these agents as the worker recorded it, with the worker's
reason for `changed` or `same`. The engine's notes state facts about the
project, such as a merge or the output of a failed goal check. The record keeps
the rest for the session and the user, who read it with `agentic-agile view`.
That rest is the result that an earlier planner expected, the notes of the
session, workers and reviewers, the reviewers' verdicts, the reason a task was
started, and the tasks it was chosen over. A constraint that the user adds with
`goal constrain` reaches every agent with the goal.

The engine starts each agent with the settings below. They live in
`src/agentic_agile/isolation.py`.

- The environment keeps the provider and credential variables and leaves out
  every variable that links a process to the session that started it. These
  are the Claude Code session ID with its messaging socket and token, and the
  cmux, tmux and editor sockets.
- Claude planners and reviewers run with `--safe-mode`, `--restricted`,
  `--strict-mcp-config` and `--tools Bash,Read,Glob,Grep`. Their input is
  their prompt and the project files alone, which leaves out CLAUDE.md files,
  skills, plugins, MCP servers and memory. Their file tools stay inside the
  project folder.
- Claude workers keep the project's settings and CLAUDE.md, because those
  describe the project. They run with auto memory off, `--strict-mcp-config`
  and the tools `Bash,Edit,Write,Read,Glob,Grep,Task`.
- Every Claude agent has the run's state folder and the host folders
  `~/.claude`, `~/.codex`, `~/.config/opencode` and `~/.local/share/opencode`
  closed to its file tools, and Claude also checks common read commands such
  as `cat` against these paths. A planner or reviewer has every
  `.agentic-agile` folder closed. A worker has the one in its worktree closed
  and can make a test state elsewhere.
- Codex agents run with hooks, plugins, memories, apps, browser use and
  computer use off, with `--ignore-user-config` and `--ephemeral`. OpenCode
  agents run with `--pure`.

The shell of a Codex or OpenCode agent, and a worker's shell on any host, can
read every file that the user can read. Their text and their environment hold
no path to the state folder or to a session, and the CLI gives each role only
what it needs. `docs/evidence/planner-isolation.md` records the test with
marker strings that checks each of these paths.

## 6. Storage

Each change is one JSON event file in `.agentic-agile/events/`. The files have
sequence numbers and are permanent. A SQLite file in the same folder is an
index. `agentic-agile rebuild` builds the index again from the events. The
index has a version number, and the tool builds it again automatically when
the version changes. Older events load with empty reasons.

Agentic Agile looks for the state folder in this order.

1. `--state PATH`, then the `AGENTIC_AGILE_STATE` environment variable;
2. `.agentic-agile/` in a checked-out worktree of a branch named `meta`;
3. `.agentic-agile/` at the top of the Git repository, or in the current
   folder for a project outside Git.

When the state folder is in a checked-out `meta` branch, Agentic Agile commits
each event immediately and stages exactly that event file. On other branches
it writes the event files and you control the commits.

## 7. Agent hosts

Each host does two things. It gives the agent the text from section 5 at the
start of a session. And when the agent reaches a stopping point while the
state is "writing the plan", "choosing", or "working", it sends that text
again as the next prompt. The agent chooses each task, and the choice is
recorded with its reason.

The text is short, because the run keeps the loop and this session keeps the
goal. Each text starts with a heading that names the state.

| Heading | When | What the session is told |
| --- | --- | --- |
| `THE RUN IS WORKING` | A run is alive | Its one status line, then follow it with `run --wait` |
| `START THE RUN` | No run, and work remains | Start it with `run --background` and follow it with `run --wait` |
| `RECORD THE GOAL` | The last run exited 0 and the plan is complete | Check the done condition, then run `root-complete` to record the goal |
| `A PERSON IS NEEDED` | No task can start | Tell the user what waits |

Once the goal is met, the hooks stay silent.

Agents that the engine starts run with hooks off, because a hook that waits for a person would look like a stall that the engine caused. Claude agents get `disableAllHooks` in `--settings` and `CMUX_CLAUDE_HOOKS_DISABLED=1`, codex agents get `--disable hooks`, and opencode agents get `--pure`. The hooks in the project `.claude/settings.json` therefore stay with the sessions of people. Claude agents also get `--include-hook-events`, so a hook that still runs shows in the stream and the monitor reports it as a wait on a hook. The section "What a fresh agent can see" lists the other settings of these agents.
The manual commands (`next`, `complete`, `replan`) stay available.

| Host | Start of session | When the agent reaches a stopping point |
| --- | --- | --- |
| Claude Code | `SessionStart` hook | `Stop` hook returns `{"decision": "block", "reason": text}` |
| Codex | `SessionStart` hook | `Stop` hook, same format. When the Codex goal for the thread is `active`, the Codex goal continues the session, because the skill sets that goal to the current task. |
| OpenCode | `experimental.chat.system.transform` on each model call | The plugin sends a prompt on `session.idle`, up to 8 times in a row. A message from the user starts the count again. |

`agentic-agile goal pause` pauses this on all hosts and keeps the plan as it
is. `agentic-agile goal resume` starts it again. The pause setting is in the
`runtime` table of the index, and it stays when the index is built again.

All displays read one snapshot, `agentic-agile view`, so they always show the
same task and the same goal state.

## The run in the background

`agentic-agile run --background` starts the engine detached in its own session and returns at once. Its log is `run.log` in the state folder. Only one run works on a state; a second `run` or `run --background` is refused while the first lives.

- `run --status` prints one line. While the run works it names the process, the step, the running tasks and the current activity. After it ends it reads `not running` and the last outcome with its exit code. Before any run it reads `no run yet`.
- `run --wait` follows the log for `--timeout` seconds (default 540, inside a 10 minute tool limit). It exits 20 while the run works. When the run ends it exits with the outcome's code, the same as a foreground run. Exit 18 means the run stopped without recording an outcome. Exit 21 means a report is open for a stuck or slow agent; the run keeps going, and the output holds the report with its evidence and the stop, extend and dismiss commands.

## What the engine does with a decision on a report

The engine stops no agent on its own. It obeys the top-level agent on each report of a worker. It reads the reports again on every pass of its loop.

- **Stop.** The engine ends the worker's process group, commits what the worker had in its worktree to its branch without merging it, and records the task as complete with a finding that names the report. The finding says the task did not finish, and it declares the understanding changed, so the next planner rebuilds the plan with that finding. The run goes on with the other tasks.
- **Extend.** The extra seconds are read again on every pass and added to the expected time of the task. A later overrun opens a new report.
- **Dismiss.** The same kind of report is not opened again for that task. The engine reads the dismissal from the store, so it holds in later attempts and after an engine restart. A dismissed slow report does not hide a stalled report, and a stalled report does not hide a slow one.

A retry of the same task is a new attempt with a fresh worker. When an attempt ends, the engine closes its open reports as `ended`, because they no longer describe a live agent, and the record keeps each one with its evidence. A dismissal by the top-level agent stays with the task. An `ended` report leaves the next attempt free to report the same kind, so the user hears again when the new worker is stuck or slow. The reports of a planner or a reviewer also close as `ended` when that agent finishes. Extra time that was given to the task stays, because it belongs to the task.

A report reaches the top-level agent at once. `run --wait` and `replan --wait` exit `21` with the report, its evidence and the three commands, and they exit `21` again until the report is decided. While a report is open and the goal is active, the Claude Code `hook-stop` holds the turn once, so the agent tells the user each report with its evidence. The next Stop ends the turn, and the session waits for the user's decision. The hook context, the band and the OpenCode sidebar show the open reports.

When a stop cannot complete the task because its checks still fail, the task stays delegated and the engine records a blocker note with the reason. The top-level agent then completes it by hand or discards the plan.

Silence on codex and opencode can be a long generation, because those hosts send no output while the model writes. The report gives the evidence, and the top-level agent can extend the time.

Lite installs copy the skill into the host folder, so they hold the old text until `install HOST --variant lite` runs again.
