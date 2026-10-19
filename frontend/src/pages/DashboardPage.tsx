import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import DemoButton from '../components/DemoButton'
import StatusBadge from '../components/StatusBadge'
import { api } from '../lib/api'
import { STATUS_LABELS, formatDate, formatInr } from '../lib/format'
import type { InvoiceStatus } from '../lib/types'

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="mt-1 text-xs text-gray-500">{hint}</p>}
    </div>
  )
}

export default function DashboardPage() {
  const dashboard = useQuery({
    queryKey: ['dashboard'],
    queryFn: api.dashboard,
    // Keep counts moving while invoices are being read.
    refetchInterval: (query) => ((query.state.data?.counts.processing ?? 0) > 0 ? 3000 : false),
  })

  if (dashboard.isPending) return <p className="text-sm text-gray-500">Loading dashboard…</p>
  if (dashboard.isError) return <p className="text-sm text-red-700">{dashboard.error.message}</p>
  const data = dashboard.data
  const invoiceCount = Object.values(data.counts).reduce((a, b) => a + b, 0)

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <div className="flex flex-wrap gap-2">
          <DemoButton />
          <Link to="/upload" className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white">
            Upload
          </Link>
        </div>
      </div>

      {invoiceCount === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 bg-white p-6 text-sm text-gray-600">
          No invoices yet. Upload your own, or click <b>Try demo invoices</b> to load six synthetic
          ones: a photo, a scan, a faint photocopy, a two-page bill and one with a misread the
          checks catch.
        </div>
      )}

      {data.ai.cap_reached && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
          The demo's daily AI limit is reached, so new uploads wait until midnight India time. The
          demo invoices still work.
        </p>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Approved spend" value={formatInr(data.approved_total) || '₹0.00'} hint={`${data.counts.approved} invoice(s) in the ledger`} />
        <Stat label="GST paid" value={formatInr(data.approved_gst) || '₹0.00'} hint="CGST + SGST + IGST, approved invoices" />
        <Stat label="This month" value={formatInr(data.approved_this_month) || '₹0.00'} hint="by invoice date" />
        <Stat
          label="Waiting for you"
          value={String(data.counts.needs_review)}
          hint={data.counts.processing ? `${data.counts.processing} still being read` : 'need review'}
        />
      </div>

      <div className="flex flex-wrap gap-2 text-sm">
        {(Object.keys(STATUS_LABELS) as InvoiceStatus[]).map((status) => (
          <Link
            key={status}
            to={`/invoices?status=${status}`}
            className="rounded-full border border-gray-200 bg-white px-3 py-1 hover:bg-gray-50"
          >
            {STATUS_LABELS[status]} <b className="tabular-nums">{data.counts[status]}</b>
          </Link>
        ))}
      </div>

      <section className="rounded-xl border border-gray-200 bg-white">
        <h2 className="border-b border-gray-200 px-4 py-3 text-sm font-semibold">Recent invoices</h2>
        {data.recent.length === 0 ? (
          <p className="px-4 py-3 text-sm text-gray-500">Nothing yet.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {data.recent.map((inv) => (
              <li key={inv.id}>
                <Link to={`/invoices/${inv.id}`} className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm hover:bg-gray-50">
                  <StatusBadge status={inv.status} />
                  <span className="min-w-0 flex-1 truncate">{inv.vendor_name ?? inv.original_name}</span>
                  <span className="text-gray-500">{formatDate(inv.invoice_date)}</span>
                  <span className="w-28 text-right tabular-nums">{formatInr(inv.total)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="text-xs text-gray-500">
        AI usage: {data.ai.extraction_calls} extraction call(s) on your invoices, $
        {Number(data.ai.cost_usd).toFixed(4)} in total. Demo server today: {data.ai.calls_today} of{' '}
        {data.ai.call_limit} AI calls.
      </p>
    </div>
  )
}
