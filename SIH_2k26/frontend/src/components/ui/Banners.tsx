import { FlaskConical, ShieldAlert } from 'lucide-react'
import type { Verification } from '../../features/applications/types'

/** Persistent label for any configuration that is not verified official rule data. */
export function DemoBanner({ text }: { text?: string | null }) {
  return (
    <div className="demo-banner" role="note">
      <FlaskConical size={16} aria-hidden="true" />
      <span>
        <strong>{text ?? 'DEMO ONLY — NOT VERIFIED AS CURRENT OFFICIAL RULES'}</strong> The requirements below are illustrative and invented for development; they are not statements of AICTE requirements.
      </span>
    </div>
  )
}

const LABEL: Record<Verification, string> = {
  verified: 'Sources verified against the official publication',
  unverified: 'Sources NOT verified against the official publication',
  demo: 'Demo only — not an official rule',
}

export function VerificationNote({ verification }: { verification: Verification }) {
  if (verification === 'demo') return null
  return (
    <p className={`verification verification--${verification}`}>
      <ShieldAlert size={14} aria-hidden="true" /> {LABEL[verification]}
    </p>
  )
}

export const VERIFICATION_LABEL = LABEL
