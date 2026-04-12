import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import Layout from './components/layout/Layout'
import Home from './pages/Home'
import Review from './pages/Review'
import Accounts from './pages/Accounts'
import Desks from './pages/Desks'
import History from './pages/History'
import Settings from './pages/Settings'
import Engagement from './pages/Engagement'
import Threads from './pages/Threads'

const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true,           element: <Home /> },
      { path: 'review',        element: <Review /> },
      { path: 'accounts',      element: <Accounts /> },
      { path: 'desks',         element: <Desks /> },
      { path: 'history',       element: <History /> },
      { path: 'settings',      element: <Settings /> },
      { path: 'engagement',    element: <Engagement /> },
      { path: 'threads',       element: <Threads /> },
    ],
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
