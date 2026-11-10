import { Activity, Bot, ChartColumn, CircleCheck, CircleX, Eye, FolderOpen, Info, MessagesSquare, Square, X } from 'lucide-react'
import { useState } from 'react'

import { errorMessage, stopProcess } from '../lib/api'
import { duration } from '../lib/format'
import { store } from '../lib/storage'
import type { ContextSnapshot, RoundStats, SessionSummary } from '../lib/protocol'
import type { LiveProcesses } from '../lib/session'
import { toast } from '../lib/toast'
import { CopyButton } from './Markdown'
import { FileBrowser } from './Files'
import { StatsPanel } from './Stats'
import { Empty, IconButton, useNow } from './ui'

export type InspectorTab = 'files' | 'stats' | 'tasks' | 'info'

const LAST_TAB = 'ene.inspector.last'

/** The tab the panel toggle reopens: whichever was used last. */
export function lastInspectorTab(): InspectorTab {
  const value = store.get(LAST_TAB)
  return value === 'stats' || value === 'tasks' || value === 'info' ? value : 'files'
}

export function rememberInspectorTab(tab: InspectorTab) {
  store.set(LAST_TAB, tab)
}

export const INSPECTOR_TABS: Array<{ id: InspectorTab; label: string; icon: typeof FolderOpen }> = [
  { id: 'files', label: 'Files', icon: FolderOpen },
  { id: 'stats', label: 'Statistics', icon: ChartColumn },
  { id: 'tasks', label: 'Background tasks', icon: Activity },
  { id: 'info', label: 'Session info', icon: Info },
]

function TasksPanel({
  sessionId,
  processes,
  connected,
  onOpenProcess,
}: {
  sessionId: string
  processes: LiveProcesses
  connected: boolean
  onOpenProcess: (processId: string) => void
}) {
  const now = useNow(processes.running > 0)
  const [armed, setArmed] = useState<string | null>(null)
  const drift = Math.max(0, (now - processes.receivedAt) / 1000)
  const stop = (processId: string) => {
    setArmed(null)
    stopProcess(sessionId, processId).catch((exc) => toast(errorMessage(exc, 'Could not stop the process.')))
  }
  return (
    <div className="tasks">
      <div className="panel-toolbar">
        <span className="muted small">{processes.running} running · {processes.finished} finished</span>
      </div>
      {processes.processes.length === 0 ? <Empty icon={Activity}>No background processes.</Empty> : null}
      {processes.processes.map((process) => {
        const running = process.status !== 'exited'
        const failed = !running && typeof process.exit_code === 'number' && process.exit_code !== 0
        const elapsed = process.elapsed_seconds + (running ? drift : 0)
        return (
          <div className={`task glass-soft ${running ? '' : 'exited'}`} key={process.process_id}>
            <div className="task-head">
              {running ? (
                <span className={`proc-dot ${process.watching ? 'watching' : ''}`} data-tip={process.watching ? 'Wakes the agent on output' : 'Runs in background'} />
              ) : failed ? (
                <CircleX size={13} className="fail" data-tip={`Exited with code ${process.exit_code}`} />
              ) : (
                <CircleCheck size={13} className="ok" data-tip={`Exited with code ${process.exit_code ?? '?'}`} />
              )}
              {process.kind === 'agent' ? <Bot size={14} className="task-kind" data-tip="Subagent" /> : null}
              <button type="button" className="task-label clamp" onClick={() => onOpenProcess(process.process_id)}>
                {process.label}
              </button>
              <span className="muted small">{duration(elapsed)}</span>
            </div>
            {process.last_line ? <pre className="task-line">{process.last_line}</pre> : null}
            <div className="task-actions">
              <span className="mono muted small">#{process.process_id}{running ? '' : ` · exit ${process.exit_code ?? '?'}`}</span>
              <span className="grow" />
              <IconButton
                icon={process.kind === 'agent' ? MessagesSquare : Eye}
                size="sm"
                label={process.kind === 'agent' ? 'Open conversation' : 'Show full log'}
                onClick={() => onOpenProcess(process.process_id)}
              />
              {running ? (
                <IconButton
                  icon={Square}
                  size="sm"
                  tone="danger"
                  active={armed === process.process_id}
                  label={armed === process.process_id ? 'Click again to stop' : 'Stop'}
                  disabled={!connected}
                  onClick={() => {
                    if (armed === process.process_id) {
                      stop(process.process_id)
                    } else {
                      setArmed(process.process_id)
                      window.setTimeout(() => setArmed((current) => (current === process.process_id ? null : current)), 3000)
                    }
                  }}
                />
              ) : null}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function InfoPanel({ summary, info }: { summary: SessionSummary; info: Record<string, string> }) {
  const rows: Array<[string, string]> = [
    ['Name', summary.name || '—'],
    ['Workspace', summary.cwd],
    ...Object.entries(info).filter(([key]) => key !== 'workspace').map(([key, value]) => [key[0].toUpperCase() + key.slice(1), value] as [string, string]),
    ['Session', summary.id],
    ['Conversation', summary.conversation_id || '—'],
  ]
  return (
    <div className="info">
      <dl>
        {rows.map(([key, value]) => (
          <div className="info-row" key={key}>
            <dt>{key}</dt>
            <dd><span className="clamp-2">{value}</span>{value.length > 12 ? <CopyButton text={value} label={`Copy ${key.toLowerCase()}`} /> : null}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export function Inspector({
  tab,
  onTab,
  onClose,
  sessionId,
  summary,
  rounds,
  busy,
  context,
  operationSince,
  processes,
  info,
  connected,
  filesRefresh,
  onOpenFile,
  onMention,
  onJumpRound,
  onOpenProcess,
}: {
  tab: InspectorTab
  onTab: (tab: InspectorTab) => void
  onClose: () => void
  sessionId: string
  summary: SessionSummary
  rounds: RoundStats[]
  busy: boolean
  context: ContextSnapshot | null
  operationSince: number | null
  processes: LiveProcesses
  info: Record<string, string>
  connected: boolean
  filesRefresh: number
  onOpenFile: (path: string, changed?: boolean, line?: number) => void
  /** Insert ``@path`` into the composer. */
  onMention: (path: string) => void
  onJumpRound: (round: RoundStats) => void
  onOpenProcess: (processId: string) => void
}) {
  const current = INSPECTOR_TABS.find((item) => item.id === tab)!
  return (
    <aside className="inspector glass" aria-label={current.label}>
      <div className="inspector-tabs" role="tablist">
        {INSPECTOR_TABS.map((item) => (
          <IconButton
            key={item.id}
            icon={item.icon}
            label={item.label}
            role="tab"
            aria-selected={tab === item.id}
            active={tab === item.id}
            badge={item.id === 'tasks' && processes.running ? processes.running : null}
            onClick={() => onTab(item.id)}
          />
        ))}
        <span className="grow" />
        <span className="inspector-title">{current.label}</span>
        <IconButton icon={X} label="Close panel" size="sm" onClick={onClose} />
      </div>
      <div className="inspector-body">
        {tab === 'files' ? <FileBrowser sessionId={sessionId} onOpen={onOpenFile} onMention={onMention} refreshKey={filesRefresh} /> : null}
        {tab === 'stats' ? (
          <StatsPanel rounds={rounds} active={busy} context={context} operationSince={operationSince} onJump={onJumpRound} />
        ) : null}
        {tab === 'tasks' ? <TasksPanel sessionId={sessionId} processes={processes} connected={connected} onOpenProcess={onOpenProcess} /> : null}
        {tab === 'info' ? <InfoPanel summary={summary} info={info} /> : null}
      </div>
    </aside>
  )
}
