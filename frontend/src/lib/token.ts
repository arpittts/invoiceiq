// The JWT is kept in localStorage so a refresh keeps you signed in. It expires
// after 60 minutes (set by the backend); after that the API answers 401 and we
// sign out. Storage can be unavailable (private mode), so every access is guarded.
const STORAGE_KEY = 'invoiceiq.token'

/** True if the token's `exp` claim is in the past. We only read it, never trust it. */
export function isExpired(token: string, now = Date.now()): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return typeof payload.exp === 'number' && payload.exp * 1000 <= now
  } catch {
    return true
  }
}

export function readStoredToken(): string | null {
  try {
    const token = localStorage.getItem(STORAGE_KEY)
    return token && !isExpired(token) ? token : null
  } catch {
    return null
  }
}

export function storeToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(STORAGE_KEY, token)
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // storage blocked: the session just won't survive a refresh
  }
}
