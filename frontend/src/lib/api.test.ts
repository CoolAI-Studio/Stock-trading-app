import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  api,
  getToken,
  login,
  setSetupRequiredHandler,
  setToken,
  setUnauthorizedHandler,
} from './api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('token storage', () => {
  afterEach(() => setToken(null))

  it('persists the token to localStorage and reads it back', () => {
    setToken('abc123')
    expect(getToken()).toBe('abc123')
    expect(localStorage.getItem('trading_app_token')).toBe('abc123')
  })

  it('clears the token from localStorage when set to null', () => {
    setToken('abc123')
    setToken(null)
    expect(getToken()).toBeNull()
    expect(localStorage.getItem('trading_app_token')).toBeNull()
  })
})

describe('api requests', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setToken(null)
    setUnauthorizedHandler(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('attaches the bearer token to authenticated requests', async () => {
    setToken('my-token')
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ok: true }))

    await api.get('/api/orders')

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer my-token')
  })

  it('does not attach an Authorization header when there is no token', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ok: true }))

    await api.get('/api/healthz')

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.has('Authorization')).toBe(false)
  })

  it('JSON-encodes the body and sets Content-Type on POST', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: 1 }))

    await api.post('/api/strategies', { name: 'test' })

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(init?.body).toBe(JSON.stringify({ name: 'test' }))
  })

  it('returns the parsed JSON body on success', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ id: 42 }))

    const result = await api.get<{ id: number }>('/api/orders/42')

    expect(result).toEqual({ id: 42 })
  })

  it('throws ApiError with the server detail message on failure', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'not found' }, 404))

    await expect(api.get('/api/orders/999')).rejects.toMatchObject({
      status: 404,
      message: 'not found',
    })
  })

  // --- 驗證失敗的說明要到得了畫面上 ----------------------------------------------
  //
  // FastAPI 對 pydantic 驗證失敗回的 `detail` 是**陣列**，不是字串。原本只認字串，於是後端
  // 每一句寫好的中文說明都被換成 HTTP 的狀態列——本機是「Unprocessable Content」，而正式
  // 機走 HTTP/2，狀態列是空字串（量到的：線上 nextHopProtocol 'h2'、statusText ""）。
  // 下面的 body 是本機真的後端對 {"symbol":"蘋果"} 回的那一份，原封不動。

  const refusedChineseName = {
    detail: [
      {
        type: 'value_error',
        loc: ['body', 'symbol'],
        msg: 'Value error, 「蘋果」是公司名稱，不是代號。請用上面的搜尋選出正確的代號 —— 例如台積電是 2330.TW。',
        input: '蘋果',
        ctx: { error: {} },
      },
    ],
  }

  it('shows the reason a validator gave, not the status line', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(refusedChineseName, 422))

    await expect(api.post('/api/watchlist', { symbol: '蘋果' })).rejects.toMatchObject({
      status: 422,
      message: '「蘋果」是公司名稱，不是代號。請用上面的搜尋選出正確的代號 —— 例如台積電是 2330.TW。',
    })
  })

  it('keeps every reason when more than one field was refused, once each', async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse(
        {
          detail: [
            { type: 'value_error', loc: ['body'], msg: 'Value error, 回測的結束時間必須晚於開始時間。' },
            { type: 'missing', loc: ['body', 'symbol'], msg: 'Field required' },
            { type: 'value_error', loc: ['body'], msg: 'Value error, 回測的結束時間必須晚於開始時間。' },
          ],
        },
        422,
      ),
    )

    await expect(api.post('/api/backtest', {})).rejects.toMatchObject({
      message: '回測的結束時間必須晚於開始時間。；Field required',
    })
  })

  it('falls back to the status line for a shape it does not recognise', async () => {
    // 元件各自的中文備用句（「加不進去，請確認代號。」）靠的是訊息為空才出現——認不得的形狀
    // 不可以被猜成一句話，否則那些備用句就再也出不來。
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: [{ nothing: 'useful' }] }, 422))

    await expect(api.post('/api/watchlist', {})).rejects.toMatchObject({ status: 422, message: '' })
  })

  it('clears the token and calls the unauthorized handler on 401', async () => {
    setToken('stale-token')
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'expired' }, 401))

    await expect(api.get('/api/orders')).rejects.toBeInstanceOf(ApiError)

    expect(getToken()).toBeNull()
    expect(handler).toHaveBeenCalledOnce()
  })

  it('returns undefined for a 204 No Content response', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }))

    const result = await api.delete('/api/strategies/1')

    expect(result).toBeUndefined()
  })
})

describe('login', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setToken(null)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    setToken(null)
  })

  it('sends form-encoded credentials and stores the returned token', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ access_token: 'tok-123', token_type: 'bearer' }))

    const token = await login('me@example.com', 'hunter2')

    expect(token).toBe('tok-123')
    expect(getToken()).toBe('tok-123')

    const [, init] = vi.mocked(fetch).mock.calls[0]
    const body = init?.body
    expect(body).toBeInstanceOf(URLSearchParams)
    expect((body as URLSearchParams).get('username')).toBe('me@example.com')
  })

  it('throws ApiError and does not store a token on bad credentials', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'Incorrect email or password' }, 401))

    await expect(login('me@example.com', 'wrong')).rejects.toBeInstanceOf(ApiError)
    expect(getToken()).toBeNull()
  })
})

// --- a deployment that has not been configured yet ---------------------------
//
// The backend stays up in setup mode and answers 503 with `setup_required` on
// every real route, so that a stranger's brand-new deploy has something to say
// instead of a 502 from a dead process. Every page in this app would otherwise
// render its own generic 「載入失敗」 and leave them there.

describe('setup mode', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    setToken(null)
    setUnauthorizedHandler(() => {})
    setSetupRequiredHandler(() => {})
  })
  afterEach(() => vi.unstubAllGlobals())

  it('calls the setup handler when the backend says it is not configured', async () => {
    const handler = vi.fn()
    setSetupRequiredHandler(handler)
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ detail: '還沒設定完成', setup_required: true }, 503),
    )

    await expect(api.get('/api/positions')).rejects.toBeInstanceOf(ApiError)

    expect(handler).toHaveBeenCalledOnce()
  })

  it('leaves an ordinary 503 alone', async () => {
    // Render's own 「service unavailable」 during a cold start looks like this.
    // Redirecting to a setup page over it would tell somebody their configured
    // deployment is unconfigured.
    const handler = vi.fn()
    setSetupRequiredHandler(handler)
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: 'upstream busy' }, 503))

    await expect(api.get('/api/positions')).rejects.toBeInstanceOf(ApiError)

    expect(handler).not.toHaveBeenCalled()
  })

  it('does not clear the token -- nothing is wrong with the session', async () => {
    setToken('still-good')
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ detail: '還沒設定完成', setup_required: true }, 503),
    )

    await expect(api.get('/api/positions')).rejects.toBeInstanceOf(ApiError)

    expect(getToken()).toBe('still-good')
  })
})
