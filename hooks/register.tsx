// Claude Code interface for Agentic Agile: a band above the prompt naming the active
// node and its goal state, and a pane with the whole graph. Everything shown
// comes from `agentic-agile view --graph`; this module changes Agentic Agile state
// through the CLI's own `goal pause|resume`.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { AgileView, Tone } from '../types'

const PANE = 'agentic-agile'
const view = atom({ plugin: 'agentic-agile', key: 'view' } as const, null, { shape: 'view-2' })
const isHidden = atom({ plugin: 'agentic-agile', key: 'isHidden' } as const, false)

// `agentic-agile view` decides every word and symbol (src/agentic_agile/render.py); this
// module maps its color roles onto Claude Code's theme.
const TONE: Record<Tone, string> = {
  success: 'success',
  warning: 'warning',
  error: 'error',
  muted: 'inactive',
  accent: 'suggestion',
}

function cli($: EngineInterface, ...args: string[]) {
  return $.process.run([`${$.plugin.root}/bin/agentic-agile`, ...args], { timeoutMs: 10_000 })
}

// Reads the snapshot. The bar appears in projects that have a goal.
async function refresh($: EngineInterface) {
  let next: AgileView | null = null

  try {
    const { exitCode, stdout } = await cli($, 'view', '--graph')
    const parsed = exitCode === 0 ? JSON.parse(stdout) : null
    next = parsed && parsed.initialized === true ? (parsed as AgileView) : null
  } catch {
    next = null
  }

  await update($, view, () => next)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({
      name: 'agile-graph',
      description: 'Show the Agentic Agile graph, active node, and goal in a pane',
    })
    await refresh($)
    // Other sessions and terminals change the graph too.
    $.clock.every(10_000, () => void refresh($))

    return started
  })

  on('command.run', { command: 'agile-graph' }, async $ => {
    await refresh($)
    await update($, isHidden, () => false)
    await $.ui.open({ id: PANE, title: 'Agentic Agile' })

    return { text: 'Agentic Agile pane opened.' }
  })

  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    const command = (e as { command?: unknown }).command

    if (typeof command === 'string' && command.includes('agentic-agile')) {
      // The tool call result always goes through, whatever the refresh does.
      await refresh($).catch(() => {})
    }

    return ran
  })

  on('turn.complete', async ($, e, next) => {
    await refresh($).catch(() => {})

    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const v = await read($, view)

    if (v === null || e.props.hasSurvey || (await read($, isHidden))) {
      return next(e)
    }

    const { Box, Button, Text } = $.ui.resolve(e)
    const canToggle = v.goal.active || v.goal.state === 'paused'

    return (
      <Box gap={1}>
        <Text color="claude" bold>
          ◆ Agentic Agile
        </Text>
        <Text color={v.node ? 'suggestion' : 'warning'} bold={v.node !== null}>
          {v.display.node}
        </Text>
        <Text color="success">{v.display.bar}</Text>
        <Text dimColor>{v.display.count}</Text>
        <Text color={TONE[v.display.tone]}>{v.display.goal}</Text>
        {v.reports.length > 0 && <Text color="warning">{v.display.reports}</Text>}
        {canToggle && (
          <Button
            key="toggle"
            label={v.goal.state === 'paused' ? 'Resume' : 'Pause'}
            onPress={async () => {
              await cli($, 'goal', v.goal.state === 'paused' ? 'resume' : 'pause')
              await refresh($)
            }}
          />
        )}
        <Button key="graph" label="Graph" onPress={() => void $.ui.open({ id: PANE, title: 'Agentic Agile' })} />
        <Button key="hide" label="Hide" onPress={() => update($, isHidden, () => true)} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const v = await read($, view)

    if (v === null) {
      return (
        <Box flexDirection="column">
          <Text dimColor>Set a goal to start.</Text>
          <Text dimColor>Tell Claude: "Use Agentic Agile for this goal: ..."</Text>
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        <Text color={TONE[v.display.tone]} bold>
          {v.display.goal}
        </Text>
        <Text bold>{v.root.outcome}</Text>
        <Text dimColor>Done when: {v.root.definition_of_done}</Text>
        <Box gap={1}>
          <Text color="success">{v.display.bar_wide}</Text>
          <Text dimColor>{v.display.count}</Text>
        </Box>
        {v.reports.length > 0 && <Text color="warning">{v.display.reports}</Text>}
        {v.reports.map(report => (
          <Text color="warning">
            {report.id}: {report.line} ({report.evidence})
          </Text>
        ))}
        <Text> </Text>
        {v.graph.map(node => {
          return (
            <Box>
              <Text dimColor>{node.prefix}</Text>
              <Text color={TONE[node.tone]}>{node.mark} </Text>
              <Text bold={node.status === 'in_progress'} dimColor={node.status === 'complete'}>
                {node.id}
              </Text>
              <Text dimColor>
                {' '}
                {node.title}
                {node.requires.length > 0 ? `  ← ${node.requires.join(', ')}` : ''}
              </Text>
            </Box>
          )
        })}
        <Text> </Text>
        {v.node !== null && (
          <Box flexDirection="column">
            <Text color="suggestion" bold>
              ▶ {v.display.node}
            </Text>
            <Text>{v.node.objective}</Text>
            <Text dimColor>Done when: {v.node.done_when}</Text>
            {v.node.why !== '' && <Text dimColor>Why: {v.node.why}</Text>}
            {v.node.picked_why !== '' && <Text dimColor>Why now: {v.node.picked_why}</Text>}
            {v.node.gates.map(gate => (
              <Text color={gate.status === 'passed' ? 'success' : gate.status === 'failed' ? 'error' : 'inactive'}>
                {gate.status === 'passed' ? '✔' : gate.status === 'failed' ? '✖' : '○'} gate: {gate.label}
              </Text>
            ))}
          </Box>
        )}
        {v.node === null && <Text color="warning">{v.display.node}</Text>}
        {v.known.length > 0 && <Text> </Text>}
        {v.known.length > 0 && <Text bold>Findings</Text>}
        {v.known.map(item => (
          <Text dimColor>
            {item.kind}
            {item.node ? ` [${item.node}]` : ''}: {item.body}
          </Text>
        ))}
      </Box>
    )
  })
}
