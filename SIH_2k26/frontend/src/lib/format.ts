const dateTime = new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short' })
const dateOnly = new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium' })

export const formatDate = (iso: string | null | undefined) => (iso ? dateTime.format(new Date(iso)) : '—')
export const formatDay = (iso: string | null | undefined) => (iso ? dateOnly.format(new Date(iso)) : '—')

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

/** "intake_increase" → "Intake increase" for event names and keys shown in history lists. */
export const humanise = (key: string) => {
  const s = key.replace(/[._-]+/g, ' ').trim()
  return s.charAt(0).toUpperCase() + s.slice(1)
}
