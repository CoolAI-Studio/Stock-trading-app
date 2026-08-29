const TOKEN_KEY = 'trading_app_token'
import { resolveApiBase } from './apiBase'

// 後端在哪裡。預設是**同源**——後端直接供應這個頁面，所以只要部署一次。
// 前端另外部署的人設 VITE_API_BASE_URL 指向他的後端；見 apiBase.ts。
const API_BASE_URL = resolveApiBase({
  base: import.meta.env.VITE_API_BASE_URL,
  dev: import.meta.env.DEV,
})

/** Set when a request comes back 401, read once by the login page. Session
 * storage rather than a state variable, because the redirect it causes
 * remounts the tree. */
export const SESSION_EXPIRED_KEY = 'session-expired'

let inMemoryToken: string | null = null
let unauthorizedHandler: (() => void) | null = null
let setupRequiredHandler: (() => void) | null = null

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

/** Called when the backend reports it has not been configured yet -- lets the
 * app send somebody to the setup page without api.ts depending on the router,
 * the same seam the 401 handler above uses.
 *
 * Without it every page renders its own generic 「載入失敗」 over a deployment
 * whose only real problem is an unfilled blank, and the person who just clicked
 * 「Deploy to Render」 has no way to find that out. */
export function setSetupRequiredHandler(handler: () => void): void {
  setupRequiredHandler = handler
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

  if (response.status === 503) {
    // Recognised by the FLAG, not by the status. Render answers a plain 503
    // during a cold start on the free tier, and sending somebody to a setup
    // page over that would tell them their working deployment is
    // unconfigured. The token is deliberately left alone -- nothing is wrong
    // with the session; the deployment simply has a blank in it.
    const body = await response
      .clone()
      .json()
      .catch(() => null)
    if (body?.setup_required === true) {
      setupRequiredHandler?.()
    }
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

/** Fetches a file and hands it to the browser as a download.
 *
 * Not a plain link: the export endpoints are behind the same bearer token as
 * everything else, and an `<a href>` carries no Authorization header, so the
 * download would arrive as a 401 page saved to disk. Fetch it, turn it into a
 * blob, click a synthetic link, revoke the URL.
 */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const token = getToken()
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (response.status === 401) {
    sessionStorage.setItem(SESSION_EXPIRED_KEY, '1')
    setToken(null)
    unauthorizedHandler?.()
    throw new ApiError(401, '登入時效到了')
  }
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response))
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Freed on the next tick rather than immediately: revoking before the
  // browser has started reading it cancels the download in Safari.
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

/** Posts a body and hands the response back as a download.
 *
 * The backup passphrase must not travel in a URL -- it would land in the
 * browser history and in whatever logs the proxy keeps -- so this is a POST
 * whose response is a file rather than JSON.
 */
export async function downloadPost(
  path: string,
  body: unknown,
  filename: string,
): Promise<void> {
  const token = getToken()
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  })
  if (response.status === 401) {
    sessionStorage.setItem(SESSION_EXPIRED_KEY, '1')
    setToken(null)
    unauthorizedHandler?.()
    throw new ApiError(401, '登入時效到了')
  }
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response))
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
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
