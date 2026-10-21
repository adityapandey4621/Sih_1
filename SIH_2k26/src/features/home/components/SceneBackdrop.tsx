import { motion, useTransform, type MotionValue } from 'motion/react'

interface SceneBackdropProps {
  /** Whole-page scroll progress, 0..1. */
  progress: MotionValue<number>
}

/** Atmosphere: cool night → warm dusk crossfade, drifting light, and a flowing perspective floor. */
export function SceneBackdrop({ progress }: SceneBackdropProps) {
  const warm = useTransform(progress, [0, 0.2, 0.42, 0.62, 0.82, 1], [0, 0.15, 0.75, 0.2, 0.7, 0.35])
  const floor = useTransform(progress, [0, 0.12], [1, 0])

  return (
    <div className="backdrop" aria-hidden="true">
      <div className="backdrop__cool" />
      <motion.div className="backdrop__warm" style={{ opacity: warm }} />
      <span className="backdrop__aurora backdrop__aurora--a" />
      <span className="backdrop__aurora backdrop__aurora--b" />
      <motion.div className="backdrop__floor" style={{ opacity: floor }}>
        <div className="backdrop__grid">
          <div className="backdrop__flow" />
        </div>
      </motion.div>
      <div className="backdrop__vignette" />
    </div>
  )
}


