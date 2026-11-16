# Headless Claude Code run 2

Real Claude Code sessions (`claude -p`, the plugin loaded from this repository with `--plugin-dir`, hook events recorded with `--include-hook-events`) worked a small goal in a fresh git project. Date 2026-11-14. The sessions ran with the worker variables unset, because a session that inherits them keeps the hooks silent by design.

The project holds `events.log` with 3000 lines. Most lines start with an ISO timestamp. 115 lines near the end use `DD/MM/YYYY HH:MM`, and the last line of the file is one of them. The README says only ISO timestamps exist.

Goal. `newest.py` prints the timestamp of the newest event in `events.log` as `YYYY-MM-DDTHH:MM:SSZ`, and `python3 -m unittest` has a test for it. The goal check is `python3 -m unittest && test "$(python3 newest.py)" = "2026-11-30T23:59:00Z"`, stored as `sh -c` because it has `&&`.

## Events

| Seq | Event | Detail |
| --- | --- | --- |
| 1–2 | aim.created, settings.changed | set by the top-level session with `init --check` |
| 3–7 | node.added, dependency.added | plan 1 from a fresh planner (`inv-formats`, `newest`, `test`) |
| 8 | node.delegated `inv-formats` | reason "rank 1, decides what parsing newest.py needs"; expected result recorded before the start |
| 9–10 | node.completed `inv-formats`, hypothesis.discarded | declared **changed**. The worker found the second format and an unordered log, so the plan was cleared |
| 11–13 | node.added, dependency.added | plan 2 from a fresh planner, written with the finding (`impl-newest`, `test-newest`) |
| 14–15 | node.delegated and node.completed `impl-newest` | declared `same` |
| 16 | plan.reviewed | a fresh reviewer answered HOLDS |
| 17–18 | node.delegated and node.completed `test-newest` | declared `same`, 4 tests pass |
| – | the planner is asked a third time | it writes no tasks and judges the goal met, and the goal check passes (run exit 0) |
| 19 | aim.completed | recorded by the top-level session after it checked the result itself |

The run wrote two plans with tasks, and a third planner call found nothing left to add. The first plan was cleared by the `changed` declaration, and the second plan was written with the `inv-formats` finding in its input.

## Stop hook outcomes

- **Rest.** The session started the run in the background and ended its turn. The Stop hook answered `{"systemMessage": "running (process N): step 1, ... a fresh planner is writing it"}`, so the session rested and the run went on alone (seen in the ledger toy session on the same plugin build, with the real hook output recorded).
- **Hold.** After the run ended and before the goal was recorded, the session tried to stop. The Stop hook answered `decision block` with the goal and the next action. The session then checked `python3 newest.py` and the tests, ran `root-complete`, and the hold ended.

## Cost and time

| Part | Cost | Time |
| --- | --- | --- |
| Top-level session that set the goal and started the run | $0.18 | 10 s, 5 turns |
| Planners, workers and reviewer in the run | $0.47 | about 7.5 min |
| Resumed session that was held and recorded the goal | $0.40 | 14 s, 4 turns |
| Earlier resume that exposed the defect below (9 holds) | $0.32 | 19 s |

Total about $1.4. Two earlier runs on other toy goals (a log of events and a ledger with a semicolon file) reached goal met through one plan each. They had no `changed` declaration, because the planner read the data first.

## Transcript summary

The first session used the skill, read `status --json`, ran `init --goal ... --done ... --check ...`, then `run --background`, and ended its turn with one sentence. The resumed session answered the hold by checking the files itself and calling `root-complete` with the evidence.

## Defects found and fixed

1. **A hold after a finished run said START THE RUN.** With every task complete and the last run ended as goal met, the Stop hook told the session to start the run again. A resumed session held nine times in a row. The hold now says RECORD THE GOAL, names `root-complete`, and says not to start the run again. Test `test_after_a_run_judged_the_goal_met_the_hold_says_to_record_it_not_to_start_the_run`.
2. **The goal-met reason started in the middle of a word.** The planner's answer was cut with `answer[-600:]`. It now keeps the end and starts at a word (`planner.tail_text`). Test `test_a_long_goal_met_reason_keeps_its_end_and_starts_at_a_word`.
3. **`run --status` printed several lines.** The last outcome carried the whole planner answer. It is now one line of at most 300 characters. Test `test_the_last_outcome_is_one_short_line_after_a_long_multiline_reason`.

What this run does not cover. The Stop hook rest and the hold were shown in separate resumed sessions, not within the first session, because a headless session ends on its first rest.
