import { ArrowDown, ArrowUp, History, MessagesSquare } from 'lucide-react'
import type { RefObject } from 'react'
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import type { RoundStats } from '../lib/protocol'
import type { SessionView, TimelineItem } from '../lib/session'
import { groupItems, itemText } from '../lib/timeline'
import {
  ActivityGroup,
  AssistantMessage,
  DiffCard,
  Notice,
  Output,
  RoundChip,
  Thinking,
  ToolCall,
  UserMessage,
} from './TimelineItems'
import { OpenFileContext } from './Markdown'
import { Empty } from './ui'

export type TimelineHandle = {
  scrollToSeq: (seq: number) => void
  /** Jump to where a round began; rounds from saved history match by prompt. */
  scrollToRound: (round: RoundStats) => void
  scrollToBottom: (smooth?: boolean) => void
}

const PIN_DISTANCE = 160
// Jump buttons appear once the view is this far from the respective edge.
const EDGE_DISTANCE = 24

// Memoized: parent re-renders (session list updates, composer typing) must
// not walk the whole transcript.
export const Timeline = memo(function Timeline({
  view,
  active,
  handle,
  query,
  matchIndex,
  onMatches,
  onOpenFile,
  onOpenStats,
}: {
  view: SessionView
  active: boolean
  handle: RefObject<TimelineHandle | null>
  query: string
  matchIndex: number
  onMatches: (count: number) => void
  onOpenFile: (path: string) => void
  onOpenStats: () => void
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)
  const savedTop = useRef<number | null>(null)
  const [canUp, setCanUp] = useState(false)
  const [canDown, setCanDown] = useState(false)
  const live = view.operationId !== null
  const nodes = useMemo(() => groupItems(view.items, live), [view.items, live])
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return [] as string[]
    return nodes.flatMap((node) => {
      const items = node.kind === 'item' ? [node.item] : node.items
      const hit = items.some((item) => itemText(item).toLowerCase().includes(needle))
      return hit ? [node.kind === 'item' ? node.item.key : node.key] : []
    })
  }, [nodes, query])
  const highlightKeys = useMemo(() => new Set(matches), [matches])
  const currentKey = matches.length ? matches[((matchIndex % matches.length) + matches.length) % matches.length] : null
  useEffect(() => onMatches(matches.length), [matches.length, onMatches])

  // Show each jump button only while the view is away from that edge.
  const measure = useCallback(() => {
    const element = scroller.current
    if (!element) return
    const distance = element.scrollHeight - element.clientHeight - element.scrollTop
    setCanUp(element.scrollTop > EDGE_DISTANCE)
    setCanDown(distance > EDGE_DISTANCE)
  }, [])

  const toTop = useCallback(() => {
    const element = scroller.current
    if (!element) return
    pinned.current = false
    element.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  const toBottom = useCallback((smooth = false) => {
    const element = scroller.current
    if (!element) return
    pinned.current = true
    element.scrollTo({ top: element.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }, [])

  const reveal = useCallback((target: HTMLElement | null) => {
    if (target) {
      pinned.current = false
      target.scrollIntoView({ block: 'start', behavior: 'smooth' })
      target.classList.add('flash')
      window.setTimeout(() => target?.classList.remove('flash'), 1400)
    }
  }, [])

  const scrollToSeq = useCallback((seq: number) => {
    const candidates = scroller.current?.querySelectorAll<HTMLElement>('[data-seq]') ?? []
    reveal([...candidates].find((candidate) => Number(candidate.dataset.seq) >= seq) ?? null)
  }, [reveal])

  const items = view.items
  const scrollToRound = useCallback((round: RoundStats) => {
    if (round.start_seq) {
      scrollToSeq(round.start_seq)
      return
    }
    const prompt = round.prompt.slice(0, 80)
    if (!prompt) return
    const match = [...items].reverse().find(
      (item) => item.kind === 'user' && item.text.split(/\s+/).join(' ').startsWith(prompt),
    )
    if (match) {
      reveal(scroller.current?.querySelector<HTMLElement>(`[data-key="${CSS.escape(match.key)}"]`) ?? null)
    }
  }, [items, reveal, scrollToSeq])

  useEffect(() => {
    handle.current = { scrollToSeq, scrollToRound, scrollToBottom: toBottom }
  }, [handle, scrollToSeq, scrollToRound, toBottom])

  // Follow new output while pinned to the tail.
  useLayoutEffect(() => {
    if (active && pinned.current) toBottom()
    measure()
  }, [view.items, view.indicator, active, toBottom, measure])

  // Hidden panes keep their position; restore it when shown again.
  useLayoutEffect(() => {
    const element = scroller.current
    if (!element) return
    if (active) {
      if (savedTop.current !== null && !pinned.current) element.scrollTop = savedTop.current
      else toBottom()
    }
    return () => {
      savedTop.current = element.scrollTop
    }
  }, [active, toBottom])

  // Keep the bottom in view when the composer grows or the viewport shrinks
  // (mobile keyboards); content resizes (expanding cards) can change which
  // jump buttons apply without any scroll event.
  useEffect(() => {
    const element = scroller.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (pinned.current) element.scrollTop = element.scrollHeight
      measure()
    })
    observer.observe(element)
    if (element.firstElementChild) observer.observe(element.firstElementChild)
    return () => observer.disconnect()
  }, [measure])

  const onScroll = useCallback(() => {
    const element = scroller.current
    if (!element) return
    const distance = element.scrollHeight - element.clientHeight - element.scrollTop
    pinned.current = distance < PIN_DISTANCE
    measure()
  }, [measure])

  useEffect(() => {
    if (!currentKey) return
    const element = scroller.current?.querySelector(`[data-key="${CSS.escape(currentKey)}"]`)
    if (element) {
      pinned.current = false
      element.scrollIntoView({ block: 'center' })
    }
  }, [currentKey])

  const renderItem = useCallback((item: TimelineItem, isLive: boolean, round?: RoundStats) => {
    switch (item.kind) {
      case 'user': return <UserMessage item={item} />
      case 'assistant': return <AssistantMessage item={item} round={round} onOpenStats={onOpenStats} />
      case 'thinking': return <Thinking item={item} />
      case 'tool': return <ToolCall item={item} live={isLive} onOpenFile={onOpenFile} />
      case 'diff': return <DiffCard diff={item} onOpenFile={onOpenFile} />
      case 'output': return <Output item={item} />
      case 'notice': return <Notice item={item} />
      case 'round': return <div className="reply-foot"><RoundChip round={item.round} onOpen={onOpenStats} /></div>
    }
  }, [onOpenFile, onOpenStats])

  const mark = (key: string) =>
    `${highlightKeys.has(key) ? 'hit' : ''} ${currentKey === key ? 'current' : ''}`

  return (
    <OpenFileContext.Provider value={onOpenFile}>
      <div className="timeline-scroller" ref={scroller} onScroll={onScroll}>
        <div className="timeline" aria-live="polite">
          {view.truncatedAt !== null ? (
            <div className="notice debug"><History size={14} /><span>Earlier history is no longer available in this view.</span></div>
          ) : null}
          {nodes.length === 0 ? (
            <Empty icon={MessagesSquare}>Send a message to start.</Empty>
          ) : null}
          {nodes.map((node) => node.kind === 'item' ? (
            <div className={`t-item k-${node.item.kind} ${mark(node.item.key)}`} key={node.item.key} data-key={node.item.key} data-seq={node.item.seq || undefined}>
              {renderItem(node.item, live, node.round?.round)}
            </div>
          ) : (
            <div
              className={`t-item k-group ${mark(node.key)}`}
              key={node.key}
              data-key={node.key}
              data-seq={node.items[0].seq || undefined}
            >
              <ActivityGroup items={node.items} live={node.live} render={renderItem} />
            </div>
          ))}
        </div>
        {canUp || canDown ? (
          <div className="jump-stack">
            {canUp ? (
              <button type="button" className="jump-btn glass" onClick={toTop} aria-label="Jump to top">
                <ArrowUp size={16} />
              </button>
            ) : null}
            {canDown ? (
              <button type="button" className="jump-btn glass" onClick={() => toBottom(true)} aria-label="Jump to latest">
                <ArrowDown size={16} />
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </OpenFileContext.Provider>
  )
})
