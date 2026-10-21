export type EvidenceState = 'awaiting' | 'received' | 'verifying' | 'verified'

export interface EvidenceItem {
  id: string
  name: string
  detail: string
}

export const EVIDENCE: readonly EvidenceItem[] = [
  { id: 'fire', name: 'Fire-safety certificate', detail: 'Issued by local fire authority' },
  { id: 'structural', name: 'Structural stability report', detail: 'Chartered engineer, signed' },
  { id: 'lease', name: 'Lease renewal deed', detail: 'Replaces the expiring lease' },
]

export interface ComplianceStep {
  title: string
  detail: string
  evidence: readonly [EvidenceState, EvidenceState, EvidenceState]
  undertakingDaysLeft: number
  leaseDaysLeft: number
  /** Whether the renewal lease has replaced the expiring one. */
  renewed: boolean
  complete: boolean
}

/** Demo values: the due date and all day counts are illustrative. */
export const UNDERTAKING = { title: 'Undertaking: safety certificates', due: '30 Nov 2026', totalDays: 30 } as const

export const COMPLIANCE_STEPS: readonly ComplianceStep[] = [
  { title: 'Undertaking issued', detail: 'Three evidence items are due. Reminders are scheduled against the due date.', evidence: ['awaiting', 'awaiting', 'awaiting'], undertakingDaysLeft: 30, leaseDaysLeft: 45, renewed: false, complete: false },
  { title: 'First evidence arrives', detail: 'One of three items received and entering verification. A partial submission stays pending.', evidence: ['verifying', 'awaiting', 'awaiting'], undertakingDaysLeft: 24, leaseDaysLeft: 38, renewed: false, complete: false },
  { title: 'Officer confirms it', detail: 'The verifying officer confirms the first item. Reminders continue for the two outstanding items.', evidence: ['verified', 'received', 'awaiting'], undertakingDaysLeft: 18, leaseDaysLeft: 30, renewed: false, complete: false },
  { title: 'Lease nears expiry', detail: 'The institution\'s lease expires in 12 days. The system flags it before it lapses.', evidence: ['verified', 'verified', 'awaiting'], undertakingDaysLeft: 12, leaseDaysLeft: 12, renewed: false, complete: false },
  { title: 'Renewal arrives', detail: 'A renewal deed is uploaded and enters verification. The old lease is not superseded until it is confirmed.', evidence: ['verified', 'verified', 'verifying'], undertakingDaysLeft: 9, leaseDaysLeft: 9, renewed: false, complete: false },
  { title: 'Verified and complete', detail: 'The officer confirms the renewal. The undertaking closes and unnecessary future reminders are cancelled.', evidence: ['verified', 'verified', 'verified'], undertakingDaysLeft: 8, leaseDaysLeft: 8, renewed: true, complete: true },
]

export const EVIDENCE_STATE_LABEL: Record<EvidenceState, string> = {
  awaiting: 'Awaiting',
  received: 'Received',
  verifying: 'In verification',
  verified: 'Verified by officer',
}
