# Installing Agentic Agile

A person or a coding agent can follow this page. Agents that install Agentic
Agile into their own harness start at "For agents".

## One command

```sh
curl -fsSL https://raw.githubusercontent.com/msmfai/agentic-agile/main/install.sh | sh -s -- all
```

This clones Agentic Agile to `~/.local/share/agentic-agile` and installs it
into every supported agent on your `PATH`. Replace `all` with `claude`,
`codex`, or `opencode`, and add any of the options below. From an existing
checkout, run `bin/agentic-agile install ...` directly.

## Choices

```text
agentic-agile install HOST [--scope global|project] [--variant full|lite] [--project DIR] [--print]
agentic-agile uninstall HOST [--scope global|project] [--project DIR] [--print]
```

| Option | Values | Meaning |
| --- | --- | --- |
| `HOST` | `claude`, `codex`, `opencode`, `all`, `self` | `self` is the agent that runs the command. |
| `--scope` | `global` (default), `project` | All projects, or the one project at `--project` (default: current folder). |
| `--variant` | `full` (default), `lite` | `full` adds the host UI. `lite` installs the skill and the goal hooks as plain files. |
| `--print` | | Preview every step. |

You can run `install` again at any time. It replaces its own entries and keeps
yours. To change the variant or the scope, run `install` with the new options.
`uninstall` removes exactly what `install` added.

## What each combination writes

| Host | Variant | Global | Project |
| --- | --- | --- | --- |
| Claude Code | full | Plugin `agentic-agile@agentic-agile`, user scope | Same plugin, enabled in `.claude/settings.json` |
| Claude Code | lite | Hooks in `~/.claude/settings.json`, skill in `~/.claude/skills/agentic-agile/` | Hooks in `.claude/settings.json`, skill in `.claude/skills/agentic-agile/` |
| Codex | full | Plugin `agentic-agile@agentic-agile` | Same as lite, because Codex plugins are global |
| Codex | lite | Hooks in `~/.codex/hooks.json`, skill in `~/.agents/skills/agentic-agile/` | Hooks in `.codex/hooks.json`, skill in `.agents/skills/agentic-agile/` |
| OpenCode | full | `plugins/agentic-agile.js`, `skills/agentic-agile/`, and a `tui.json` entry under `~/.config/opencode/` | The same three under `.opencode/` |
| OpenCode | lite | `plugins/agentic-agile.js` and `skills/agentic-agile/` | The same two under `.opencode/` |

Each host shows the loop in its own way.

- **Claude Code full** has a bar above the prompt (current task, progress, goal
  state, Pause and Resume) and a plan pane (`/agile-graph`). It needs Claude
  Code 2.1.287 or later.
- **OpenCode full** has an indicator beside the prompt, a sidebar section with
  the plan, and a toast when the task changes.
- **Codex** gives the live view with `agentic-agile watch` in a side pane, in both
  variants.
- **Lite on all hosts** gives the view with `agentic-agile statusline` and
  `agentic-agile watch` in the terminal.

`agile` is the short name for `agentic-agile`. The `bin` folder of the
checkout holds both, so every command in these pages works with either one.

After the installation, restart the agent. In Codex, also run `/hooks` one
time and trust the two Agentic Agile hooks. Codex runs hooks that you trust.

## For agents

The user asked you to install Agentic Agile into your own harness. Do these
steps.

1. **Get a checkout.** A checkout is a folder that contains
   `bin/agentic-agile`. To make one, run
   `git clone https://github.com/msmfai/agentic-agile ~/.local/share/agentic-agile`.
2. **Confirm two choices with the user.** These are all projects or this project, and
   full (with UI) or lite (plain files).
3. **Preview.** Run this command and show the user the output.
   `~/.local/share/agentic-agile/bin/agentic-agile install HOST --scope SCOPE --variant VARIANT --print`
   Use the name of your own harness for `HOST` (`claude`, `codex`, or
   `opencode`). Naming the harness is the most reliable choice. `self` works
   when the harness identifies itself in the environment.
4. **Install.** Run the same command again, this time with `--print` removed.
   Exit code 0 means success. A line that starts with `!` shows a step that
   needs attention.
5. **Tell the user** to restart the harness. For Codex, also tell the user to
   trust the hooks with `/hooks`. The new hooks and the UI appear in the next
   session.

Let the installer make all changes to the harness settings. When the installer
prints a line that starts with `!`, stop and show the user that line. It names
a file that the user manages by hand.

## The loop runs in the background

In Claude Code and Codex, the session hooks tell the agent to start the run
with `agentic-agile run --background` and to follow it with
`agentic-agile run --wait`. The run plans, works and reviews with fresh agents
of the planner host, so the host needs `claude`, `codex` or `opencode` on the
`PATH`. `agentic-agile planner` shows the host and sets it. A goal can carry a
check that runs in the project folder, set with `init --check COMMAND`.
Commands with shell operators such as `&&` run through `sh -c`. The test suite
needs a Python 3.9 or later that has `sqlite3`, and it clears the
`AGENTIC_AGILE_*` variables of the session that runs it.

## Verify

```sh
agentic-agile install HOST --print     # shows where each file goes
agentic-agile statusline               # prints the status line in a project that has a goal
```

In Claude Code, `claude plugin list` shows `agentic-agile@agentic-agile` for a
full install. In Codex, `/hooks` lists the two hooks. In OpenCode full, the
indicator appears beside the prompt on the home screen of a project that has
a goal.
