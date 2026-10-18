import { useQuery } from '@tanstack/react-query'
import { API_URL, fetchHealth } from './lib/api'

function StatusRow({ label, ok, text }: { label: string; ok: boolean | null; text: string }) {
  const dot = ok === null ? 'bg-gray-300' : ok ? 'bg-emerald-500' : 'bg-red-500'
  return (
    <div className="flex items-center justify-between py-3">
      <span className="text-gray-600">{label}</span>
      <span className="flex items-center gap-2 font-medium text-gray-900">
        <span className={`h-2.5 w-2.5 rounded-full ${dot}`} />
        {text}
      </span>
    </div>
  )
}

export default function App() {
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth, retry: 1 })

  // Free hosts can sleep, so the first request may take a while (a "cold start").
  const apiText = health.isPending
    ? 'Waking up the server…'
    : health.isError
      ? 'Unreachable'
      : health.data.status
  const apiOk = health.isPending ? null : !health.isError && health.data.status === 'ok'
  const dbOk = health.isSuccess ? health.data.database === 'ok' : null

  return (
    <main className="min-h-screen bg-gray-50 px-4 py-16">
      <div className="mx-auto max-w-md">
        <h1 className="text-3xl font-semibold tracking-tight text-gray-900">InvoiceIQ</h1>
        <p className="mt-2 text-gray-600">
          AI-assisted GST invoice extraction, validation and ledger Q&amp;A.
        </p>

        <section className="mt-8 rounded-xl border border-gray-200 bg-white px-5 shadow-sm">
          <div className="divide-y divide-gray-100">
            <StatusRow label="API" ok={apiOk} text={apiText} />
            <StatusRow label="Database" ok={dbOk} text={health.data?.database ?? '—'} />
          </div>
        </section>

        <p className="mt-3 text-xs text-gray-500">Backend: {API_URL}</p>
        <p className="mt-8 text-xs text-gray-500">
          Output is an automation aid, not tax or legal advice. Rates and codes change, so always
          verify.
        </p>
      </div>
    </main>
  )
}
