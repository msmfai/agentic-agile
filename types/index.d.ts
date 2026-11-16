export type PathPart = { id: string; title: string; kind: string }

export type Tone = 'success' | 'warning' | 'error' | 'muted' | 'accent'

export type GraphNode = {
  id: string
  title: string
  kind: string
  status: 'pending' | 'in_progress' | 'delegated' | 'complete'
  depth: number
  last: boolean[]
  ready: boolean
  requires: string[]
  /** The background agent that holds the task, or ''. */
  delegated_to: string
  /** Drawn as given: decided by src/agentic_agile/render.py. */
  mark: string
  tone: Tone
  prefix: string
}

/** pursuing a block, choosing one, or writing the hypothesis all count as an active goal. */
export type GoalState = 'pursuing' | 'choosing' | 'hypothesizing' | 'waiting' | 'planning' | 'paused' | 'blocked' | 'complete'

/** A stuck or slow agent. The engine stops nothing; the top-level agent decides. */
export type Report = {
  id: string
  kind: 'stalled' | 'slow'
  /** The task the agent works on, or ''. */
  task: string
  /** planner, reviewer or worker. */
  role: string
  host: string
  evidence: string
  /** ISO time the report opened. */
  opened: string
  status: 'open' | 'stopped' | 'extended' | 'dismissed'
  /** Extra seconds given when the status is extended. */
  extension: number
  /** Drawn as given: decided by src/agentic_agile/render.py. */
  line: string
}

/** What `agentic-agile view --graph` prints. */
export type AgileView = {
  initialized: true
  state: string
  root: { outcome: string; definition_of_done: string; constraints: string; status: string }
  node: {
    id: string
    title: string
    kind: string
    objective: string
    done_when: string
    why: string
    picked_why: string
    expect: string
    alternatives: string[]
    path: PathPart[]
    gates: { label: string; status: string }[]
  } | null
  ready: string[]
  progress: { done: number; total: number }
  goal: { state: GoalState; active: boolean }
  /** What finished blocks taught: kept across every hypothesis. */
  known: { kind: string; body: string; node: string | null }[]
  /** Tasks running now: the one in progress here and the delegated ones. */
  running: { id: string; title: string; status: string; delegated_to: string }[]
  parallel: number
  round_closing: boolean
  finished: number
  root_eligible: boolean
  /** Open reports only. */
  reports: Report[]
  /** Ready-made labels, so every UI says the same thing. */
  display: { node: string; goal: string; tone: Tone; bar: string; bar_wide: string; count: string; line: string; reports: string }
  graph: GraphNode[]
}

declare module 'claude-code' {
  interface PluginState {
    'agentic-agile': { view: Shaped<AgileView | null>; isHidden: boolean }
  }
}
