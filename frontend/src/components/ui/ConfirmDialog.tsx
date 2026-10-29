import { useEffect, useRef, type ReactNode } from 'react'
import { Button } from './Button'

interface Props {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel: string
  busy?: boolean
  danger?: boolean
  /** Disable the confirm button (e.g. until a required reason has been typed). */
  confirmDisabled?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/** Modal confirmation built on the native <dialog>: focus is trapped, Escape cancels, background is inert. */
export function ConfirmDialog({ open, title, children, confirmLabel, busy, danger, confirmDisabled, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    if (open && !el.open) el.showModal()
    if (!open && el.open) el.close()
  }, [open])

  return (
    <dialog ref={ref} className="dialog" aria-labelledby="dialog-title" onCancel={(e) => { e.preventDefault(); if (!busy) onCancel() }}>
      {open && (
        <form method="dialog" onSubmit={(e) => { e.preventDefault(); if (!confirmDisabled && !busy) onConfirm() }}>
          <h2 id="dialog-title" className="dialog__title">
            {title}
          </h2>
          <div className="dialog__body">{children}</div>
          <div className="dialog__actions">
            <Button variant="quiet" onClick={onCancel} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" variant={danger ? 'danger' : 'primary'} loading={busy} disabled={confirmDisabled}>
              {confirmLabel}
            </Button>
          </div>
        </form>
      )}
    </dialog>
  )
}
