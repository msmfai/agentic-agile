# Working on Agentic Agile

Guidance for coding agents and people who change this repository. The code is
small: it uses the Python standard library, and each concern has one home.

## Map

| Path | What it owns |
| --- | --- |
| `src/agentic_agile/core.py` | The model and the loop. Events, the SQLite index, which tasks are ready, completing a task and clearing the plan, `view()`, and `capsule()`: the text that the agent gets in each state. |
| `src/agentic_agile/render.py` | **Every word and symbol shown anywhere.** Marks, goal labels, colors, status line, tree, dashboard. |
| `src/agentic_agile/cli.py` | Argument parsing, the `hook-context` / `hook-stop` host hooks, `watch`. |
| `src/agentic_agile/hosts.py` | Reads from hosts: the hook input, and the Codex goal. |
| `src/agentic_agile/isolation.py` | What a planner, reviewer or worker that the engine starts can reach: its environment, its host flags, and the paths closed to it. |
| `src/agentic_agile/install.py` | `install` / `uninstall` for each host, scope, and variant. |
| `skills/agentic-agile/SKILL.md` | What the agent is told to do. Shared by all three hosts. |
| `hooks/hooks.json`, `hooks/codex.json` | Goal hooks for the Claude Code and Codex plugins. |
| `hooks/register.tsx`, `types/index.d.ts` | Claude Code bar and pane. Draws `agentic-agile view --graph`. |
| `opencode/agentic-agile.js` | OpenCode goal hook: gives the agent its instructions and continues on idle. |
| `opencode/agentic-agile-tui.tsx` | OpenCode sidebar and prompt indicator. Draws `agentic-agile view --graph`. |
| `docs/protocol.md` | The exact rules. Update it when behaviour changes. |
| `media/loop.py` | Generates `media/loop.svg`, the loop diagram in the README. Edit the script and run it. |

## Common changes

- **Change a symbol, label, or color** (for example "goal active" to
  "working"): edit `MARKS` or `GOAL` in `render.py`. The UIs draw
  `view.display` and the `mark` / `tone` / `prefix` of each task, so the bar,
  pane, sidebar, status line, and dashboard all follow. Keep every label in
  `render.py`.
- **Show a new fact** (for example, elapsed time on the task): add it to
  `Store.view()` in `core.py`, format it in `render.display()`, then draw it
  where you want it. Add the field to `types/index.d.ts` for the Claude Code
  module.
- **Change the Claude Code bar or pane layout:** `hooks/register.tsx`. The
  host API is typed. Its declaration file comes with the `plugin-authoring`
  skill of Claude Code. Write helpers that take `$` as top-level functions.
- **Change the OpenCode sidebar:** `opencode/agentic-agile-tui.tsx` (Solid,
  OpenTUI elements, colors from `api.theme.current`).
- **Change what the agent is told** in a state (write the plan, choose a task,
  do a task): `hypothesis_capsule`, `choose_capsule`, and `context` in
  `core.py`. Every host sends that exact text.
- **Change when the agent continues:** `_hook()` in `cli.py` and
  `Store.capsule()`. All three hosts call them, so they stay consistent. The
  OpenCode retry limit is in `opencode/agentic-agile.js`.
- **Change the loop:** `Store.complete` and `Store.discard` in `core.py`, then
  section 4 of `docs/protocol.md` and the skill.
- **Change how the agent learns the method:** `skills/agentic-agile/SKILL.md`.
  Lite installs copy it, so run `agentic-agile install` again afterwards.
- **Add a host:** a function in `install.py` registered in `STEPS`, a
  `--host` choice in `cli.py`, and a row in `INSTALL.md`.

## Rules that keep it consistent

- Events are the source of truth. State changes go through `Store.append` and
  `Store._apply`, and old event files keep loading. When the SQLite layout
  changes, increase `PROJECTION_VERSION` so existing projects build the index
  again on first use.
- The goal is fixed, each completed task clears the plan, and the findings are
  permanent. Every task has a reason. Changes keep these three properties,
  because they are the purpose of the tool.
- Each planner, reviewer and worker starts from the goal, the findings and the
  project. Text for these agents comes from `independent` views in `core.py`,
  their processes start through `isolation.py`, and `_guard_role` in `cli.py`
  gives each role its commands. A new field that reaches them is a finding or
  a fact from the engine.
- Use the same four words in all text that a person or an agent reads: goal,
  plan, task, finding. The stored data uses `aim` and `node`; keep those names
  in the data.
- State what the tool does and why. All text uses positive statements.
- Displays read `agentic-agile view` for the current task and the goal state,
  and they change state through the CLI.
- Hooks return quickly and exit 0: they read stdin with a time limit, and they
  stay silent in projects that have yet to set a goal.
- The installer removes exactly what it wrote: hook entries whose command runs
  `agentic-agile hook-…`, and skill folders that contain
  `.agentic-agile-install`.

## Check your change

```sh
python3 -m unittest discover -s tests -v            # model, hooks, renderers, installer
claude plugin validate . && claude plugin test .    # Claude Code bar and pane, with type checks
node --check opencode/agentic-agile.js
```

To develop Agentic Agile with its own run, start the run from a different
checkout, such as the main one, with `--cwd` set to your working copy. The run
merges each worker's changes into the working copy, and a separate checkout
keeps the code of the running engine and its workers the same from start to end.

To see a change live before you install it: run `claude --plugin-dir .` for
Claude Code. For OpenCode, run `bin/agentic-agile install opencode --scope
project --project /some/test/project` and start OpenCode there.
`bin/agentic-agile watch` shows the terminal dashboard for the current project.

## Customising your own copy

Installs point at this checkout, so an edit to the files above changes your
installed Agentic Agile after you restart the agent. Lite installs are the
easiest to adapt for one project: the hooks are two entries in a JSON file and
the skill is a Markdown file in the project, and you can edit both in place.
`agentic-agile install` writes the skill copy and the hook entries again each
time it runs, so keep lasting changes in the checkout.
