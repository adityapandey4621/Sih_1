import { useId, useState, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react'
import { Eye, EyeOff } from 'lucide-react'

interface FieldChrome {
  label: string
  hint?: ReactNode
  error?: string | null
  optional?: boolean
}

interface WrapProps extends FieldChrome {
  id: string
  children: ReactNode
}

function Wrap({ id, label, hint, error, optional, children }: WrapProps) {
  return (
    <div className="field" data-invalid={error ? 'true' : undefined}>
      <label className="field__label" htmlFor={id}>
        {label}
        {optional && <span className="field__optional"> (optional)</span>}
      </label>
      {children}
      {hint && (
        <p className="field__hint" id={`${id}-hint`}>
          {hint}
        </p>
      )}
      {error && (
        <p className="field__error" id={`${id}-error`}>
          {error}
        </p>
      )}
    </div>
  )
}

const describedBy = (id: string, hint: ReactNode, error?: string | null) =>
  [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(' ') || undefined

export function TextField({ label, hint, error, optional, ...input }: FieldChrome & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId()
  return (
    <Wrap id={id} label={label} hint={hint} error={error} optional={optional}>
      <input
        {...input}
        id={id}
        className="field__control"
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
      />
    </Wrap>
  )
}

export function TextAreaField({ label, hint, error, optional, ...input }: FieldChrome & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const id = useId()
  return (
    <Wrap id={id} label={label} hint={hint} error={error} optional={optional}>
      <textarea
        {...input}
        id={id}
        className="field__control field__control--area"
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(id, hint, error)}
      />
    </Wrap>
  )
}

export function PasswordField({ label, hint, error, ...input }: FieldChrome & Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>) {
  const id = useId()
  const [visible, setVisible] = useState(false)
  return (
    <Wrap id={id} label={label} hint={hint} error={error}>
      <div className="field__password">
        <input
          {...input}
          id={id}
          type={visible ? 'text' : 'password'}
          className="field__control"
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(id, hint, error)}
        />
        <button
          type="button"
          className="field__toggle"
          onClick={() => setVisible((v) => !v)}
          aria-pressed={visible}
          aria-label={visible ? 'Hide password' : 'Show password'}
        >
          {visible ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}
        </button>
      </div>
    </Wrap>
  )
}

interface Option {
  value: string
  label: string
}

export function SelectField({ label, hint, error, optional, options, placeholder = 'Select…', ...select }: FieldChrome & SelectHTMLAttributes<HTMLSelectElement> & { options: Option[]; placeholder?: string }) {
  const id = useId()
  return (
    <Wrap id={id} label={label} hint={hint} error={error} optional={optional}>
      <select {...select} id={id} className="field__control" aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, hint, error)}>
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </Wrap>
  )
}

export function CheckboxField({ label, hint, error, ...input }: FieldChrome & Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>) {
  const id = useId()
  return (
    <div className="field field--check" data-invalid={error ? 'true' : undefined}>
      <div className="field__checkrow">
        <input {...input} id={id} type="checkbox" className="field__checkbox" aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, hint, error)} />
        <label className="field__label" htmlFor={id}>
          {label}
        </label>
      </div>
      {hint && (
        <p className="field__hint" id={`${id}-hint`}>
          {hint}
        </p>
      )}
      {error && (
        <p className="field__error" id={`${id}-error`}>
          {error}
        </p>
      )}
    </div>
  )
}