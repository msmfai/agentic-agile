# Which opencode child processes count as a running command

Question. opencode gives no start signal for a tool call. The detector therefore reads `ps` rows below the opencode pid. Helpers that opencode starts for itself must not count as a command, or a real stall is hidden. Date 2026-11-14, opencode 1.17.18, macOS, model `deepseek/deepseek-flash`, run without `--pure`, with the user's global config loaded.

Setup. The project has an `opencode.json` with a local MCP server (`mcp.py`, tool `slow_echo` waits 10 s) and a fake LSP server (`lsp.py`, for `.fake` files). Files are in `docs/evidence/opencode-children/`. The prompt for the LSP run was to write `x.fake`, run `sleep 8`, edit `x.fake`. The MCP run called `slow_echo` once. Rows were sampled every 0.25 s.

## Helper rows seen

| Row | Starts | Ends | Notes |
| --- | --- | --- | --- |
| `Python mcp.py` (the MCP server) | 4.0 s | exit of opencode | Starts during startup, 1.6 s before the first event. Alive before, during and after the 10 s tool call. |
| `Python lsp.py` (the LSP server) | 7.3 s | exit of opencode | Starts lazily when the first `write` touches a `.fake` file, after the first event. Stays alive through the rest of the run. |
| `cmux hooks opencode session-start` and `... stop` | all run long | 0.3 to 1 s each | Started by the user's global plugin about every 3 s, also while idle. |
| `(git)`, `(env)`, `(Python)` | brief | one sample | Short rows seen at tool boundaries. They are exec transitions, not commands. |
| `<defunct>` rows | startup | | Zombies from the plugin. |
| `sleep 8` | 14.6 s | 22.5 s | The real command. A direct child, with no `zsh -c` wrapper, because the shell execs its last command. |

During the MCP tool call (7.3 s to 17.1 s) the only row below opencode was the MCP server itself. The tool call is plain stdio and starts no child.

## Findings

1. MCP servers start before the first event, so a baseline taken at the first event removes them. The earlier prototype did this.
2. LSP servers start lazily, after the first event. The baseline rule counts them as a command for the rest of the run. In the LSP replay it reported 17.3 s of false busy over a 32.5 s run.
3. A name ignore list cannot cover this. Both servers are ordinary `python` or `node` rows, the same shape as a command a worker runs. The list is still needed for the plugin rows (`cmux`), which flap every few seconds and would reset an idle clock.
4. A shell wrapper rule (count only `sh -c ...` children) does not work. The earlier recordings show `sleep 6` as a direct child.

## Chosen rule

Count a child as a running command only when all three hold.

1. It is not defunct, not an `(name)` exec row, and not on the name ignore list (`cmux`, plus the existing Codex helper names).
2. It was started after the most recent event from the host. Use the process start time from `ps` (`lstart`), not the time it was first seen, with one second of slack.
3. It is alive now.

Do not make a counted child sticky. A sticky LSP would stay counted until exit.

Why this works. opencode emits `step_start` just before it runs a tool, so a command always starts after the latest event. A helper that started earlier is older than the latest event and drops out. The cost is one bounded window. An LSP that starts after the last event counts as a command until the next event arrives. If opencode hangs in that window (a write waiting on diagnostics lasted 5 s here), the stall is hidden. A monitor can accept this, because the window opens only at an LSP start.

## Replayed result

`python3 replay_rules.py mcp lsp` replays each rule against the recorded events and rows. Older runs from the earlier detector work (simple, background, multi) were replayed with the same script.

| Rule | MCP run false busy | LSP run false busy | Missed command time on older runs |
| --- | --- | --- | --- |
| name ignore list only | 15.4 s | 20.6 s | 0 |
| baseline at first event plus list | 0 s | 17.3 s | 0 |
| started after last event plus list (chosen) | 0 s | 4.7 s | 0.2 s, 0.6 s and 1.0 s of 6 to 15 s |
| the same without the `cmux` entry | 2.4 s | 8.9 s | not measured |

The false busy that remains in the chosen rule ends at the next event. The missed time is the start of a command that began in the same 0.1 s as an event, and it is shorter than any stall limit.

## Open questions

- A real language server (typescript-language-server, pyright, gopls) was not installed, so the fake LSP stands in. A real one may also spawn its own children. They would be older than the latest event once the next event arrives, so the rule covers them, but this was not run.
- Parallel tool calls were not recorded on opencode. If one tool finishes while another still runs, the running one started before the latest event and would be ignored. The multi-command run was sequential. The detector task should record a parallel run before relying on the rule, or accept a report that a user can dismiss.
- Remote MCP servers and servers started through `sh -c npx ...` look like commands. They start at startup, so the rule drops them.
- Linux `ps` start times (`lstart`) were not checked here. That is the `inv-linux-ps` task.
