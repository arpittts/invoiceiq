import type { AskResult, InvoiceDetail, InvoiceList } from './types'

// Base URL of the backend, set per environment through VITE_API_URL.
export const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')
const V1 = `${API_URL}/api/v1`

export type Health = { status: string; database: string }

export async function fetchHealth(): Promise<Health> {
  const response = await fetch(`${V1}/health`)
  // A 503 still carries a JSON body describing what is down.
  if (!response.ok && response.status !== 503) {
    throw new Error(`Health check failed with HTTP ${response.status}`)
  }
  return response.json()
}

/** An HTTP error with the API's own message (FastAPI puts it in `detail`). */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// The auth layer registers these so every request carries the token, and a
// 401 (expired or invalid token) signs the user out everywhere at once.
let getToken: () => string | null = () => null
let onUnauthorized: () => void = () => {}
export function configureAuth(tokenGetter: () => string | null, handle401: () => void) {
  getToken = tokenGetter
  onUnauthorized = handle401
}

function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function errorFrom(response: Response): Promise<ApiError> {
  let message = `Request failed (HTTP ${response.status})`
  try {
    const body = await response.json()
    if (typeof body.detail === 'string') message = body.detail
    // Validation errors (422) come as a list: show the first readable one.
    else if (Array.isArray(body.detail) && body.detail[0]?.msg) message = body.detail[0].msg
  } catch {
    // not JSON: keep the generic message
  }
  if (response.status === 401) onUnauthorized()
  return new ApiError(response.status, message)
}

async function request<T>(path: string, options: { method?: string; json?: unknown } = {}): Promise<T> {
  const response = await fetch(`${V1}${path}`, {
    method: options.method ?? 'GET',
    headers: {
      ...authHeaders(),
      ...(options.json !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: options.json !== undefined ? JSON.stringify(options.json) : undefined,
  })
  if (!response.ok) throw await errorFrom(response)
  return response.json()
}

export const api = {
  register: (email: string, password: string) =>
    request<{ id: number; email: string }>('/auth/register', { method: 'POST', json: { email, password } }),
  login: (email: string, password: string) =>
    request<{ access_token: string }>('/auth/login', { method: 'POST', json: { email, password } }),
  me: () => request<{ id: number; email: string }>('/auth/me'),

  listInvoices: (params: Record<string, string | number | undefined>) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') query.set(key, String(value))
    }
    return request<InvoiceList>(`/invoices?${query}`)
  },
  getInvoice: (id: number) => request<InvoiceDetail>(`/invoices/${id}`),
  updateInvoice: (id: number, changes: unknown) =>
    request<InvoiceDetail>(`/invoices/${id}`, { method: 'PATCH', json: changes }),
  dismissIssue: (id: number, issueId: number) =>
    request<InvoiceDetail>(`/invoices/${id}/issues/${issueId}/dismiss`, { method: 'POST' }),
  approve: (id: number) => request<InvoiceDetail>(`/invoices/${id}/approve`, { method: 'POST' }),
  reject: (id: number) => request<InvoiceDetail>(`/invoices/${id}/reject`, { method: 'POST' }),
  ask: (question: string) => request<AskResult>('/ask', { method: 'POST', json: { question } }),

  /** A page image as a blob URL. <img src> can't send the token header, so we fetch it. */
  async pageImageUrl(id: number, page: number): Promise<string> {
    const response = await fetch(`${V1}/invoices/${id}/pages/${page}`, { headers: authHeaders() })
    if (!response.ok) throw await errorFrom(response)
    return URL.createObjectURL(await response.blob())
  },

  /** Upload with progress (fetch can't report upload progress, XMLHttpRequest can). */
  upload(file: File, onProgress: (fraction: number) => void): Promise<{ id: number }> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${V1}/invoices`)
      for (const [name, value] of Object.entries(authHeaders())) xhr.setRequestHeader(name, value)
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(event.loaded / event.total)
      }
      xhr.onload = () => {
        let body: { id?: number; detail?: string } = {}
        try {
          body = JSON.parse(xhr.responseText)
        } catch {
          // keep empty
        }
        if (xhr.status === 201 && body.id !== undefined) resolve({ id: body.id })
        else {
          if (xhr.status === 401) onUnauthorized()
          reject(new ApiError(xhr.status, body.detail ?? `Upload failed (HTTP ${xhr.status})`))
        }
      }
      xhr.onerror = () => reject(new ApiError(0, 'Network error: is the server running?'))
      const form = new FormData()
      form.append('file', file)
      xhr.send(form)
    })
  },
}
