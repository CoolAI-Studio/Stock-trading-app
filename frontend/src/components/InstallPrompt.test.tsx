import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { InstallPrompt } from './InstallPrompt'
import * as platform from '../lib/platform'

vi.mock('../lib/platform', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/platform')>()),
  currentPushAvailability: vi.fn(),
}))

/**
 * Telling an iPhone owner the one thing that has to happen first.
 *
 * On iOS, Web Push works only for a site added to the Home Screen -- Apple's
 * rule, not this app's. That makes installing a PRECONDITION for the entire
 * product, not a nicety: without it there is no push, and push is what an
 * alerting app is for.
 *
 * It was explained in exactly one place: the notification-channel form, behind
 * 通知 → 新增管道 → 瀏覽器推播. Three interactions deep, on a radio button that
 * is not selected by default. Somebody who never went looking never found out,
 * and had no reason to go looking, because nothing anywhere said their phone
 * needed anything.
 *
 * So it moved to where it cannot be missed. Dismissible, because a banner that
 * cannot be silenced gets ignored -- but only for the session, because the
 * app still does not do its job until this is done.
 */

function availability(kind: platform.PushAvailability['kind']) {
  vi.mocked(platform.currentPushAvailability).mockReturnValue(
    kind === 'ready' ? { kind: 'ready' } : { kind, message: 'stub message' },
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
})

afterEach(() => sessionStorage.clear())

describe('主畫面安裝提示', () => {
  it('在 iPhone 的 Safari 裡（還沒安裝）就要出現', () => {
    availability('needs-install')
    render(<InstallPrompt />)

    expect(screen.getByRole('status')).toHaveTextContent(/主畫面/)
  })

  it('已經從主畫面開啟時完全不出現', () => {
    availability('ready')
    render(<InstallPrompt />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('根本不支援推播的瀏覽器不要叫人去裝，那沒有用', () => {
    // Sending somebody to look for a Home Screen they do not have wastes their
    // time and makes the app look broken.
    availability('unsupported')
    render(<InstallPrompt />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('說明是展開的，不是再藏一層', () => {
    availability('needs-install')
    render(<InstallPrompt />)

    fireEvent.click(screen.getByRole('button', { name: '怎麼做？' }))

    // The banner headline already says 加入主畫面, so match the step itself.
    expect(screen.getByText(/分享/)).toBeInTheDocument()
    expect(screen.getByText(/往下捲/)).toBeInTheDocument()
  })

  it('可以關掉，關掉之後這次就不再出現', () => {
    availability('needs-install')
    const { rerender } = render(<InstallPrompt />)

    fireEvent.click(screen.getByRole('button', { name: '關閉' }))
    rerender(<InstallPrompt />)

    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('關掉只記到這次瀏覽階段為止 —— 沒裝好，這個 app 就還沒在做它的事', () => {
    availability('needs-install')
    render(<InstallPrompt />)
    fireEvent.click(screen.getByRole('button', { name: '關閉' }))

    // A new session (a fresh sessionStorage) brings it back. localStorage
    // would make one stray tap permanent, and the owner would never learn why
    // their phone stays silent.
    sessionStorage.clear()
    render(<InstallPrompt />)

    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('說清楚這是 Apple 的限制，不是這個 app 壞了', () => {
    availability('needs-install')
    render(<InstallPrompt />)

    fireEvent.click(screen.getByRole('button', { name: '怎麼做？' }))

    expect(screen.getByText(/Apple/)).toBeInTheDocument()
  })
})

describe('從 LINE 之類的內建瀏覽器打開', () => {
  it('也要出現橫幅 —— 這正是最需要引導的情況', () => {
    availability('in-app-browser')
    render(<InstallPrompt />)

    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('叫人先用 Safari 開，不要給主畫面步驟', () => {
    availability('in-app-browser')
    render(<InstallPrompt />)

    expect(screen.getByRole('status')).toHaveTextContent(/Safari/)
    expect(screen.queryByRole('button', { name: '怎麼做？' })).not.toBeInTheDocument()
  })
})
