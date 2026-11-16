# Command reference

`agentic-agile` and `agile` are the same command. Every command takes
`--state PATH` (or the `AGENTIC_AGILE_STATE` variable) to use a specific state
folder, and `--cwd PATH` to work on a different project.

| Command | Purpose |
| --- | --- |
| `init --goal TEXT --done TEXT [--constraints TEXT]` | Set the goal. Do this one time. |
| `add ID --title T --objective WHAT --done HOW --why WHY [--parent ID] [--kind KIND]` | Add a task to the plan. |
| `depends TASK REQUIRED [--why TEXT]` | `TASK` starts after `REQUIRED` is complete. |
| `gate-add TASK --label TEXT -- COMMAND ARG...` | Add a check. `TASK` is complete when the command passes. |
| `next [TASK] --why TEXT --expect TEXT` | Start one ready task and print its instructions. The tool records the other ready tasks; you give the reason and the expected result. |
| `complete [TASK] --learned TEXT --understanding changed\|same --because TEXT` | Run the checks, complete the task, and save the finding. `changed` clears the plan; `same` keeps it after a fresh reviewer agrees. |
| `replan [--host H] [--model M] [--timeout S] [--background \| --wait]` | Have a fresh planner write the next plan and start the first task. `--background` returns at once; `--wait` waits for that planner and prints its result, or exits `21` with the report text when a report is open. |
| `run [--host H] [--model M] [--max-steps N] [--max-minutes M] [--background \| --wait \| --status] [--timeout S]` | Run the loop with fresh planners, workers and reviewers until a person is needed. `--background` starts it detached and returns at once; `--wait` follows it and exits `20` while it works, exits `21` with the report text while a report is open, else with the outcome's code; `--status` prints one line. |
| `planner [--host H] [--model M] [--timeout S] [--command CMD]` | Show or set the planner and reviewer for this machine. A planner or reviewer that runs past the limit (600 s for planners, at most 300 s for reviewers), or that sends no output for 120 s while it runs no command (600 s on codex), gets a report. The engine stops nothing on its own. The run goes on until it ends or you stop it with `report stop`, and `report extend` gives it more time. `AGENTIC_AGILE_STALL` changes the silence limit. Silence on codex and opencode can be a long generation. |
| `report list [--json] [--all]` | Show the open reports of stuck or slow agents with their evidence. `--all` includes resolved reports. |
| `report ID stop` | End the agent of a report. A worker keeps its changes on its branch without a merge, and its task is recorded as complete with a finding that says it did not finish. A task whose checks still fail stays delegated, and a blocker note says why. A planner or reviewer that is stopped ends the run with exit `15`. |
| `report ID extend --seconds N` | Give the task `N` more seconds. A later overrun opens a new report. |
| `report ID dismiss` | Close the report. The engine keeps that kind of report closed for the task, also in a retry and after an engine restart. The reports of an agent that ends by itself close as `ended`, and a retry reports again. |
| `delegate TASK --to NAME [--why TEXT] [--expect TEXT]` | Hand a ready task, or the task in progress, to a background agent. |
| `parallel [N]` | Show or set how many tasks can run at the same time. |
| `discard --why TEXT` | Clear the tasks that are still waiting. |
| `note finding\|surprise\|evidence\|decision\|blocker TEXT [--node TASK]` | Record a note for the session and the user. A fresh planner, reviewer or worker reads the findings of completed tasks and the engine's own notes, so put what can change the plan into the finding. |
| `goal-check [COMMAND] [--run]` | Show the goal's check, set or change it to `COMMAND`, or run it now. A command with shell operators such as `&&`, `||`, `;` or `|` runs through `sh -c`, and a plain command runs as its own argv. `root-complete` and the run's goal-met outcome both need it to pass. |
| `root-complete --why TEXT` | Record that the goal is met. |
| `context [TASK]` | Print the current instructions for the agent, or the instructions for one task. |
| `tree` / `watch [--once]` | The plan / a live view with the current task and the findings. |
| `statusline` / `view [--graph]` | One line for status bars / the same snapshot as JSON. |
| `goal [pause\|resume]` | Show, pause, or resume the goal. |
| `goal constrain TEXT` | Add a constraint that the user decided during the run, in the user's words. The outcome and the done condition stay as set. Every later planner, reviewer and worker reads it with the goal. |
| `install` / `uninstall HOST [--scope S] [--variant V]` | See [INSTALL.md](../INSTALL.md). Lite installs copy the skill, so run `install HOST --variant lite` again after an update. |
| `status` / `frontier` / `rebuild` | State summary / ready tasks / build the SQLite index from the events. |

## Task kinds

| Kind | Use it for |
| --- | --- |
| `task` | Ordinary work. The default. |
| `investigation` | Work that answers a question. |
| `validation` | A check that something already built still works. |
| `human` | A question for the user. |
| `group` | One reason for a set of tasks. It is complete when the tasks below it are complete. |

## Exit codes

`0` means success. `2` means the command needs something first, and the
message says what. `install` and `uninstall` exit `1` when a step needs your
attention, and they print that step on a line that starts with `!`.

`run` and `run --wait` return the reason the engine handed control back.

| Exit | Reason | Next action |
| --- | --- | --- |
| `0` | Goal met, or goal recorded | Check the goal's done condition, then `root-complete` |
| `10` | A task needs a person | Ask the user, then `next` and `complete` that task |
| `11` | A worker did not finish its task | Read the blocker in `view`; do the task, or `complete` it as `changed` so the next plan splits it |
| `12` | A worker's changes conflict with the project | Merge the named branch with `git merge`, then run again. The run keeps a NOT MERGED note on the finding until it finds the branch merged, and then it records a MERGED note. |
| `13` | Step or time limit | Report to the user; run again with larger limits if they agree |
| `14` | Every open task waits for another | `discard` and run again for a fresh plan |
| `15` | The planner could not write the plan | Fix the planner or write the plan by hand |
| `16` | The goal is paused | `goal resume` when the user agrees |
| `17` | Running tasks belong to other agents | Wait for them or `complete` the task |
| `18` | The run stopped without an outcome | Read `run.log`, then run again |
| `19` | The planner judges the goal met and the goal check fails | Read the blocker note with the output, `discard` what is missing, run again |
| `20` | `run --wait` only: the run still works | Run `--wait` again |
| `21` | `run --wait` and `replan --wait`: a report is open, and the run keeps going | Read the report with its evidence. Stop the task with `report ID stop`, give it more time with `report ID extend --seconds N`, or run `report ID dismiss`. Then run `--wait` again. `hook-stop` holds the turn once while a report is open, so the agent tells the user, and the next Stop waits for the user's decision |

## Worker commits

A worker does its task in its own worktree on the branch `agile/TASK`. When
the task is done the run commits the worker's changes as `agile: TASK`. The
commit leaves out build output, which is `__pycache__`, `*.pyc`, `.DS_Store`,
`node_modules` and `.pytest_cache`. The run then merges the branch with
`agile: merge TASK`. A conflict gives exit `12`.

## The goal check

`init --check COMMAND` or `goal-check COMMAND` sets a command that runs in the
project folder. The goal is recorded only when it exits 0. A plain command
runs as its own argv, and a command with shell operators runs through `sh -c`.

## The planner text

The planner gets every finding in full, so its text grows with each completed
task. Reconsider how the findings are given to it at about 100,000
characters.

## Hook texts

The session hooks start with one of four headings. `THE RUN IS WORKING` shows
the run's status line. `START THE RUN` tells the session to run
`run --background` and `run --wait`. `RECORD THE GOAL` follows a run that
exited `0` and tells the session to check the done condition and run
`root-complete`. `A PERSON IS NEEDED` says that no task can start.
