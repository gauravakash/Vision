import { Link } from 'react-router-dom'
import { AlertCircle } from 'lucide-react'

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center px-4">
      <AlertCircle className="w-16 h-16 text-rose-500 mb-6" />
      <h1 className="text-4xl font-bold text-gray-100 mb-2">404 - Not Found</h1>
      <p className="text-gray-400 mb-8 max-w-md">
        The page you are looking for doesn't exist or has been moved.
      </p>
      <Link
        to="/"
        className="px-6 py-2.5 bg-brand-500 hover:bg-brand-400 text-white font-medium rounded-lg transition-colors"
      >
        Return Home
      </Link>
    </div>
  )
}
