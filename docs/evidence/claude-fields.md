# Claude stream-json field names

Recorded 2026-11-14 with Claude Code 2.1.325, `claude -p --model sonnet --output-format stream-json --verbose --include-partial-messages --include-hook-events`. The task asked for a foreground Bash call, a `run_in_background` Bash call and an Agent subagent. The full recording is `claude-fields-run.jsonl` (113 lines). The trimmed test fixture is `tests/fixtures/claude-stream-2.1.325.jsonl` (partial-message lines cut to 3).

Every name monitor.py reads matches the recording. No change to monitor.py was needed.

| Field monitor.py reads | Real line (recording line number) |
| --- | --- |
| `tool_use` block with `id`, `name`, `input.command` | 11 `assistant` with `Bash` `toolu_01B1CN2f...` (foreground), 25 background Bash, 42 `Agent` |
| `tool_result` block with `tool_use_id` | 16 `user`, same id as line 11. Line 31 answers the background call at once, while the task keeps running |
| `parent_tool_use_id` | `null` on every main-agent line. Line 46 (a `user` line from inside the subagent) holds the Agent call id, with `agent_id` and `subagent_type` |
| `system` `task_started` with `task_type` `local_bash`, `task_id`, `description` | 30 (`b6pewxj3v`, `is_backgrounded` true, `tool_use_id` of line 25) |
| `task_started` with `task_type` `local_agent` | 44 (subagent, `spawn_depth` 1, `tool_use_id` of line 42) |
| `system` `task_notification` with `task_id`, `status` | 80 closes the background command, 49 closes the subagent, 83 closes a local_bash task |
| `system` `hook_started`, `hook_response` | 1 and 2 (SessionStart), 109 to 112 (Stop). Fields `hook_id`, `hook_name`, `hook_event`, `exit_code`, `outcome` |
| `result` | 113 |

Things the recording shows that matter for wiring

- A foreground Bash call has no `task_started`. It is open from its `tool_use` to its `tool_result` only.
- A background Bash call gets its `tool_result` at once (line 31) and a `task_started` right after the `tool_use` (line 30, before the result). The task id ends it. The monitor keys these as `task:<id>`, so the open command survives the tool_result. Correct.
- A plain `sleep 8; cat ...` Bash call also produced a `task_started` and `task_notification` pair (lines 82 and 83, `is_backgrounded` false) around its own tool_use and tool_result. Both mechanisms close, so nothing stays open.
- Subagents show as `task_started` with `task_type` `local_agent`. monitor.py ignores them, and a subagent's lines still reach the stream with `parent_tool_use_id` set, so they count as output.
- Between `tool_use` and `tool_result` of a short command, `task_updated` and `background_tasks_changed` lines appear. monitor.py treats them as output only.
- `stream_event` lines (partial messages) and `rate_limit_event` lines arrive often. The first counts as alive without a state change, the second is an ordinary event.

Open question. A subagent that runs a long Bash command of its own writes a `tool_use` with `parent_tool_use_id` set. monitor.py records it as open like any other, which is what is wanted. This was not recorded with a long command.
