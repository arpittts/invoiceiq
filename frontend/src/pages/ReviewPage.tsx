import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import StatusBadge from '../components/StatusBadge'
import { api } from '../lib/api'
import { buildUpdate, emptyLine, toDraft } from '../lib/form'
import { fieldLabel, formatInr } from '../lib/format'
import { flagFor, isLowConfidence, lineField, openErrorCount, openIssuesByField, type FieldFlag } from '../lib/issues'
import {
  HEADER_FIELDS,
  LINE_FIELDS,
  type Draft,
  type HeaderField,
  type InvoiceDetail,
  type ValidationIssue,
} from '../lib/types'

export default function ReviewPage() {
  const id = Number(useParams().id)
  const invoice = useQuery({
    queryKey: ['invoice', id],
    queryFn: () => api.getInvoice(id),
    // Poll while the AI is still reading the invoice.
    refetchInterval: (query) => (query.state.data?.status === 'processing' ? 2000 : false),
  })

  if (invoice.isPending) return <p className="text-sm text-gray-500">Loading invoice…</p>
  if (invoice.isError) return <p className="text-sm text-red-700">{invoice.error.message}</p>

  const data = invoice.data
  return (
    <div>
      <Link to="/invoices" className="text-sm text-gray-600 underline">
        ← All invoices
      </Link>
      <div className="mt-3 grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <PageViewer invoice={data} />
        {data.status === 'processing' ? (
          <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm">
            <StatusBadge status="processing" />
            <p className="mt-3 text-gray-700">The AI is reading this invoice. This usually takes about 10 seconds…</p>
          </div>
        ) : (
          // A fresh editor whenever the saved invoice changes, so the form always starts from the server's values.
          <ReviewEditor key={JSON.stringify(toDraft(data)) + data.status} invoice={data} />
        )}
      </div>
    </div>
  )
}

function PageViewer({ invoice }: { invoice: InvoiceDetail }) {
  const [page, setPage] = useState(1)
  const [zoom, setZoom] = useState(1)
  const pageCount = invoice.file.page_count
  const image = useQuery({
    queryKey: ['page-image', invoice.id, page],
    queryFn: () => api.pageImageUrl(invoice.id, page),
    staleTime: Infinity,
  })

  return (
    <div className="lg:sticky lg:top-4 lg:self-start">
      <div className="flex items-center gap-2 text-sm">
        <span className="min-w-0 flex-1 truncate text-gray-600">{invoice.file.original_name}</span>
        <button type="button" onClick={() => setZoom(Math.max(0.5, zoom - 0.25))} className="rounded border border-gray-300 bg-white px-2">−</button>
        <span className="w-12 text-center tabular-nums">{Math.round(zoom * 100)}%</span>
        <button type="button" onClick={() => setZoom(Math.min(3, zoom + 0.25))} className="rounded border border-gray-300 bg-white px-2">+</button>
        {pageCount > 1 && (
          <>
            <button type="button" disabled={page === 1} onClick={() => setPage(page - 1)} className="rounded border border-gray-300 bg-white px-2 disabled:opacity-40">‹</button>
            <span className="tabular-nums">{page}/{pageCount}</span>
            <button type="button" disabled={page === pageCount} onClick={() => setPage(page + 1)} className="rounded border border-gray-300 bg-white px-2 disabled:opacity-40">›</button>
          </>
        )}
      </div>
      <div className="mt-2 h-[75vh] overflow-auto rounded-xl border border-gray-200 bg-white">
        {image.data ? (
          <img src={image.data} alt={`Page ${page} of the invoice`} style={{ width: `${zoom * 100}%` }} className="max-w-none" />
        ) : (
          <p className="p-6 text-sm text-gray-500">{image.isError ? image.error.message : 'Loading page…'}</p>
        )}
      </div>
    </div>
  )
}

function ReviewEditor({ invoice }: { invoice: InvoiceDetail }) {
  const queryClient = useQueryClient()
  const original = toDraft(invoice)
  const [draft, setDraft] = useState<Draft>(original)
  const [error, setError] = useState<string | null>(null)

  const editable = invoice.status === 'needs_review' || invoice.status === 'failed'
  const changes = buildUpdate(original, draft)
  const byField = openIssuesByField(invoice.validation_issues)
  const openErrors = openErrorCount(invoice.validation_issues)

  // Every action returns the updated invoice: put it straight into the cache.
  const act = useMutation({
    mutationFn: (action: () => Promise<InvoiceDetail>) => action(),
    onSuccess: (updated) => {
      setError(null)
      queryClient.setQueryData(['invoice', invoice.id], updated)
      queryClient.invalidateQueries({ queryKey: ['invoices'] })
    },
    onError: (err) => setError(err.message),
  })

  const setHeader = (name: HeaderField, value: string) => setDraft({ ...draft, [name]: value })
  const setLine = (index: number, name: string, value: string) =>
    setDraft({
      ...draft,
      line_items: draft.line_items.map((line, i) => (i === index ? { ...line, [name]: value } : line)),
    })

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 bg-white p-4">
        <div className="mr-auto">
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold">Invoice #{invoice.id}</h1>
            <StatusBadge status={invoice.status} />
          </div>
          <p className="text-xs text-gray-500">
            {invoice.extraction
              ? `Read by AI in ${(invoice.extraction.total_latency_ms / 1000).toFixed(1)}s, ${invoice.extraction.attempts} attempt(s)`
              : 'Entered by hand'}
            {invoice.overall_confidence !== null && ` · confidence ${Math.round(invoice.overall_confidence * 100)}%`}
          </p>
        </div>
        {editable && (
          <>
            <button
              type="button"
              disabled={act.isPending}
              onClick={() => confirm('Reject this invoice? It will not enter the ledger.') && act.mutate(() => api.reject(invoice.id))}
              className="rounded-md border border-gray-300 px-4 py-2 text-sm font-medium hover:bg-gray-50"
            >
              Reject
            </button>
            <button
              type="button"
              disabled={act.isPending || openErrors > 0 || changes !== null || invoice.status !== 'needs_review'}
              aria-label="Approve"
              title={
                changes ? 'Save your corrections first' : openErrors ? 'Fix or dismiss the errors first' : 'Add to the ledger'
              }
              onClick={() => act.mutate(() => api.approve(invoice.id))}
              className="rounded-md bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              Approve
            </button>
          </>
        )}
      </div>

      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {invoice.status === 'failed' && (
        <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <p className="font-medium">The AI couldn't read this invoice.</p>
          {invoice.extraction?.issues?.map((issue, i) => (
            <p key={i} className="mt-1">{issue.message}</p>
          ))}
          <p className="mt-2">You can type the values in below and save; then it can be reviewed and approved.</p>
        </div>
      )}

      <IssuesPanel
        issues={invoice.validation_issues}
        canDismiss={invoice.status === 'needs_review' && !act.isPending}
        onDismiss={(issueId) => act.mutate(() => api.dismissIssue(invoice.id, issueId))}
      />

      <section className="rounded-xl border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Invoice details</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {HEADER_FIELDS.map((name) => (
            <Field
              key={name}
              label={fieldLabel(name)}
              value={draft[name]}
              onChange={(value) => setHeader(name, value)}
              flag={flagFor(byField, name)}
              confidence={invoice.field_confidence[name]}
              readOnly={!editable}
              type={name === 'invoice_date' ? 'date' : 'text'}
              hint={['subtotal', 'cgst', 'sgst', 'igst', 'total'].includes(name) ? formatInr(draft[name]) : undefined}
            />
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-gray-200 bg-white p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Line items ({draft.line_items.length})</h2>
          {editable && (
            <button type="button" onClick={() => setDraft({ ...draft, line_items: [...draft.line_items, emptyLine()] })}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs">+ Add line</button>
          )}
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="text-xs">
            <thead>
              <tr className="text-left text-gray-500">
                <th className="px-1 py-1">#</th>
                {LINE_FIELDS.map((name) => (
                  <th key={name} className="px-1 py-1 font-medium">{fieldLabel(name)}</th>
                ))}
                {editable && <th />}
              </tr>
            </thead>
            <tbody>
              {draft.line_items.map((line, index) => (
                <tr key={index}>
                  <td className="px-1 py-1 text-gray-500">{index + 1}</td>
                  {LINE_FIELDS.map((name) => {
                    const path = lineField(index + 1, name)
                    return (
                      <td key={name} className="px-1 py-1">
                        <CellInput
                          value={line[name]}
                          onChange={(value) => setLine(index, name, value)}
                          flag={flagFor(byField, path)}
                          lowConfidence={isLowConfidence(invoice.field_confidence[path])}
                          readOnly={!editable}
                          wide={name === 'description'}
                        />
                      </td>
                    )
                  })}
                  {editable && (
                    <td className="px-1">
                      <button type="button" title="Remove line" className="text-gray-400 hover:text-red-700"
                        onClick={() => setDraft({ ...draft, line_items: draft.line_items.filter((_, i) => i !== index) })}>
                        ✕
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {editable && (
        <div className="sticky bottom-0 flex items-center gap-3 rounded-xl border border-gray-200 bg-white p-3 shadow-sm">
          <span className="mr-auto text-sm text-gray-600">
            {changes ? `${Object.keys(changes).length} change(s) not saved yet` : 'No unsaved changes'}
          </span>
          <button type="button" disabled={!changes || act.isPending} onClick={() => setDraft(original)}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-40">Discard</button>
          <button
            type="button"
            disabled={!changes || act.isPending}
            onClick={() => changes && act.mutate(() => api.updateInvoice(invoice.id, changes))}
            className="rounded-md bg-gray-900 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-40"
          >
            {act.isPending ? 'Saving…' : 'Save corrections'}
          </button>
        </div>
      )}
    </div>
  )
}

function IssuesPanel({ issues, canDismiss, onDismiss }: {
  issues: ValidationIssue[]
  canDismiss: boolean
  onDismiss: (issueId: number) => void
}) {
  if (!issues.length) {
    return (
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
        ✓ All validation checks passed.
      </div>
    )
  }
  return (
    <section className="rounded-xl border border-gray-200 bg-white p-4">
      <h2 className="text-sm font-semibold">Validation issues</h2>
      <ul className="mt-2 space-y-2">
        {issues.map((issue) => (
          <li key={issue.id} className={`flex items-start gap-3 rounded-md px-3 py-2 text-sm ${
            issue.resolved ? 'bg-gray-50 text-gray-500' : issue.severity === 'error' ? 'bg-red-50 text-red-800' : 'bg-amber-50 text-amber-900'
          }`}>
            <span className="min-w-0 flex-1">
              <span className="font-semibold">{issue.rule_code}</span>{' '}
              <code className="text-xs">{issue.field}</code>
              <span className="block">{issue.message}</span>
            </span>
            {issue.resolved ? (
              <span className="text-xs">Dismissed</span>
            ) : (
              canDismiss && (
                <button type="button" onClick={() => onDismiss(issue.id)}
                  className="shrink-0 rounded border border-current px-2 py-0.5 text-xs" title="Mark as checked and acceptable">
                  Dismiss
                </button>
              )
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

const FLAG_RING: Record<Exclude<FieldFlag, null>, string> = {
  error: 'border-red-500 ring-2 ring-red-200',
  warning: 'border-amber-500 ring-2 ring-amber-200',
}

function Field(props: {
  label: string
  value: string
  onChange: (value: string) => void
  flag: FieldFlag
  confidence: number | undefined
  readOnly: boolean
  type: 'text' | 'date'
  hint?: string
}) {
  const low = isLowConfidence(props.confidence)
  return (
    <label className="block text-sm">
      <span className="flex items-center gap-2 text-xs font-medium text-gray-600">
        {props.label}
        {low && (
          <span className="rounded bg-amber-100 px-1.5 text-[10px] font-semibold text-amber-800"
            title={`AI confidence ${Math.round((props.confidence ?? 0) * 100)}%`}>
            check
          </span>
        )}
      </span>
      <input
        type={props.type}
        value={props.value}
        readOnly={props.readOnly}
        onChange={(e) => props.onChange(e.target.value)}
        className={`mt-1 w-full rounded-md border px-2 py-1.5 ${props.flag ? FLAG_RING[props.flag] : 'border-gray-300'} ${
          props.readOnly ? 'bg-gray-50' : ''
        }`}
      />
      {props.hint && <span className="mt-0.5 block text-xs text-gray-500">{props.hint}</span>}
    </label>
  )
}

function CellInput(props: {
  value: string
  onChange: (value: string) => void
  flag: FieldFlag
  lowConfidence: boolean
  readOnly: boolean
  wide: boolean
}) {
  const border = props.flag ? FLAG_RING[props.flag] : props.lowConfidence ? 'border-amber-400' : 'border-gray-200'
  return (
    <input
      value={props.value}
      readOnly={props.readOnly}
      onChange={(e) => props.onChange(e.target.value)}
      className={`${props.wide ? 'w-44' : 'w-20'} rounded border px-1.5 py-1 ${border} ${props.readOnly ? 'bg-gray-50' : ''}`}
    />
  )
}
