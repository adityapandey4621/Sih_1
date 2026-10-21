import type { ReactNode } from 'react'
import { motion, useTransform, type MotionValue } from 'motion/react'
import type { PointerParallax } from '../../../animation/hooks/usePointerParallax'

interface DepthLayerProps {
  pointer: PointerParallax
  /** Pixels of pointer-driven travel (farther layers use smaller values). */
  depth: number
  /** Scroll progress and the pixel travel it maps to. */
  scroll: MotionValue<number>
  scrollShift: number
  className?: string
  children: ReactNode
}

/** Moves a layer by pointer position and scroll progress at its own depth, independent of ambient CSS motion inside. */
export function DepthLayer({ pointer, depth, scroll, scrollShift, className, children }: DepthLayerProps) {
  const x = useTransform(pointer.x, (v) => v * depth)
  const y = useTransform([pointer.y, scroll], (latest: number[]) => {
    const [py = 0, s = 0] = latest
    return py * depth + s * scrollShift
  })
  return (
    <motion.div className={className} style={{ x, y }}>
      {children}
    </motion.div>
  )
}
