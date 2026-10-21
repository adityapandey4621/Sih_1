import { motion } from 'motion/react'
import { STAGES, STAGE_BY_ID, type StageConfig, type StageId } from '../demo/workflow.config'
import { NODE_STATUS_LABEL, type Connector, type NodeStatus } from '../demo/journey'

interface WorkflowPipelineProps {
  nodeStatus: Record<StageId, NodeStatus>
  connectors: readonly Connector[]
  tokenAt: StageId
  selected: StageId
  onSelect: (id: StageId) => void
}

const W = 1000
const H = 260

function curve(a: StageConfig, b: StageConfig) {
  const mid = (b.x - a.x) / 2
  return `M${a.x} ${a.y} C ${a.x + mid} ${a.y}, ${b.x - mid} ${b.y}, ${b.x} ${b.y}`
}

const align = (i: number) => (i === 0 ? 'start' : i === STAGES.length - 1 ? 'end' : 'center')

/** Stage nodes on curved luminous paths. A file token rides to the active stage; conditional stages are dashed. */
export function WorkflowPipeline({ nodeStatus, connectors, tokenAt, selected, onSelect }: WorkflowPipelineProps) {
  const token = STAGE_BY_ID[tokenAt]

  return (
    <div className="pipeline">
      <svg className="pipeline__svg" viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
        {connectors.map((c) => {
          const d = curve(STAGE_BY_ID[c.from], STAGE_BY_ID[c.to])
          return (
            <g key={`${c.from}-${c.to}`} data-kind={c.kind} data-state={c.state}>
              <path className="pipe-base" d={d} />
              {c.state !== 'upcoming' ? <path className="pipe-fill" d={d} /> : null}
              {c.state === 'active' || (c.kind === 'return' && c.state !== 'upcoming') ? (
                <path className="pipe-pulse" d={d} pathLength={100} />
              ) : null}
            </g>
          )
        })}
        <motion.g
          initial={false}
          animate={{ x: token.x, y: token.y - 46 }}
          transition={{ type: 'spring', stiffness: 70, damping: 15 }}
        >
          <g className="token">
            <rect x={-11} y={-14} width={22} height={28} rx={3} />
            <path d="M-6 -6H6M-6 0H6M-6 6H2" />
          </g>
        </motion.g>
      </svg>

      <ol className="pipeline__nodes">
        {STAGES.map((stage, i) => {
          const status = nodeStatus[stage.id]
          return (
            <li
              key={stage.id}
              className="pnode"
              style={{ '--x': `${stage.x / 10}%`, '--y': `${(stage.y / H) * 100}%` } as React.CSSProperties}
            >
              <button
                type="button"
                className="pnode__btn"
                data-status={status}
                data-conditional={stage.conditional ? '' : undefined}
                data-selected={selected === stage.id || undefined}
                data-align={align(i)}
                aria-pressed={selected === stage.id}
                aria-label={`${stage.name}: ${NODE_STATUS_LABEL[status]}`}
                onClick={() => onSelect(stage.id)}
              >
                <span className="pnode__orb" aria-hidden="true">{i + 1}</span>
                <span className="pnode__label">
                  {stage.short}
                  <small>{NODE_STATUS_LABEL[status]}</small>
                </span>
                <span className="pnode__tip" role="tooltip">
                  <strong>{stage.name}</strong>
                  <span>Status · {NODE_STATUS_LABEL[status]}</span>
                  <span>Role · {stage.role}</span>
                  <span>Deadline · {stage.deadline}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
