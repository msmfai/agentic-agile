# etime in `ps` on Debian procps, Alpine BusyBox and macOS

Task `inv-linux-etime`, checked 2026-11-14. It answers open question 4 of `opencode-parallel.md` and its BusyBox gap.

## Method

The call is `ps -A -o pid=,ppid=,stat=,etime=,args=`. Each container ran with `--pid=host`, so `ps` listed every process of the Docker VM. The VM had been up for 428 884 s (4 days 23 hours), which gives processes older than one day, older than one hour and younger than one hour in one listing. A probe script added a zombie (a child that exits while its parent stays alive and never waits) and a few young processes. Images were `debian:bookworm-slim` with `procps` installed (`ps from procps-ng 4.0.2`) and `alpine:3.22` (BusyBox `ps`, which has no version flag). The full outputs are the fixture files `tests/fixtures/ps-procps-4.0.2.txt`, `tests/fixtures/ps-busybox-1.37.txt` and `tests/fixtures/ps-macos.txt` (host names in `sshd` rows were replaced by `user`).

## Raw rows

Debian procps 4.0.2. Elapsed time is right aligned in an 11 column field.

```
      1       0 Ss    4-23:08:03 /sbin/init
    495     491 S     4-23:08:00 (sd-pam)
 795530       2 I       21:47:10 [kworker/2:2-events]
 838602       2 I       16:31:06 [kworker/1:2-inet_frag_wq]
 975028  974799 S          00:01 sleep 300
 975032  975028 Z          00:01 [sh] <defunct>
 975038  974799 R          00:00 ps -A -o pid=,ppid=,stat=,etime=,args=
```

Alpine BusyBox.

```
    1     0 S     4d23   {systemd} /sbin/init
  495   491 S     4d23   (sd-pam)
918329     2 IW    6h44   [kworker/0:0-ine]
947776     2 IW    3h07   [kworker/u8:0-ev]
972346     2 IW    9:59   [kworker/u8:3-ev]
973676     2 IW    0:45   [kworker/2:1]
975122 975101 S     0:02   sleep 300
975126 975122 Z     0:02   [sh]
975132 975101 R     0:01   ps -A -o pid= ppid= stat= etime= args=
```

macOS (`ps` from the system) matches procps, for example `1 0 Ss 19-13:36:35 /sbin/launchd`.

## What the formats are

| Age | procps and macOS | BusyBox 1.37 |
| --- | --- | --- |
| under 1 hour | `00:01`, `59:59` (`mm:ss`) | `0:02`, `9:59` (`m:ss`) |
| 1 hour to under 1 day | `16:31:06` (`hh:mm:ss`) | `3h07`, `16h31` (hours, `h`, minutes) |
| 1 day or more | `4-23:08:03` (`d-hh:mm:ss`) | `4d23` (days, `d`, hours) |

The BusyBox forms for hours and days were not what open question 4 expected. That note said BusyBox prints `[[dd-]hh:]mm:ss`. It does not. The `replay.py` function `et` raises `ValueError` on `4d23` and `3h07`, so a parser built from it would crash on any BusyBox process older than one hour. The old reading was only right below one hour.

BusyBox also drops precision as the age grows. Above one hour it floors to the minute and above one day it floors to the hour, so `4d23` means 4 days 23 hours up to 59 min 59 s. That is no problem for a process that has run longer than 1 hour (the error is below 1 hour against an age above 1 day and below 1 minute against an age above 1 hour). It does matter for a process of an hour or more that is meant to give a start time to the second. The detector only needs start times of commands that started in the last few minutes, and those print `m:ss` with 1 s precision.

## Zombie and parenthesised rows

- Zombies have stat `Z` on both. procps prints args as `[sh] <defunct>` and BusyBox prints `[sh]` with no marker, so the stat column is the only portable test. Their etime parses like any other row. The zombie keeps the age of the child, not of the parent.
- Rows such as `(sd-pam)` and `(udev-worker)` are processes that rewrote their own name. Both `ps` builds print the name inside parentheses, with the same etime columns. Because `args` is the last column, parentheses, brackets and spaces in it cannot disturb the other four columns.
- Kernel threads print as `[name]` with parent 2 and are ordinary rows.
- BusyBox adds `{systemd}` in front of the args of pid 1 and BusyBox keeps stat letters such as `IW` or `IW<` that are longer than one letter. `stat` has no spaces on either build, so one whitespace split of four fields followed by the rest works.

## Parser

`src/agentic_agile/procs.py` has `parse_etime` and `parse_ps`. `tests/test_procs.py` tests each format: the value table of every shape, the procps fixture, the BusyBox fixture (days, hours, minutes, zombie, parenthesised row), the macOS fixture, and noise lines. It runs without sqlite, so it passes where `test_core.py` cannot import (Python 3.14 here has no `sqlite3`; `test_install` fails the same four tests with or without this change).

## Open questions

- BusyBox boundaries were not hit. The change from `59:59` to `1h00` and from `23h59` to `1d00` is inferred from the three forms seen and is covered in the unit test only as parsing. A process aged exactly 1 hour was not observed.
- Ages of 100 days or more were seen only in the parser tests. Procps prints `123-01:02:03` per its source and BusyBox presumably prints `123d05`. Neither was run.
- Only procps 4.0.2 (Debian bookworm) and BusyBox from Alpine 3.22 were run. The version of BusyBox is the one that ships with that image. An older procps (3.3) was not checked.
- `ps -A` in a container without `--pid=host` shows only the container. That is what the engine sees too.
- No `ps` parser lived in `src` before this task. The monitor should use `procs.parse_ps` instead of the `et` copy in the evidence script.
