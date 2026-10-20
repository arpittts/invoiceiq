import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link } from 'react-router'
import { api } from '../lib/api'
import { fieldLabel, formatDate, formatInr } from '../lib/format'
import type { BookInvoice, MatchStatus, ReconReport, ReconRow } from '../lib/types'

const RESULT: Record<MatchStatus, { label: string; tone: string; help: string }> = {
  matched: { label: 'Matched', tone: 'bg-emerald-50 text-emerald-800', help: 'In your books and in 2B; amounts agree' },
  mismatched: { label: 'Differs', tone: 'bg-amber-50 text-amber-900', help: 'Found, but an amount or the date differs' },
  possible: { label: 'Possible match', tone: 'bg-sky-50 text-sky-900', help: 'Same supplier and amounts, number written differently' },
  only_2b: { label: 'Not in your books', tone: 'bg-gray-100 text-gray-800', help: 'The supplier reported it; you have no approved invoice' },
}

function lastMonth(): string {
  const today = new Date()
  const first = new Date(today.getFullYear(), today.getMonth() - 1, 1)
  return `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, '0')}`
}

export default function ReconcilePage() {
  const queryClient = useQueryClient()
  const [period, setPeriod] = useState<string | null>(null)
  const [samplePeriod, setSamplePeriod] = useState(lastMonth())
  const fileInput = useRef<HTMLInputElement>(null)

  const statements = useQuery({ queryKey: ['statements'], queryFn: api.statements })
  const selected = period ?? statements.data?.[0]?.period ?? null
  const report = useQuery({
    queryKey: ['recon', selected],
    queryFn: () => api.reconReport(selected!),
    enabled: selected !== null,
  })

  const show = (result: ReconReport) => {
    queryClient.setQueryData(['recon', result.period], result)
    queryClient.invalidateQueries({ queryKey: ['statements'] })
    setPeriod(result.period)
  }
  const upload = useMutation({ mutationFn: api.uploadGstr2b, onSuccess: show })
  const sample = useMutation({ mutationFn: api.sampleStatement, onSuccess: show })
  const recheck = useMutation({ mutationFn: api.recheck, onSuccess: show })
  const exportCsv = useMutation({ mutationFn: api.exportReconciliation })
  const error = upload.error ?? sample.error ?? recheck.error ?? exportCsv.error

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">GSTR-2B reconciliation</h1>
        <p className="mt-1 max-w-3xl text-sm text-gray-600">
          GSTR-2B is the government's statement of the input tax credit your suppliers reported
          for a month. Upload the Excel file from the GST portal to check it against your approved
          invoices: what matches,
          what differs, and which invoices' credit is at risk because the supplier hasn't
          reported them yet.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 bg-white p-4 text-sm">
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={upload.isPending}
          className="rounded-md bg-gray-900 px-4 py-2 font-medium text-white disabled:opacity-50"
        >
          {upload.isPending ? 'Reconciling…' : 'Upload GSTR-2B (Excel or JSON)'}
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".xlsx,.json,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) upload.mutate(file)
            e.target.value = ''
          }}
        />
        <span className="text-gray-400">or</span>
        <label className="flex items-center gap-2 text-gray-700">
          try a sample built from your approved invoices for
          <input
            type="month"
            value={samplePeriod}
            onChange={(e) => setSamplePeriod(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-1"
          />
        </label>
        <button
          type="button"
          onClick={() => sample.mutate(samplePeriod)}
          disabled={sample.isPending || !samplePeriod}
          className="rounded-md border border-gray-900 px-4 py-2 font-medium hover:bg-gray-100 disabled:opacity-50"
        >
          {sample.isPending ? 'Building…' : 'Try a sample 2B'}
        </button>
      </div>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error.message}</p>}

      {statements.data && statements.data.length > 0 && (
        <div className="flex flex-wrap gap-2 text-sm">
          {statements.data.map((s) => (
            <button
              key={s.period}
              type="button"
              onClick={() => setPeriod(s.period)}
              className={`rounded-full border px-3 py-1 ${
                s.period === selected ? 'border-gray-900 bg-gray-900 text-white' : 'border-gray-200 bg-white hover:bg-gray-50'
              }`}
            >
              {s.period}
              {s.counts.books_only > 0 && <span className="ml-1 text-amber-500">●</span>}
            </button>
          ))}
        </div>
      )}

      {statements.data?.length === 0 && (
        <p className="rounded-xl border border-dashed border-gray-300 bg-white p-6 text-sm text-gray-600">
          No statements yet. Approve some invoices, then upload that month's GSTR-2B or try a sample.
        </p>
      )}

      {report.data && (
        <Report
          report={report.data}
          onRecheck={() => recheck.mutate(report.data.period)}
          onExport={() => exportCsv.mutate(report.data.period)}
          busy={recheck.isPending || exportCsv.isPending}
        />
      )}
    </div>
  )
}

function Report({
  report,
  onRecheck,
  onExport,
  busy,
}: {
  report: ReconReport
  onRecheck: () => void
  onExport: () => void
  busy: boolean
}) {
  const [filter, setFilter] = useState<MatchStatus | 'all'>('all')
  const rows = report.rows.filter((r) => filter === 'all' || r.match_status === filter)
  const synthetic = report.source_format.startsWith('synthetic')

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">{report.period}</h2>
          <p className="text-xs text-gray-500">
            {report.file_name} · {report.source_format}
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={onRecheck} disabled={busy}
            title="Match again, e.g. after approving more invoices"
            className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium hover:bg-gray-50 disabled:opacity-50">
            Re-check
          </button>
          <button type="button" onClick={onExport} disabled={busy}
            className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium hover:bg-gray-50 disabled:opacity-50">
            Export CSV
          </button>
        </div>
      </div>

      {synthetic ? (
        <p className="rounded-md bg-sky-50 px-3 py-2 text-sm text-sky-900">
          This is a synthetic sample built from your own approved invoices (one reported with a
          higher amount, one left out, one extra supplier invoice), not a real GSTR-2B.
        </p>
      ) : (
        !report.verified_against_portal && (
          <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
            The JSON reader follows public descriptions of the portal's GSTR-2B JSON and hasn't
            been checked against a downloaded file yet (the Excel reader has). Compare a few
            results with the portal before relying on them.
          </p>
        )
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {(Object.keys(RESULT) as MatchStatus[]).map((status) => (
          <button key={status} type="button" onClick={() => setFilter(filter === status ? 'all' : status)}
            title={RESULT[status].help}
            className={`rounded-xl border p-3 text-left ${filter === status ? 'border-gray-900' : 'border-gray-200'} bg-white`}>
            <span className="block text-xs text-gray-500">{RESULT[status].label}</span>
            <span className="text-2xl font-semibold tabular-nums">{report.counts[status]}</span>
          </button>
        ))}
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-3">
          <span className="block text-xs text-amber-900">ITC at risk</span>
          <span className="text-lg font-semibold tabular-nums">{formatInr(report.itc_at_risk) || '₹0.00'}</span>
          <span className="block text-xs text-amber-900">{report.counts.books_only} invoice(s)</span>
        </div>
        <div className="rounded-xl border border-emerald-300 bg-emerald-50 p-3">
          <span className="block text-xs text-emerald-900">ITC confirmed</span>
          <span className="text-lg font-semibold tabular-nums">{formatInr(report.itc_claimable) || '₹0.00'}</span>
          <span className="block text-xs text-emerald-900">matched, ITC available</span>
        </div>
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-gray-200 text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th className="px-3 py-2">Result</th>
              <th className="px-3 py-2">Supplier</th>
              <th className="px-3 py-2">Invoice</th>
              <th className="px-3 py-2">Date</th>
              <th className="px-3 py-2 text-right">Taxable</th>
              <th className="px-3 py-2 text-right">Tax</th>
              <th className="px-3 py-2">What differs</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row) => <Row key={row.id} row={row} />)}
            {rows.length === 0 && (
              <tr><td colSpan={7} className="px-3 py-4 text-gray-500">Nothing here.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <BookList
        title="In your books, not in this 2B: ITC at risk"
        help="You approved these invoices for this month, but the supplier hasn't reported them. You can't claim their credit until they do: ask the supplier to file."
        invoices={report.books_only}
      />
      <BookList
        title="Older invoices still waiting"
        help="Dated before this month and not reported in any uploaded 2B yet."
        invoices={report.still_waiting}
      />
    </section>
  )
}

function Row({ row }: { row: ReconRow }) {
  const result = RESULT[row.match_status]
  return (
    <tr>
      <td className="px-3 py-2">
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${result.tone}`}>{result.label}</span>
      </td>
      <td className="px-3 py-2">
        <div>{row.supplier_name ?? '—'}</div>
        <div className="font-mono text-xs text-gray-500">{row.supplier_gstin}</div>
      </td>
      <td className="px-3 py-2">
        {row.matched_invoice_id ? (
          <Link to={`/invoices/${row.matched_invoice_id}`} className="underline">{row.invoice_number}</Link>
        ) : (
          row.invoice_number
        )}
        {!row.itc_available && <div className="text-xs text-red-700">ITC not available</div>}
      </td>
      <td className="px-3 py-2">{formatDate(row.invoice_date)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{formatInr(row.taxable_value)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{formatInr(row.tax)}</td>
      <td className="px-3 py-2 text-xs text-gray-700">
        {row.differences?.map((d) => (
          <div key={d.field}>
            {d.field === 'invoice_number' ? 'Number in your books' : fieldLabel(d.field)}:{' '}
            {d.field === 'invoice_number' ? d.books : `books ${d.books ?? '—'}, 2B ${d.gstr2b ?? '—'}`}
          </div>
        ))}
      </td>
    </tr>
  )
}

function BookList({ title, help, invoices }: { title: string; help: string; invoices: BookInvoice[] }) {
  if (invoices.length === 0) return null
  return (
    <div className="rounded-xl border border-gray-200 bg-white">
      <div className="border-b border-gray-200 px-4 py-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="text-xs text-gray-500">{help}</p>
      </div>
      <ul className="divide-y divide-gray-100 text-sm">
        {invoices.map((inv) => (
          <li key={inv.id}>
            <Link to={`/invoices/${inv.id}`} className="flex flex-wrap gap-3 px-4 py-2 hover:bg-gray-50">
              <span className="min-w-0 flex-1 truncate">{inv.vendor_name ?? inv.vendor_gstin}</span>
              <span>{inv.invoice_number}</span>
              <span className="text-gray-500">{formatDate(inv.invoice_date)}</span>
              <span className="w-28 text-right tabular-nums">tax {formatInr(inv.tax)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
