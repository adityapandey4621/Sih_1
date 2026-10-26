import { ButtonLink } from '../components/ui/Button'
import { EmptyState } from '../components/ui/feedback'
import { PageHeader } from '../features/shell/PageHeader'
import { Wordmark } from '../components/ui/Wordmark'

export function NotFoundPage() {
  return (
    <div className="boot" style={{ alignContent: 'center', justifyItems: 'start', gap: 'var(--s-5)' }}>
      <Wordmark />
      <h1 className="display" style={{ fontSize: '2.25rem', color: 'var(--ink)' }}>
        Page not found
      </h1>
      <p style={{ color: 'var(--text-muted)' }}>The address you followed does not exist.</p>
      <ButtonLink to="/" variant="secondary">
        Go to the overview
      </ButtonLink>
    </div>
  )
}

export function NotFoundInShell() {
  return (
    <>
      <PageHeader title="Page not found" />
      <EmptyState title="There is nothing at this address" action={<ButtonLink to="/app" variant="secondary" size="sm">Back to my dashboard</ButtonLink>} />
    </>
  )
}
