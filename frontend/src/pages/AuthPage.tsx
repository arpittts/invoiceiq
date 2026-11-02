import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router'
import { useAuth } from '../lib/auth-context'

export default function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  const { token, login, register, startDemo } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [demoBusy, setDemoBusy] = useState(false)

  if (token) return <Navigate to="/" replace />

  const isLogin = mode === 'login'

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await (isLogin ? login(email, password) : register(email, password))
      navigate('/invoices')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  async function tryDemo() {
    setError(null)
    setDemoBusy(true)
    try {
      await startDemo()
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The demo could not start')
    } finally {
      setDemoBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-semibold tracking-tight">InvoiceIQ</h1>
        <p className="mt-1 text-sm text-gray-600">
          {isLogin ? 'Sign in to review your invoices.' : 'Create an account to get started.'}
        </p>

        <label className="mt-6 block text-sm font-medium" htmlFor="email">
          Email
        </label>
        <input
          id="email"
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
        />

        <label className="mt-4 block text-sm font-medium" htmlFor="password">
          Password
        </label>
        <input
          id="password"
          type="password"
          required
          minLength={isLogin ? undefined : 8}
          autoComplete={isLogin ? 'current-password' : 'new-password'}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
        />
        {!isLogin && <p className="mt-1 text-xs text-gray-500">At least 8 characters.</p>}

        {error && <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

        <button
          type="submit"
          disabled={busy}
          className="mt-6 w-full rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {busy ? 'Please wait…' : isLogin ? 'Sign in' : 'Create account'}
        </button>
        <div className="my-5 flex items-center gap-3 text-xs text-gray-400">
          <span className="h-px flex-1 bg-gray-200" />
          or
          <span className="h-px flex-1 bg-gray-200" />
        </div>
        <button
          type="button"
          onClick={tryDemo}
          disabled={demoBusy || busy}
          className="w-full rounded-md border border-gray-900 px-4 py-2 text-sm font-medium hover:bg-gray-100 disabled:opacity-50"
        >
          {demoBusy ? 'Setting up your demo…' : 'Try the demo, no sign-up'}
        </button>
        <p className="mt-2 text-center text-xs text-gray-500">
          A private sample account with invoices to review, a ledger to ask about and a GSTR-2B
          to reconcile. Deleted after 24 hours.
        </p>
        <p className="mt-4 text-center text-sm text-gray-600">
          {isLogin ? (
            <>
              New here? <Link className="font-medium underline" to="/register">Create an account</Link>
            </>
          ) : (
            <>
              Have an account? <Link className="font-medium underline" to="/login">Sign in</Link>
            </>
          )}
        </p>
        <p className="mt-4 text-center text-xs text-gray-500">
          The first request can take up to a minute while the free server wakes up.
        </p>
      </form>
    </div>
  )
}
