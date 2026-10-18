import type { ValidationIssue } from './types'

// Below this, a field gets a "check this" badge. The model's confidences are
// usually 0.95-1.0, so anything under 0.8 is genuinely unusual.
export const LOW_CONFIDENCE = 0.8

export type FieldFlag = 'error' | 'warning' | null

/** Open (not dismissed) issues grouped by field, e.g. "total" or "line_items[2].gst_rate". */
export function openIssuesByField(issues: ValidationIssue[]): Map<string, ValidationIssue[]> {
  const byField = new Map<string, ValidationIssue[]>()
  for (const issue of issues) {
    if (issue.resolved) continue
    byField.set(issue.field, [...(byField.get(issue.field) ?? []), issue])
  }
  return byField
}

/** The strongest open flag on a field: an error wins over a warning. */
export function flagFor(byField: Map<string, ValidationIssue[]>, field: string): FieldFlag {
  const issues = byField.get(field) ?? []
  if (issues.some((issue) => issue.severity === 'error')) return 'error'
  if (issues.length) return 'warning'
  return null
}

export function lineField(position: number, name: string): string {
  return `line_items[${position}].${name}`
}

export function openErrorCount(issues: ValidationIssue[]): number {
  return issues.filter((issue) => issue.severity === 'error' && !issue.resolved).length
}

export function isLowConfidence(score: number | undefined): boolean {
  return score !== undefined && score < LOW_CONFIDENCE
}
