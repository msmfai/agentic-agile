import { expect, test } from 'claude-code/testing'

const VIEW = {
  initialized: true,
  state: 'active',
  root: { outcome: 'Ship v1.0', definition_of_done: 'Deployed', constraints: '', status: 'active' },
  node: {
    id: 'api',
    title: 'Implement endpoints',
    kind: 'task',
    objective: 'Add the invoice endpoints',
    done_when: 'Endpoint tests pass',
    why: 'The release needs invoices',
    picked_why: 'Its result decides the schema',
    expect: 'Schema B fits',
    alternatives: ['migrate'],
    path: [
      { id: 'build', title: 'Build the release', kind: 'group' },
      { id: 'api', title: 'Implement endpoints', kind: 'task' },
    ],
    gates: [{ label: 'unit tests', status: 'pending' }],
  },
  ready: ['migrate'],
  progress: { done: 1, total: 3 },
  goal: { state: 'pursuing', active: true },
  known: [{ kind: 'finding', body: 'Schema B fits', node: 'design' }],
  running: [],
  parallel: 1,
  round_closing: false,
  finished: 1,
  root_eligible: false,
  display: { node: 'build › api', goal: '● goal active', tone: 'success', bar: '▰▰▱▱▱', bar_wide: '▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱', count: '1/3', line: '', reports: '' },
  reports: [] as unknown[],
  graph: [
    { id: 'build', title: 'Build the release', kind: 'group', status: 'pending', depth: 0, last: [false], ready: false, requires: [], delegated_to: '', mark: '○', tone: 'muted', prefix: '├ ' },
    { id: 'api', title: 'Implement endpoints', kind: 'task', status: 'in_progress', depth: 1, last: [false, true], ready: false, requires: ['design'], delegated_to: '', mark: '▶', tone: 'accent', prefix: '│ └ ' },
    { id: 'design', title: 'Choose the schema', kind: 'investigation', status: 'complete', depth: 0, last: [true], ready: false, requires: [], delegated_to: '', mark: '○', tone: 'muted', prefix: '├ ' },
  ],
}

const BAND = {
  plugin: 'agentic-agile',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isCollapsed: false,
    isWorking: false,
    maxRows: 10,
    bodyColumns: 100,
    scroll: { offset: 0, bodyRows: 10 },
    view: {},
  },
} as const

const PANE = {
  plugin: 'agentic-agile',
  component: 'Pane',
  requestId: 'agentic-agile',
  props: {
    title: 'Agentic Agile',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

test('the band names the active node and the goal state', async ($, on) => {
  let view: unknown = VIEW
  const ran: string[][] = []
  on('command.register', () => ({ value: { command: 'agile-graph' } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('clock.every', () => ({ value: undefined }))
  on('process.run', (_, e) => {
    ran.push([...e.argv.slice(1)])
    if (e.argv[1] === 'goal') {
      const isPaused = e.argv[2] === 'pause'
      view = {
        ...VIEW,
        goal: { state: isPaused ? 'paused' : 'pursuing', active: !isPaused },
        display: { ...VIEW.display, goal: isPaused ? '⏸ goal paused' : '● goal active', tone: isPaused ? 'warning' : 'success' },
      }
    }

    return { value: { exitCode: 0, stdout: JSON.stringify(view), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /›/ }))?.text).toBe('build › api')
  expect((await ui.find({ type: 'Text', text: /goal/ }))?.text).toBe('● goal active')
  expect((await ui.find({ type: 'Text', text: /\d\/\d/ }))?.text).toBe('1/3')

  await ui.press({ key: 'toggle' })
  expect(ran).toContainEqual(['goal', 'pause'])
  expect((await ui.find({ type: 'Text', text: /goal/ }))?.text).toBe('⏸ goal paused')
  await ui.unmount()

  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await pane.find({ type: 'Text', text: /Ship/ }))?.text).toBe('Ship v1.0')
  expect((await pane.find({ type: 'Text', text: /gate/ }))?.text).toBe('○ gate: unit tests')
  expect((await pane.find({ type: 'Text', text: /Why now/ }))?.text).toBe('Why now: Its result decides the schema')
  expect((await pane.find({ type: 'Text', text: /Schema B/ }))?.text).toBe('finding [design]: Schema B fits')
  await pane.unmount()
})

test('a project without an aim draws no band', async ($, on) => {
  on('command.register', () => ({ value: { command: 'agile-graph' } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('clock.every', () => ({ value: undefined }))
  on('process.run', () => ({
    value: { exitCode: 0, stdout: JSON.stringify({ initialized: false }), stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)

    return <Text>engine</Text>
  })

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /engine/ }))?.text).toBe('engine')
  expect(await ui.find({ type: 'Text', text: /agentic-agile/ })).toBeUndefined()
  await ui.unmount()
})

test('open reports show in the band and the pane', async ($, on) => {
  const report = { id: 'r1', kind: 'slow', task: 'api', role: 'worker', host: 'claude', evidence: 'ran 700 s of 600 s', opened: '', status: 'open', extension: 0, line: 'worker on api ran past its expected time' }
  const view = { ...VIEW, reports: [report], display: { ...VIEW.display, reports: '1 report open: worker on api ran past its expected time' } }
  on('command.register', () => ({ value: { command: 'agile-graph' } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('clock.every', () => ({ value: undefined }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: JSON.stringify(view), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /report/ }))?.text).toBe('1 report open: worker on api ran past its expected time')
  await ui.unmount()
  const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await pane.find({ type: 'Text', text: /r1:/ }))?.text).toContain('ran 700 s of 600 s')
  await pane.unmount()
})
