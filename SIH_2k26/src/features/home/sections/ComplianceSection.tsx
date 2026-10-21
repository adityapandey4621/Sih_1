import { AnimatePresence, motion, type MotionValue } from 'motion/react'
import { SceneSection } from '../components/SceneSection'
import { Ctl, DemoControls } from '../../../components/ui/DemoControls'
import { StatusPill, type PillTone } from '../../../components/ui/StatusPill'
import { stepper, useScrollDriven } from '../hooks/useScrollDriven'
import { COMPLIANCE_STEPS, EVIDENCE, EVIDENCE_STATE_LABEL, UNDERTAKING, type EvidenceState } from '../demo/compliance'
import { SCENES } from '../home.constants'

const TONE: Record<EvidenceState, PillTone> = { awaiting: 'neutral', received: 'active', verifying: 'proposed', verified: 'verified' }
const COUNT = COMPLIANCE_STEPS.length

export function ComplianceSection() {
  return (
    <SceneSection copy={SCENES.compliance}>
      {(progress) => <ComplianceStage progress={progress} />}
    </SceneSection>
  )
}

function ComplianceStage({ progress }: { progress: MotionValue<number> }) {
  const { value: step, set, following, follow } = useScrollDriven(progress, stepper(COUNT), 0)
  const s = COMPLIANCE_STEPS[step] ?? (COMPLIANCE_STEPS[0] as (typeof COMPLIANCE_STEPS)[number])
  const verified = s.evidence.filter((e) => e === 'verified').length
  const dueUsed = (UNDERTAKING.totalDays - s.undertakingDaysLeft) / UNDERTAKING.totalDays
  const leaseTone: PillTone = s.renewed ? 'verified' : s.leaseDaysLeft <= 14 ? 'warning' : 'active'
  const pending = s.evidence.filter((e) => e !== 'verified').length

  return (
    <div className="compliance">
      <div className="compliance__grid">
        <article className="surface undertaking" data-complete={s.complete || undefined}>
          <p className="meta-label">{UNDERTAKING.title}</p>
          <h3 className="undertaking__due">Due {UNDERTAKING.due} <small>(demo)</small></h3>
          <div className="undertaking__bar" aria-hidden="true">
            <motion.span initial={false} animate={{ scaleX: s.complete ? 1 : dueUsed }} transition={{ type: 'spring', stiffness: 80, damping: 18 }} />
          </div>
          <p className="undertaking__days">{s.complete ? 'Completed before due date' : `${s.undertakingDaysLeft} days left`}</p>
          <StatusPill tone={s.complete ? 'verified' : 'warning'}>
            {s.complete ? 'Complete · verified' : `Pending · ${verified} of ${EVIDENCE.length} verified`}
          </StatusPill>
          <p className="undertaking__reminders" data-stopped={s.complete || undefined}>
            {s.complete ? 'Future reminders cancelled: nothing left to chase.' : `Reminders active for ${pending} outstanding item${pending === 1 ? '' : 's'}.`}
          </p>
        </article>

        <ul className="evidence" aria-label="Evidence attachments">
          {EVIDENCE.map((item, i) => {
            const state = s.evidence[i] ?? 'awaiting'
            return (
              <motion.li
                key={item.id}
                className="surface ev"
                data-state={state}
                initial={false}
                animate={{ x: state === 'awaiting' ? 26 : 0, opacity: state === 'awaiting' ? 0.5 : 1 }}
                transition={{ type: 'spring', stiffness: 90, damping: 16 }}
              >
                <span className="ev__icon" aria-hidden="true">{state === 'verified' ? '✓' : '▤'}</span>
                <span className="ev__body">
                  <strong>{item.name}</strong>
                  <small>{item.detail}</small>
                </span>
                <StatusPill tone={TONE[state]}>{EVIDENCE_STATE_LABEL[state]}</StatusPill>
              </motion.li>
            )
          })}
        </ul>

        <div className="leases" aria-label="Lease validity">
          <p className="meta-label">Document validity</p>
          <div className="leases__stack">
            <AnimatePresence>
              {s.renewed ? (
                <motion.article
                  key="new"
                  className="surface lease lease--new"
                  initial={{ y: -60, opacity: 0, rotateX: 18 }}
                  animate={{ y: 0, opacity: 1, rotateX: 0 }}
                  transition={{ type: 'spring', stiffness: 80, damping: 15 }}
                >
                  <StatusPill tone="verified">Current · verified</StatusPill>
                  <h4>Lease renewal deed</h4>
                  <p>Valid until 31 Mar 2036 <small>(demo)</small></p>
                </motion.article>
              ) : null}
            </AnimatePresence>
            <motion.article
              className="surface lease lease--old"
              initial={false}
              animate={{ scale: s.renewed ? 0.94 : 1, opacity: s.renewed ? 0.45 : 1, y: s.renewed ? 12 : 0 }}
              transition={{ type: 'spring', stiffness: 80, damping: 16 }}
            >
              <StatusPill tone={s.renewed ? 'neutral' : leaseTone}>{s.renewed ? 'Superseded' : s.leaseDaysLeft <= 14 ? 'Expiring soon' : 'Valid'}</StatusPill>
              <h4>Lease deed (2016)</h4>
              <p className="lease__count">{s.renewed ? 'Replaced by renewal' : <><b>{s.leaseDaysLeft}</b> days to expiry</>}</p>
            </motion.article>
          </div>
        </div>
      </div>

      <p className="compliance__step" aria-live="polite"><strong>{s.title}.</strong> {s.detail}</p>

      <DemoControls label="Compliance demo" following={following} onFollow={follow} status={`${s.title}. ${s.detail}`}>
        <Ctl onClick={() => set((v) => Math.max(0, v - 1))} disabled={step === 0}>Back</Ctl>
        <Ctl primary onClick={() => set((v) => Math.min(COUNT - 1, v + 1))} disabled={step === COUNT - 1}>Next event</Ctl>
        <Ctl onClick={() => set(0)}>Reset</Ctl>
      </DemoControls>
    </div>
  )
}


