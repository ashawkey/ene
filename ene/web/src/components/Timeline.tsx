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
import { Empty, Spinner } from './ui'

export type TimelineHandle = {
  scrollToSeq: (seq: number) => void
  /** Jump to where a round began; rounds from saved history match by prompt. */
  scrollToRound: (round: RoundStats) => void
  scrollToBottom: (smooth?: boolean) => void
}

// Following the tail is decided by the direction of travel, not by a
// tolerance band: a tolerance re-pins every small upward step (trackpads,
// smooth wheels, scrollbar drags), and the next streamed update or resize
// yanks the view back down, so the user can never leave the bottom.
// Any upward move off the bottom releases the tail; reaching it re-pins.
const PIN_DISTANCE = 24

/**
 * Where a session's transcript was left: following the tail, or the item at
 * the top of the view and its offset. Kept per session for the page's
 * lifetime, so switching sessions (or re-attaching one) returns to the same
 * spot. The item anchor survives history trimmed above it; `top` is the
 * fallback when that item is gone.
 */
type ScrollMemory = { pinned: boolean; key: string | null; offset: number; top: number }
const scrollMemory = new Map<string, ScrollMemory>()
// Positions are recorded once scrolling pauses, after lazily rendered items
// (content-visibility) have their real heights.
const SAVE_DELAY = 150
// Frames spent re-aligning a restored anchor while item heights settle.
const SETTLE_FRAMES = 12

/** The first `.t-item` whose bottom is below the scroller's top edge (binary search). */
function topItem(element: HTMLElement): { key: string | null; offset: number } {
  const items = element.querySelector('.timeline')?.children
  if (!items?.length) return { key: null, offset: 0 }
  const edge = element.getBoundingClientRect().top
  let low = 0
  let high = items.length - 1
  while (low < high) {
    const middle = (low + high) >> 1
    if (items[middle].getBoundingClientRect().bottom <= edge) low = middle + 1
    else high = middle
  }
  const item = items[low] as HTMLElement
  return { key: item.dataset.key ?? null, offset: item.getBoundingClientRect().top - edge }
}
// Jump buttons appear once the view is this far from the respective edge.
const EDGE_DISTANCE = 24
// Older history is requested once the top is this close to the view.
const PRELOAD_DISTANCE = 800

/** The top item if it is a transcript item, else the first one below it. */
function keyedTopItem(element: HTMLElement): { key: string; offset: number } | null {
  const top = topItem(element)
  if (top.key) return { key: top.key, offset: top.offset }
  const first = element.querySelector<HTMLElement>('.timeline > [data-key]')
  if (!first?.dataset.key) return null
  return { key: first.dataset.key, offset: first.getBoundingClientRect().top - element.getBoundingClientRect().top }
}

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
  onLoadOlder,
  memoryKey,
}: {
  view: SessionView
  active: boolean
  /** Remember the scroll position under this key (the session ID). */
  memoryKey?: string
  handle: RefObject<TimelineHandle | null>
  query: string
  matchIndex: number
  onMatches: (count: number) => void
  onOpenFile: (path: string) => void
  onOpenStats: () => void
  /** Request the page of history before the oldest loaded item. */
  onLoadOlder?: () => void
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const memory = useRef<ScrollMemory>(
    (memoryKey ? scrollMemory.get(memoryKey) : undefined) ?? { pinned: true, key: null, offset: 0, top: 0 },
  )
  // Mirrors memory.pinned; kept as its own ref because most code reads only it.
  const pinned = useRef(memory.current.pinned)
  // Last scrollTop seen or set here; an upward move from it is the user's.
  const lastTop = useRef(0)
  // Set while a remembered position still has to be applied (the anchor may
  // not have been rendered yet, e.g. right after re-attaching).
  const restorePending = useRef(false)
  const saveTimer = useRef<number | null>(null)
  const settleFrame = useRef<number | null>(null)
  const stopSettling = useCallback(() => {
    if (settleFrame.current !== null) cancelAnimationFrame(settleFrame.current)
    settleFrame.current = null
  }, [])
  // While an older page loads, the item the reader is looking at; prepending
  // the page re-aligns it so the view does not jump.
  const olderAnchor = useRef<{ key: string; offset: number } | null>(null)
  const firstKey = useRef<string | null>(null)
  const sentinel = useRef<HTMLDivElement>(null)
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
    stopSettling()
    pinned.current = false
    element.scrollTo({ top: 0, behavior: 'smooth' })
  }, [stopSettling])

  const toBottom = useCallback((smooth = false) => {
    const element = scroller.current
    if (!element) return
    stopSettling()
    pinned.current = true
    element.scrollTo({ top: element.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
    // Not for smooth scrolls, which only start moving here.
    if (!smooth) lastTop.current = element.scrollTop
  }, [stopSettling])

  const reveal = useCallback((target: HTMLElement | null) => {
    if (target) {
      stopSettling()
      pinned.current = false
      target.scrollIntoView({ block: 'start', behavior: 'smooth' })
      target.classList.add('flash')
      window.setTimeout(() => target?.classList.remove('flash'), 1400)
    }
  }, [stopSettling])

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

  /** Record the current position; skipped while hidden (display: none reads as 0). */
  const save = useCallback(() => {
    const element = scroller.current
    if (!element || !element.clientHeight) return
    const anchor = pinned.current ? { key: null, offset: 0 } : topItem(element)
    memory.current = { pinned: pinned.current, ...anchor, top: element.scrollTop }
    if (memoryKey) scrollMemory.set(memoryKey, memory.current)
  }, [memoryKey])

  /** Scroll the remembered anchor back into place; false when it is not rendered. */
  const align = useCallback((): boolean => {
    const element = scroller.current
    const saved = memory.current
    const anchor = element && saved.key
      ? element.querySelector<HTMLElement>(`[data-key="${CSS.escape(saved.key)}"]`)
      : null
    if (!element || !anchor) return false
    const delta = anchor.getBoundingClientRect().top - element.getBoundingClientRect().top - saved.offset
    if (Math.abs(delta) >= 1) element.scrollTop += delta
    return true
  }, [])

  /** Apply the remembered position; false while its anchor is not rendered yet. */
  const restore = useCallback((): boolean => {
    const element = scroller.current
    if (!element) return true
    const saved = memory.current
    pinned.current = saved.pinned
    stopSettling()
    if (saved.pinned) {
      toBottom()
      return true
    }
    if (!align()) {
      element.scrollTop = saved.top
      return false
    }
    // Items near the anchor render at their real height over the next
    // frames and push it around; hold it in place until the user takes over.
    let frames = SETTLE_FRAMES
    const tick = () => {
      align()
      settleFrame.current = --frames > 0 ? requestAnimationFrame(tick) : null
    }
    settleFrame.current = requestAnimationFrame(tick)
    return true
  }, [toBottom, align, stopSettling])

  // Follow new output while pinned to the tail.
  useLayoutEffect(() => {
    const first = view.items[0]?.key ?? null
    const previousFirst = firstKey.current
    firstKey.current = first
    const prepended = previousFirst !== null && first !== previousFirst &&
      view.items.some((item) => item.key === previousFirst)
    const anchor = olderAnchor.current
    if (prepended) olderAnchor.current = null
    if (active && prepended && anchor && !pinned.current) {
      memory.current = { pinned: false, ...anchor, top: scroller.current?.scrollTop ?? 0 }
      restore()
    } else if (active && restorePending.current && view.items.length) {
      // One more try now that the transcript has loaded; then settle.
      restore()
      restorePending.current = false
    } else if (active && pinned.current) toBottom()
    measure()
  }, [view.items, view.indicator, active, toBottom, measure, restore])

  // Hidden panes keep their position; restore it when shown again.
  useLayoutEffect(() => {
    if (active) restorePending.current = !restore()
  }, [active, restore])

  useEffect(() => () => {
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current)
    stopSettling()
  }, [stopSettling])

  // Load older history as the top comes near. Re-observing after each page
  // reports the sentinel again, so a short page that leaves it in view keeps
  // loading until the view is filled.
  const historyBefore = view.historyBefore
  // Where full detail begins: above it, only the conversation was retained.
  // Marked once the condensed part is loaded, or when nothing is older.
  const detailFrom = view.detailFrom
  const condensedAt = useMemo(() => {
    if (detailFrom === null || !nodes.length) return -1
    const index = nodes.findIndex((node) =>
      (node.kind === 'item' ? node.item.seq : node.items[0].seq) >= detailFrom)
    if (index < 0) return nodes.length
    return index > 0 || historyBefore === null ? index : -1
  }, [nodes, detailFrom, historyBefore])
  useEffect(() => {
    const element = scroller.current
    const target = sentinel.current
    if (!active || !onLoadOlder || historyBefore === null || !element || !target) return
    if (typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      olderAnchor.current = pinned.current ? null : keyedTopItem(element)
      onLoadOlder()
    }, { root: element, rootMargin: `${PRELOAD_DISTANCE}px 0px 0px 0px` })
    observer.observe(target)
    return () => observer.disconnect()
  }, [active, onLoadOlder, historyBefore])

  // Keep the bottom in view when the composer grows or the viewport shrinks
  // (mobile keyboards); content resizes (expanding cards) can change which
  // jump buttons apply without any scroll event.
  useEffect(() => {
    const element = scroller.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      if (pinned.current) {
        element.scrollTop = element.scrollHeight
        lastTop.current = element.scrollTop
      }
      measure()
    })
    observer.observe(element)
    if (element.firstElementChild) observer.observe(element.firstElementChild)
    return () => observer.disconnect()
  }, [measure])

  const onScroll = useCallback(() => {
    const element = scroller.current
    if (!element) return
    // A hidden pane has no layout; its scroll events carry no position.
    if (!element.clientHeight) return
    const top = element.scrollTop
    const distance = element.scrollHeight - element.clientHeight - top
    // Content shrinking under a pinned view clamps scrollTop upward too, but
    // it stays at the bottom (distance ~0), so it does not count as leaving.
    if (top < lastTop.current - 0.5 && distance > 1) pinned.current = false
    else if (distance <= PIN_DISTANCE) pinned.current = true
    lastTop.current = top
    // Track what the reader looks at until the requested page arrives.
    if (olderAnchor.current) olderAnchor.current = pinned.current ? null : keyedTopItem(element)
    measure()
    // A restore still settling is not a new position.
    if (settleFrame.current !== null) return
    // Keep the raw offset current right away (a quick switch may hide the
    // pane before the anchor is measured); the anchor refines it below.
    memory.current = { pinned: pinned.current, key: null, offset: 0, top: element.scrollTop }
    if (memoryKey) scrollMemory.set(memoryKey, memory.current)
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = null
      save()
    }, SAVE_DELAY)
  }, [measure, save, memoryKey])

  useEffect(() => {
    if (!currentKey) return
    const element = scroller.current?.querySelector(`[data-key="${CSS.escape(currentKey)}"]`)
    if (element) {
      stopSettling()
      pinned.current = false
      element.scrollIntoView({ block: 'center' })
    }
  }, [currentKey, stopSettling])

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
      <div
        className="timeline-scroller"
        ref={scroller}
        onScroll={onScroll}
        // Any user input ends a settling restore.
        onWheel={stopSettling}
        onTouchStart={stopSettling}
        onPointerDown={stopSettling}
        onKeyDown={stopSettling}
      >
        <div className="timeline" aria-live="polite">
          {historyBefore !== null && onLoadOlder ? (
            <div className="notice debug" ref={sentinel}><Spinner size={14} /><span>Loading earlier history…</span></div>
          ) : null}
          {nodes.length === 0 && historyBefore === null ? (
            <Empty icon={MessagesSquare}>Send a message to start.</Empty>
          ) : null}
          {nodes.flatMap((node, index) => [...(index === condensedAt ? [(
            <div className="notice debug" key="condensed">
              <History size={14} /><span>Earlier tool activity is no longer retained; only the conversation is shown above.</span>
            </div>
          )] : []), node.kind === 'item' ? (
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
          )])}
          {condensedAt === nodes.length ? (
            <div className="notice debug"><History size={14} /><span>Earlier tool activity is no longer retained.</span></div>
          ) : null}
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
