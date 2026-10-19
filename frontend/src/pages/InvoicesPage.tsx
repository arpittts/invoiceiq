import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router'
import DemoButton from '../components/DemoButton'
import StatusBadge from '../components/StatusBadge'
import { api } from '../lib/api'
import { STATUS_LABELS, formatDate, formatInr } from '../lib/format'
import type { InvoiceStatus } from '../lib/types'

const PAGE_SIZE = 20

export default function InvoicesPage() {
  const [searchParams] = useSearchParams()
  const notice = (useLocation().state as { notice?: string } | null)?.notice
  const [status, setStatus] = useState<InvoiceStatus | ''>(
    (searchParams.get('status') as InvoiceStatus | null) ?? '',
  )
  const exportCsv = useMutation({ mutationFn: api.exportCsv })
  const [vendor, setVendor] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [page, setPage] = useState(0)

  const filters = { status, vendor: vendor.trim(), date_from: dateFrom, date_to: dateTo }
  const invoices = useQuery({
    queryKey: ['invoices', filters, page],
    queryFn: () => api.listInvoices({ ...filters, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    placeholderData: keepPreviousData,
    // While anything is still being extracted, check again every 3 seconds.
    refetchInterval: (query) =>
      query.state.data?.items.some((i) => i.status === 'processing') ? 3000 : false,
  })

  const total = invoices.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const resetPage = <T,>(setter: (value: T) => void) => (value: T) => {
    setter(value)
    setPage(0)
  }

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="text-xl font-semibold">Invoices</h1>
        <div className="flex flex-wrap items-start gap-2">
          <button
            type="button"
            onClick={() => exportCsv.mutate()}
            disabled={exportCsv.isPending}
            title="Approved invoices as a spreadsheet"
            className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium hover:bg-gray-50 disabled:opacity-50"
          >
            {exportCsv.isPending ? 'Exporting…' : 'Export CSV'}
          </button>
          <DemoButton />
          <Link to="/upload" className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white">
            Upload
          </Link>
        </div>
      </div>
      {notice && <p className="mt-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p>}
      {exportCsv.isError && <p className="mt-3 text-sm text-red-700">{exportCsv.error.message}</p>}

      <div className="mt-4 flex flex-wrap gap-3 text-sm">
        <select
          aria-label="Status"
          value={status}
          onChange={(e) => resetPage(setStatus)(e.target.value as InvoiceStatus | '')}
          className="rounded-md border border-gray-300 bg-white px-2 py-1.5"
        >
          <option value="">All statuses</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <input
          aria-label="Vendor"
          placeholder="Vendor name"
          value={vendor}
          onChange={(e) => resetPage(setVendor)(e.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1.5"
        />
        <label className="flex items-center gap-1 text-gray-600">
          From
          <input type="date" value={dateFrom} onChange={(e) => resetPage(setDateFrom)(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-1" />
        </label>
        <label className="flex items-center gap-1 text-gray-600">
          To
          <input type="date" value={dateTo} onChange={(e) => resetPage(setDateTo)(e.target.value)}
            className="rounded-md border border-gray-300 px-2 py-1" />
        </label>
      </div>

      <div className="mt-4 overflow-x-auto rounded-xl border border-gray-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-gray-200 text-xs uppercase tracking-wide text-gray-500">
            <tr>
              <th className="px-4 py-2 font-medium">#</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Vendor</th>
              <th className="px-4 py-2 font-medium">Invoice no.</th>
              <th className="px-4 py-2 font-medium">Date</th>
              <th className="px-4 py-2 text-right font-medium">Total</th>
              <th className="px-4 py-2 font-medium">Checks</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {invoices.data?.items.map((invoice) => (
              <tr key={invoice.id} className="hover:bg-gray-50">
                <td className="px-4 py-2">
                  <Link to={`/invoices/${invoice.id}`} className="font-medium underline">
                    {invoice.id}
                  </Link>
                </td>
                <td className="px-4 py-2"><StatusBadge status={invoice.status} /></td>
                <td className="px-4 py-2">{invoice.vendor_name ?? <span className="text-gray-400">{invoice.original_name}</span>}</td>
                <td className="px-4 py-2">{invoice.invoice_number}</td>
                <td className="px-4 py-2 whitespace-nowrap">{formatDate(invoice.invoice_date)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{formatInr(invoice.total)}</td>
                <td className="px-4 py-2 text-xs">
                  {invoice.open_errors > 0 && <span className="text-red-700">{invoice.open_errors} error(s) </span>}
                  {invoice.open_warnings > 0 && <span className="text-amber-700">{invoice.open_warnings} warning(s)</span>}
                  {invoice.status !== 'processing' && invoice.open_errors + invoice.open_warnings === 0 && (
                    <span className="text-emerald-700">OK</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {invoices.isPending && <p className="px-4 py-6 text-sm text-gray-500">Loading…</p>}
        {invoices.isError && <p className="px-4 py-6 text-sm text-red-700">{invoices.error.message}</p>}
        {invoices.data?.items.length === 0 && (
          <p className="px-4 py-6 text-sm text-gray-500">
            No invoices yet. <Link to="/upload" className="underline">Upload one</Link>.
          </p>
        )}
      </div>

      <div className="mt-3 flex items-center justify-between text-sm text-gray-600">
        <span>{total} invoice(s)</span>
        <span className="flex items-center gap-2">
          <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}
            className="rounded-md border border-gray-300 px-2 py-1 disabled:opacity-40">Previous</button>
          Page {page + 1} of {pages}
          <button type="button" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}
            className="rounded-md border border-gray-300 px-2 py-1 disabled:opacity-40">Next</button>
        </span>
      </div>
    </div>
  )
}
