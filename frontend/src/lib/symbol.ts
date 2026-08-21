/** Whether a symbol can ever produce a price, answered before the request.
 *
 * Mirrors services/symbol_search.looks_unpriceable, deliberately: the server
 * refuses these too, and the person should be told while they are still
 * looking at the field rather than after a round trip. The server remains the
 * authority; this only makes the answer arrive sooner.
 *
 * Shared rather than copied. It started inside SymbolInput and the chart needs
 * the same judgement -- two copies of a rule about which company a symbol
 * refers to is exactly the kind of duplication that drifts and then points at
 * the wrong stock on one of the two screens.
 */
export function looksUnpriceable(value: string): string | null {
  const text = value.trim()
  if (!text) return null

  // eslint-disable-next-line no-control-regex
  if (!/^[\x00-\x7F]*$/.test(text)) {
    return '這是公司名稱，不是代號。請從搜尋結果選一個，例如台積電要用 2330.TW。'
  }
  if (/^\d{4,6}$/.test(text)) {
    return `台股代號要帶市場後綴，只寫「${text}」會被行情來源當成其他市場的股票。請改用 ${text}.TW 或 ${text}.TWO。`
  }
  return null
}
