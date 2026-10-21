import { useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion, type MotionValue } from 'motion/react'
import { SceneSection } from '../components/SceneSection'
import { Ctl, DemoControls } from '../../../components/ui/DemoControls'
import { StatusPill, type PillTone } from '../../../components/ui/StatusPill'
import { clamp, lerp } from '../../../lib/math'
import { useScrollDriven } from '../hooks/useScrollDriven'
import { DEADLINE_POLICY, HIERARCHY, VISIT_DATE, getDeadlineView, type DeadlinePhase } from '../demo/deadlines'
import { SCENES } from '../home.constants'

const { minDay, maxDay } = DEADLINE_POLICY
const dayFromProgress = (p: number) => Math.round(lerp(minDay, maxDay, clamp((p - 0.3) / 0.4)))

const PHASE_TONE: Record<DeadlinePhase, PillTone> = {
  scheduled: 'active', reminder: 'active', approaching: 'warning', overdue: 'overdue', acknowledged: 'verified',
}
const PHASE_LABEL: Record<DeadlinePhase, string> = {
  scheduled: 'Scheduled', reminder: 'Reminder', approaching: 'Approaching', overdue: 'Overdue', acknowledged: 'Acknowledged',
}
const FLOW: readonly DeadlinePhase[] = ['scheduled', 'reminder', 'approaching', 'overdue', 'acknowledged']

const fmt = (d: Date) => d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
const addDays = (d: Date, n: number) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n)

function Calendar({ day, phase }: { day: number; phase: DeadlinePhase }) {
  const cells = useMemo(() => {
    const first = new Date(VISIT_DATE.getFullYear(), VISIT_DATE.getMonth(), 1)
    const lead = (first.getDay() + 6) % 7
    const total = new Date(VISIT_DATE.getFullYear(), VISIT_DATE.getMonth() + 1, 0).getDate()
    return [...Array<null>(lead).fill(null), ...Array.from({ length: total }, (_, i) => i + 1)]
  }, [])
  const todayDate = addDays(VISIT_DATE, day).getDate()
  const visit = VISIT_DATE.getDate()

  return (
    <div className="cal surface" data-phase={phase}>
      <div className="cal__head">
        <p className="meta-label">EVC calendar · Nov 2026</p>
        <span className="cal__today">Today (simulated): {fmt(addDays(VISIT_DATE, day))}</span>
      </div>
      <div className="cal__grid" role="img" aria-label={`Calendar. Visit date ${fmt(VISIT_DATE)}. Simulated today ${fmt(addDays(VISIT_DATE, day))}.`}>
        {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d, i) => <span key={i} className="cal__dow">{d}</span>)}
        {cells.map((n, i) => (
          <span
            key={i}
            className="cal__cell"
            data-empty={n === null || undefined}
            data-visit={n === visit || undefined}
            data-today={n === todayDate || undefined}
          >
            {n}
          </span>
        ))}
      </div>
      <ul className="cal__panel">
        <li><span aria-hidden="true">▣</span> EVC Panel A <small>3 members · demo</small></li>
        <li><span aria-hidden="true">▣</span> Coordinator <small>Regional office</small></li>
      </ul>
    </div>
  )
}

function Timeline({ day }: { day: number }) {
  const pct = (d: number) => ((d - minDay) / (maxDay - minDay)) * 100
  const marks = [
    { d: -DEADLINE_POLICY.reminderDaysBefore, label: 'Reminder', tone: 'cyan' },
    { d: -DEADLINE_POLICY.approachingDaysBefore, label: 'Warning', tone: 'amber' },
    { d: 0, label: 'Due', tone: 'ivory' },
    ...DEADLINE_POLICY.escalations.map((e) => ({ d: e.afterDays, label: `L${e.level}`, tone: 'red' })),
  ]
  return (
    <div className="tl" aria-hidden="true">
      <div className="tl__track">
        <span className="tl__zone tl__zone--overdue" style={{ left: `${pct(0)}%` }} />
        {marks.map((m) => (
          <span key={m.label} className="tl__mark" data-tone={m.tone} style={{ left: `${pct(m.d)}%` }}>
            <i />
            <b>{m.label}</b>
          </span>
        ))}
        <span className="tl__head" style={{ left: `${pct(day)}%` }}><i /></span>
      </div>
    </div>
  )
}

export function DeadlinesSection() {
  return (
    <SceneSection copy={SCENES.deadlines}>
      {(progress) => <DeadlineStage progress={progress} />}
    </SceneSection>
  )
}

function DeadlineStage({ progress }: { progress: MotionValue<number> }) {
  const { value: day, set, following, follow } = useScrollDriven(progress, dayFromProgress, minDay)
  const [ack, setAck] = useState(false)
  const view = getDeadlineView(day, ack)
  const canAck = day > 0 && !ack
  const countdown = day <= 0 ? `T−${-day}` : `+${day}`

  useEffect(() => {
    if (day <= 0) setAck(false)
  }, [day])

  const stepDay = (delta: number) => set((d) => clamp(d + delta, minDay, maxDay))

  return (
    <div className="deadlines">
      <div className="deadlines__grid">
        <Calendar day={day} phase={view.phase} />

        <div className="surface alert" data-phase={view.phase}>
          <p className="meta-label">Case AICTE-2026-0042 · EVC visit</p>
          <p className="alert__count" aria-hidden="true">{countdown}<small>days</small></p>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={view.phase + view.level}
              className="alert__msg"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -12 }}
              transition={{ duration: 0.3 }}
            >
              <StatusPill tone={PHASE_TONE[view.phase]}>{PHASE_LABEL[view.phase]}</StatusPill>
              <h3>{view.headline}</h3>
              <p>{view.detail}</p>
            </motion.div>
          </AnimatePresence>
          <ol className="flow" aria-label="Alert progression">
            {FLOW.map((p) => (
              <li key={p} data-on={p === view.phase || undefined} data-past={FLOW.indexOf(p) < FLOW.indexOf(view.phase) || undefined}>{PHASE_LABEL[p]}</li>
            ))}
          </ol>
        </div>

        <div className="ladder surface">
          <p className="meta-label">Escalation path</p>
          <ol className="ladder__tiers">
            {[...HIERARCHY].map((role, i) => ({ role, i })).reverse().map(({ role, i }) => (
              <li key={role} className="tier" data-active={i === view.level || undefined} data-reached={i <= view.level && view.phase === 'overdue' || undefined} style={{ '--i': i } as React.CSSProperties}>
                <span className="tier__role">{role}</span>
                {i === view.level ? (
                  <motion.span layoutId="case-token" className="tier__token" transition={{ type: 'spring', stiffness: 120, damping: 16 }}>
                    <span aria-hidden="true">▤</span> Case 0042
                  </motion.span>
                ) : null}
              </li>
            ))}
          </ol>
          <p className="ladder__note">Alerts route the case to an authorised official. They never approve or reject an application.</p>
        </div>
      </div>

      <Timeline day={day} />

      <DemoControls
        label="Deadline simulation"
        following={following}
        onFollow={follow}
        status={`Day ${day}. ${view.headline}`}
      >
        <Ctl onClick={() => stepDay(-1)} disabled={day <= minDay}>−1 day</Ctl>
        <Ctl primary onClick={() => stepDay(1)} disabled={day >= maxDay}>+1 day</Ctl>
        <label className="range">
          <span className="sr-only">Simulated day relative to visit date</span>
          <input type="range" min={minDay} max={maxDay} value={day} onChange={(e) => set(Number(e.target.value))} />
        </label>
        <Ctl onClick={() => setAck(true)} disabled={!canAck}>Acknowledge</Ctl>
        <Ctl onClick={() => { setAck(false); set(minDay) }}>Reset</Ctl>
      </DemoControls>
    </div>
  )
}




