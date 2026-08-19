const TOKEN_KEY = 'trading_app_token'
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

/** Set when a request comes back 401, read once by the login page. Session
 * storage rather than a state variable, because the redirect it causes
 * remounts the tree. */
export const SESSION_EXPIRED_KEY = 'session-expired'

let inMemoryToken: string | null = null
let unauthorizedHandler: (() => void) | null = null

export function getToken(): string | null {
  if (inMemoryToken === null) {
    inMemoryToken = localStorage.getItem(TOKEN_KEY)
  }
  return inMemoryToken
}

export function setToken(token: string | null): void {
  inMemoryToken = token
  if (token) {
    localStorage.setItem(TOKEN_KEY, token)
  } else {
    localStorage.removeItem(TOKEN_KEY)
  }
}

/** Called on any 401 -- lets the app redirect to /login without api.ts
 * depending on the router. */
export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json()
    return typeof body?.detail === 'string' ? body.detail : response.statusText
  } catch {
    return response.statusText
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (init.body && !(init.body instanceof URLSearchParams) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })

  if (response.status === 401) {
    // Recorded before the token goes: the login page reads it to explain why
    // the owner is suddenly back here. Without it, being bounced mid-task --
    // halfway through a strategy, with the code they were writing gone --
    // looks like the app crashed or logged them out on purpose.
    sessionStorage.setItem(SESSION_EXPIRED_KEY, '1')
    setToken(null)
    unauthorizedHandler?.()
    throw new ApiError(401, await parseErrorDetail(response))
  }

  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response))
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string): Promise<T> => request<T>(path),
  post: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: 'PATCH', body: body !== undefined ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown): Promise<T> =>
    request<T>(path, { method: 'PUT', body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string): Promise<T> => request<T>(path, { method: 'DELETE' }),
}

export async function login(email: string, password: string): Promise<string> {
  const params = new URLSearchParams({ username: email, password })
  const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: params,
  })

  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response))
  }

  const data = (await response.json()) as { access_token: string }
  setToken(data.access_token)
  return data.access_token
}
