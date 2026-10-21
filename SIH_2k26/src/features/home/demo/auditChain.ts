import { sha256 } from '../../../lib/sha256'

export interface AuditEvent {
  id: string
  at: string
  actor: string
  action: string
  reason: string
}

export interface LedgerEntry {
  event: AuditEvent
  prevHash: string
  hash: string
}

export interface LinkCheck {
  ok: boolean
  /** Hash recomputed from the entry as it is stored now. */
  recomputed: string
}

export interface VerificationResult {
  ok: boolean
  /** Index of the first entry whose stored hash no longer matches its content or predecessor. */
  firstBroken: number | null
  checks: LinkCheck[]
}

export const GENESIS_HASH = '0'.repeat(64)

const canonical = (e: AuditEvent) => JSON.stringify([e.id, e.at, e.actor, e.action, e.reason])

export const hashEntry = (prevHash: string, event: AuditEvent) => sha256(`${prevHash}|${canonical(event)}`)

export function buildLedger(events: readonly AuditEvent[]): LedgerEntry[] {
  let prev = GENESIS_HASH
  return events.map((event) => {
    const hash = hashEntry(prev, event)
    const entry = { event, prevHash: prev, hash }
    prev = hash
    return entry
  })
}

/** Recomputes every link from stored content and checks it against the stored hashes. */
export function verifyLedger(entries: readonly LedgerEntry[]): VerificationResult {
  let expectedPrev = GENESIS_HASH
  let firstBroken: number | null = null
  const checks = entries.map((entry, i) => {
    const recomputed = hashEntry(entry.prevHash, entry.event)
    const linkOk = entry.prevHash === expectedPrev && recomputed === entry.hash
    if (!linkOk && firstBroken === null) firstBroken = i
    // Once a link breaks, everything after it chains from a record that no longer checks out.
    const ok = firstBroken === null
    expectedPrev = entry.hash
    return { ok, recomputed }
  })
  return { ok: firstBroken === null, firstBroken, checks }
}

export const AUDIT_EVENTS: readonly AuditEvent[] = [
  { id: 'EV-0001', at: '2026-10-02 09:14 UTC', actor: 'Institution portal user', action: 'Application submitted', reason: 'Initial submission of AICTE-2026-0042' },
  { id: 'EV-0002', at: '2026-10-06 11:02 UTC', actor: 'Scrutiny officer', action: 'Returned for deficiencies', reason: 'Fire-safety certificate missing' },
  { id: 'EV-0003', at: '2026-10-15 16:40 UTC', actor: 'Institution portal user', action: 'Documents resubmitted', reason: 'Certificate uploaded in response to return' },
  { id: 'EV-0004', at: '2026-10-19 10:25 UTC', actor: 'Regional office coordinator', action: 'EVC visit scheduled', reason: 'Visit date set for 18 Nov 2026' },
  { id: 'EV-0005', at: '2026-10-27 14:08 UTC', actor: 'Verifying officer', action: 'Lease details verified', reason: 'Officer confirmed extracted lessor and validity dates' },
  { id: 'EV-0006', at: '2026-11-24 17:31 UTC', actor: 'Competent Authority', action: 'Decision recorded', reason: 'Explicit sign-off after committee review' },
]

export const TAMPER_INDEX = 2

