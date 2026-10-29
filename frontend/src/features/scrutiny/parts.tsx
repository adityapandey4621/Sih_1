import type { ReactNode } from 'react'
import { BadgeCheck, ClipboardCheck, FileSearch, FileText, Flag, Gavel, Inbox, MessageSquareReply, PenLine, Send, type LucideIcon } from 'lucide-react'
import { Badge, type Tone } from '../../components/ui/feedback'
import type { CaseState, FindingState, NoticeStatus, PolicyResolution, RuleVersionRef, SourceRef } from './types'

const CASE_TONE: Record<CaseState, Tone> = {
  AWAITING_ASSIGNMENT: 'neutral',
  READY_FOR_SCRUTINY: 'info',
  UNDER_SCRUTINY: 'info',
  AWAITING_NOTICE_DECISION: 'warning',
  AWAITING_RESPONSE: 'warning',
  AWAITING_RESCRUTINY: 'info',
  UNDER_RESCRUTINY: 'info',
  SCRUTINY_COMPLETED: 'success',
  CONFIGURATION_EXCEPTION: 'error',
}

/** The state's wording comes from the server (`label`); only the tone is chosen here. The label is always shown, never colour alone. */
export const CaseStateBadge = ({ state, label }: { state: CaseState; label: string }) => <Badge tone={CASE_TONE[state] ?? 'neutral'}>{label}</Badge>

export type StageKind = 'automated' | 'finding' | 'draft' | 'in-review' | 'notice' | 'response' | 'rescrutiny' | 'completed' | 'next-stage'

const STAGES: Record<StageKind, { label: string; icon: LucideIcon; hint: string }> = {
  automated: { label: 'Automated preliminary issue', icon: FileSearch, hint: 'Found by a technical check. Not a finding and not a notice.' },
  finding: { label: 'Reviewer finding', icon: PenLine, hint: 'Recorded by an assigned reviewer. Not yet a formal notice.' },
  draft: { label: 'Notice draft — not issued', icon: FileText, hint: 'Internal. The institution cannot see it.' },
  'in-review': { label: 'Prepared — awaiting an issuing decision', icon: Send, hint: 'Internal. An authorised issuer has not issued it.' },
  notice: { label: 'Formal deficiency notice — issued', icon: Gavel, hint: 'Issued by an authorised person and visible to the institution.' },
  response: { label: 'Institution response', icon: Inbox, hint: 'Received, not accepted. Re-scrutiny examines it.' },
  rescrutiny: { label: 'Re-scrutiny result', icon: MessageSquareReply, hint: 'The reviewer’s conclusion after examining the response.' },
  completed: { label: 'Scrutiny completed', icon: ClipboardCheck, hint: 'A stage outcome. This is not an approval.' },
  'next-stage': { label: 'Ready for next stage', icon: Flag, hint: 'Queued for the next configured stage; that stage’s workflow is not available yet.' },
}

/** Names the kind of record in words and an icon so the product's separate steps are never confused with one another. */
export function StageTag({ kind }: { kind: StageKind }) {
  const s = STAGES[kind]
  return (
    <span className="stage-tag" data-kind={kind} title={s.hint}>
      <s.icon size={13} aria-hidden="true" />
      {s.label}
    </span>
  )
}

const FINDING: Record<FindingState, { tone: Tone; label: string }> = {
  OPEN: { tone: 'warning', label: 'Open' },
  NOTICED: { tone: 'warning', label: 'In an issued notice — response awaited' },
  UNRESOLVED: { tone: 'error', label: 'Unresolved after re-scrutiny' },
  RESOLVED: { tone: 'success', label: 'Resolved' },
  NOT_SUPPORTED: { tone: 'neutral', label: 'Not supported' },
}
export const FindingStateBadge = ({ state }: { state: FindingState }) => <Badge tone={FINDING[state].tone}>{FINDING[state].label}</Badge>

const NOTICE: Record<NoticeStatus, { tone: Tone; label: string }> = {
  DRAFT: { tone: 'neutral', label: 'Draft — not issued' },
  IN_REVIEW: { tone: 'warning', label: 'Awaiting issuing decision' },
  ISSUED: { tone: 'warning', label: 'Issued — awaiting response' },
  RESPONSE_RECEIVED: { tone: 'info', label: 'Response received' },
  CLOSED: { tone: 'success', label: 'Closed after re-scrutiny' },
  WITHDRAWN: { tone: 'neutral', label: 'Withdrawn — never issued' },
}
export const NoticeStatusBadge = ({ status }: { status: NoticeStatus }) => <Badge tone={NOTICE[status].tone}>{NOTICE[status].label}</Badge>

export function RuleTag({ rule }: { rule: RuleVersionRef }) {
  return (
    <span className="rule-tag">
      <span className="mono">
        {rule.rule_set} · v{rule.number}
      </span>
      {rule.classification === 'DEMO' && <span className="demo-tag">DEMO ONLY</span>}
    </span>
  )
}

export function SourceRefView({ source, status }: { source: SourceRef | null; status: string }) {
  if (status !== 'ESTABLISHED' || !source) return <span className="note">No source reference established — flagged for source verification.</span>
  return (
    <span>
      {source.label}
      {source.clause_ref ? ` — ${source.clause_ref}` : ''}
      <span className="muted">
        {' '}
        ·{' '}
        {source.verification_status === 'VERIFIED_OFFICIAL' ? (
          <span>
            <BadgeCheck size={12} aria-hidden="true" /> verified as official
          </span>
        ) : (
          'not verified as official'
        )}
      </span>
    </span>
  )
}

const POLICY_WORDS: Record<string, string> = { COMPUTED: 'from a verified policy', DEMO_COMPUTED: 'from a demonstration policy', NO_DURATION: 'not established', MISSING: 'no policy configured', AMBIGUOUS: 'more than one policy applies', UNVERIFIED: 'source not verified', UNSUPPORTED: 'cannot be counted' }

export function PolicyView({ policy }: { policy: PolicyResolution }) {
  const period = policy.duration_value ? `${policy.duration_value} ${policy.duration_unit}${policy.calendar_convention ? ` (${policy.calendar_convention.toLowerCase()})` : ''}` : null
  return (
    <div className="policy" data-state={policy.state}>
      <p>
        <strong>Response period:</strong> {POLICY_WORDS[policy.state] ?? policy.state.toLowerCase()}
        {policy.demo && ' — DEMO ONLY'}
      </p>
      <p className="note">{policy.message}</p>
      {period && (
        <p className="note mono">
          {period}
          {policy.source ? ` · ${policy.source.title} (${policy.source.verification_status.replace(/_/g, ' ').toLowerCase()})` : ''}
        </p>
      )}
    </div>
  )
}

export function Facts({ children }: { children: ReactNode }) {
  return <dl className="facts">{children}</dl>
}
export const Fact = ({ label, children }: { label: string; children: ReactNode }) => (
  <div>
    <dt>{label}</dt>
    <dd>{children}</dd>
  </div>
)
