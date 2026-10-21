export const clamp = (value: number, min = 0, max = 1) => Math.min(max, Math.max(min, value))

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t

export const smoothstep = (t: number) => {
  const x = clamp(t)
  return x * x * (3 - 2 * x)
}
