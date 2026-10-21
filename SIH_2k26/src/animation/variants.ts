import type { Variants } from 'motion/react'
import { DURATION, EASE_OUT } from './motion-config'

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 28 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: DURATION.base, ease: EASE_OUT },
  },
}

export const stagger = (gap = 0.08, delay = 0): Variants => ({
  hidden: {},
  visible: { transition: { staggerChildren: gap, delayChildren: delay } },
})

/** A word rising out of an overflow-hidden mask. */
export const wordRise: Variants = {
  hidden: { y: '110%' },
  visible: { y: '0%', transition: { duration: DURATION.slow, ease: EASE_OUT } },
}
