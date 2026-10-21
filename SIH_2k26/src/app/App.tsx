import { RouterProvider } from 'react-router-dom'
import { MotionConfig } from 'motion/react'
import { router } from './router'

export function App() {
  return (
    <MotionConfig reducedMotion="user">
      <RouterProvider router={router} />
    </MotionConfig>
  )
}
