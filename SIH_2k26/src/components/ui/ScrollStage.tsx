import type { ReactNode } from 'react'
import { motion, useReducedMotion, useTransform, type MotionValue } from 'motion/react'

interface ScrollStageProps {
  progress: MotionValue<number>
  className?: string
  children: ReactNode
}

/** Gives a scene's visual a spatial entrance and exit: it tips in from depth, settles, then recedes. */
export function ScrollStage({ progress, className, children }: ScrollStageProps) {
  const reduced = useReducedMotion()
  const rotateX = useTransform(progress, [0, 0.35, 0.65, 1], [14, 0, 0, -8])
  const y = useTransform(progress, [0, 0.35, 0.65, 1], [70, 0, 0, -50])
  const opacity = useTransform(progress, [0, 0.22, 0.8, 1], [0, 1, 1, 0.2])
  const scale = useTransform(progress, [0, 0.35, 0.65, 1], [0.95, 1, 1, 0.97])

  return (
    <div className={`stage ${className ?? ''}`}>
      <motion.div className="stage__inner" style={reduced ? undefined : { rotateX, y, opacity, scale }}>
        {children}
      </motion.div>
    </div>
  )
}
