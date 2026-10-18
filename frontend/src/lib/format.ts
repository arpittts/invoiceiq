import type { InvoiceStatus } from './types'

/** '123456.5' -> '₹1,23,456.50' (Indian digit grouping). Empty for null. */
export function formatInr(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''
  const amount = Number(value)
  if (Number.isNaN(amount)) return value
  return amount.toLocaleString('en-IN', { style: 'currency', currency: 'INR' })
}

/** '2026-10-12' -> '12 Oct 2026'. Parsed as a calendar date, so no timezone shift. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return ''
  const [year, month, day] = value.slice(0, 10).split('-').map(Number)
  if (!year || !month || !day) return value
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${day} ${months[month - 1]} ${year}`
}

export const STATUS_LABELS: Record<InvoiceStatus, string> = {
  processing: 'Processing',
  needs_review: 'Needs review',
  approved: 'Approved',
  rejected: 'Rejected',
  failed: 'Failed',
}

/** 'vendor_gstin' -> 'Vendor GSTIN', 'hsn_sac_code' -> 'HSN/SAC code'. */
export function fieldLabel(name: string): string {
  const special: Record<string, string> = {
    vendor_gstin: 'Vendor GSTIN',
    buyer_gstin: 'Buyer GSTIN',
    hsn_sac_code: 'HSN/SAC',
    gst_rate: 'GST %',
    cgst: 'CGST',
    sgst: 'SGST',
    igst: 'IGST',
    cgst_amount: 'CGST',
    sgst_amount: 'SGST',
    igst_amount: 'IGST',
    unit_price: 'Rate',
    taxable_value: 'Taxable value',
    line_total: 'Line total',
  }
  if (special[name]) return special[name]
  const text = name.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}
