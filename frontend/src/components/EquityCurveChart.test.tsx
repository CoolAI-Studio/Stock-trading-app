import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EquityCurveChart } from './EquityCurveChart'
import type { EquityPoint } from '../lib/types'

function point(timestamp: string, equity: string): EquityPoint {
  return { timestamp, close: '100', position_qty: '0', cash: equity, equity }
}

const RISING: EquityPoint[] = [
  point('2026-01-05T00:00:00Z', '100000'),
  point('2026-01-06T00:00:00Z', '100400'),
  point('2026-01-07T00:00:00Z', '100200'),
  point('2026-01-08T00:00:00Z', '101500'),
]

const FALLING: EquityPoint[] = [
  point('2026-01-05T00:00:00Z', '100000'),
  point('2026-01-06T00:00:00Z', '98800'),
  point('2026-01-07T00:00:00Z', '97250'),
]

function vertices(container: HTMLElement): { x: number; y: number }[] {
  const raw = container.querySelector('polyline')?.getAttribute('points')?.trim() ?? ''
  if (!raw) return []
  return raw.split(/\s+/).map((pair) => {
    const [x, y] = pair.split(',')
    return { x: Number(x), y: Number(y) }
  })
}

describe('EquityCurveChart', () => {
  it('plots one vertex per candle', () => {
    const { container } = render(<EquityCurveChart points={RISING} initialCapital="100000" />)

    expect(vertices(container)).toHaveLength(RISING.length)
  })

  it('draws a curve that ended above its starting capital in the gain colour', () => {
    const { container } = render(<EquityCurveChart points={RISING} initialCapital="100000" />)

    expect(container.querySelector('polyline')).toHaveClass('stroke-emerald-400')
  })

  it('draws a curve that ended below its starting capital in the loss colour', () => {
    // Telling those two apart at a glance is the entire job of this chart, so
    // the losing case must not read as merely a lower green line.
    const { container } = render(<EquityCurveChart points={FALLING} initialCapital="100000" />)

    expect(container.querySelector('polyline')).toHaveClass('stroke-red-400')
  })

  it('marks the starting capital so the curve can be read as profit or loss', () => {
    render(<EquityCurveChart points={RISING} initialCapital="100000" />)

    expect(screen.getByText(/起始本金/)).toBeInTheDocument()
  })

  it('keeps the starting capital inside the drawn range even when the curve never reaches it', () => {
    // A curve that spent every candle below its capital would otherwise scale
    // to its own min/max and push the break-even line off the canvas, leaving
    // a rising line that is in fact loss all the way through.
    const { container } = render(<EquityCurveChart points={FALLING} initialCapital="120000" />)

    const baselineY = Number(container.querySelector('line')?.getAttribute('y1'))
    expect(baselineY).toBeGreaterThanOrEqual(0)
    expect(baselineY).toBeLessThanOrEqual(Math.min(...vertices(container).map((v) => v.y)))
  })

  it('names the chart and where it ended for a reader who cannot see it', () => {
    render(<EquityCurveChart points={RISING} initialCapital="100000" />)

    expect(screen.getByRole('img', { name: /權益曲線/ })).toHaveAccessibleName(/101,500/)
  })

  it('says so rather than drawing an invisible line when there is only one candle', () => {
    const { container } = render(
      <EquityCurveChart points={[point('2026-01-05T00:00:00Z', '100000')]} initialCapital="100000" />,
    )

    expect(screen.getByText(/只有 1 根 K 棒/)).toBeInTheDocument()
    expect(container.querySelector('polyline')).toBeNull()
  })

  it('still draws a flat account without dividing by a zero range', () => {
    const flat = [point('2026-01-05T00:00:00Z', '100000'), point('2026-01-06T00:00:00Z', '100000')]
    const { container } = render(<EquityCurveChart points={flat} initialCapital="100000" />)

    expect(vertices(container).every((v) => Number.isFinite(v.x) && Number.isFinite(v.y))).toBe(true)
  })
})
