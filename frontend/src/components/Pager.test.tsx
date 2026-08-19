import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Pager } from './Pager'

describe('Pager', () => {
  it('stays out of the way when everything fits on one page', () => {
    const { container } = render(<Pager offset={0} pageSize={50} shown={12} onChange={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('offers a next page when the page came back full', () => {
    // A full page is the only signal available that there is more -- the API
    // returns no total, and guessing one would be worse than not saying.
    render(<Pager offset={0} pageSize={50} shown={50} onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: '下一頁' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '上一頁' })).toBeDisabled()
  })

  it('says which rows are on screen', () => {
    render(<Pager offset={50} pageSize={50} shown={20} onChange={vi.fn()} />)
    expect(screen.getByText('第 51–70 筆')).toBeInTheDocument()
  })

  it('steps forward by a page', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Pager offset={0} pageSize={50} shown={50} onChange={onChange} />)

    await user.click(screen.getByRole('button', { name: '下一頁' }))
    expect(onChange).toHaveBeenCalledWith(50)
  })

  it('never steps back past the beginning', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Pager offset={20} pageSize={50} shown={20} onChange={onChange} />)

    await user.click(screen.getByRole('button', { name: '上一頁' }))
    expect(onChange).toHaveBeenCalledWith(0)
  })
})
