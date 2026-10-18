import {
  HEADER_FIELDS,
  LINE_FIELDS,
  type Draft,
  type DraftLine,
  type InvoiceDetail,
  type LineItem,
} from './types'

/** Turn an invoice into editable strings ('' for missing values). */
export function toDraft(invoice: InvoiceDetail): Draft {
  const header = Object.fromEntries(
    HEADER_FIELDS.map((name) => [name, invoice[name] ?? '']),
  ) as Record<(typeof HEADER_FIELDS)[number], string>
  return { ...header, line_items: invoice.line_items.map(toDraftLine) }
}

function toDraftLine(line: LineItem): DraftLine {
  return Object.fromEntries(LINE_FIELDS.map((name) => [name, line[name] ?? ''])) as DraftLine
}

export function emptyLine(): DraftLine {
  return Object.fromEntries(LINE_FIELDS.map((name) => [name, ''])) as DraftLine
}

/** Two values are the same if they're equal as numbers ("100" == "100.00") or as text. */
function same(a: string, b: string): boolean {
  if (a.trim() === b.trim()) return true
  const x = Number(a)
  const y = Number(b)
  return a.trim() !== '' && b.trim() !== '' && !Number.isNaN(x) && !Number.isNaN(y) && x === y
}

const nullIfEmpty = (value: string) => (value.trim() === '' ? null : value.trim())

/**
 * The PATCH body for what the reviewer changed: only edited header fields, and
 * the full line list if any line changed (the API replaces lines as a whole).
 * Returns null when nothing changed.
 */
export function buildUpdate(original: Draft, draft: Draft): Record<string, unknown> | null {
  const changes: Record<string, unknown> = {}
  for (const name of HEADER_FIELDS) {
    if (!same(original[name], draft[name])) changes[name] = nullIfEmpty(draft[name])
  }
  const linesChanged =
    original.line_items.length !== draft.line_items.length ||
    draft.line_items.some((line, i) => LINE_FIELDS.some((name) => !same(original.line_items[i][name], line[name])))
  if (linesChanged) {
    changes.line_items = draft.line_items.map((line) =>
      Object.fromEntries(LINE_FIELDS.map((name) => [name, nullIfEmpty(line[name])])),
    )
  }
  return Object.keys(changes).length ? changes : null
}
