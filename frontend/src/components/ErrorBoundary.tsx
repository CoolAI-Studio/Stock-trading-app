import { Component, type ErrorInfo, type ReactNode } from 'react'

/** Catches a render that threw, so the page is not simply blank.
 *
 * React unmounts the whole tree when a render throws and nothing catches it.
 * On this app that meant a black page with no navigation, no message and no
 * way back -- the owner's only option was to guess at refreshing, and they
 * would have no idea whether their data was gone.
 *
 * Deliberately a class: this is the one thing hooks cannot do.
 */
export class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Kept, because the message on screen is deliberately non-technical and
    // this is the only other record of what actually happened.
    console.error('unhandled render error', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <div role="alert" className="mx-auto max-w-lg space-y-4 p-8 text-slate-200">
        <h1 className="text-lg font-semibold">這個畫面出錯了</h1>
        <p className="text-sm text-slate-400">
          畫面壞掉不代表資料有問題——你的策略、部位和訂單都還在，背景的盯盤和通知也還在跑。
          重新整理通常就會恢復。
        </p>
        <button
          onClick={() => window.location.reload()}
          className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-500"
        >
          重新載入
        </button>
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">技術細節（回報問題時附上這段）</summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap">{this.state.error.message}</pre>
        </details>
      </div>
    )
  }
}
