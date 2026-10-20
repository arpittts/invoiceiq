import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import Layout from './components/Layout'
import { useAuth } from './lib/auth-context'
import AskPage from './pages/AskPage'
import AuthPage from './pages/AuthPage'
import DashboardPage from './pages/DashboardPage'
import ReconcilePage from './pages/ReconcilePage'
import InvoicesPage from './pages/InvoicesPage'
import ReviewPage from './pages/ReviewPage'
import SettingsPage from './pages/SettingsPage'
import UploadPage from './pages/UploadPage'

/** Pages behind sign-in: send visitors without a token to the login page. */
function RequireAuth({ children }: { children: ReactNode }) {
  const { token } = useAuth()
  return token ? children : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<DashboardPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/invoices" element={<InvoicesPage />} />
        <Route path="/invoices/:id" element={<ReviewPage />} />
        <Route path="/ask" element={<AskPage />} />
        <Route path="/reconcile" element={<ReconcilePage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
