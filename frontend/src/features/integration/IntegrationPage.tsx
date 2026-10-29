import { useState } from 'react'
import { Button } from '../../components/ui/Button'
import { ConfirmDialog } from '../../components/ui/ConfirmDialog'
import { Alert, Badge, EmptyState, type Tone } from '../../components/ui/feedback'
import { ApiError, request } from '../../lib/api'
import { formatDate, humanise } from '../../lib/format'
import { useResource } from '../../lib/useResource'
import { ResourceView } from '../admin/shared'
import { useAuth } from '../auth/AuthContext'
import { Section } from '../applications/parts'
import { Fact, Facts } from '../scrutiny/parts'
import { PageHeader } from '../shell/PageHeader'
import '../applications/applications.css'
import '../scrutiny/scrutiny.css'
import '../committee/committee.css'

interface SyncRow {
  id: string
  kind: string
  status: string
  application_reference: string | null
  attempts: number
  last_error: string | null
  last_outcome: string | null
  next_attempt_at: string | null
  last_attempt_at: string | null
  confirmed_at: string | null
  external_ref: string | null
  adapter: string | null
}

interface IntegrationStatus {
  mode: string
  mode_note: string
  adapter: { adapter: string; demo_only: boolean; configured: boolean; circuit_open: boolean; contract_version: string; base_host: string | null } | null
  verified: boolean
  verification_note: string
  contract: { version: string; label: string }
  counts: Record<string, number>
  recent: SyncRow[]
  log: { at: string; sync_id: string; adapter: string; operation: string; attempt_no: number; outcome: string; http_status: number | null; latency_ms: number }[]
  limits: { max_attempts: number; backoff_seconds: number }
}

const TONE: Record<string, Tone> = { SYNCED: 'success', SYNC_PENDING: 'warning', FAILED: 'error', RECONCILIATION_REQUIRED: 'warning', NOT_CONFIGURED: 'neutral' }
const LABEL: Record<string, string> = { SYNCED: 'Confirmed', SYNC_PENDING: 'Pending', FAILED: 'Failed', RECONCILIATION_REQUIRED: 'Reconciliation required', NOT_CONFIGURED: 'Not configured' }

export default function IntegrationPage() {
  const { can } = useAuth()
  const [res, reload] = useResource(() => request<IntegrationStatus>('/api/integration/status'), [])
  const [retryId, setRetryId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function retry() {
    if (!retryId) return
    setBusy(true)
    setError(null)
    try {
      await request(`/api/integration/syncs/${retryId}/retry`, { method: 'POST', body: {} })
      setRetryId(null)
      reload()
    } catch (e) {
      setRetryId(null)
      setError(e instanceof ApiError ? e.message : 'Unexpected error.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <PageHeader title="Integration with AICTE" description="What this system has sent to an external AICTE-side system, and what that system has confirmed." crumbs={[{ label: 'Workspace', to: '/app' }, { label: 'Integration' }]} />
      <ResourceView resource={res} reload={reload}>
        {(s) => (
          <>
            <Alert tone="warning" title="Real AICTE integration is UNVERIFIED">
              {s.verification_note}
            </Alert>
            <Section title="Configuration" aside={s.adapter?.demo_only ? <Badge tone="warning">DEMO ONLY</Badge> : undefined}>
              <Facts>
                <Fact label="Mode">
                  <span className="mono">{s.mode}</span> — {s.mode_note}
                </Fact>
                <Fact label="Adapter">{s.adapter ? `${s.adapter.adapter}${s.adapter.base_host ? ` → ${s.adapter.base_host}` : ''}` : 'None: nothing is sent'}</Fact>
                <Fact label="Contract">
                  <span className="mono">{s.contract.version}</span> — {s.contract.label}
                </Fact>
                <Fact label="Retries">
                  At most {s.limits.max_attempts} attempts, backing off from {s.limits.backoff_seconds}s. Then a person must decide.
                </Fact>
                {s.adapter?.circuit_open && <Fact label="Circuit">Open: calls are paused briefly after repeated failures.</Fact>}
              </Facts>
              <p className="chips">
                {Object.entries(s.counts).map(([k, n]) => (
                  <Badge key={k} tone={TONE[k] ?? 'neutral'}>
                    {LABEL[k] ?? humanise(k)} {n}
                  </Badge>
                ))}
              </p>
            </Section>
            {error && <Alert tone="error">{error}</Alert>}
            <Section title="Records">
              {s.recent.length === 0 ? (
                <EmptyState title="Nothing has been synchronised yet">An official outcome or a Council step creates a record here when it is issued or recorded.</EmptyState>
              ) : (
                <div className="table-wrap">
                  <table className="table queue-table">
                    <caption className="sr-only">Synchronisation records</caption>
                    <thead>
                      <tr>
                        <th scope="col">Application</th>
                        <th scope="col">What</th>
                        <th scope="col">State</th>
                        <th scope="col">Attempts</th>
                        <th scope="col" />
                      </tr>
                    </thead>
                    <tbody>
                      {s.recent.map((r) => (
                        <tr key={r.id}>
                          <td className="mono">{r.application_reference}</td>
                          <td>{humanise(r.kind)}</td>
                          <td>
                            <Badge tone={TONE[r.status] ?? 'neutral'}>{LABEL[r.status] ?? humanise(r.status)}</Badge>
                            {r.external_ref && <div className="mono muted">{r.external_ref}</div>}
                            {r.last_error && <div className="note">{r.last_error}</div>}
                            {r.next_attempt_at && r.status === 'SYNC_PENDING' && <div className="muted">Next attempt after {formatDate(r.next_attempt_at)}</div>}
                          </td>
                          <td>{r.attempts}</td>
                          <td>
                            {can('integration:manage') && (r.status === 'FAILED' || r.status === 'RECONCILIATION_REQUIRED') && (
                              <Button size="sm" variant="secondary" onClick={() => setRetryId(r.id)}>
                                Try again…
                              </Button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>
            <Section title="Call log">
              <p className="note">Outcome codes and timings only. The log holds no message bodies, documents or secrets.</p>
              {s.log.length === 0 ? (
                <p className="note">No call has been made.</p>
              ) : (
                <ul className="plain-list">
                  {s.log.map((g, i) => (
                    <li key={`${g.at}-${i}`}>
                      <span className="mono">{formatDate(g.at)}</span> · {g.adapter} {g.operation} #{g.attempt_no} → <strong>{humanise(g.outcome)}</strong>
                      {g.http_status ? ` (HTTP ${g.http_status})` : ''} · {g.latency_ms} ms
                    </li>
                  ))}
                </ul>
              )}
            </Section>
            <ConfirmDialog open={retryId !== null} title="Try this again?" confirmLabel="Try again" busy={busy} onCancel={() => setRetryId(null)} onConfirm={retry}>
              <p>It gets a fresh set of attempts and one is made now, with the same message id, so the receiver cannot end up with it twice. Use this after the cause has been fixed.</p>
            </ConfirmDialog>
          </>
        )}
      </ResourceView>
    </>
  )
}
