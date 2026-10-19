// Shapes returned by the InvoiceIQ API. Money and rates arrive as strings
// (exact decimals from Python), never as floats.

export type InvoiceStatus = 'processing' | 'needs_review' | 'approved' | 'rejected' | 'failed'

export type ValidationIssue = {
  id: number
  rule_code: string
  field: string
  severity: 'error' | 'warning'
  message: string
  resolved: boolean
}

export type LineItem = {
  position: number
  description: string | null
  hsn_sac_code: string | null
  quantity: string | null
  unit: string | null
  unit_price: string | null
  discount: string | null
  taxable_value: string | null
  gst_rate: string | null
  cgst_amount: string | null
  sgst_amount: string | null
  igst_amount: string | null
  line_total: string | null
  confidence: number | null
  // HSN/SAC lookup (Phase 5)
  suggested_hsn_code: string | null
  suggested_gst_rate: string | null
  hsn_confidence: number | null
  hsn_reason: string | null
  hsn_source: HsnCode | null
}

export type HsnCode = {
  id: number
  code: string
  kind: 'HSN' | 'SAC'
  description: string
  heading: string | null
  default_gst_rate: string | null
  source_note: string
}

export type InvoiceDetail = {
  id: number
  status: InvoiceStatus
  created_at: string
  approved_at: string | null
  file: { id: number; original_name: string; mime_type: string; page_count: number }
  vendor_name: string | null
  vendor_gstin: string | null
  buyer_name: string | null
  buyer_gstin: string | null
  invoice_number: string | null
  invoice_date: string | null
  place_of_supply: string | null
  subtotal: string | null
  cgst: string | null
  sgst: string | null
  igst: string | null
  total: string | null
  currency: string
  overall_confidence: number | null
  line_items: LineItem[]
  validation_issues: ValidationIssue[]
  extraction: {
    attempts: number
    total_cost_usd: string | null
    total_latency_ms: number
    issues: { code: string; message: string }[] | null
  } | null
  field_confidence: Record<string, number>
}

export type InvoiceSummary = {
  id: number
  status: InvoiceStatus
  created_at: string
  original_name: string
  vendor_name: string | null
  invoice_number: string | null
  invoice_date: string | null
  total: string | null
  overall_confidence: number | null
  open_errors: number
  open_warnings: number
}

export type InvoiceList = { items: InvoiceSummary[]; total: number }

// Header fields the reviewer can edit, in display order.
export const HEADER_FIELDS = [
  'vendor_name', 'vendor_gstin', 'buyer_name', 'buyer_gstin', 'invoice_number',
  'invoice_date', 'place_of_supply', 'subtotal', 'cgst', 'sgst', 'igst', 'total',
] as const
export type HeaderField = (typeof HEADER_FIELDS)[number]

export const LINE_FIELDS = [
  'description', 'hsn_sac_code', 'quantity', 'unit', 'unit_price', 'discount',
  'taxable_value', 'gst_rate', 'cgst_amount', 'sgst_amount', 'igst_amount', 'line_total',
] as const
export type LineField = (typeof LINE_FIELDS)[number]

// What the form edits: every value as a string ('' means empty).
export type DraftLine = Record<LineField, string>
export type Draft = Record<HeaderField, string> & { line_items: DraftLine[] }

export type AskResult = {
  question: string
  answer: string | null
  sql: string | null
  columns: string[]
  rows: (string | number | boolean | null)[][]
  error: string | null
}
