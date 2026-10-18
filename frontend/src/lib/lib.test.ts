import { describe, expect, it } from 'vitest'
import { isExpired } from './token'
import { buildUpdate, emptyLine, toDraft } from './form'
import { fieldLabel, formatDate, formatInr } from './format'
import { flagFor, isLowConfidence, openErrorCount, openIssuesByField } from './issues'
import type { InvoiceDetail, ValidationIssue } from './types'

const invoice: InvoiceDetail = {
  id: 1, status: 'needs_review', created_at: '2026-10-18T10:00:00Z', approved_at: null,
  file: { id: 1, original_name: 'inv.pdf', mime_type: 'application/pdf', page_count: 1 },
  vendor_name: 'Sharma Auto Parts', vendor_gstin: '27AAPFU0939F1ZV', buyer_name: null,
  buyer_gstin: null, invoice_number: 'A-1', invoice_date: '2026-10-12',
  place_of_supply: 'Maharashtra', subtotal: '3000.00', cgst: '420.00', sgst: '420.00',
  igst: null, total: '3840.00', currency: 'INR', overall_confidence: 0.95,
  line_items: [{
    position: 1, description: 'Brake pads', hsn_sac_code: '8708', quantity: '2.000', unit: 'Nos',
    unit_price: '1500.00', discount: null, taxable_value: '3000.00', gst_rate: '28.00',
    cgst_amount: '420.00', sgst_amount: '420.00', igst_amount: null, line_total: '3840.00',
    confidence: 0.95,
  }],
  validation_issues: [], extraction: null, field_confidence: {},
}

describe('format', () => {
  it('uses Indian digit grouping for rupees', () => {
    expect(formatInr('123456.5')).toBe('₹1,23,456.50')
    expect(formatInr(null)).toBe('')
  })
  it('formats ISO dates without a timezone shift', () => {
    expect(formatDate('2026-10-01')).toBe('1 Oct 2026')
  })
  it('labels fields readably', () => {
    expect(fieldLabel('vendor_gstin')).toBe('Vendor GSTIN')
    expect(fieldLabel('place_of_supply')).toBe('Place of supply')
  })
})

describe('issues', () => {
  const issues: ValidationIssue[] = [
    { id: 1, rule_code: 'TOTAL_MATH', field: 'total', severity: 'error', message: 'x', resolved: false },
    { id: 2, rule_code: 'DATE_RANGE', field: 'total', severity: 'warning', message: 'y', resolved: false },
    { id: 3, rule_code: 'TAX_MATH', field: 'line_items[1].gst_rate', severity: 'error', message: 'z', resolved: true },
  ]
  it('groups open issues by field and lets errors win', () => {
    const byField = openIssuesByField(issues)
    expect(flagFor(byField, 'total')).toBe('error')
    expect(flagFor(byField, 'line_items[1].gst_rate')).toBeNull() // dismissed
    expect(openErrorCount(issues)).toBe(1)
  })
  it('flags low confidence', () => {
    expect(isLowConfidence(0.6)).toBe(true)
    expect(isLowConfidence(0.95)).toBe(false)
    expect(isLowConfidence(undefined)).toBe(false)
  })
})

describe('buildUpdate', () => {
  it('returns null when nothing changed, even if numbers are written differently', () => {
    const original = toDraft(invoice)
    const draft = { ...original, total: '3840', line_items: [{ ...original.line_items[0], quantity: '2' }] }
    expect(buildUpdate(original, draft)).toBeNull()
  })
  it('sends only edited header fields, with blanks as null', () => {
    const original = toDraft(invoice)
    expect(buildUpdate(original, { ...original, total: '3940.00', vendor_name: ' ' })).toEqual({
      total: '3940.00',
      vendor_name: null,
    })
  })
  it('sends the whole line list when a line changes or is added', () => {
    const original = toDraft(invoice)
    const edited = { ...original, line_items: [{ ...original.line_items[0], quantity: '3' }] }
    const update = buildUpdate(original, edited) as { line_items: Record<string, unknown>[] }
    expect(update.line_items[0].quantity).toBe('3')
    expect(update.line_items[0].discount).toBeNull()

    const added = buildUpdate(original, { ...original, line_items: [...original.line_items, emptyLine()] })
    expect((added as { line_items: unknown[] }).line_items).toHaveLength(2)
  })
})

describe('isExpired', () => {
  const token = (exp: number) => `x.${btoa(JSON.stringify({ exp }))}.y`
  it('reads the exp claim', () => {
    expect(isExpired(token(2_000_000_000), 1_000_000_000_000)).toBe(false)
    expect(isExpired(token(1_000), 1_000_000_000_000)).toBe(true)
    expect(isExpired('garbage')).toBe(true)
  })
})
