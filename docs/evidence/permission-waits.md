# Permission waits on codex, opencode and claude

Question. Can an agent in a headless engine run wait for a person at a permission request, and can the report evidence name that wait. Date 2026-11-14, macOS, claude 2.1.325, codex 0.153.4, opencode 1.17.18 (model `deepseek/deepseek-flash`), cmux 0.64.22 with its shims first on PATH. Probes ran in scratch folders under `/tmp` and sampled `ps` once a second.

## Result in one table

| Host | Headless permission request | Waits for a person | What the stream and ps show |
| --- | --- | --- | --- |
| codex `exec --json` | none can be made | no | The session policy is `never`. The model says escalation is disabled. The command fails at once with `Operation not permitted`. |
| opencode `run --format json` without `--auto` | made for `external_directory` | no | opencode rejects it on its own after 1.5 s. The tool part ends with status `error`. There is no wait and no child process. |
| claude `-p` with the cmux hooks active | made, and the cmux hook holds it | yes, 115 s here | `hook_started PermissionRequest` stays open. One `cmux hooks feed --source claude` row lives under the agent. |

So only claude can show a permission wait, and it is a hook wait. The name rule from `headless-hooks.md` already covers it.

## codex

I ran `codex exec --json --skip-git-repo-check` with a prompt that wrote to `$HOME` (outside the writable roots) and asked for escalation. I tried `-s read-only` and `-s workspace-write` with `-c approval_policy=on-request`, and also `on-failure`. Each run ended in 8 to 12 s.

```
{"type":"turn.started"}
{"type":"item.completed","item":{"type":"agent_message","text":"The sandbox blocked the command: `Operation not permitted`. ... I can't request escalation because this session's approval policy is set to `never`."}}
{"type":"turn.completed", ...}
```

The `-c` value is read but `exec` still reports `never`. `approval_policy=untrusted` is refused at start (`no longer supported`). `--approve-for-me` routes asks to an automatic reviewer and not to a person. With it, a write to `/tmp` needed no ask at all because `/tmp` is writable. No process other than codex and the shell command ran in any probe, and `ps` showed no hook child, since the engine passes `--disable hooks`.

So `codex exec` has no permission wait. A PermissionRequest hook cannot fire either, because nothing asks. The only codex wait for a person that was measured is a user hook on a turn (`headless-hooks.md`), and the engine switch removes it. Not tested here is the model tool that asks the user a question, which `exec` may also refuse.

## opencode

Run `opencode run --pure --format json -m deepseek/deepseek-flash` with no `--auto`, from `/tmp/pw/o`, asking the bash tool to `touch /tmp/pw/outside-o.txt`.

```
stderr   ! permission requested: external_directory (/tmp/pw/*); auto-rejecting
stdout   step_start
stdout   tool_use  tool=bash  state.status=error
         error="The user rejected permission to use this specific tool call."
stdout   step_finish reason=tool-calls
```

The whole run took 2.6 s and the file was not made. Without `--auto`, `opencode run` rejects every ask by itself and names the rule on stderr. The ps sample showed only `opencode run` and the timer, with no child. The engine passes `--auto`, so asks are approved and no wait forms. An `opencode serve` or `--attach` session with a person at a screen could hold an ask, but the engine does not use it.

An ordinary stream for opencode carries no wait event. Its `error` status plus the text `rejected permission` is the one trace, and it comes after the fact.

## claude with the cmux hooks

The earlier probes saw no cmux hooks under the wrapper. The cause was found here. The worker environment sets `CMUX_CLAUDE_HOOKS_DISABLED=1`, and the wrapper then passes through with no injected settings. With `env -u CMUX_CLAUDE_HOOKS_DISABLED` the wrapper injects its settings, which hold nine hook events including `PermissionRequest` with `"${CMUX_CLAUDE_HOOK_CMUX_BIN:-cmux}" hooks feed --source claude` and a timeout of 125.

Command, run from a scratch folder with no other switches.

```
claude -p 'Run exactly this shell command: A=touch; $A /tmp/pw/cl/x.txt' \
  --output-format stream-json --verbose --include-hook-events \
  --allowedTools 'Bash(agentic-agile:*)'
```

The allow rule cannot match, so the command needs approval. Stream times are from the first line.

| Time | Stream |
| --- | --- |
| 0.0 s | `hook_started` and `hook_response` for SessionStart (two hooks) and UserPromptSubmit |
| 0.2 s | `system init` |
| 2.4 s | `assistant` tool call, `hook_started PreToolUse:Bash` and its response, then `hook_started PermissionRequest:Bash` |
| 2.4 s to 117.4 s | no line on stdout |
| 117.4 s | `hook_response PermissionRequest:Bash` with outcome `success`, output `{}`, exit 0, then `permission_denied` |
| 121.2 s | `result success` |

The hook closed itself after 115 s, before the 125 s timeout, with an empty answer, and claude then denied the command. Nobody answered in the cmux feed, and the empty answer means cmux gave no decision.

Process rows, the same pid in all 107 samples from 3.2 s on.

```
95206  95004  SNs  00:01  .../cmux.app/Contents/Resources/bin/cmux hooks feed --source claude
```

The `etime` column showed `00:01` in the first sample and grew. The row is a child of the agent. The command text does not say that it waits for a person. It would count as a running command in a monitor that only looks for live children, and the 2 minute silence rule would have fired at 2 minutes of silence, nearly the same moment the hook ended.

## What the report evidence can name

1. claude. The stream names it exactly. An open `hook_started` with `hook_event` `PermissionRequest` (or `hook_name` `PermissionRequest:Bash`) is a wait for a person when a `cmux hooks feed` row is alive. The report can say "waits on a PermissionRequest hook, command `cmux hooks feed --source claude`". It needs `--include-hook-events`. Without the flag the wait looks like silence.
2. codex. No permission wait exists under `exec`. The report need not name one. If a `cmux` or `.cmux/hooks` row shows up, it is a hook wait and not a permission wait.
3. opencode. No wait exists under `run`, with or without `--auto`. The report need not name one. If a run is silent with no child, the cause is the model or the network and the report should say so.
4. Process rows alone cannot say "permission" on any host. They can say "hook" when the text holds `cmux hooks` or `.cmux/hooks/`. The word "permission" comes only from the claude stream.

Effect on the wiring tasks. The wording `waits for a person` applies to claude hook events only. Do not build a codex or opencode permission rule. The engine should still keep `disableAllHooks` and `CMUX_CLAUDE_HOOKS_DISABLED=1` for claude agents, so this wait does not occur in engine runs, and the report rule stays as a guard for runs that miss the switch.

## Open questions

- The empty `{}` answer at 115 s may come from a cmux feed timeout shorter than 125 s, or from a closed feed. It was not traced. A wait could last up to 125 s, or 600 s with many requests, as in the earlier surprise run.
- A person who answers in the cmux feed was not simulated.
- codex tools that ask the user a question, and codex `app-server`, were not tested. The engine uses `exec`.
- opencode with a server and an attached screen holds asks. It was not run because the engine does not use it.
