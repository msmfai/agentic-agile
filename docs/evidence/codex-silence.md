# Silence on codex and opencode without a command

Question. How long can codex and opencode send no line while they run no command, which item types appear in worker-style runs, and does a 120 s stall rule give false reports? Date 2026-11-14, codex-cli 0.153.4 (`codex exec --json`, workspace-write sandbox), opencode 1.17.18 (`--pure`, `-m deepseek/deepseek-flash`), macOS.

Files are in `docs/evidence/codex-silence/`. `record.py` runs a host and stamps each stdout line with its elapsed time. `gaps.py` prints the longest gap and the types seen. Each run is one recording (`*.jsonl`, columns elapsed, epoch, line).

## Silent runs, no command

| Run | Host | Total | Longest gap | What ended the gap |
| --- | --- | --- | --- | --- |
| think (long reasoning prompt) | codex | 78.8 s | 78.3 s | final `agent_message` (2213 reasoning tokens) |
| gen (4000 word essay) | codex | 307.1 s | 306.6 s | final `agent_message` |
| think | opencode | 116.7 s | 115.0 s | `text` (23377 reasoning tokens) |
| gen (4000 word essay) | opencode | 55.3 s | 53.5 s | `text` |
| gen2 (12000 word essay asked) | opencode | 111.7 s | 109.1 s | `text` |

Facts.

1. Codex writes no line while the model reasons or generates. The only lines are `thread.started`, `turn.started`, then nothing until the whole `agent_message` and `turn.completed` arrive. No `reasoning` item appeared in any run, only a `reasoning_output_tokens` count in the final usage. The silent gap was 306.6 s, so a plain 120 s rule would have reported this healthy run as a stall.
2. Opencode writes `step_start`, then nothing until the whole `text` and `step_finish`. Reasoning gave 115 s and a 12000 word request gave 109 s. The model stopped on its own output limit, so these runs sit just under 120 s. A slower model day or a longer limit would cross it. The gap has the same shape as on codex, so the same conclusion holds.
3. Neither host emits partial messages in this mode. Silence is a long generation as often as it is a stall.

## Worker-style run

Prompt: create `hello.py`, create `notes.txt`, edit `hello.py`, run it.

| Host | Total | Longest gap | Types seen |
| --- | --- | --- | --- |
| codex | 13.5 s | 5.1 s (before the first file change) | `command_execution`, `file_change` (kinds `add` and `update`), `agent_message`, `error` |
| opencode | 9.8 s | 1.6 s | `step_start`, `tool_use` (tools `bash`, `write`, `edit`), `text`, `step_finish` |

Facts.

1. Codex edits are `file_change` items, not `patch`. No `reasoning` item and no `patch` item appeared.
2. Every `item.started` closed with an `item.completed` within 0 to 50 ms for `file_change` and `command_execution`. No item stayed open without a command. This run had only quick commands, so a long command still shows as an open `command_execution` and must count as busy.
3. Opencode reports a file edit as a `tool_use` with tool `write` or `edit`, written at completion. These are not shell commands, so no child process runs for them. They finish in under 1 s.
4. Codex also emits two `item.completed` rows of type `error` at the start. The text is a hook trust warning from the local setup. A detector must not treat them as failures.

## Answer

The 120 s rule gives false reports on both hosts when the agent only thinks or writes. Codex shows it clearly, with 307 s of healthy silence. Opencode came within 5 s of the limit. Worker-style runs gave no false reports, since gaps were 5 s or less and no item stayed open.

What this means for the work.

1. The report text for codex and opencode must say that silence can be a long generation, as the goal expects. The report states evidence and leaves the decision to the top-level agent.
2. A silence rule on codex has no open item to rely on during generation. A longer limit for codex (several minutes) or a report that is labelled as possibly a long generation both fit the evidence. The engine must not stop the agent.
3. An open `command_execution` item is a reliable busy signal on codex. Open `file_change` items were never long.

Open. Only one model per host was tried. Codex with the highest reasoning effort and a very long output may be silent for longer. Opencode with a model that has a larger output limit may exceed 120 s. Claude hosts were not part of this run.
