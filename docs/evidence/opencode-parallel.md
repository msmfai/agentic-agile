# Parallel tool calls on opencode, and reading process start times on Linux

Question. The opencode detector counts a child process as a running command only when it started after the latest event. Does that hold when opencode runs tools in parallel? How does the detector read a process start time on Linux and macOS? Date 2026-11-14, opencode 1.17.18, macOS, model `deepseek/deepseek-flash`, run with `--pure`.

Files are in `docs/evidence/opencode-parallel/`. `record.py` runs opencode and samples `ps` every 0.25 s. `replay.py` replays a rule over the new runs. `replay_old.py` replays over the runs in `opencode-children/` and the earlier detector runs.

## Parallel run

The prompt asked for three bash calls in one turn (`sleep 6`, `sleep 14`, `sleep 24`). opencode ran all three at once.

| Time | What happened |
| --- | --- |
| 1.2 s | `step_start` |
| 3.4 s | All three commands start. The last child shows at 3.7 s. |
| 9.3 s | `tool_use` for `sleep 6`, then its row ends |
| 17.4 s | `tool_use` for `sleep 14`, then its row ends |
| 27.5 s | `tool_use` for `sleep 24`, `step_finish` |

Two facts follow.

1. opencode writes `tool_use` only when the tool completes. There is no start event, and parallel tools all start after the same `step_start` and before any `tool_use`.
2. After the first `tool_use` at 9.3 s, the other two commands started before the latest event. The rule "started after the latest event" drops them. For 18.2 s of the 24.1 s that a command ran, the detector saw no command. With a 2 minute stall limit, a parallel batch whose long tool outlasts the short one by more than 2 minutes would be reported as a stall that is not one.

## Rule that fits

Use the latest `step_start` as the reference time, not the latest event of any kind. Count a child when it is alive, passes the existing name filter, and started no more than 1 s before that `step_start`. All tools of a step start after its `step_start`. A parallel batch is then covered until the next `step_start`, and the oldest running child decides nothing extra, because every child of the step is counted.

Replay against `ps` ground truth (a `sleep N` row is present):

| Run | Rule | Command time | Missed | False busy |
| --- | --- | --- | --- | --- |
| parallel 3 tools (28.7 s) | latest event | 24.1 s | 18.2 s | 0 |
| parallel 3 tools | latest `step_start` | 24.1 s | 0 | 0 |
| parallel 2 tools then one more step (23.7 s) | latest event | 19.6 s | 12.5 s | 0 |
| parallel 2 tools then one more step | latest `step_start` | 19.6 s | 0 | 0 |
| earlier simple, background, multi runs | latest event | 11.8, 6.0, 15.3 s | 0.2, 0.6, 1.0 s | 0 |
| earlier simple, background, multi runs | latest `step_start` | same | 0, 0, 0 | 0 |
| MCP run | either | 0 | 0 | 0 |
| fake LSP run | latest event | 7.9 s | 0 | 4.7 s |
| fake LSP run | latest `step_start` | 7.9 s | 0 | 6.4 s |

The `step_start` rule also removes the small misses at a step edge. Its one cost is the fake LSP case. A language server that starts mid step counts as a command until the next `step_start`, 1.7 s longer than before in this run, and a step with a long model pause would stretch it. That window opens only when a server starts and closes at the next step.

Not covered. A command that a step starts in the background (`cmd &`) drops out once the next `step_start` arrives. A tool that spans several steps does not exist, since a step ends only after its tools finish.

## Reading a process start time

macOS (`ps` from the system) prints `lstart` as `Sat Nov 14 13:27:12 2026`, local time, 1 s resolution. It has no `etimes`.

Linux checked in containers, 2026-11-14.

| `ps` | `etimes` | `etime` | `lstart` | Notes |
| --- | --- | --- | --- | --- |
| procps-ng 4.0.4 (Debian) | `3` | `00:03` | `Sat Nov 14 13:28:54 2026` | `lstart` follows `TZ` (`TZ=America/New_York` printed 08:28 for a 13:28 UTC start). Slim images have no `ps` until `procps` is installed. |
| BusyBox 1.37 (Alpine) | not supported | `0:04` | not supported | `-p` is not supported. `-A` is accepted. `command` is not a column, `args` is. |

`lstart` is a poor choice. It depends on the time zone of the process that runs `ps`, it needs date parsing, and BusyBox lacks it. `etimes` is missing on macOS and BusyBox.

Portable read. Run `ps -A -o pid=,ppid=,stat=,etime=,args=` once per sample. All three `ps` versions accept it (checked on macOS, procps 4.0.4 and BusyBox 1.37). Parse `etime` as `[[dd-]hh:]mm:ss`, where BusyBox prints `m:ss`, and take `start = sample time - elapsed seconds`. The sample time is the wall clock read just before `ps` runs. Elapsed time is floored to whole seconds, so the computed start is up to 1 s late. The 1 s slack in the rule covers it. This avoids time zones and date parsing. `replay.py` uses this read and gives the same replay results as `lstart`. Build the child tree from `pid` and `ppid`, as the recorder does.

On Linux `/proc/PID/stat` field 22 (clock ticks since boot) plus `btime` from `/proc/stat` gives a start time without `ps`. It was read here (`CLK_TCK` 100) but it is not needed.

## Open questions

- BusyBox `etime` was seen only below one minute. The format for hours and days came from reading the parser, not from a run. The parser accepts `m:ss`, `hh:mm:ss` and `dd-hh:mm:ss`.
- Only Debian procps and Alpine BusyBox were checked. Other Linux `ps` builds were not.
- opencode ran with `--pure` here. The `cmux` plugin rows were not part of the parallel runs and stay on the ignore list.
- Whether opencode ever splits a parallel batch across two `step_start` events was not seen in two runs. If it did, the later step would start its own commands after its own `step_start` and the rule would still hold.
- Remote or agent subtasks (opencode's task tool) were not recorded.
