import { render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { StrategyParams, type ParamValue } from './StrategyParams'

/**
 * The numbers inside a strategy, edited without editing Python.
 *
 * CLAUDE.md says the audience is 「不會寫 Python 的使用者」. Every number a
 * strategy decides on -- the moving-average window, the threshold, how many
 * bars to look back -- was a literal in the source, so changing 5 to 20 meant
 * editing Python in a textarea. For this audience that is the same as not
 * being able to change it.
 *
 * The source declares `self.params = {"window": 5}`; the validator reports
 * those defaults; this renders one field per parameter and hands back only
 * what differs. Only the differences, because storing the whole merged dict
 * would pin the strategy to whatever the defaults were on the day it was
 * saved.
 */

const DECLARED = { window: 5, threshold: 1.5, enabled: true, label: 'hi' }

/** A stateful parent, because the component is controlled.
 *
 * With a bare spy the value prop never moves, so the box snaps back to the
 * default after every keystroke and `user.type('20')` produces 5 → 52 → 50.
 * The harness has to behave like the real form or the test is measuring the
 * harness.
 */
function show(props: Partial<React.ComponentProps<typeof StrategyParams>> = {}) {
  const onChange = vi.fn()

  function Harness() {
    const [value, setValue] = useState<Record<string, ParamValue>>(props.value ?? {})
    return (
      <StrategyParams
        declared={props.declared ?? DECLARED}
        value={value}
        onChange={(next) => {
          onChange(next)
          setValue(next)
        }}
      />
    )
  }

  render(<Harness />)
  return { onChange }
}

// --- one field per parameter ---------------------------------------------------

describe('宣告了參數的策略', () => {
  it('每個參數都給一個輸入框', () => {
    show()

    expect(screen.getByLabelText('window')).toBeInTheDocument()
    expect(screen.getByLabelText('threshold')).toBeInTheDocument()
  })

  it('真假值用打勾，不是叫人打 true', () => {
    show()

    expect(screen.getByLabelText('enabled')).toHaveAttribute('type', 'checkbox')
  })

  it('數字用數字框，手機才會跳數字鍵盤', () => {
    show()

    expect(screen.getByLabelText('window')).toHaveAttribute('type', 'number')
  })

  it('文字就是文字', () => {
    show()

    expect(screen.getByLabelText('label')).toHaveAttribute('type', 'text')
  })

  it('沒有參數的策略不要留一塊空白區塊', () => {
    const { container } = render(<StrategyParams declared={{}} value={{}} onChange={vi.fn()} />)

    expect(container).toBeEmptyDOMElement()
  })
})

// --- what the boxes show -------------------------------------------------------

describe('框裡顯示什麼', () => {
  it('沒動過就顯示作者的預設值', () => {
    show()

    expect(screen.getByLabelText('window')).toHaveValue(5)
  })

  it('動過就顯示你設的值', () => {
    show({ value: { window: 20 } })

    expect(screen.getByLabelText('window')).toHaveValue(20)
  })

  it('說得出作者的預設值是多少 —— 不然改壞了不知道怎麼改回去', () => {
    show({ value: { window: 20 } })

    // Scoped to the row: 「預設 1.5」 on the threshold matches a loose /預設.*5/ too.
    const row = screen.getByLabelText('window').closest('label')!
    expect(within(row).getByText(/預設 5/)).toBeInTheDocument()
  })
})

// --- what it hands back ----------------------------------------------------------

describe('回報的值', () => {
  it('只回報跟預設不同的 —— 存整份會把策略釘死在今天的預設值上', async () => {
    const user = userEvent.setup()
    const { onChange } = show()

    await user.clear(screen.getByLabelText('window'))
    await user.type(screen.getByLabelText('window'), '20')

    expect(onChange).toHaveBeenLastCalledWith({ window: 20 })
  })

  it('改回預設值就從覆蓋清單裡拿掉', async () => {
    const user = userEvent.setup()
    const { onChange } = show({ value: { window: 20 } })

    await user.clear(screen.getByLabelText('window'))
    await user.type(screen.getByLabelText('window'), '5')

    expect(onChange).toHaveBeenLastCalledWith({})
  })

  it('數字要送出數字，不是字串', async () => {
    // 「20」 as text compares against a number in ways nobody predicts, and the
    // backend refuses it -- correctly, but only after a round trip.
    const user = userEvent.setup()
    const { onChange } = show()

    await user.clear(screen.getByLabelText('threshold'))
    await user.type(screen.getByLabelText('threshold'), '2.5')

    expect(onChange).toHaveBeenLastCalledWith({ threshold: 2.5 })
  })

  it('打勾送出真假值', async () => {
    const user = userEvent.setup()
    const { onChange } = show()

    await user.click(screen.getByLabelText('enabled'))

    expect(onChange).toHaveBeenLastCalledWith({ enabled: false })
  })

  it('清空數字框不要送出 NaN', async () => {
    // Mid-edit the box is empty, and Number('') is 0 -- which would silently
    // save 0 as the window and produce a strategy that divides by zero.
    const user = userEvent.setup()
    const { onChange } = show({ value: { window: 20 } })

    await user.clear(screen.getByLabelText('window'))

    const last = onChange.mock.calls.at(-1)?.[0]
    expect(last?.window === undefined || Number.isFinite(last.window)).toBe(true)
  })
})

// --- a stored value for a parameter that no longer exists -------------------------

describe('程式碼改過之後', () => {
  it('存著的值對應不到任何宣告時，要講出來而不是安靜地留著', () => {
    // The backend refuses this on save. Saying so here is what stops somebody
    // wondering why their setting does nothing.
    show({ value: { removed: 1 } })

    expect(screen.getByRole('alert')).toHaveTextContent(/removed/)
  })
})
