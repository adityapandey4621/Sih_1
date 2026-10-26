import { CheckboxField, SelectField, TextAreaField, TextField } from '../../components/ui/Field'
import type { ChecklistState } from './types'

interface Props {
  item: ChecklistState
  value: unknown
  error?: string | null
  onChange: (value: unknown) => void
  disabled?: boolean
}

const hintFor = (i: ChecklistState) => [i.explanation, i.clause_ref ? `Reference: ${i.clause_ref}` : null].filter(Boolean).join(' ') || undefined

/** Renders one configured form item. Types, limits and options come entirely from the configuration. */
export function FormItem({ item, value, error, onChange, disabled }: Props) {
  const label = item.label
  const optional = !item.required
  const hint = hintFor(item)
  const str = value === undefined || value === null ? '' : String(value)

  if (item.item_type === 'declaration') {
    return <CheckboxField label={label} hint={hint} error={error} checked={value === true} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
  }
  switch (item.field_kind) {
    case 'long_text':
      return <TextAreaField label={label} hint={hint} error={error} optional={optional} value={str} disabled={disabled} maxLength={item.validation?.max_length ?? 4000} onChange={(e) => onChange(e.target.value)} />
    case 'select':
      return <SelectField label={label} hint={hint} error={error} optional={optional} value={str} disabled={disabled} options={(item.options ?? []).map((o) => ({ value: o, label: o }))} onChange={(e) => onChange(e.target.value)} />
    case 'number':
      return <TextField label={label} hint={hint} error={error} optional={optional} type="number" inputMode="decimal" value={str} disabled={disabled} min={item.validation?.min} max={item.validation?.max} onChange={(e) => onChange(e.target.value)} />
    case 'date':
      return <TextField label={label} hint={hint} error={error} optional={optional} type="date" value={str} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
    default:
      return <TextField label={label} hint={hint} error={error} optional={optional} value={str} disabled={disabled} maxLength={item.validation?.max_length ?? 200} onChange={(e) => onChange(e.target.value)} />
  }
}
