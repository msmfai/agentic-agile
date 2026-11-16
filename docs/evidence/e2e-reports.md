# Reports in a real headless run

Real Claude Code sessions (`claude -p`, sonnet, the plugin loaded from this worktree with `--plugin-dir`, hook events recorded with `--include-hook-events`) ran a goal in scratch git projects under `/tmp/e2e-reports`. Date 2026-11-14. The sessions ran with the worker variables unset, so the hooks were active.

## Setup

Each project has the goal "Create done.txt containing the word finished" with the check `grep -q finished done.txt`, and one ready task `slow-job` added with `--minutes 1`. The task tells the worker to run `python3 -c 'import time; time.sleep(N)'` in the foreground and then write `done.txt`. N is 600 for the stop run and 150 for the others. The prompt for the top-level session is "A goal is set in this project. Work it with Agentic Agile." The runs `stop2` and `dismiss2` add "If the run reports a stuck or slow agent, tell me what it says and wait for my decision. Do not decide on your own." A second session resumed with `--resume` carried the decision ("Stop it." or "Dismiss it."). The `extend` run had no such sentence, so the session chose by itself.

## What the first turn showed (stop2 and dismiss2)

1. The SessionStart hook said START THE RUN. The session ran `agentic-agile run --background` and then `agentic-agile run --wait`.
2. At 60 s the worker passed its expected time. `run --wait` returned **exit 21** with this text.

```
[run] report: slow slow-job: worker slow-job on claude ran 60 s, past the expected 60 s
Agents need a decision. The run keeps going until you act.

Report r1: worker on slow-job ran past its expected time
  Evidence: worker slow-job on claude ran 60 s, past the expected 60 s; silence 55 s; open command python3 -c 'import time; time.sleep(600)' for 58 s; 2 commands open; 5 events, last system; claude streams every event, so silence with no command means the model or the network did not answer
  Stop the task:   agentic-agile report r1 stop
  Give more time:  agentic-agile report r1 extend --seconds N
  Dismiss:         agentic-agile report r1 dismiss
```

3. The session told the user the report and the three choices and tried to end its turn. **hook-stop blocked once** with this reason (the hook text that carries the report).

```
# AGENTIC AGILE: THE RUN IS WORKING
running (process 67059): step 1, tasks running: slow-job; now: report: slow slow-job: ...
## Agents that need a decision
- r1: worker on slow-job ran past its expected time (worker slow-job on claude ran 60 s, ...)
## Instructions
The engine stops no agent on its own. Tell the user each report and its evidence, then end your turn and wait for their decision. For each report, stop the task, give it more time, or dismiss the report:
  agentic-agile report ID stop
  agentic-agile report ID extend --seconds N
  agentic-agile report ID dismiss
```

4. The session answered the block by repeating the choices. The next Stop returned only `{"systemMessage": "1 report open: worker on slow-job ran past its expected time"}` and the turn ended. **The block came once and did not loop.** This held in all four first turns.
5. The resumed session got the same report in its SessionStart hook text (hook-context carries the section "Agents that need a decision").

## The decisions

| Run | Decision | What the session ran | Result |
| --- | --- | --- | --- |
| stop2 (user said "Stop it.") | stop | `agentic-agile report r1 stop; agentic-agile run --wait` | "Report r1 stopped." The log says `slow-job: stopped on report r1`. A fresh planner wrote `write-done`, its worker finished, the planner judged the goal met, and the session ran `root-complete` after the Stop hook said RECORD THE GOAL. `report list --all` shows only r1 `[stopped]`. |
| extend (session chose alone) | extend | `agentic-agile report r1 extend --seconds 180; agentic-agile run --wait` | "Report r1 given more time by 180 s." No new report opened. `slow-job` finished its 150 s sleep and the goal was recorded. |
| dismiss2 (user said "Dismiss it.") | dismiss | `agentic-agile report r1 dismiss; agentic-agile run --wait` | "Report r1 dismissed." The run finished with exit 0 and no second report. `report list --all` shows r1 `[dismissed]`. |

After each decision the Stop hook moved to RECORD THE GOAL, the session checked `done.txt` itself, ran `root-complete`, and the next Stop returned `{}`.

## Defect found and fixed

In the first stop run (`stop`, same setup with N 600) the log read

```
[run] report: slow slow-job: ... ran 60 s ...        (r1)
[run] report: slow slow-job: ... ran 73 s ...        (r2)
[run] slow-job: stopped on report r1
```

The stop of r1 was only handled at the end of a watch pass. The same pass had already opened r2, because r1 was no longer open. The worker then ended, and r2 stayed open for an agent that no longer existed. The resumed session was asked about r2 and the Stop hook held it again, with "tasks running: none".

The fix in `src/agentic_agile/engine.py`. `_watch` now handles a stopped report before it checks for new findings, and `_stop` closes the other open reports of that worker as ended. Two tests in `tests/test_engine_watch.py` cover it (`test_stop_leaves_no_open_report_for_the_stopped_worker` and `test_stop_closes_other_open_reports_of_the_worker`). The full suite passes under `/usr/bin/python3` (240 tests). The `stop2` run above ran with the fix and showed only r1.

## Cost

About $0.08 per first turn and $0.03 to $0.10 per resumed turn, plus $0.10 to $0.14 for the engine agents in each run.

## Open points

- A session with no instruction to wait may decide by itself, as in the `extend` run (it read the evidence, saw a deliberate sleep, and extended). That is allowed by the skill, and the engine's reports reached it either way.
- The planner and reviewer path (exit 15 after a stop) was not run live here. Unit tests cover it.
- The engine ran the claude host only. Silence reports on codex and opencode were not run in this task.
- A stopped worker left `slow-job` complete with a finding, and the next planner wrote a task that did the work. That is the designed path, not a failure.
