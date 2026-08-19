import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ActionError } from './ActionError'
import { ApiError } from '../lib/api'

describe('ActionError', () => {
  it('renders nothing when the action has not failed', () => {
    const { container } = render(<ActionError error={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("passes the backend's own wording through", () => {
    render(<ActionError error={new ApiError(422, '持倉不足，無法賣出 10 股')} />)
    expect(screen.getByRole('alert')).toHaveTextContent('持倉不足，無法賣出 10 股')
  })

  it('still says something when the failure is not an Error', () => {
    render(<ActionError error={'boom'} />)
    expect(screen.getByRole('alert')).toHaveTextContent('操作失敗')
  })
})
