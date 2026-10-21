import { useCallback, useRef, useState } from 'react'
import { useMotionValueEvent, type MotionValue } from 'motion/react'
import { clamp } from '../../../lib/math'

/** Maps scene progress to an integer step across [from, to] of the scroll range. */
export const stepper = (count: number, from = 0.3, to = 0.7) => (p: number) =>
  clamp(Math.floor(((p - from) / (to - from)) * count), 0, count - 1)

/**
 * A demo value that follows scroll progress until the user takes control,
 * then stays where they put it until they choose to follow the scroll again.
 * State only updates when the mapped value changes, never per scroll frame.
 */
export function useScrollDriven(progress: MotionValue<number>, toValue: (p: number) => number, initial = 0) {
  const [value, setValue] = useState(initial)
  const [manual, setManual] = useState(false)
  const manualRef = useRef(false)
  const mapRef = useRef(toValue)
  mapRef.current = toValue

  useMotionValueEvent(progress, 'change', (p) => {
    if (manualRef.current) return
    const next = mapRef.current(p)
    setValue((prev) => (prev === next ? prev : next))
  })

  const set = useCallback((next: number | ((prev: number) => number)) => {
    manualRef.current = true
    setManual(true)
    setValue(next)
  }, [])

  const follow = useCallback(() => {
    manualRef.current = false
    setManual(false)
    setValue(mapRef.current(progress.get()))
  }, [progress])

  return { value, set, following: !manual, follow }
}
