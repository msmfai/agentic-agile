---
name: agentic-agile
description: Work toward a fixed goal with a plan that you write again after each task. Use when the user wants to set a goal, plan the work, run or continue Agentic Agile, see the current task, or finish; or when the user asks for a goal that is divided into ordered tasks and planned again as you learn.
---

# Agentic Agile

There are three things. Each one has its own rule.

- **The goal** belongs to the user. Keep it exactly as the user set it. The
  goal is met when its done condition is true and the user's evidence shows it.
- **The plan** is yours. It is the tasks that you think are necessary, in the
  order that you think is correct. It is an estimate. Each completed task
  clears the plan, and you write a fresh one.
- **The findings** are what completed tasks showed. The findings carry from
  one plan to the next.

`agile` is a short name for the same command. As a fallback, use the full path
`"${CLAUDE_PLUGIN_ROOT}/bin/agentic-agile"`.

## Your part: keep the goal and start the run

The tool carries out the loop. `agentic-agile run` writes each plan with a
fresh planner, starts each task with a fresh worker, records each finding, and
clears the plan after each task. You keep the goal and you are the one the
engine returns to when a person is needed.

Each planner, reviewer and worker starts with the goal, the findings and the
project, and nothing from this session. Your notes, your reasons and earlier
plans stay with you and the user, so each plan comes from the evidence alone.
When the user decides something about the scope during the run, record it in
the user's words with `agentic-agile goal constrain "TEXT"`. Every later agent
reads it with the goal. Record only what the user said.

1. **Set the goal with the user.** Run `agentic-agile status --json`. For a new
   project, ask the user for the result that they want and for a way to tell
   that it is met. Then run
   `agentic-agile init --goal ... --done ... [--constraints ...]`. Keep the
   goal exactly as the user set it.
2. **Start the run.** Run `agentic-agile run --background`. It returns at once
   and the engine works detached. When your shell allows long commands, plain
   `agentic-agile run` also works and returns the exit code itself.
3. **Follow the run.** Run `agentic-agile run --wait`. It follows the log for up
   to 540 seconds. Exit `20` means the run still works, so run `--wait` again.
   Any other exit code is the outcome. `agentic-agile run --status` prints one
   line with the step, the running tasks, and the last outcome.
4. **Act on the exit code.** Read the message that the run prints and do the
   action in this table. After the action, start the run again (step 2).

| Exit | Meaning | Your action |
| --- | --- | --- |
| `0` goal met | The planner judges the goal met, or the goal is recorded. | Check the goal's done condition yourself with its evidence. When it holds, run `agentic-agile root-complete --why "the evidence"`. When it does not, run `agentic-agile discard --why "what is missing"` and start the run again. When the message says the goal is recorded, tell the user and stop. |
| `10` human task | A task needs a person. The message names the task and its question. | Ask the user that question. With the answer, run `agentic-agile next TASK --why "the user answered" --expect "the answer"` and then `agentic-agile complete TASK --learned "the answer" --understanding changed\|same --because "why"`. Start the run again. |
| `11` worker failed | Two fresh workers ended without a result. A blocker note names the task, and the task stays in progress. | Run `agentic-agile view` and read the blocker. Do the task yourself when it is small. Otherwise run `agentic-agile complete TASK --learned "what the workers could not do and why" --understanding changed --because "the task needs a different shape"`; that keeps the failure as a finding, clears the plan, and the next planner splits the work. Then start the run again. |
| `12` merge conflict | A worker's changes conflict with the project. They stay on the branch that the message names, and a NOT MERGED note tells every later planner that the task's finding describes work absent from the project. | Merge that branch yourself with `git merge BRANCH`, resolve the conflict, and commit. Then start the run again; the run records a MERGED note and deletes the branch. |
| `13` limit | The run reached its step or time limit. | Tell the user what the run did (`agentic-agile view`). Continue with `agentic-agile run --background --max-steps N --max-minutes M` when the user agrees to more. |
| `14` blocked | Every open task waits for another task. | The plan has a cycle or a missing task. Run `agentic-agile tree`, then `agentic-agile discard --why "the plan is blocked"` (it clears the tasks that are waiting) and start the run again for a fresh plan. |
| `15` planner failed | The planner could not write the plan. | Read the message. Fix the cause (for example `agentic-agile planner --host H`), or write the plan yourself with `add`, `depends` and `gate-add`. Then start the run again. |
| `16` paused | The goal is paused. | Ask the user. Run `agentic-agile goal resume` when they say to continue. |
| `17` waiting | The running tasks belong to agents outside this run. | Wait for those agents, or `agentic-agile complete` the task when its done condition is true. Then start the run again. |
| `18` crashed | The run stopped without recording an outcome. | Read `run.log` in the state folder, fix the cause, and start the run again. |
| `19` goal check fails | The planner judges the goal met, and the goal's check command fails. A blocker note keeps the output, and every later planner reads it. | Read the output and start the run again. The next planner reads the check's output and writes tasks that make the check pass. Run `agentic-agile goal-check --run` to see the check again. |
| `20` still working | Only `run --wait` returns this. | Run `agentic-agile run --wait` again. |
| `21` report open | `run --wait` and `replan --wait` return this while an agent is stuck or slow. The run keeps going. The output names each report with its evidence and its commands. | Tell the user what the report says and decide with them. Run `agentic-agile report ID stop` to stop the task, `report ID extend --seconds N` to give it more time, or `report ID dismiss`. Then run `--wait` again. |

## When a report is open

The engine reports an agent that sends no output for 2 minutes while it runs
no command, and a task that runs past its expected time. It stops no agent on
its own. You decide with the user. Run `agentic-agile report list` to see the
open reports, each with its evidence.

- `agentic-agile report ID stop` ends the agent. For a worker, its changes stay
  on its branch and are not merged, and the task is recorded as complete with
  a finding that says it did not finish, so the next planner rebuilds the plan.
  A stopped task whose checks still fail stays delegated, and a blocker note
  says why. For a planner or reviewer, the run ends with exit `15`.
- `agentic-agile report ID extend --seconds N` gives the task more time. A later
  overrun opens a new report.
- `agentic-agile report ID dismiss` keeps that kind of report from opening again
  for the task. The dismissal holds in a retry and after an engine restart.
  When an agent ends by itself, the engine closes its open reports as `ended`,
  and a retry of the task reports again when it is stuck or slow.

Exit `21` carries the report, so read it before you run `--wait` again.
`run --wait` returns `21` every time while a report is open, until you decide.
The hook that runs when you try to rest holds the turn once while a report is
open. Tell the user each report with its evidence and the three choices, then
end your turn and wait for their answer. Only you and the user decide on reports. An
agent of the run is refused.

Silence from a codex or opencode agent can be a long generation, because those
hosts send nothing while the model writes. Look at the evidence and extend the
time when the work looks alive.

Lite installs copy this skill into the host folder. After the plugin updates,
run `agentic-agile install HOST --variant lite` again to copy the new text.

When `run` is refused because another run works, use `run --wait` or
`run --status` and wait for that run.

The hooks help you here. With a run working, the session shows the run status
line and rests. With no run, the hooks say START THE RUN with the goal and one
state line; follow step 2.

## The loop the run carries out

Know these steps, so you can read the findings and do a step by hand when the
engine returns it to you. Run `agentic-agile context` at any time to see what
the current state needs.

1. **Write the plan.** A fresh planner reads the goal, the project in its
   current state, and the findings, and none of the previous plan. Each task
   says what to do, how to tell that it is done, and why it helps to meet the
   goal.
   - `agentic-agile add ID --title T --objective WHAT-TO-DO --done HOW-TO-TELL --why REASON`
   - `agentic-agile depends TASK REQUIRED-TASK --why REASON-FOR-THIS-ORDER`
   - `--parent ID` puts a task below a `group` task.
   - `agentic-agile gate-add TASK --label L -- COMMAND` adds a check. The task
     is complete when the check passes.
   - Turn each open question into an `investigation` task and each question
     for the user into a `human` task.
2. **Choose a task.** Of the ready tasks, the one whose result can change what
   comes next teaches the most. `agentic-agile next TASK --why "the decision this result can change, and the other ready tasks compared" --expect "the result you expect"`
   records the alternatives and the expected result before the task starts.
   The tool shows the expected result beside the finding when the task ends.
3. **Do one task.** One task runs at a time unless `agentic-agile parallel N`
   allows more. Check how the result works, in addition to the tests. Put
   everything that can change a later decision into the finding, because a
   fresh planner reads the findings. Notes from `agentic-agile note` stay with
   you and the user.
4. **Record the finding and say whether the understanding changed.**
   `agentic-agile complete TASK --learned "what you learned, and the questions that are still open" --understanding changed|same --because "why"`.
   `changed` clears the plan. `same` keeps it after a fresh reviewer agrees
   with the finding.
5. **Plan again.** `agentic-agile replan` starts a fresh planner (add
   `--background` and then `--wait` when your shell stops long commands).

Work that you hand to a background agent with `agentic-agile delegate TASK --to NAME`
stays tracked: the task stays in progress until that agent records its finding
with `complete`.

## When you explain your work

When you tell the user what you do or how you do it, also tell them why. Say
which part of the goal the task helps, why it comes before the other tasks,
and what its result can change. The reasons are in the plan. Use them.

## Stop and continue

The agent host keeps the session active while there is something to do: a task
in progress, a task to choose, or a plan to write.

- Claude Code and OpenCode: the host does this for you.
- Codex: when the `create_goal` and `update_goal` tools are available, copy the
  current task into the Codex goal. After `agentic-agile next`, call
  `create_goal` with the task, its done condition, and its reason. After
  `agentic-agile complete`, call `update_goal` with the status `complete`.
- When the user tells you to stop or pause, run `agentic-agile goal pause` and
  stop. Run `agentic-agile goal resume` when the user tells you to continue.
  Complete a task when its done condition is true.
- When the plan waits for a person, tell the user what you need from them.

When new information shows that the plan needs to change before a task is
completed, run `agentic-agile discard --why TEXT` and write a new plan. Run
`agentic-agile tree` or `agentic-agile watch --once` to see the plan. Read
[protocol.md](references/protocol.md) for the exact rules.
