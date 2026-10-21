import { useEffect, useRef, useState } from 'react'
import { motion, useTransform, type MotionValue } from 'motion/react'
import { SceneSection } from '../components/SceneSection'
import { Ctl, DemoControls } from '../../../components/ui/DemoControls'
import { StatusPill } from '../../../components/ui/StatusPill'
import { AUDIT_EVENTS, TAMPER_INDEX, buildLedger, verifyLedger, type LedgerEntry, type VerificationResult } from '../demo/auditChain'
import { SCENES } from '../home.constants'

const short = (h: string) => `${h.slice(0, 6)}…${h.slice(-4)}`
const baseLedger = () => buildLedger(AUDIT_EVENTS)

export function AuditSection() {
  return (
    <SceneSection copy={SCENES.audit}>
      {(progress) => <AuditStage progress={progress} />}
    </SceneSection>
  )
}

function AuditStage({ progress }: { progress: MotionValue<number> }) {
  const [ledger, setLedger] = useState<LedgerEntry[]>(baseLedger)
  const [result, setResult] = useState<VerificationResult | null>(null)
  const [scanned, setScanned] = useState(-1)
  const timer = useRef<number | undefined>(undefined)
  const tampered = ledger.some((e, i) => e.event.reason !== (AUDIT_EVENTS[i]?.reason ?? ''))
  const rail = useTransform(progress, [0.25, 0.65], [0, 1])

  useEffect(() => () => window.clearInterval(timer.current), [])

  const verify = () => {
    const res = verifyLedger(ledger)
    setResult(res)
    setScanned(-1)
    window.clearInterval(timer.current)
    let i = -1
    timer.current = window.setInterval(() => {
      i += 1
      setScanned(i)
      if (i >= ledger.length - 1) window.clearInterval(timer.current)
    }, 260)
  }

  const reset = () => {
    window.clearInterval(timer.current)
    setLedger(baseLedger())
    setResult(null)
    setScanned(-1)
  }

  const tamper = () => {
    window.clearInterval(timer.current)
    setLedger((prev) =>
      prev.map((entry, i) =>
        i === TAMPER_INDEX
          ? { ...entry, event: { ...entry.event, reason: 'Certificate waived (edited after the fact)' } }
          : entry,
      ),
    )
    setResult(null)
    setScanned(-1)
  }

  const statusText = result
    ? result.ok
      ? `All ${ledger.length} links verified.`
      : `Verification failed at event ${ledger[result.firstBroken ?? 0]?.event.id}.`
    : 'Not yet verified.'

  return (
    <div className="audit">
      <div className="audit__layout">
        <div className="audit__log">
          <motion.span className="audit__rail" style={{ scaleY: rail }} aria-hidden="true" />
          <ol className="audit__list">
            {ledger.map((entry, i) => {
              const check = result?.checks[i]
              const reached = result !== null && i <= scanned
              const state = !reached ? 'idle' : check?.ok ? 'ok' : 'bad'
              return (
                <li key={entry.event.id} className="ae" data-state={state}>
                  <span className="ae__node" aria-hidden="true">{state === 'ok' ? '✓' : state === 'bad' ? '!' : i + 1}</span>
                  <div className="ae__body">
                    <div className="ae__top">
                      <strong>{entry.event.action}</strong>
                      <span className="ae__id">{entry.event.id}</span>
                    </div>
                    <p className="ae__who">{entry.event.at} · {entry.event.actor}</p>
                    <p className="ae__reason">Reason: {entry.event.reason}</p>
                    <p className="ae__hash" title={`prev ${entry.prevHash}\nhash ${entry.hash}`}>
                      <span>{i === 0 ? 'genesis' : short(entry.prevHash)}</span>
                      <b aria-hidden="true">→</b>
                      <span>{short(entry.hash)}</span>
                      {reached && check && !check.ok ? <em> recomputed {short(check.recomputed)}</em> : null}
                    </p>
                  </div>
                </li>
              )
            })}
          </ol>
        </div>

        <aside className="surface verify" aria-live="polite">
          <p className="meta-label">Chain verification</p>
          <div className="verify__state">
            <StatusPill tone={result ? (result.ok ? 'verified' : 'overdue') : 'neutral'}>
              {result ? (result.ok ? 'Chain intact' : 'Chain broken') : 'Not run'}
            </StatusPill>
          </div>
          <p className="verify__text">{statusText}</p>
          <p className="verify__small">
            Each hash is a SHA-256 of the previous hash plus the event&rsquo;s own fields, computed locally in your browser just now.
            {tampered ? ' Event 3 has been edited without recomputing its hash, so verification will catch it.' : ''}
          </p>
        </aside>
      </div>

      <DemoControls label="Audit verification demo" status={statusText}>
        <Ctl primary onClick={verify}>Verify chain</Ctl>
        <Ctl onClick={tamper} disabled={tampered}>Simulate an edit to event 3</Ctl>
        <Ctl onClick={reset} disabled={!tampered && result === null}>Restore</Ctl>
      </DemoControls>
    </div>
  )
}

