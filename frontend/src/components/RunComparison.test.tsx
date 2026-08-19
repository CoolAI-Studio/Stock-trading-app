import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { RunComparison } from './RunComparison'
import type { BacktestAssumptions, BacktestRun, BacktestSummary } from '../lib/types'

/**
 * Comparing two runs.
 *
 * The history page could already show thirty runs and no way to hold two of
 * them against each other, so "is this version better?" was answered by
 * scrolling and remembering -- which is how a 2% improvement gets attributed
 * to a code change that was actually a date-range change.
 *
 * The thing this component exists to say is therefore NOT "B is better". It
 * is "here is everything that differs between them", and, when more than one
 * thing does, that the comparison cannot attribute the difference to any of
 * them. A tool that reports a winner while quietly holding two variables is
 * worse than no tool: it manufactures a conclusion.
 */

const ASSUMPTIONS: BacktestAssumptions = {
  fill_price_basis: 'next_open',
  commission_rate: '0.001425',
  slippage_rate: '0.0005',
  sell_tax_rate: '0.003',
  quantity: '100',
  initial_capital: '100000',
  stop_loss_pct: '0.05',
  take_profit_pct: '0.1',
}

const SUMMARY: BacktestSummary = {
  bars_total: 260,
  bars_tested: 230,
  signals: 6,
  skipped_signals: 1,
  unfilled_signals: 0,
  trade_count: 4,
  wins: 2,
  losses: 2,
  stop_loss_exits: 1,
  take_profit_exits: 1,
  ambiguous_exit_bars: 0,
  win_rate_pct: '50',
  average_win: '820.5',
  average_loss: '-310.25',
  net_pnl: '510.25',
  total_costs: '618.4',
  total_return_pct: '5',
  max_drawdown_pct: '10',
  final_equity: '105000',
  open_quantity: '0',
  open_avg_entry_price: '0',
  buy_and_hold_return_pct: '3',
  excess_return_pct: '2',
  profit_factor: '2.4',
  exposure_pct: '61.5',
}

const RUN: BacktestRun = {
  id: 1,
  strategy_id: 1,
  strategy_name: 'ma5-cross',
  code_hash: 'aaa111',
  symbol: '2330.TW',
  timeframe: '1d',
  data_source: 'yfinance',
  range_start: '2025-03-01T00:00:00Z',
  range_end: '2026-03-01T23:59:59Z',
  created_at: '2026-03-02T01:00:00Z',
  assumptions: ASSUMPTIONS,
  summary: SUMMARY,
}

function other(overrides: Partial<BacktestRun> = {}): BacktestRun {
  return { ...RUN, id: 2, created_at: '2026-03-03T01:00:00Z', ...overrides }
}

function show(a: BacktestRun, b: BacktestRun) {
  return render(<RunComparison a={a} b={b} />)
}

function row(label: string): HTMLElement {
  return screen.getByText(label).closest('tr') as HTMLElement
}

// --- the deltas -------------------------------------------------------------

describe('把兩次的數字擺在一起', () => {
  it('每個指標都給 A、B 和差額', () => {
    show(
      RUN,
      other({ summary: { ...SUMMARY, total_return_pct: '9' } }),
    )

    const cells = within(row('總報酬率')).getAllByRole('cell')
    expect(cells[1]).toHaveTextContent('+5.00%')
    expect(cells[2]).toHaveTextContent('+9.00%')
    expect(cells[3]).toHaveTextContent('+4.00')
  })

  it('回撤變大是變差，不能因為數字上升就標成好事', () => {
    // The one that catches a naive implementation: every other headline is
    // better when it goes up, and drawdown is the opposite.
    show(RUN, other({ summary: { ...SUMMARY, max_drawdown_pct: '18' } }))

    const delta = within(row('最大回撤')).getAllByRole('cell')[3]
    expect(delta).toHaveTextContent('+8.00')
    expect(delta.className).toContain('red')
  })

  it('報酬變高標成好事', () => {
    show(RUN, other({ summary: { ...SUMMARY, total_return_pct: '9' } }))

    expect(within(row('總報酬率')).getAllByRole('cell')[3].className).toContain('emerald')
  })

  it('成本變高是變差', () => {
    show(RUN, other({ summary: { ...SUMMARY, total_costs: '900' } }))

    expect(within(row('成本總額')).getAllByRole('cell')[3].className).toContain('red')
  })

  it('沒得比的指標畫一槓，不要拿 null 當 0 去算差額', () => {
    show(RUN, other({ summary: { ...SUMMARY, profit_factor: null } }))

    const cells = within(row('獲利因子')).getAllByRole('cell')
    expect(cells[2]).toHaveTextContent('—')
    expect(cells[3]).toHaveTextContent('—')
  })

  it('完全一樣的兩次要說「沒有差別」，而不是一排 +0.00', () => {
    show(RUN, other())

    expect(screen.getByText(/兩次的結果完全一樣/)).toBeInTheDocument()
  })
})

// --- what actually differs --------------------------------------------------

describe('差別到底出在哪', () => {
  it('程式碼一樣時說一樣，這樣才知道差別來自別的地方', () => {
    show(RUN, other({ assumptions: { ...ASSUMPTIONS, sell_tax_rate: '0' } }))

    const box = screen.getByLabelText('兩次的差異')
    expect(within(box).getByText(/程式碼相同/)).toBeInTheDocument()
    expect(within(box).getByText(/賣出交易稅率/)).toBeInTheDocument()
  })

  it('程式碼不同就講出來，因為那通常才是重點', () => {
    show(RUN, other({ code_hash: 'bbb222' }))

    expect(within(screen.getByLabelText('兩次的差異')).getByText(/程式碼不同/)).toBeInTheDocument()
  })

  it('換了股票就講，這種比較根本不是同一件事', () => {
    show(RUN, other({ symbol: '2317.TW' }))

    const box = screen.getByLabelText('兩次的差異')
    expect(within(box).getByText(/2330\.TW/)).toBeInTheDocument()
    expect(within(box).getByText(/2317\.TW/)).toBeInTheDocument()
  })

  it('換了區間就講', () => {
    show(RUN, other({ range_start: '2024-01-01T00:00:00Z' }))

    expect(within(screen.getByLabelText('兩次的差異')).getByText(/區間/)).toBeInTheDocument()
  })

  it('停損停利改了也算一項差異', () => {
    show(RUN, other({ assumptions: { ...ASSUMPTIONS, stop_loss_pct: '0' } }))

    expect(within(screen.getByLabelText('兩次的差異')).getByText(/停損/)).toBeInTheDocument()
  })
})

// --- the part that stops a wrong conclusion ---------------------------------

describe('不能把差別歸給不知道哪一個原因', () => {
  it('同時改了兩個以上就直說：這樣看不出是哪一個造成的', () => {
    show(RUN, other({ code_hash: 'bbb222', symbol: '2317.TW' }))

    expect(screen.getByRole('status')).toHaveTextContent(/看不出/)
  })

  it('只差一項時不要警告 —— 警告太常出現就沒人看了', () => {
    show(RUN, other({ code_hash: 'bbb222' }))

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('兩次都測不到同樣多的 K 棒，也要當成一項差異講出來', () => {
    // Same requested range, but one provider call reached further back than
    // the other. The ranges look identical in the header while the runs
    // covered different periods -- exactly the silent confound this box is
    // for.
    show(RUN, other({ summary: { ...SUMMARY, bars_tested: 120 } }))

    expect(within(screen.getByLabelText('兩次的差異')).getByText(/K 棒/)).toBeInTheDocument()
  })
})
