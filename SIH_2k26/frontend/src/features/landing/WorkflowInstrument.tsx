import { useEffect, useRef, useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { usePrefersReducedMotion } from '../../lib/usePrefersReducedMotion'
import { ROUTE_STAGES } from './landing.content'

const STEP_MS = 3600
const COL = 200 // viewBox units per stage column
const W = COL * ROUTE_STAGES.length

type StageState = 'done' | 'current' | 'upcoming'
const STATE_LABEL: Record<StageState, string> = { done: 'Completed', current: 'In progress', upcoming: 'Not yet reached' }

function Sheet({ state }: { state: StageState }) {
  return (
    <svg viewBox="0 0 28 34" width="28" height="34" aria-hidden="true" className="sheet" data-state={state}>
      <path d="M2 1.5h16l8 8V32.5H2z" className="sheet__body" />
      <path d="M18 1.5v8h8" className="sheet__fold" />
      <path d="M6 15h14M6 19.5h14M6 24h8" className="sheet__lines" />
      {state === 'done' && <path d="M15 27.5l3 3 6-6.5" className="sheet__check" />}
      {state === 'current' && <circle cx="21" cy="27" r="3" className="sheet__dot" />}
    </svg>
  )
}

/**
 * Illustrative route of a sample application. It is explanatory only: it does not read, simulate
 * or imply any real application, approval or AICTE system.
 */
export function WorkflowInstrument() {
  const reduced = usePrefersReducedMotion()
  const [active, setActive] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [visible, setVisible] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState(false)

  useEffect(() => {
    const el = root.current
    if (!el || typeof IntersectionObserver === 'undefined') return setVisible(true)
    const io = new IntersectionObserver(([entry]) => setVisible(entry?.isIntersecting ?? false), { threshold: 0.3 })
    io.observe(el)
    return () => io.disconnect()
  }, [])

  const running = playing && !reduced && visible && !hover
  useEffect(() => {
    if (!running) return
    const id = window.setInterval(() => setActive((i) => (i + 1) % ROUTE_STAGES.length), STEP_MS)
    return () => window.clearInterval(id)
  }, [running])

  const stateOf = (i: number): StageState => (i < active ? 'done' : i === active ? 'current' : 'upcoming')
  const stage = ROUTE_STAGES[active]!
  const select = (i: number) => {
    setActive(i)
    setPlaying(false) // choosing a stage hands control to the reader
  }
  // The conditional stage can be bypassed: draw the skip arc around it, from the stage before to the stage after.
  const conditional = ROUTE_STAGES.findIndex((s) => s.conditional)
  const skipFrom = COL / 2 + (conditional - 1) * COL
  const skipTo = COL / 2 + (conditional + 1) * COL

  return (
    <div
      className="instrument"
      ref={root}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      data-reduced={reduced || undefined}
    >
      <div className="instrument__bar">
        <p className="instrument__label">
          <span className="instrument__tag">Illustrative</span>
          Route of a sample application — not live data, not connected to any AICTE system
        </p>
        {!reduced && (
          <button type="button" className="instrument__toggle" onClick={() => setPlaying((p) => !p)} aria-pressed={!playing}>
            {playing ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
            {playing ? 'Pause' : 'Play'}
            <span className="sr-only"> the walkthrough animation</span>
          </button>
        )}
      </div>

      <div className="instrument__plan">
        <svg className="route" viewBox={`0 0 ${W} 90`} role="img" aria-label="Diagram: a sample application moves left to right through six stages, with committee processes applying only where needed.">
          {/* skip path for the conditional stage */}
          <path d={`M ${skipFrom + 12} 36 C ${skipFrom + 70} 2, ${skipTo - 70} 2, ${skipTo - 12} 36`} className="route__skip" />
          <line x1={COL / 2} y1="45" x2={W - COL / 2} y2="45" className="route__track" />
          <line
            x1={COL / 2}
            y1="45"
            x2={W - COL / 2}
            y2="45"
            className="route__done"
            style={{ transform: `scaleX(${active / (ROUTE_STAGES.length - 1)})`, transformOrigin: '0 50%', transformBox: 'fill-box' }}
          />
          {ROUTE_STAGES.map((s, i) => (
            <g key={s.id} transform={`translate(${COL / 2 + i * COL} 45)`} className="route__node" data-state={stateOf(i)} data-conditional={s.conditional || undefined}>
              <circle r="9" />
              {stateOf(i) === 'done' && <path d="M-4 0.5l3 3 5-6" className="route__tick" />}
            </g>
          ))}
          <g className="route__marker" style={{ transform: `translate(${COL / 2 + active * COL}px, 45px)` }}>
            <circle r="15" className="route__ring" />
            <circle r="4" className="route__pip" />
          </g>
        </svg>

        <ol className="stages">
          {ROUTE_STAGES.map((s, i) => {
            const st = stateOf(i)
            return (
              <li key={s.id} className="stage" data-state={st}>
                <button type="button" className="stage__btn" onClick={() => select(i)} aria-current={i === active ? 'step' : undefined}>
                  <span className="stage__num mono">{String(i + 1).padStart(2, '0')}</span>
                  <Sheet state={st} />
                  <span className="stage__label">{s.label}</span>
                  <span className="stage__short">{s.short}</span>
                  <span className="stage__state">
                    {STATE_LABEL[st]}
                    {s.conditional && ' · where applicable'}
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      </div>

      <div className="instrument__detail" aria-live={playing && !reduced ? 'off' : 'polite'}>
        <p className="eyebrow">
          Stage {active + 1} of {ROUTE_STAGES.length} · Acts: {stage.actor}
        </p>
        <p className="instrument__summary">{stage.summary}</p>
        <p className="instrument__evidence">{stage.evidence}</p>
      </div>
    </div>
  )
}
