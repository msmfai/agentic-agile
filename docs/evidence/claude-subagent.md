# Claude subagents that run a long Bash or stay silent

Recorded 2026-11-14 with Claude Code 2.1.325, `claude -p --model sonnet --output-format stream-json --verbose --include-partial-messages --include-hook-events --permission-mode bypassPermissions --setting-sources project --settings '{"disableAllHooks":true}'`, with each line stamped on arrival. Fixtures are `tests/fixtures/claude-subagent-busy-2.1.325.jsonl` and `claude-subagent-silent-2.1.325.jsonl`. Each row is `{"t": seconds since start, "line": the stream line}`. Partial messages are cut to 3 and long strings to 300 characters.

Busy case. The subagent ran `python3 -c "import time; time.sleep(40); print('done')"` (a bare `sleep 40` is refused by claude's Bash tool, so the first try recorded nothing useful).

| t (s) | Line |
| --- | --- |
| 2.0 | `assistant` `Agent` tool_use, then `task_started` `local_agent` |
| 3.2 | `assistant` `Bash` tool_use with `parent_tool_use_id` set to the Agent call id and an `agent_id` |
| 6.3 | `task_started` `local_bash` with `owned_by_subagent` true, `parent_task_id`, and the Bash `tool_use_id` |
| 32.0 | `tool_progress` with `heartbeat` true, `tool_name` Agent, `elapsed_time_seconds` 30 |
| 43.3 | `task_notification` for the Bash task, then its `tool_result` |
| 44.2 | `task_notification` for the subagent, then the Agent `tool_result` |

Silent case. The subagent used no tools and wrote a 3000 word essay. The main stream carried nothing from it for 75 s except `tool_progress` heartbeats at 33 s and 63 s. The subagent's text appeared only when it finished (77.9 s). Partial messages of a subagent do not reach the stream.

Findings

- A heartbeat is `type` `tool_progress` with `heartbeat` true, sent every 30 s for each open Agent call. It is a timer of the parent, not proof that the subagent works.
- Monitor result, busy case. The subagent's Bash opens at its `tool_use` and closes at its `tool_result`, and the `task:<id>` entry closes at its `task_notification`. Three hours of silence while it runs gives no report. Correct as designed.
- Monitor result, silent case, before the fix. Every line moved the silence clock, heartbeats too, so a hung or very slow subagent would never be reported. Fix made in monitor.py. Heartbeat lines no longer count as output. In the recording, the last real line came at 3.0 s, so the report opens at 123 s (kind `stalled`, no command open). That recording ended at 77.9 s, so no report came. A subagent that writes for longer than 120 s will be reported, which is the stated rule and the evidence says so.
- Report text, before the fix. It said "worker on claude" and did not name the task or the subagent. Fix made. It now reads `worker task-1 on claude ...`, and adds `inside subagent <description> of this worker run` for a command inside a subagent, or `subagent <description> of this worker run open for N s` when no command is open. Descriptions come from the Agent call's `input.description`.
- The Agent call stays open until its `task_notification`, not its `tool_result`. In one earlier trial claude backgrounded the subagent, so the Agent `tool_result` came at once and the work ran on. That trial was not saved.
- A subagent's nested commands map to their Agent call through `parent_tool_use_id`, so a parallel pair of subagents is told apart.

Open questions

- Whether a subagent that is truly hung keeps its heartbeats going. Not measured, and the fix works either way.
- A silent subagent writing for more than 120 s is reported as stalled although it works. The top-level agent can dismiss or give time, as designed. No fix planned.

## Hung subagent command

Recorded 2026-11-14 with Claude Code 2.1.325 and the same flags. The subagent ran `python3 -c "import time; time.sleep(600)"` and nothing else. The run was killed at 200 s. Fixture is `tests/fixtures/claude-subagent-hang-2.1.325.jsonl`, same row format as above.

| t (s) | Line |
| --- | --- |
| 2.1 | Agent `tool_use`, then `task_started` `local_agent` |
| 3.7 | subagent `Bash` `tool_use` with `parent_tool_use_id` set |
| 6.7 | `task_started` `local_bash` |
| 32, 62, 92, 122, 152, 182 | `tool_progress` with `heartbeat` true, `tool_name` Agent, `elapsed_time_seconds` 30 to 180 |

Findings

- A hung subagent command is a quiet stream. After 6.7 s nothing arrives except the heartbeats, which went on every 30 s to the end of the recording. This answers the open question above for a hung Bash.
- Monitor result. The Bash call and its `task:<id>` entry stay open, heartbeats do not count as output, so no `stalled` report comes, even 3 h on. With no expected time nothing is reported. With an expected time (tested with 120 s) the report is `slow`, and its evidence names `time.sleep(600)`, `inside subagent hang test` and the worker run. So the open Bash is detected as busy and reported as overrun, as expected.
- Consequence. For a hung subagent command the only signal is the task's expected time. The engine must always give a task an expected time, since the silence rule cannot fire while a command is open.
- No change to monitor.py. The test is `test_hung_subagent_command_is_busy_then_overrun`.
