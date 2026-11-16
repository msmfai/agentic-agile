/** @jsxImportSource @opentui/solid */
// Agentic Agile in the OpenCode TUI: the active node and goal state beside the
// prompt, and the whole graph in the sidebar. It polls
// `agentic-agile view --graph` and draws what it gets.
//
// Load it from tui.json ("plugin": ["/path/to/agentic-agile/opencode/agentic-agile-tui.tsx"])
// or run `agentic-agile install opencode`.
import { execFile } from "node:child_process"
import * as fs from "node:fs"
import * as path from "node:path"
import { fileURLToPath } from "node:url"
import { createSignal, For, Show } from "solid-js"

const HERE = path.dirname(fs.realpathSync(fileURLToPath(import.meta.url)))
const BUNDLED = path.join(HERE, "..", "bin", "agentic-agile")
const CLI = process.env.AGENTIC_AGILE_BIN || (fs.existsSync(BUNDLED) ? BUNDLED : "agentic-agile")
const POLL_MS = 2000
const MAX_TREE_ROWS = 14

// `agentic-agile view` decides every word and symbol (src/agentic_agile/render.py); this
// plugin maps its color roles onto the OpenCode theme.
const TONE = { success: "success", warning: "warning", error: "error", muted: "textMuted", accent: "accent" }

// Keep the active node in view when the graph is taller than the sidebar slot.
const windowed = (graph) => {
  if (graph.length <= MAX_TREE_ROWS) return graph
  const active = Math.max(0, graph.findIndex((node) => node.status === "in_progress"))
  const start = Math.min(Math.max(0, active - Math.floor(MAX_TREE_ROWS / 2)), graph.length - MAX_TREE_ROWS)
  return graph.slice(start, start + MAX_TREE_ROWS)
}

const tui = async (api) => {
  const [view, setView] = createSignal(null)

  const refresh = () => {
    const cwd = api.state.path.directory || process.cwd()
    execFile(CLI, ["--cwd", cwd, "view", "--graph"], { timeout: 10_000 }, (error, stdout) => {
      let next = null
      if (!error) {
        try {
          const parsed = JSON.parse(stdout)
          next = parsed.initialized === true ? parsed : null
        } catch {}
      }
      // Redraw when the snapshot changes.
      if (JSON.stringify(next) !== JSON.stringify(view())) setView(next)
    })
  }

  refresh()
  const timer = setInterval(refresh, POLL_MS)
  api.lifecycle.onDispose(() => clearInterval(timer))

  const theme = () => api.theme.current

  // One line beside the prompt: which node is active, and whether a goal is.
  const Indicator = () => (
    <Show when={view()}>
      {(v) => (
        <box flexDirection="row" gap={1} flexShrink={0}>
          <text fg={theme().accent}>
            <b>◆ {v().display.node}</b>
          </text>
          <text fg={theme().textMuted}>{v().display.count}</text>
          <text fg={theme()[TONE[v().display.tone]]}>{v().display.goal}</text>
        </box>
      )}
    </Show>
  )

  const Sidebar = () => (
    <Show when={view()}>
      {(v) => (
        <box flexDirection="column">
          <text fg={theme().text}>
            <b>Agentic Agile</b>
          </text>
          <text fg={theme()[TONE[v().display.tone]]}>{v().display.goal}</text>
          <Show when={v().reports.length > 0}>
            <text fg={theme().warning}>{v().display.reports}</text>
          </Show>
          <text fg={theme().textMuted}>{v().root.outcome}</text>
          <text fg={theme().success}>
            {v().display.bar_wide} <span style={{ fg: theme().textMuted }}>{v().display.count}</span>
          </text>
          <For each={windowed(v().graph)}>
            {(node) => (
              <text fg={node.status === "complete" ? theme().textMuted : theme().text}>
                <span style={{ fg: theme().textMuted }}>{node.prefix}</span>
                <span style={{ fg: theme()[TONE[node.tone]] }}>{node.mark} </span>
                {node.status === "in_progress" ? <b>{node.id}</b> : node.id}
              </text>
            )}
          </For>
          <Show when={v().graph.length > MAX_TREE_ROWS}>
            <text fg={theme().textMuted}>… {v().graph.length - MAX_TREE_ROWS} more (agentic-agile tree)</text>
          </Show>
          <Show when={v().node}>
            {(node) => (
              <box flexDirection="column" paddingTop={1}>
                <text fg={theme().accent}>
                  <b>▶ {node().title}</b>
                </text>
                <text fg={theme().textMuted}>Done when: {node().done_when}</text>
                <Show when={node().why}>
                  <text fg={theme().textMuted}>Why: {node().why}</text>
                </Show>
                <For each={node().gates}>
                  {(gate) => (
                    <text fg={gate.status === "passed" ? theme().success : gate.status === "failed" ? theme().error : theme().textMuted}>
                      {gate.status === "passed" ? "✔" : gate.status === "failed" ? "✖" : "○"} gate: {gate.label}
                    </text>
                  )}
                </For>
              </box>
            )}
          </Show>
        </box>
      )}
    </Show>
  )

  api.slots.register({
    order: 150,
    slots: {
      sidebar_content() {
        return <Sidebar />
      },
      session_prompt_right() {
        return <Indicator />
      },
      home_prompt_right() {
        return <Indicator />
      },
    },
  })
}

export default { id: "agentic-agile", tui }
