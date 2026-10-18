// Base URL of the backend, set per environment through VITE_API_URL.
export const API_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

export type Health = { status: string; database: string }

export async function fetchHealth(): Promise<Health> {
  const response = await fetch(`${API_URL}/api/v1/health`)
  // A 503 still carries a JSON body describing what is down.
  if (!response.ok && response.status !== 503) {
    throw new Error(`Health check failed with HTTP ${response.status}`)
  }
  return response.json()
}
