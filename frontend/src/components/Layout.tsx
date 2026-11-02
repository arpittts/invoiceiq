import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet, useNavigate } from 'react-router'
import { api, fetchHealth } from '../lib/api'
import { useAuth } from '../lib/auth-context'

function navClass({ isActive }: { isActive: boolean }) {
  return `rounded-md px-3 py-1.5 text-sm font-medium ${
    isActive ? 'bg-gray-900 text-white' : 'text-gray-700 hover:bg-gray-100'
  }`
}

export default function Layout() {
  const { token, logout } = useAuth()
  const navigate = useNavigate()
  // Keyed by the token, so signing in as someone else never shows the old account.
  const me = useQuery({ queryKey: ['me', token], queryFn: api.me, staleTime: Infinity })
  // Free hosting sleeps when idle: the first request can take ~50 seconds.
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth, retry: 1 })
  const apiText = health.isPending
    ? 'Waking up the server…'
    : health.isError
      ? 'API unreachable'
      : health.data.status === 'ok'
        ? 'API ok'
        : 'API degraded'

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-2 px-4 py-3">
          <span className="mr-4 text-lg font-semibold tracking-tight">InvoiceIQ</span>
          <NavLink to="/" end className={navClass}>
            Dashboard
          </NavLink>
          <NavLink to="/upload" className={navClass}>
            Upload
          </NavLink>
          <NavLink to="/invoices" className={navClass}>
            Invoices
          </NavLink>
          <NavLink to="/ask" className={navClass}>
            Ask
          </NavLink>
          <NavLink to="/reconcile" className={navClass}>
            Reconcile
          </NavLink>
          <NavLink to="/settings" className={navClass}>
            Settings
          </NavLink>
          <span className="ml-auto text-xs text-gray-500">{apiText}</span>
          <button
            type="button"
            onClick={logout}
            className="rounded-md px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
          >
            Sign out
          </button>
        </div>
      </header>
      {me.data?.is_demo && (
        <div className="border-b border-amber-200 bg-amber-50">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm text-amber-900">
            <span>
              <b>Demo account:</b> sample data only, deleted 24 hours after you started. Uploads are
              read by a free AI tier, so use sample invoices, not real ones.
            </span>
            <button
              type="button"
              onClick={() => {
                logout()
                navigate('/register')
              }}
              className="font-medium underline"
            >
              Create your own account
            </button>
          </div>
        </div>
      )}
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-6 text-xs text-gray-500">
        Output is an automation aid, not tax or legal advice. Rates and codes change, so always
        verify.
      </footer>
    </div>
  )
}
