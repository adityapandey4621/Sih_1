import { lazy, Suspense } from 'react'
import { createBrowserRouter } from 'react-router-dom'
import { SiteLayout } from '../components/layout/SiteLayout'

const HomePage = lazy(() => import('../features/home/HomePage'))

export const router = createBrowserRouter([
  {
    element: <SiteLayout />,
    children: [
      {
        index: true,
        element: (
          <Suspense fallback={null}>
            <HomePage />
          </Suspense>
        ),
      },
    ],
  },
])
