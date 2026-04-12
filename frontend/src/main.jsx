import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'react-hot-toast'
import App from './App'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster
        position="bottom-center"
        toastOptions={{
          duration: 3000,
          style: {
            background: '#1A1208',
            color: '#FDFAF6',
            fontFamily: 'Satoshi, sans-serif',
            fontSize: '14px',
            borderRadius: '12px',
            padding: '10px 16px',
          },
          success: { duration: 3000, iconTheme: { primary: '#1A7A4A', secondary: '#fff' } },
          error:   { duration: 5000, iconTheme: { primary: '#C0392B', secondary: '#fff' } },
        }}
      />
    </QueryClientProvider>
  </React.StrictMode>
)
