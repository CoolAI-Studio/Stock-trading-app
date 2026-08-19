import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

function Explodes(): never {
  throw new Error('boom from a render')
}

describe('ErrorBoundary', () => {
  beforeEach(() => {
    // React logs the caught error itself; silence it so a passing test does
    // not look like a failing one.
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => vi.restoreAllMocks())

  it('renders its children when nothing is wrong', () => {
    render(
      <ErrorBoundary>
        <p>the dashboard</p>
      </ErrorBoundary>,
    )
    expect(screen.getByText('the dashboard')).toBeInTheDocument()
  })

  it('shows a way out instead of a blank page', () => {
    render(
      <ErrorBoundary>
        <Explodes />
      </ErrorBoundary>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('這個畫面出錯了')
    expect(screen.getByRole('button', { name: '重新載入' })).toBeInTheDocument()
  })

  it('says the data and the background worker are unaffected', () => {
    // The owner's first fear on a blank page is that something was lost.
    render(
      <ErrorBoundary>
        <Explodes />
      </ErrorBoundary>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('資料')
    expect(screen.getByRole('alert')).toHaveTextContent('通知也還在跑')
  })

  it('keeps the technical message available for a bug report', () => {
    render(
      <ErrorBoundary>
        <Explodes />
      </ErrorBoundary>,
    )
    expect(screen.getByText('boom from a render')).toBeInTheDocument()
  })
})
