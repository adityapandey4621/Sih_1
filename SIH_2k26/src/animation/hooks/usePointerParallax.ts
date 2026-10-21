import { useEffect } from 'react'
import { useMotionValue, useReducedMotion, useSpring, type MotionValue } from 'motion/react'
import { SPRING_POINTER } from '../motion-config'

export interface PointerParallax {
  /** Normalised pointer position, -1..1, smoothed. */
  x: MotionValue<number>
  y: MotionValue<number>
}

/** Tracks the pointer on fine-pointer devices; stays at rest on touch or reduced motion. */
export function usePointerParallax(): PointerParallax {
  const reduced = useReducedMotion()
  const rawX = useMotionValue(0)
  const rawY = useMotionValue(0)
  const x = useSpring(rawX, SPRING_POINTER)
  const y = useSpring(rawY, SPRING_POINTER)

  useEffect(() => {
    if (reduced || !window.matchMedia('(pointer: fine)').matches) return
    const onMove = (event: PointerEvent) => {
      rawX.set((event.clientX / window.innerWidth) * 2 - 1)
      rawY.set((event.clientY / window.innerHeight) * 2 - 1)
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    return () => window.removeEventListener('pointermove', onMove)
  }, [reduced, rawX, rawY])

  return { x, y }
}
