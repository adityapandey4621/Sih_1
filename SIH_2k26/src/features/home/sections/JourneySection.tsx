import { useState } from 'react'
import { motion, type MotionValue } from 'motion/react'
import { SceneSection } from '../components/SceneSection'
import { WorkflowPipeline } from '../components/WorkflowPipeline'
import { Ctl, DemoControls } from '../../../components/ui/DemoControls'
import { StatusPill, type PillTone } from '../../../components/ui/StatusPill'
import { stepper, useScrollDriven } from '../hooks/useScrollDriven'
import { getJourneyView, getScenario } from '../demo/journey'
import { SCENARIOS, SLA_POLICY, STAGE_BY_ID, type ClockState, type StageId } from '../demo/workflow.config'
import { SCENES } from '../home.constants'

const CLOCK_LABEL: Record<ClockState, string> = { idle: 'Not started', running: 'Running', paused: 'Paused', stopped: 'Stopped' }
const CLOCK_TONE: Record<ClockState, PillTone> = { idle: 'neutral', running: 'active', paused: 'warning', stopped: 'verified' }

function Clock({ label, state, day, limit }: { label: string; state: ClockState; day: number; limit: number }) {
  return (
    <div className="clock">
      <div className="clock__row">
        <span className="clock__label">{label}</span>
        <StatusPill tone={CLOCK_TONE[state]}>{CLOCK_LABEL[state]}</StatusPill>
      </div>
      <div className="clock__bar" data-state={state} aria-hidden="true">
        <motion.span initial={false} animate={{ scaleX: Math.min(day / limit, 1) }} transition={{ type: 'spring', stiffness: 80, damping: 18 }} />
      </div>
      <p className="clock__text">Day {day} of {limit} <span>(demo)</span></p>
    </div>
  )
}

export function JourneySection() {
  const [scenarioId, setScenarioId] = useState<'returned' | 'clean'>('returned')
  const [selected, setSelected] = useState<StageId>('scrutiny')
  const scenario = getScenario(scenarioId)
  const count = scenario.steps.length

  return (
    <SceneSection copy={SCENES.journey}>
      {(progress) => (
        <JourneyStage
          key={scenarioId}
          progress={progress}
          scenarioId={scenarioId}
          count={count}
          selected={selected}
          onSelect={setSelected}
          onScenario={setScenarioId}
        />
      )}
    </SceneSection>
  )
}

interface JourneyStageProps {
  progress: MotionValue<number>
  scenarioId: 'returned' | 'clean'
  count: number
  selected: StageId
  onSelect: (id: StageId) => void
  onScenario: (id: 'returned' | 'clean') => void
}

function JourneyStage({ progress, scenarioId, count, selected, onSelect, onScenario }: JourneyStageProps) {
  const { value: step, set, following, follow } = useScrollDriven(progress, stepper(count), 0)
  const scenario = getScenario(scenarioId)
  const view = getJourneyView(scenario, step)
  const stage = STAGE_BY_ID[selected]
  const { current } = view

  return (
    <div className="journey">
      <WorkflowPipeline
        nodeStatus={view.nodeStatus}
        connectors={view.connectors}
        tokenAt={current.stageId}
        selected={selected}
        onSelect={onSelect}
      />

      <DemoControls
        label="Case timeline demo"
        following={following}
        onFollow={follow}
        status={`Step ${step + 1} of ${count}: ${current.title}`}
      >
        <div className="seg" role="group" aria-label="Scenario">
          {SCENARIOS.map((s) => (
            <button
              key={s.id}
              type="button"
              className="seg__btn"
              aria-pressed={scenarioId === s.id}
              onClick={() => onScenario(s.id)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <Ctl onClick={() => set((v) => Math.max(0, v - 1))} disabled={step === 0}>Back</Ctl>
        <Ctl primary onClick={() => set((v) => Math.min(count - 1, v + 1))} disabled={step === count - 1}>Advance case</Ctl>
        <Ctl onClick={() => set(0)}>Reset</Ctl>
      </DemoControls>

      <div className="journey__panels">
        <article className="surface case" aria-live="polite">
          <p className="meta-label">Step {step + 1} of {count} · AICTE-2026-0042</p>
          <h3 className="case__title">{current.title}</h3>
          <p className="case__detail">{current.detail}</p>
          <p className="case__stage">
            Current stage: <strong>{STAGE_BY_ID[current.stageId].name}</strong> · {STAGE_BY_ID[current.stageId].role}
          </p>
        </article>

        <article className="surface clocks">
          <p className="meta-label">SLA clocks · demo policy</p>
          <Clock label="Officer SLA" state={current.officerClock} day={current.officerDay} limit={SLA_POLICY.officerDays} />
          <Clock label="Institution response" state={current.institutionClock} day={current.institutionDay} limit={SLA_POLICY.institutionResponseDays} />
        </article>

        <article className="surface stage-info">
          <p className="meta-label">Selected stage</p>
          <h3 className="stage-info__title">{stage.name}</h3>
          <p className="stage-info__copy">{stage.description}</p>
          <dl className="stage-info__meta">
            <div><dt>Role</dt><dd>{stage.role}</dd></div>
            <div><dt>Deadline</dt><dd>{stage.deadline}</dd></div>
          </dl>
          {stage.conditional ? <StatusPill tone="proposed">Conditional: {stage.conditional}</StatusPill> : null}
        </article>
      </div>
    </div>
  )
}

