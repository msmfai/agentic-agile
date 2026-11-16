# Keeping user hooks and permission prompts out of engine agents

Question. Interactive hooks (cmux and similar) can wait for a person. A headless agent has none, so the wait lasts until the hook times out and looks like a running command. Which switch removes the hooks for each host, and how does a monitor name a hook wait. Date 2026-11-14, macOS, claude 2.1.325, codex 0.153.4, opencode 1.17.18, cmux 0.64.22 with its shims for `claude` and `codex` first on PATH. Probes used a scratch project and `/tmp` scripts that sample `ps` every 0.1 to 1 s.

## Switch per host

| Host | Switch | Tested live | Result |
| --- | --- | --- | --- |
| claude | `--settings '{"disableAllHooks":true}'` | yes | Through the cmux wrapper with default sources, SessionStart and Stop hooks fired without it and no hook fired with it. With a PermissionRequest hook set in `--settings`, no `hook_started` appeared and the denial came at 2.7 s. It also removes hooks from the project `.claude/settings.json`. |
| claude | `CMUX_CLAUDE_HOOKS_DISABLED=1` in the child env | yes | The wrapper then execs the real claude with no injected hooks. It only covers cmux, and it must be in the child env (the shim reads it). |
| claude | `--setting-sources project` | yes, not enough | It drops user settings hooks, but `--settings` is always applied. A replica of the cmux hook JSON passed with `--settings` still fired SessionStart, UserPromptSubmit and Stop under `--setting-sources project`. |
| claude | `--permission-prompts none` | yes, not enough | A PermissionRequest hook from `--settings` still started and blocked for its full timeout (10 s of a 10 s limit) before the denial. |
| claude | `--bare` | not run | The help says it skips hooks, but it also skips settings, CLAUDE.md and the keychain login, so it is too broad. |
| codex | `--disable hooks` | yes | With a trusted hook (`--dangerously-bypass-hook-trust` in a scratch `CODEX_HOME`) the hook ran and the run took 12.7 s. With `--disable hooks` the hook did not run and the run took 4.2 s. A fresh profile does not run untrusted hooks at all. |
| codex | `CMUX_CODEX_HOOKS_DISABLED=1` | partly | The shim execs the real codex with no injected hooks. `codex exec` is a session entry, so the shim does inject for it. The user's own `~/.codex/hooks.json` has cmux hooks that stay on without `--disable hooks`. |
| opencode | `--pure` | yes | Without it, `cmux hooks opencode ...` rows appeared at 1.3 s and the run took 25.8 s with `sleep 7` starting at 11.9 s. With it, no cmux row appeared, the run took 12.1 s and `sleep 7` started at 4.3 s. `--auto` already answers permission asks. |

The engine already passes `--disable hooks --disable plugins` to codex and `--pure --auto` to opencode. Only claude needs a change. The worker and the planner and reviewer should add `--settings '{"disableAllHooks":true}'` and set `CMUX_CLAUDE_HOOKS_DISABLED=1` in the child env. They should keep `--setting-sources project`, which still keeps user settings, user plugins and user permission rules away and is the reason to keep it. It works with `--output-format stream-json`.

## The hook wait, reproduced

With a `PermissionRequest` hook of `cmux hooks feed --source claude` (timeout 12) in `--settings`, run through `claude -p` with `--allowedTools 'Bash(agentic-agile:*)'` and the prompt `A=touch; $A /tmp/x` (a command the allow rule cannot match), the stream was:

| Time | Event |
| --- | --- |
| 0.5 s | `system init` |
| 2.7 s | `assistant` tool call, then `system hook_started PermissionRequest` |
| 2.7 s to 14.7 s | nothing on stdout |
| 14.7 s | `system hook_response PermissionRequest cancelled`, then `permission_denied` |

`cmux hooks feed --source claude` run by hand with a PermissionRequest payload also blocked until the 20 s timeout. In the shipped cmux hook the timeout is 125 s, and the surprise run saw 600 s with many requests in a row.

## Signals that name a hook wait

1. Claude stream, only with `--include-hook-events`. `hook_started` with `hook_event` set to a name, closed by a `hook_response` with the same event. An open `hook_started` means the agent waits on a hook. Without the flag the stream is silent, so the engine should pass it to claude agents.
2. Process rows. The hook is a child of the agent. Claude runs it directly (`cmux hooks feed --source claude`, or `sleep N` for a generic hook). Codex runs it from `~/.cmux/hooks/cmux-codex-hook-*.sh` or the hook command (a `sleep 8` child at 1.3 s). opencode shows `cmux hooks opencode ...` and a `(cmux)` exec row. A generic hook command is an ordinary row, so a name rule cannot cover every hook. A rule on the command text (`cmux` and `hooks`, or the path `.cmux/hooks/`) names the known ones.
3. Codex `--json` and opencode `--format json` have no hook events. A codex hook ran before `turn.started`, and the stream was silent for the whole wait.

If the engine disables hooks, a remaining `hook_started` or a `cmux hooks` row is a signal in itself. The report should say "waits on a hook" and not "runs a command", and the report text should name the hook command.

## Open questions

- The cmux wrapper hooks did not appear in a wrapped `claude -p --setting-sources project` run here, though a manual replica of the same JSON did. The wrapper decision for `-p` runs was not traced. The surprise run did stall, so do not rely on that gap. Use `disableAllHooks`.
- A real cmux PermissionRequest wait with the shipped 125 s timeout was not run to the end. The 12 s and 20 s runs show the same shape.
- codex hook waits on permission requests were not run, because `codex exec` with `workspace-write` made no approval request in the probes. Only a UserPromptSubmit wait was measured.
- opencode permission waits were not tested, since `--auto` answers them.

## Decision and cost

Hooks stay off for every claude planner, reviewer and worker. The engine passes `--settings '{"disableAllHooks":true}'`, sets `CMUX_CLAUDE_HOOKS_DISABLED=1` in the child env, keeps `--setting-sources project` and adds `--include-hook-events`. The cost is that hooks in the project `.claude/settings.json` do not run for engine agents either. A project that needs one of them must do the work in the engine or in the task text. The monitor still reports an open `hook_started` as a wait on a hook, as a guard for a hook that runs despite the switch.
