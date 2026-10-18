import { STATUS_LABELS } from '../lib/format'
import type { InvoiceStatus } from '../lib/types'

const COLORS: Record<InvoiceStatus, string> = {
  processing: 'bg-blue-50 text-blue-700 ring-blue-200',
  needs_review: 'bg-amber-50 text-amber-800 ring-amber-200',
  approved: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  rejected: 'bg-gray-100 text-gray-600 ring-gray-200',
  failed: 'bg-red-50 text-red-700 ring-red-200',
}

export default function StatusBadge({ status }: { status: InvoiceStatus }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${COLORS[status]}`}>
      {STATUS_LABELS[status]}
    </span>
  )
}
