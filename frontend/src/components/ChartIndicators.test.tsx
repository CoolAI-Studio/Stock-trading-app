import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChartIndicators, INDICATOR_STORAGE_KEY, type SelectedIndicator } from './ChartIndicators'
import { api } from '../lib/api'
import type { AvailableIndicators } from '../lib/types'

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn(), post: vi.fn() },
}))

/**
 * Picking indicators without writing Python.
 *
 * The owner's words: 「沒有任何指標可以選擇，重點就是要那些指標才有辦法下策略跟
 * 回測」. There are forty indicators in the runtime and the only way to reach one
 * was to write a strategy in a textarea, which for this app's audience is the
 * same as not having them.
 *
 * WHAT IS TESTED HERE is the choosing: that the list comes from the server,
 * that the tuning knobs are editable, that a choice survives a reload. What is
 * NOT tested here -- and must never be implemented here -- is the arithmetic.
 * Every value comes from the server, computed by the same function object the
 * strategy sandbox hands to user code. A moving average in TypeScript would be
 * a second implementation, and the day the two disagreed the chart would be a
 * picture of something that is not happening.
 */

const AVAILABLE: AvailableIndicators = {
  indicators: [
    {
      name: 'sma',
      title: '簡單移動平均',
      category: 'trend',
      category_label: '趨勢',
      outputs: [{ key: '', pane: 'price', scale: 'sma' }],
      params: [{ name: 'period', type: 'int', default: 20 }],
    },
    {
      name: 'rsi',
      title: '相對強弱指標',
      category: 'momentum',
      category_label: '動能',
      outputs: [{ key: '', pane: 'own', scale: 'rsi' }],
      params: [{ name: 'period', type: 'int', default: 14 }],
    },
    {
      name: 'macd',
      title: 'MACD',
      category: 'trend',
      category_label: '趨勢',
      outputs: [
        { key: 'macd', pane: 'own', scale: 'macd' },
        { key: 'signal', pane: 'own', scale: 'macd' },
        { key: 'histogram', pane: 'own', scale: 'macd' },
      ],
      params: [],
    },
  ],
}

function show(props: Partial<Parameters<typeof ChartIndicators>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onChange = props.onChange ?? vi.fn()
  render(
    <QueryClientProvider client={client}>
      <ChartIndicators selected={props.selected ?? []} onChange={onChange} />
    </QueryClientProvider>,
  )
  return onChange
}

beforeEach(() => {
  vi.clearAllMocks()
  window.localStorage.clear()
  vi.mocked(api.get).mockResolvedValue(AVAILABLE as never)
})

/** The list is the server's. */
describe('what can be picked', () => {
  it('offers what the backend says it can compute', async () => {
    show()

    expect(await screen.findByRole('option', { name: /簡單移動平均/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /相對強弱指標/ })).toBeInTheDocument()
  })

  it('does not ship its own list of indicator names', async () => {
    // A hard-coded list here would drift from the runtime the first time an
    // indicator was added or renamed, and would offer a choice the server
    // then refuses.
    show()

    await screen.findByRole('option', { name: /簡單移動平均/ })
    expect(api.get).toHaveBeenCalledWith('/api/market/indicators/available')
  })

  it('groups them under a heading a reader recognises, not the enum value', async () => {
    // CLAUDE.md: the audience is not an engineer. 「trend」 as a group heading
    // is the enum leaking onto the screen.
    show()

    await screen.findByRole('option', { name: /簡單移動平均/ })
    expect(screen.getByRole('group', { name: '趨勢' })).toBeInTheDocument()
  })

  it('says so when the list cannot be loaded, rather than looking empty', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('boom'))
    show()

    expect(await screen.findByRole('alert')).toHaveTextContent(/指標清單/)
  })
})

describe('picking one', () => {
  it('reports the choice with the author’s own defaults', async () => {
    const onChange = show()

    await screen.findByRole('option', { name: /簡單移動平均/ })
    await userEvent.selectOptions(screen.getByLabelText('加入指標'), 'sma')


    expect(onChange).toHaveBeenCalledWith([{ name: 'sma', params: { period: 20 } }])
  })

  it('lets the numbers be changed without touching code', async () => {
    const onChange = show({ selected: [{ name: 'sma', params: { period: 20 } }] })

    await screen.findByLabelText('sma period')
    await userEvent.clear(screen.getByLabelText('sma period'))
    await userEvent.type(screen.getByLabelText('sma period'), '60')

    expect(onChange).toHaveBeenLastCalledWith([{ name: 'sma', params: { period: 60 } }])
  })

  it('does not snap the box back while a new number is being typed', async () => {
    // Bound straight to the parent's value, clearing the box and typing 60
    // produces 2060: the parent never hears an empty box, so it keeps
    // reporting 20 and the field refills mid-edit.
    show({ selected: [{ name: 'sma', params: { period: 20 } }] })

    const field = await screen.findByLabelText('sma period')
    await userEvent.clear(field)

    expect(field).toHaveValue(null)
  })

  it('removes one that is no longer wanted', async () => {
    const onChange = show({ selected: [{ name: 'sma', params: { period: 20 } }] })

    await userEvent.click(await screen.findByRole('button', { name: /移除 sma/ }))

    expect(onChange).toHaveBeenCalledWith([])
  })

  it('does not offer one that is already on the chart', async () => {
    // Two identical lines drawn on top of each other, and one of the eight
    // slots spent on it. Offering a choice that does nothing is worse than not
    // offering it.
    show({ selected: [{ name: 'sma', params: { period: 20 } }] })

    expect(await screen.findByRole('option', { name: /相對強弱指標/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /簡單移動平均/ })).not.toBeInTheDocument()
  })

  it('stops at the cap the server enforces, and says why', async () => {
    // The server refuses a ninth with a 422. Finding that out by having the
    // chart go blank is not an answer anybody can act on.
    const many: SelectedIndicator[] = ['sma', 'rsi', 'macd', 'ema', 'wma', 'atr', 'cci', 'mfi'].map(
      (name) => ({ name, params: {} }),
    )
    show({ selected: many })

    expect(await screen.findByText(/最多 8 個/)).toBeInTheDocument()
    expect(screen.getByLabelText('加入指標')).toBeDisabled()
  })
})

describe('the choice survives a reload', () => {
  it('remembers what was picked last time', async () => {
    window.localStorage.setItem(
      INDICATOR_STORAGE_KEY,
      JSON.stringify([{ name: 'rsi', params: { period: 14 } }]),
    )

    expect(ChartIndicators.restore()).toEqual([{ name: 'rsi', params: { period: 14 } }])
  })

  it('ignores stored rubbish rather than crashing the dashboard', async () => {
    // localStorage is editable by hand and survives every deploy, so a shape
    // this version does not understand WILL eventually be read back. Throwing
    // here would take the whole dashboard down with the chart.
    window.localStorage.setItem(INDICATOR_STORAGE_KEY, '{not json')

    expect(ChartIndicators.restore()).toEqual([])
  })

  it('drops a stored entry that is not shaped like an indicator', async () => {
    window.localStorage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify([{ nope: 1 }, 'sma']))

    expect(ChartIndicators.restore()).toEqual([])
  })

  it('a stored entry with no params at all does not brick the page', async () => {
    // THE WORST BUG THIS COMPONENT CAN HAVE. localStorage survives every
    // reload and every deploy, so a stored shape this version does not handle
    // throws on EVERY mount -- the dashboard is permanently broken and the fix
    // is 「open developer tools and clear localStorage」, which CLAUDE.md says
    // is the same as no fix at all for this audience.
    window.localStorage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify([{ name: 'sma' }]))

    const restored = ChartIndicators.restore()
    expect(restored).toEqual([{ name: 'sma', params: {} }])

    // And it has to survive being rendered, which is where the crash was.
    show({ selected: restored })
    expect(await screen.findByLabelText('sma period')).toHaveValue(20)
  })

  it('a stored param that is not a value the server accepts is dropped', async () => {
    // Shown as the default but still SENT, so the chart answers 422 forever
    // and the box on screen says the number is fine.
    window.localStorage.setItem(
      INDICATOR_STORAGE_KEY,
      JSON.stringify([{ name: 'sma', params: { period: { evil: true } } }]),
    )

    expect(ChartIndicators.restore()).toEqual([{ name: 'sma', params: {} }])
  })

  it('a stored entry whose params is not an object at all is dropped', async () => {
    window.localStorage.setItem(
      INDICATOR_STORAGE_KEY,
      JSON.stringify([{ name: 'sma', params: 'twenty' }]),
    )

    expect(ChartIndicators.restore()).toEqual([{ name: 'sma', params: {} }])
  })

  it('writes the choice down so the next visit gets it back', async () => {
    show({ selected: [{ name: 'sma', params: { period: 60 } }] })

    await waitFor(() =>
      expect(JSON.parse(window.localStorage.getItem(INDICATOR_STORAGE_KEY) ?? '[]')).toEqual([
        { name: 'sma', params: { period: 60 } },
      ]),
    )
    expect(ChartIndicators.restore()).toEqual([{ name: 'sma', params: { period: 60 } }])
  })
})
