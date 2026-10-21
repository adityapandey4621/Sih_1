import { SCENARIOS, STAGES, type Scenario, type StageId } from './workflow.config'

export type NodeStatus = 'done' | 'active' | 'paused' | 'upcoming' | 'skipped'

export interface Connector {
  from: StageId
  to: StageId
  kind: 'normal' | 'return'
  state: 'done' | 'active' | 'upcoming'
}

const ORDER = STAGES.map((s) => s.id)

export const getScenario = (id: Scenario['id']): Scenario => SCENARIOS.find((s) => s.id === id) ?? (SCENARIOS[0] as Scenario)

export function getJourneyView(scenario: Scenario, step: number) {
  const current = scenario.steps[Math.min(step, scenario.steps.length - 1)] ?? (scenario.steps[0] as Scenario['steps'][number])
  const curIdx = ORDER.indexOf(current.stageId)
  const route = ORDER.filter((id) => scenario.steps.some((s) => s.stageId === id))

  const nodeStatus = {} as Record<StageId, NodeStatus>
  for (const id of ORDER) {
    const idx = ORDER.indexOf(id)
    if (!route.includes(id)) nodeStatus[id] = 'skipped'
    else if (id === current.stageId) nodeStatus[id] = current.paused ? 'paused' : 'active'
    else nodeStatus[id] = idx < curIdx ? 'done' : 'upcoming'
  }

  const connectors: Connector[] = route.slice(0, -1).map((from, i) => {
    const to = route[i + 1] as StageId
    const fromIdx = ORDER.indexOf(from)
    const toIdx = ORDER.indexOf(to)
    return {
      from,
      to,
      kind: from === 'scrutiny' && to === 'rescrutiny' ? 'return' : 'normal',
      state: toIdx <= curIdx ? 'done' : fromIdx === curIdx ? 'active' : 'upcoming',
    }
  })

  return { current, nodeStatus, connectors }
}

export const NODE_STATUS_LABEL: Record<NodeStatus, string> = {
  done: 'Done',
  active: 'Active',
  paused: 'Returned · SLA paused',
  upcoming: 'Up next',
  skipped: 'Not triggered',
}
