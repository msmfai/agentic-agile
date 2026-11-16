# Live claude planner and reviewer through run_headless

Real claude sessions (claude via the cmux shim, `--setting-sources project`, `--settings {"disableAllHooks":true}`, `--include-hook-events`, `CMUX_CLAUDE_HOOKS_DISABLED=1`, stream-json with partial messages) on a toy wordcount project. Date 2026-11-14. `AGENTIC_AGILE_STALL=5` throughout, so a false stall report would show with only 5 s of silence.

## Runs

| Run | What | Result |
| --- | --- | --- |
| 1 | `replan --host claude --timeout 40` on the toy plan | 16 s, 4 tasks written, $0.06, **no report** |
| 2 | `run_headless` role planner, expected 25 s, prompt runs two `python3 time.sleep` commands (30 s and 20 s, claude ran them side by side) | 53 s, exit 0, **one slow report r1** at 25 s, evidence `ran 25 s, past the expected 25 s; silence 20 s; open command python3 -c "import time; time.sleep(30)" for 23 s; 2 commands open; 5 events, last system`. No stalled report in 20 s of stream silence, because the open command counts as busy. The planner was not stopped and the report stayed open |
| 3 | Real planner prompt, every stream line timed | 24 s, 482 lines, longest gap with no command open 3.8 s (the final answer) |
| 4 | `planner.review` role reviewer, timeout 300 | answered CHANGES in 3 s, no report |

## Verdict

- **A report opens through run_headless and nothing is stopped.** Run 2 opened exactly one slow report, once, and the process ended by itself with exit 0.
- **Stream-only command silence causes no false report.** Claude emits tool_use and tool_result in the stream, so a long command is busy for the whole time. With a 5 s limit, runs 1, 3 and 4 gave no stalled report. Partial messages keep the stream moving while the model writes. The longest quiet gap was 3.8 s, so the real 120 s limit has about 30 times of room.
- **Expected times are safe but loose.** Real planner runs took 16 to 24 s on a small plan, the reviewer 3 s. The 600 s planner time and 300 s reviewer time are 25 to 100 times the observed time, so a slow report means something is wrong. A large project plan will take longer, and the report can be extended, so keep both values until a long real plan is timed. The only guard for a hung command with open stream is the slow report, as `inv-subagent-hang` found.
- **Not covered.** A real stalled report (model or network silence over the limit) cannot be made on demand with claude. It is covered by fixture tests only. `replan` runs for under a minute here, so the 600 s case was not reached live.

## Tasks or fixes for a false report

None needed. Open question: time a planner on a project with many files, to check that 600 s still leaves room.
