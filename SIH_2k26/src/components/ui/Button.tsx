import type { ReactNode } from 'react'
import { motion } from 'motion/react'
import './ui.css'

interface ButtonProps {
  href: string
  children: ReactNode
  variant?: 'primary' | 'ghost'
  size?: 'md' | 'sm'
  onClick?: () => void
}

export function Button({ href, children, variant = 'primary', size = 'md', onClick }: ButtonProps) {
  return (
    <motion.a
      className="btn"
      data-variant={variant}
      data-size={size}
      href={href}
      onClick={onClick}
      whileHover={{ y: -2 }}
      whileTap={{ scale: 0.96, y: 0 }}
      transition={{ type: 'spring', stiffness: 420, damping: 24 }}
    >
      <span className="btn__label">{children}</span>
      <span className="btn__arrow" aria-hidden="true">→</span>
    </motion.a>
  )
}
