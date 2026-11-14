import { ChartColumn, CircleX, Coins, Cpu, Gauge, Timer, Wrench } from 'lucide-react'
import { memo, useMemo, useState } from 'react'

import { compactNumber, duration, latency, percent, tokenRate } from '../lib/format'
import type { ContextSnapshot, RoundStats } from '../lib/protocol'
import { contextFraction, totals } from '../lib/session'
import { ContextRing, Empty, useNow } from './ui'

const CHART_ROUNDS = 40

type Series = { key: string; label: string; value: (round: RoundStats) => number }

// Stacked bottom-up in this order; colors are categorical slots 1-3.
const TOKEN_SERIES: Series[] = [
  { key: 'fresh', label: 'Input', value: (r) => Math.max(0, r.input_tokens - r.cached_tokens) },
  { key: 'cached', label: 'Cached input', value: (r) => Math.min(r.cached_tokens, r.input_tokens) },
  { key: 'output', label: 'Output', value: (r) => r.output_tokens },
]

// Rounds recorded before the split was measured show only "Other".
const TIME_SERIES: Series[] = [
  { key: 'model', label: 'Model', value: (r) => Math.min(r.model_time, r.duration) },
  { key: 'tool', label: 'Tools', value: (r) => Math.min(r.tool_time, Math.max(0, r.duration - r.model_time)) },
  { key: 'other', label: 'Other', value: (r) => Math.max(0, r.duration - r.model_time - r.tool_time) },
]

function niceMax(value: number) {
  if (value <= 0) return 1
  const power = 10 ** Math.floor(Math.log10(value))
  const step = [1, 2, 2.5, 5, 10].find((candidate) => candidate * power >= value) ?? 10
  return step * power
}

type Hover = { round: RoundStats; x: number }

function BarChart({
  rounds,
  series,
  format,
  axisFormat = format,
  onSelect,
  label,
}: {
  rounds: RoundStats[]
  series: Series[]
  format: (value: number) => string
  axisFormat?: (value: number) => string
  onSelect: (round: RoundStats) => void
  label: string
}) {
  const [hover, setHover] = useState<Hover | null>(null)
  const width = 320
  const height = 116
  const top = 8
  const bottom = 18
  const left = 34
  const plot = height - top - bottom
  const valueOf = (round: RoundStats) => series.reduce((sum, item) => sum + item.value(round), 0)
  const max = niceMax(Math.max(0, ...rounds.map(valueOf)))
  const slot = (width - left) / Math.max(rounds.length, 8)
  const bar = Math.max(3, Math.min(18, slot - 2))
  const y = (value: number) => top + plot - (value / max) * plot
  return (
    <div className="chart" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label} preserveAspectRatio="none">
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line className="grid" x1={left} x2={width} y1={y(max * fraction)} y2={y(max * fraction)} />
            <text className="axis" x={left - 6} y={y(max * fraction) + 3} textAnchor="end">{axisFormat(max * fraction)}</text>
          </g>
        ))}
        {rounds.map((round, index) => {
          const x = left + index * slot + (slot - bar) / 2
          let base = 0
          const parts = series.map((item) => ({ key: item.key, value: item.value(round) }))
          const visible = parts.filter((part) => part.value > 0)
          return (
            <g key={round.id} className={hover?.round.id === round.id ? 'hovered' : ''}>
              {visible.map((part, partIndex) => {
                const y0 = y(base)
                base += part.value
                const y1 = y(base)
                // 2px surface gap between stacked segments.
                const gap = partIndex > 0 ? 1 : 0
                const h = Math.max(0, y0 - y1 - gap)
                const isTop = partIndex === visible.length - 1
                return (
                  <path
                    key={part.key}
                    className={`bar s-${part.key} ${round.cancelled ? 'cancelled' : ''}`}
                    d={roundedTop(x, y1, bar, h, isTop ? Math.min(4, bar / 2, h) : 0)}
                    transform={gap ? `translate(0 ${-gap})` : undefined}
                  />
                )
              })}
              {/* Hit target spans the full column, larger than the mark. */}
              <rect
                className="hit"
                x={left + index * slot}
                y={top}
                width={slot}
                height={plot}
                onMouseEnter={() => setHover({ round, x: (left + index * slot + slot / 2) / width })}
                onClick={() => onSelect(round)}
              />
            </g>
          )
        })}
        <line className="baseline" x1={left} x2={width} y1={top + plot} y2={top + plot} />
        {rounds.length ? (
          <>
            <text className="axis" x={left} y={height - 4}>#{rounds[0].index}</text>
            <text className="axis" x={width} y={height - 4} textAnchor="end">#{rounds.at(-1)!.index}</text>
          </>
        ) : null}
      </svg>
      {hover ? (
        <div className="chart-tip glass" style={{ left: `${Math.min(78, Math.max(22, hover.x * 100))}%` }}>
          <div className="tip-title">Round {hover.round.index} · {duration(hover.round.duration)}</div>
          {series.map((item) => (
            <div className="tip-row" key={item.key}>
              <span className={`swatch s-${item.key}`} />
              <span>{item.label}</span>
              <b>{format(item.value(hover.round))}</b>
            </div>
          ))}
          {hover.round.ttft !== null ? <div className="tip-row"><span>TTFT</span><b>{latency(hover.round.ttft)}</b></div> : null}
          {hover.round.tps !== null ? <div className="tip-row"><span>Speed</span><b>{tokenRate(hover.round.tps)}</b></div> : null}
          {hover.round.prompt ? <div className="tip-prompt clamp">{hover.round.prompt}</div> : null}
        </div>
      ) : null}
    </div>
  )
}

function roundedTop(x: number, y: number, w: number, h: number, r: number) {
  if (h <= 0) return ''
  if (r <= 0) return `M${x},${y}h${w}v${h}h${-w}Z`
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`
}

// Memoized: the inspector re-renders on every streamed update, and the round
// table and charts must not re-render with it.
export const StatsPanel = memo(function StatsPanel({
  rounds,
  active,
  context,
  operationSince,
  onJump,
}: {
  rounds: RoundStats[]
  active: boolean
  context: ContextSnapshot | null
  operationSince: number | null
  onJump: (round: RoundStats) => void
}) {
  const sum = useMemo(() => totals(rounds), [rounds])
  const recent = rounds.slice(-CHART_ROUNDS)
  const now = useNow(active)
  const fraction = contextFraction(context)
  const hit = sum.input > 0 ? sum.cached / sum.input : 0
  const avg = sum.rounds ? sum.duration / sum.rounds : 0

  return (
    <div className="stats">
      <div className="tiles">
        <div className="tile">
          <span className="tile-label"><ChartColumn size={13} />Rounds</span>
          <span className="tile-value">{sum.rounds}</span>
          <span className="tile-sub">{active && operationSince ? `running ${duration(now / 1000 - operationSince)}` : `avg ${duration(avg)}`}</span>
        </div>
        <div className="tile">
          <span className="tile-label"><Timer size={13} />Agent time</span>
          <span className="tile-value">{duration(sum.duration)}</span>
          <span className="tile-sub">{sum.edits} edits</span>
        </div>
        <div className="tile">
          <span className="tile-label"><Cpu size={13} />Model time</span>
          <span className="tile-value">{duration(sum.modelTime)}</span>
          <span className="tile-sub">{sum.duration > 0 ? `${percent(Math.min(1, sum.modelTime / sum.duration))} of agent time` : '—'}</span>
        </div>
        <div className="tile">
          <span className="tile-label"><Wrench size={13} />Tool time</span>
          <span className="tile-value">{duration(sum.toolTime)}</span>
          <span className="tile-sub">
            {sum.tools} calls{sum.failures ? <> · <CircleX size={11} className="fail" /> {sum.failures} failed</> : null}
          </span>
        </div>
        <div className="tile">
          <span className="tile-label"><Coins size={13} />Tokens</span>
          <span className="tile-value">{compactNumber(sum.input + sum.output)}</span>
          <span className="tile-sub">↑{compactNumber(sum.input)} ↓{compactNumber(sum.output)} · {percent(hit)} cached</span>
        </div>
        <div className="tile">
          <span className="tile-label"><Gauge size={13} />Speed</span>
          <span className="tile-value">{sum.tps !== null ? tokenRate(sum.tps) : '—'}</span>
          <span className="tile-sub">{sum.ttft !== null ? `TTFT ${latency(sum.ttft)} avg` : 'TTFT —'}</span>
        </div>
      </div>

      {context ? (
        <div className="context-card">
          <ContextRing fraction={fraction} size={34} />
          <div>
            <div className="tile-label">Context window</div>
            <div className="context-line">
              <b>{compactNumber(context.context_tokens)}</b>
              {context.context_limit ? <span className="muted"> / {compactNumber(context.context_limit)} · {percent(fraction ?? 0)}</span> : null}
            </div>
            <div className="tile-sub">Session ↑{compactNumber(context.input_tokens)} ↓{compactNumber(context.output_tokens)}</div>
          </div>
        </div>
      ) : null}

      {rounds.length === 0 ? (
        <Empty icon={ChartColumn}>Statistics appear after the first round.</Empty>
      ) : (
        <>
          <section className="chart-card">
            <header>
              <span>Tokens per round</span>
              <span className="legend">
                {TOKEN_SERIES.map((item) => (
                  <span key={item.key}><i className={`swatch s-${item.key}`} />{item.label}</span>
                ))}
              </span>
            </header>
            <BarChart rounds={recent} series={TOKEN_SERIES} format={compactNumber} onSelect={onJump} label="Tokens per round" />
          </section>
          <section className="chart-card">
            <header>
              <span>Time per round</span>
              <span className="legend">
                {TIME_SERIES.map((item) => (
                  <span key={item.key}><i className={`swatch s-${item.key}`} />{item.label}</span>
                ))}
              </span>
            </header>
            <BarChart
              rounds={recent}
              series={TIME_SERIES}
              format={duration}
              axisFormat={(value) => duration(value).replace(/\.\d+s$/, 's')}
              onSelect={onJump}
              label="Time per round"
            />
          </section>
          <section className="round-table" aria-label="Rounds">
            <div className="rt-head">
              <span>#</span><span>Prompt</span><span>Time</span><span>Tokens</span><span>tok/s</span><span>Tools</span>
            </div>
            {[...rounds].reverse().map((round) => (
              <button type="button" className={`rt-row ${round.cancelled ? 'cancelled' : ''}`} key={round.id} onClick={() => onJump(round)}>
                <span className="muted">{round.index}</span>
                <span className="clamp">{round.prompt || <em className="muted">{round.kind === 'notification' ? 'notification' : 'continued'}</em>}</span>
                <span>{duration(round.duration)}</span>
                <span>{compactNumber(round.input_tokens + round.output_tokens)}</span>
                <span>{round.tps !== null ? Math.round(round.tps) : '—'}</span>
                <span>{round.tool_calls}{round.tool_failures ? <em className="fail">·{round.tool_failures}</em> : null}</span>
              </button>
            ))}
          </section>
        </>
      )}
    </div>
  )
})
