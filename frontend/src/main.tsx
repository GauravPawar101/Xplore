import React from 'react'
import ReactDOM from 'react-dom/client'
import { ClerkProvider } from '@clerk/clerk-react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import LandingPage from '@/pages/LandingPage'
import AppLayout from '@/pages/AppLayout'
import ProtectedRoute from '@/pages/ProtectedRoute'
import AuthRequestInterceptor from '@/components/AuthRequestInterceptor'
import { TourProvider } from '@/context/TourContext'
import EzDocsIDE from '@/CodeMap'
import MyGraphsPage from '@/pages/MyGraphsPage'
import ConversationPage from '@/pages/ConversationPage'
import '@/index.css'
import './CodeMap.css'

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY
if (!PUBLISHABLE_KEY) {
  throw new Error('Missing Clerk Publishable Key. Set VITE_CLERK_PUBLISHABLE_KEY in .env.local')
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="/">
      <AuthRequestInterceptor />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route
            path="/app"
            element={
              <ProtectedRoute>
                <TourProvider>
                  <AppLayout />
                </TourProvider>
              </ProtectedRoute>
            }
          >
            <Route index element={<EzDocsIDE />} />
            <Route path="graphs" element={<MyGraphsPage />} />
            <Route path="conversation" element={<ConversationPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </ClerkProvider>
  </React.StrictMode>,
)
