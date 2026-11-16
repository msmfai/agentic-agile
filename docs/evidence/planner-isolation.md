# What a fresh planner can reach

Each planner must start from the goal, the findings and the project, so that
its plan is independent of the session that drives the run and of the plans
before it. This note records the test of that, before and after the change
that added `src/agentic_agile/isolation.py`. Date 2026-11-15, Claude Code
2.1.325, sonnet, macOS.

## Method

`leak-test.py` in this folder plants a marker string in each place where a
fresh agent could pick up information from outside its brief.

| Marker | Where it is | What it stands for |
| --- | --- | --- |
| `CANARY-MEMORY` | `~/.claude/projects/<project>/memory/MEMORY.md` | The auto memory of a session in the same project folder, such as the top-level session |
| `CANARY-TRANSCRIPT` | `~/.claude/projects/<project>/fake-session.jsonl` | A transcript of the top-level session |
| `CANARY-INPROJECT-STATE` | `.agentic-agile/events/` in the project | Event files that hold every earlier plan |
| `CANARY-OLDPLAN` | A discarded task in the run's real state | An earlier plan |
| `CANARY-SESSION-NOTE` | A note that the session recorded | The top-level agent's own idea about the plan |

It then runs a real headless planner and a real worker with the argv and the
environment that the engine builds. Each agent quotes any marker in its
context, tries to read each place with its file tools and its shell, and runs
`view`, `context` and `tree` from the CLI. The script prints the markers that
appear anywhere in the agent's output stream.

## Before the change (commit 9d2c64a)

The planner ran with `--setting-sources project`, `disableAllHooks`, and the
session's environment minus `CLAUDECODE` and `CLAUDE_CODE_ENTRYPOINT`.

| Path | Result |
| --- | --- |
| Memory | `CANARY-MEMORY` was in the planner's context at start. A session in the same project folder shares its auto memory with every planner. |
| Event files in the project and outside it | Both read with the Read tool |
| Session transcript | Read with the Read tool |
| Session tools | `ListAgents` loaded, `SendMessage` deferred, and the parent's messaging socket and token in the environment |
| CLI | `view` and `context` showed the record, with the reasons of earlier tasks and the tasks they were chosen over |
| Planner text | Held the session's notes, the reviewers' verdicts, which name tasks of the earlier plan, and the result that each earlier planner expected |

## After the change

| Path | Planner | Worker |
| --- | --- | --- |
| Memory | Off, no marker in context | Off, no marker in context |
| Event files in the project | Refused, closed path | Refused, closed path |
| The run's state folder | Refused, closed path | Refused, closed path |
| Session transcript and memory files | Refused, closed path (Read and `cat`) | Refused, closed path (Read and `cat`) |
| Grep of the project for markers | No match | No match |
| CLI `view`, `context old` | Refused, "this is a planner; it runs add, depends, gate-add, tree" | Refused, "a worker records only its own result" |
| CLI `tree` | Shows the goal and the current task only | Refused |
| Session tools | Tools are `Bash, Glob, Grep, Read`, no MCP servers | Tools are `Task, Bash, Edit, Glob, Grep, Read, Write`, no MCP servers |
| Markers anywhere in the stream | None | None |

A worker's shell shows its own `CLAUDE_CODE_SESSION_ID` and messaging socket.
A comparison of hashed values showed that the session ID, the socket and the
token all differ from those of the parent session, so they belong to the
worker's own Claude process.

Planner text now holds each finding with the worker's reason, the engine's
notes, and the constraints from the user. Tests in `tests/test_isolation.py`
check each part of the text, each CLI role, the environment and the closed
paths.

A Codex planner started with the new flags (`--disable` for hooks, plugins,
memories, apps, browser use and computer use, `--ignore-user-config`,
`--ephemeral`) and answered normally.

## What stays open

- The shell of a Codex or OpenCode agent, and a worker's shell on any host, can
  read any file that the user can read, for example with a script. Claude
  checks common read commands such as `cat` against the closed paths. Nothing
  in the agent's text or environment names the state folder or a session.
- OpenCode reads its global `AGENTS.md` when one exists. None exists on the
  test machine.
- Project files are shared on purpose. A worker that writes notes about the
  plan into the project, such as an evidence file that names tasks, passes
  them to later planners through the project. The worker's role text asks it
  to name tasks and plans only in its finding.
