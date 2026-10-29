import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Link, type LinkProps } from 'react-router-dom'
import { Loader2 } from 'lucide-react'

type Variant = 'primary' | 'secondary' | 'quiet' | 'danger'
type Size = 'md' | 'sm'

interface Common {
  variant?: Variant
  size?: Size
  children: ReactNode
}

const cls = (variant: Variant, size: Size) => `btn btn--${variant} btn--${size}`

interface ButtonProps extends Common, Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  loading?: boolean
}

export function Button({ variant = 'primary', size = 'md', loading = false, disabled, children, className, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      className={`${cls(variant, size)} ${className ?? ''}`.trim()}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading && <Loader2 className="btn__spinner" size={16} aria-hidden="true" />}
      {children}
    </button>
  )
}

interface ButtonLinkProps extends Common, Omit<LinkProps, 'children'> {}

export function ButtonLink({ variant = 'primary', size = 'md', children, className, ...rest }: ButtonLinkProps) {
  return (
    <Link {...rest} className={`${cls(variant, size)} ${className ?? ''}`.trim()}>
      {children}
    </Link>
  )
}

/** Same look as ButtonLink but for in-page anchors (#hash) where a router Link would navigate. */
export function ButtonAnchor({ variant = 'primary', size = 'md', children, className, ...rest }: Common & React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a {...rest} className={`${cls(variant, size)} ${className ?? ''}`.trim()}>
      {children}
    </a>
  )
}
