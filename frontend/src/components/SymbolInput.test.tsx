import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SymbolInput } from './SymbolInput'
import { api } from '../lib/api'
import type { SymbolSearchResponse } from '../lib/types'

vi.mock('../lib/api', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

/**
 * The box where somebody types a stock.
 *
 * In the owner's words: 「輸入欄位使用者不會知道要如何填，通常是打台積電或2330這種
 * 代表性指標」. The app needed 「2330.TW」, and nothing said so anywhere. Both of
 * the natural inputs failed, and both failed quietly -- 「台積電」 stored a
 * watchlist row that never priced, and a bare 「2330」 resolved on Yahoo to an
 * unrelated Japanese company, so it priced the wrong stock with no sign of it.
 *
 * The rule: SUGGEST, NEVER SUBSTITUTE. This component will not silently turn
 * what was typed into something else. It offers candidates, the person picks
 * one, and until they do, what is typed is what would be submitted -- with a
 * warning if that cannot work.
 */

const TSMC: SymbolSearchResponse = {
  query: '台積電',
  matches: [
    {
      symbol: '2330.TW',
      name: '台積電',
      detail: '上市 · 台灣積體電路製造股份有限公司',
      market: '台股',
      data_source: 'yfinance',
      verified: true,
      currency: 'TWD',
    },
  ],
  listings_generated_at: '2026-08-19',
  us_listings_generated_at: '2026-08-19',
}

const AMBIGUOUS: SymbolSearchResponse = {
  query: '11',
  matches: [
    {
      symbol: '1101.TW',
      name: '台泥',
      detail: '上市 · 臺灣水泥股份有限公司',
      market: '台股',
      data_source: 'yfinance',
      verified: true,
      currency: 'TWD',
    },
    {
      symbol: '1102.TW',
      name: '亞泥',
      detail: '上市 · 亞洲水泥股份有限公司',
      market: '台股',
      data_source: 'yfinance',
      verified: true,
      currency: 'TWD',
    },
  ],
  listings_generated_at: '2026-08-19',
  us_listings_generated_at: '2026-08-19',
}

const NOTHING: SymbolSearchResponse = {
  query: 'zzzz',
  matches: [],
  listings_generated_at: '2026-08-19',
  us_listings_generated_at: '2026-08-19',
}

function show(props: Partial<React.ComponentProps<typeof SymbolInput>> = {}) {
  const onChange = props.onChange ?? vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <SymbolInput id="sym" label="代號" value="" onChange={onChange} {...props} />
    </QueryClientProvider>,
  )
  return { onChange }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue(NOTHING as never)
})

// --- finding the thing ------------------------------------------------------

describe('用中文名稱找股票', () => {
  it('打「台積電」就查得到 2330.TW', async () => {
    vi.mocked(api.get).mockResolvedValue(TSMC as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), '台積電')

    const list = await screen.findByRole('listbox', { name: '搜尋結果' })
    expect(within(list).getByText('2330.TW')).toBeInTheDocument()
    expect(within(list).getByText('台積電')).toBeInTheDocument()
  })

  it('選了之後才把值換成代號 —— 不會自己偷偷換掉', async () => {
    vi.mocked(api.get).mockResolvedValue(TSMC as never)
    const user = userEvent.setup()
    const { onChange } = show()

    await user.type(screen.getByLabelText('代號'), '台積電')
    const list = await screen.findByRole('listbox', { name: '搜尋結果' })
    await user.click(within(list).getByText('2330.TW'))

    expect(onChange).toHaveBeenLastCalledWith('2330.TW', expect.objectContaining({ market: '台股' }))
  })

  it('有多個結果時不會替使用者決定', async () => {
    // Picking the top hit automatically is exactly how a watchlist ends up
    // pointing at the wrong company while looking correct.
    vi.mocked(api.get).mockResolvedValue(AMBIGUOUS as never)
    const user = userEvent.setup()
    const { onChange } = show()

    await user.type(screen.getByLabelText('代號'), '11')

    const list = await screen.findByRole('listbox', { name: '搜尋結果' })
    expect(within(list).getAllByRole('option')).toHaveLength(2)
    expect(onChange).not.toHaveBeenCalledWith('1101.TW', expect.anything())
  })

  it('查不到就直說，不要留一片空白讓人以為還在載入', async () => {
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), 'zzzz')

    expect(await screen.findByText(/找不到/)).toBeInTheDocument()
  })

  it('沒把握的美股代號要標示出來，不能跟查證過的並列', async () => {
    vi.mocked(api.get).mockResolvedValue({
      query: 'AAPL',
      matches: [
        {
          symbol: 'AAPL',
          name: 'AAPL',
          detail: '美股代號（沒有對照表可以核對）',
          market: '美股',
          data_source: 'yfinance',
          verified: false,
        },
      ],
      listings_generated_at: '2026-08-19',
      us_listings_generated_at: '2026-08-19',
    } as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), 'AAPL')

    const list = await screen.findByRole('listbox', { name: '搜尋結果' })
    expect(within(list).getByText(/未核對/)).toBeInTheDocument()
  })
})

// --- refusing to let a doomed value through ---------------------------------

describe('打了不可能成立的東西', () => {
  it('中文留在框裡沒選代號時，警告它不會有報價', async () => {
    vi.mocked(api.get).mockResolvedValue(TSMC as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), '台積電')

    expect(await screen.findByRole('alert')).toHaveTextContent(/公司名稱/)
  })

  it('光打 2330 要警告，因為那會抓到別的市場的股票', async () => {
    // The dangerous case: it does not fail, it succeeds on the wrong company.
    vi.mocked(api.get).mockResolvedValue({
      ...TSMC,
      query: '2330',
      matches: TSMC.matches,
    } as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), '2330')

    expect(await screen.findByRole('alert')).toHaveTextContent(/2330\.TW/)
  })

  it('選好代號之後警告就消失', async () => {
    vi.mocked(api.get).mockResolvedValue(TSMC as never)
    show({ value: '2330.TW' })

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('合法的美股代號不要亂警告', async () => {
    show({ value: 'AAPL' })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

// --- not hammering the backend ----------------------------------------------

describe('查詢的節制', () => {
  it('太短的輸入不送查詢', async () => {
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), 'a')

    await new Promise((r) => setTimeout(r, 400))
    expect(api.get).not.toHaveBeenCalled()
  })

  it('查詢字串有做 URL 編碼，中文才不會把網址弄壞', async () => {
    vi.mocked(api.get).mockResolvedValue(TSMC as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), '台積電')

    await waitFor(() => expect(api.get).toHaveBeenCalled())
    const url = vi.mocked(api.get).mock.calls.at(-1)?.[0] as string
    expect(url).toContain(encodeURIComponent('台積電'))
  })
})

// --- telling two listings of the same company apart -------------------------
//
// 「台積電」 now returns BOTH 2330.TW and TSM. It has to, or the ambiguity is
// unreachable: somebody holding the ADR types the company's name, gets only
// the Taiwanese line, and sets 「跌破 220」 meaning US$220 against a NT$2,375
// stock. It never fires -- once, ever -- and the row looks healthy.
//
// But offering both is only safe if they are distinguishable, and the two
// things that distinguish them are the market and the currency. The provider's
// own name is identical for both.

describe('同一家公司的兩個掛牌', () => {
  const BOTH: SymbolSearchResponse = {
    query: '台積電',
    matches: [
      {
        symbol: '2330.TW',
        name: '台積電',
        detail: '上市 · 台灣積體電路製造股份有限公司',
        market: '台股',
        data_source: 'yfinance',
        verified: true,
        currency: 'TWD',
      },
      {
        symbol: 'TSM',
        name: 'Taiwan Semiconductor Manufactur',
        detail: '2330 的美股 ADR，與台股掛牌是不同的標的',
        market: '美股',
        data_source: 'yfinance',
        verified: true,
        currency: 'USD',
      },
    ],
    listings_generated_at: '2026-08-20',
    us_listings_generated_at: '2026-08-20',
  }

  it('兩條都列出來', async () => {
    vi.mocked(api.get).mockResolvedValue(BOTH as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), '台積電')

    const list = await screen.findByRole('listbox', { name: '搜尋結果' })
    expect(within(list).getByText('2330.TW')).toBeInTheDocument()
    expect(within(list).getByText('TSM')).toBeInTheDocument()
  })

  it('每一條都標出幣別 —— 那是唯一分得出 220 是哪個 220 的東西', async () => {
    vi.mocked(api.get).mockResolvedValue(BOTH as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), '台積電')

    const list = await screen.findByRole('listbox', { name: '搜尋結果' })
    expect(within(list).getByText(/台股.*TWD/)).toBeInTheDocument()
    expect(within(list).getByText(/美股.*USD/)).toBeInTheDocument()
  })

  it('沒有幣別時只顯示市場，不要憑空補一個', async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...BOTH,
      matches: [{ ...BOTH.matches[0], currency: null }],
    } as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), '台積電')

    const list = await screen.findByRole('listbox', { name: '搜尋結果' })
    expect(within(list).getByText('台股')).toBeInTheDocument()
  })
})

// --- 查不到的時候，到底是誰的問題 --------------------------------------------
//
// 「找不到」 has three causes and only one of them is the owner's mistake: a
// typo, a company listed after the tables were bundled, or a market this app
// does not model. The empty state named one date -- 「台股清單更新於…」 -- and
// there are two tables now, so a US company listed last week produced an empty
// result explained in terms of Taiwan.
//
// Without the dates, 「this is too new for the list」 is indistinguishable from
// 「you typed it wrong」, and somebody retypes a stock that exists five times
// before giving up on it.

describe('查不到的時候要說是在哪張表裡找不到', () => {
  const EMPTY: SymbolSearchResponse = {
    query: 'zzzzzzzz',
    matches: [],
    listings_generated_at: '2026-08-19',
    us_listings_generated_at: '2026-08-20',
  }

  it('兩張表的日期都要說', async () => {
    vi.mocked(api.get).mockResolvedValue(EMPTY as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), 'zzzzzzzz')

    expect(await screen.findByText(/2026-08-19/)).toBeInTheDocument()
    expect(screen.getByText(/2026-08-20/)).toBeInTheDocument()
  })

  it('沒有日期時不要憑空生一個出來', async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...EMPTY,
      listings_generated_at: null,
      us_listings_generated_at: null,
    } as never)
    const user = userEvent.setup()
    show()

    await user.type(screen.getByLabelText('代號'), 'zzzzzzzz')

    expect(await screen.findByText(/找不到/)).toBeInTheDocument()
    expect(screen.queryByText(/更新於/)).not.toBeInTheDocument()
  })
})
