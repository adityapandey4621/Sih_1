import { useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { SceneSection } from '../components/SceneSection'
import { Ctl, DemoControls } from '../../../components/ui/DemoControls'
import { StatusPill } from '../../../components/ui/StatusPill'
import { EXTRACTED_FIELDS, SCAN_LINES } from '../demo/authority'
import { SCENES } from '../home.constants'

type FieldState = { status: 'proposed' | 'verified'; value: string; corrected: boolean }

const initial = (): Record<string, FieldState> =>
  Object.fromEntries(EXTRACTED_FIELDS.map((f) => [f.id, { status: 'proposed', value: f.proposed, corrected: false }]))

export function AuthoritySection() {
  return <SceneSection copy={SCENES.authority}>{() => <AuthorityStage />}</SceneSection>
}

function AuthorityStage() {
  const [fields, setFields] = useState(initial)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [signed, setSigned] = useState(false)

  const allVerified = EXTRACTED_FIELDS.every((f) => fields[f.id]?.status === 'verified')
  const verifiedCount = EXTRACTED_FIELDS.filter((f) => fields[f.id]?.status === 'verified').length

  const confirm = (id: string) =>
    setFields((prev) => ({ ...prev, [id]: { ...(prev[id] as FieldState), status: 'verified' } }))

  const saveEdit = (id: string) => {
    const value = draft.trim()
    if (!value) return
    setFields((prev) => ({ ...prev, [id]: { status: 'verified', value, corrected: value !== (EXTRACTED_FIELDS.find((f) => f.id === id)?.proposed ?? '') } }))
    setEditing(null)
  }

  const reset = () => {
    setFields(initial())
    setEditing(null)
    setSigned(false)
  }

  return (
    <div className="authority">
      <div className="authority__grid">
        <figure className="scan" aria-label="Scanned lease deed with highlighted text lines">
          <span className="scan__beam" aria-hidden="true" />
          <div className="scan__paper">
            {SCAN_LINES.map((line, i) => (
              <p key={line} className={i === 0 ? 'scan__title' : undefined}>{line}</p>
            ))}
          </div>
          <figcaption>1 · Scanned document</figcaption>
        </figure>

        <div className="extract" aria-label="Extracted fields">
          <p className="meta-label">2 · OCR proposes, an officer decides</p>
          <ul>
            {EXTRACTED_FIELDS.map((f, i) => {
              const state = fields[f.id] as FieldState
              const isEditing = editing === f.id
              return (
                <motion.li
                  key={f.id}
                  className="field"
                  data-status={state.status}
                  initial={{ opacity: 0, x: -24 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true, margin: '-10% 0px' }}
                  transition={{ delay: i * 0.18, duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
                >
                  <span className="field__label">{f.label}</span>
                  {isEditing ? (
                    <form className="field__edit" onSubmit={(e) => { e.preventDefault(); saveEdit(f.id) }}>
                      <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} aria-label={`Correct ${f.label}`} />
                      <button type="submit" className="ctl" data-primary>Save</button>
                      <button type="button" className="ctl ctl--quiet" onClick={() => setEditing(null)}>Cancel</button>
                    </form>
                  ) : (
                    <>
                      <span className="field__value">{state.value}</span>
                      {state.status === 'proposed' ? (
                        <>
                          <StatusPill tone="proposed">Machine-extracted · unverified</StatusPill>
                          <span className="field__actions">
                            <button type="button" className="ctl" onClick={() => confirm(f.id)}>Confirm</button>
                            <button type="button" className="ctl ctl--quiet" onClick={() => { setDraft(state.value); setEditing(f.id) }}>Correct</button>
                          </span>
                        </>
                      ) : (
                        <StatusPill tone="verified">{state.corrected ? 'Corrected by officer' : 'Verified by officer'}</StatusPill>
                      )}
                    </>
                  )}
                </motion.li>
              )
            })}
          </ul>
        </div>

        <aside className="surface signoff" data-signed={signed || undefined}>
          <p className="meta-label">3 · Authority sign-off</p>
          <h3>Competent Authority decision</h3>
          <p className="signoff__copy">
            {signed
              ? 'Decision recorded by the Competent Authority (demo). Event EV-0006 appended to the audit chain.'
              : allVerified
                ? 'All extracted values are verified. Only the authorised official can record the decision.'
                : `${verifiedCount} of ${EXTRACTED_FIELDS.length} values verified. Sign-off unlocks once an officer has verified every proposal.`}
          </p>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={String(signed)} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              {signed ? (
                <StatusPill tone="verified">Signed off · Competent Authority</StatusPill>
              ) : (
                <button type="button" className="ctl" data-primary disabled={!allVerified} onClick={() => setSigned(true)}>
                  Sign off as Competent Authority
                </button>
              )}
            </motion.div>
          </AnimatePresence>
        </aside>
      </div>

      <DemoControls label="Authority demo" status={signed ? 'Decision signed off.' : `${verifiedCount} of ${EXTRACTED_FIELDS.length} values verified.`}>
        <Ctl onClick={reset} disabled={verifiedCount === 0 && !signed}>Reset demo</Ctl>
      </DemoControls>
    </div>
  )
}
