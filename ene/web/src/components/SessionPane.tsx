import { ChevronDown, ChevronUp, Cpu, Folder, PanelLeft, Search, Unlink, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { basename, compactNumber, percent } from '../lib/format'
import type { RoundStats, SessionSummary } from '../lib/protocol'
import { contextFraction } from '../lib/session'
import { useSession } from '../lib/useSession'
import { Composer } from './Composer'
import { FileViewer } from './Files'
import type { InspectorTab } from './Inspector'
import { INSPECTOR_TABS, Inspector } from './Inspector'
import { PromptSheet } from './PromptSheet'
import type { TimelineHandle } from './Timeline'
import { Timeline } from './Timeline'
import { ContextRing, IconButton, LinkBanner, LinkDot } from './ui'

export function SessionPane({
  summary,
  active,
  inspector,
  onInspector,
  onDetach,
  detaching,
  onGone,
  onMenu,
  findOpen,
  onFindOpen,
}: {
  summary: SessionSummary
  active: boolean
  inspector: InspectorTab | null
  onInspector: (tab: InspectorTab | null) => void
  onDetach: () => void
  detaching: boolean
  onGone: () => void
  onMenu: () => void
  findOpen: boolean
  onFindOpen: (open: boolean) => void
}) {
  const session = useSession(summary.id, onGone)
  const { view } = session
  const connected = session.status === 'open'
  const timeline = useRef<TimelineHandle | null>(null)
  const [viewing, setViewing] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [matchIndex, setMatchIndex] = useState(0)
  const [matchCount, setMatchCount] = useState(0)
  const [focusSignal, setFocusSignal] = useState(0)
  const findInput = useRef<HTMLInputElement>(null)
  const busy = view.operationId !== null
  const fraction = contextFraction(view.context)

  useEffect(() => {
    if (active) setFocusSignal((value) => value + 1)
  }, [active])

  useEffect(() => {
    if (active && findOpen) findInput.current?.focus()
    if (!findOpen) setQuery('')
  }, [active, findOpen])

  useEffect(() => setMatchIndex(0), [query])

  // Refresh the file browser whenever a round finishes (the agent may have
  // written files).
  const filesRefresh = view.rounds.length

  const openStats = useCallback(() => onInspector('stats'), [onInspector])
  const jumpRound = useCallback((round: RoundStats) => {
    timeline.current?.scrollToRound(round)
    if (window.matchMedia?.('(max-width: 1100px)').matches) onInspector(null)
  }, [onInspector])

  const mention = useCallback((path: string) => {
    const draft = session.draft
    const token = `@${path} `
    session.setDraft(draft && !/\s$/.test(draft) ? `${draft} ${token}` : `${draft}${token}`)
    setViewing(null)
    setFocusSignal((value) => value + 1)
  }, [session])

  const contextTip = useMemo(() => {
    const context = view.context
    if (!context) return 'Context'
    const limit = context.context_limit ? ` / ${compactNumber(context.context_limit)}` : ''
    return `Context ${compactNumber(context.context_tokens)}${limit}`
  }, [view.context])

  const title = summary.name || summary.title || basename(summary.cwd)
  const model = view.context?.model || summary.model

  return (
    <section className={`pane ${active ? 'active' : ''}`} hidden={!active} aria-label={title}>
      <div className="pane-main">
        <header className="pane-header glass">
          <IconButton icon={PanelLeft} label="Sessions" className="menu-btn" onClick={onMenu} />
          <div className="pane-title">
            <div className="pane-name">
              <span className={`state-dot ${busy ? 'working' : summary.state}`} />
              <span className="clamp">{title}</span>
            </div>
            <div className="pane-chips">
              <button type="button" className="chip" onClick={() => onInspector('files')} data-tip={summary.cwd}>
                <Folder size={12} />{basename(summary.cwd)}
              </button>
              {model ? <span className="chip" data-tip="Model">{view.context?.model_icon ? <span>{view.context.model_icon}</span> : <Cpu size={12} />}{model}</span> : null}
            </div>
          </div>
          <span className="grow" />
          <button type="button" className="context-btn" onClick={openStats} data-tip={contextTip} aria-label={contextTip}>
            <ContextRing fraction={fraction} />
            <span>{fraction !== null ? percent(fraction) : view.context ? compactNumber(view.context.context_tokens) : '—'}</span>
          </button>
          <div className="header-actions">
            <IconButton icon={Search} label="Find in conversation (Ctrl+F)" active={findOpen} onClick={() => onFindOpen(!findOpen)} />
            {INSPECTOR_TABS.slice(0, 3).map((tab) => (
              <IconButton
                key={tab.id}
                icon={tab.icon}
                label={tab.label}
                className="hide-narrow"
                active={inspector === tab.id}
                badge={tab.id === 'tasks' && view.processes.running ? view.processes.running : null}
                onClick={() => onInspector(inspector === tab.id ? null : tab.id)}
              />
            ))}
            <IconButton icon={INSPECTOR_TABS[3].icon} label="Session info" className="show-narrow" active={inspector !== null} onClick={() => onInspector(inspector ? null : 'files')} />
            <IconButton icon={Unlink} label="Detach (keeps running)" busy={detaching} onClick={onDetach} />
            <LinkDot status={session.status} />
          </div>
        </header>

        {findOpen && active ? (
          <div className="find-bar glass">
            <Search size={14} />
            <input
              ref={findInput}
              value={query}
              placeholder="Find in conversation"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  setMatchIndex((value) => value + (event.shiftKey ? -1 : 1))
                } else if (event.key === 'Escape') {
                  event.preventDefault()
                  onFindOpen(false)
                }
              }}
            />
            <span className="muted small">{query ? (matchCount ? `${(((matchIndex % matchCount) + matchCount) % matchCount) + 1}/${matchCount}` : '0') : ''}</span>
            <IconButton icon={ChevronUp} size="sm" label="Previous" disabled={!matchCount} onClick={() => setMatchIndex((value) => value - 1)} />
            <IconButton icon={ChevronDown} size="sm" label="Next" disabled={!matchCount} onClick={() => setMatchIndex((value) => value + 1)} />
            <IconButton icon={X} size="sm" label="Close" onClick={() => onFindOpen(false)} />
          </div>
        ) : null}

        <Timeline
          view={view}
          active={active}
          handle={timeline}
          query={findOpen ? query : ''}
          matchIndex={matchIndex}
          onMatches={setMatchCount}
          onOpenFile={setViewing}
          onOpenStats={openStats}
        />

        <div className="dock">
          {active ? <LinkBanner status={session.status} onRetry={() => session.retry()} /> : null}
          {view.prompt ? (
            <PromptSheet prompt={view.prompt} connected={connected} onAnswer={session.answer} onCancel={session.dismissPrompt} />
          ) : null}
          <Composer
            sessionId={summary.id}
            draft={session.draft}
            onDraft={session.setDraft}
            onSubmit={session.submit}
            onCancel={session.cancel}
            onWithdraw={session.withdraw}
            busy={busy}
            connected={connected}
            commands={view.commands}
            history={view.history}
            pending={view.pending}
            outbox={session.outbox}
            withdrawing={session.withdrawing}
            indicator={view.indicator}
            operationSince={view.operationSince}
            processes={view.processes}
            onOpenTasks={() => onInspector('tasks')}
            focusSignal={focusSignal}
          />
        </div>
      </div>

      {active && inspector ? (
        <>
          <div className="inspector-scrim" onClick={() => onInspector(null)} />
          <Inspector
            tab={inspector}
            onTab={onInspector}
            onClose={() => onInspector(null)}
            sessionId={summary.id}
            summary={summary}
            rounds={view.rounds}
            busy={busy}
            context={view.context}
            operationSince={view.operationSince}
            processes={view.processes}
            info={view.info}
            commands={view.commands}
            connected={connected}
            filesRefresh={filesRefresh}
            onOpenFile={setViewing}
            onJumpRound={jumpRound}
            onCommand={(text) => session.command(text)}
          />
        </>
      ) : null}

      {active && viewing ? (
        <FileViewer sessionId={summary.id} path={viewing} onClose={() => setViewing(null)} onMention={mention} />
      ) : null}
    </section>
  )
}
