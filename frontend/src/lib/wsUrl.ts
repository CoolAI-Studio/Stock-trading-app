/** The WebSocket address, derived from the API address when nobody set one.
 *
 * The deploy flow asked for both, and the README's own instruction for the
 * second was 「同一個網址，但開頭改成 wss://」 -- which is a transformation, not
 * a decision. A blank whose correct value is mechanically derivable from the
 * blank above it is a blank that should not be asked for: it is one more thing
 * to paste, one more place to typo, and getting it wrong produces a dashboard
 * whose prices never update with nothing on screen saying why.
 *
 * VITE_WS_URL still wins when it is set, for the deployment that genuinely
 * serves its socket somewhere else.
 */
export function websocketBaseUrl(apiBaseUrl: string, explicit?: string): string {
  const override = (explicit ?? '').trim()
  if (override) return override

  const api = apiBaseUrl.trim()
  if (api.startsWith('https://')) return `wss://${api.slice('https://'.length)}`
  if (api.startsWith('http://')) return `ws://${api.slice('http://'.length)}`
  // Not a URL this can transform. Returned unchanged rather than guessed at:
  // a wrong socket address fails silently, and the caller's own default is a
  // better answer than an invented one.
  return api
}
