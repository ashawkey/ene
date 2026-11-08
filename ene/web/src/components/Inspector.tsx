import { Activity, ChartColumn, Eye, FolderOpen, Info, ListTree, Square, X } from 'lucide-react'
import { useState } from 'react'

import { duration } from '../lib/format'
import type { ContextSnapshot, RoundStats, SessionSummary } from '../lib/protocol'
import type { LiveProcesses } from '../lib/session'
import { CopyButton } from './Markdown'
import { FileBrowser } from './Files'
import { StatsPanel } from './Stats'
import { Empty, IconButton, useNow } from './ui'

export type InspectorTab = 'files' | 'stats' | 'tasks' | 'info'

export const INSPECTOR_TABS: Array<{ id: InspectorTab; label: string; icon: typeof FolderOpen }> = [
  { id: 'files', label: 'Files', icon: FolderOpen },
  { id: 'stats', label: 'Statistics', icon: ChartColumn },
  { id: 'tasks', label: 'Background tasks', icon: Activity },
  { id: 'info', label: 'Session info', icon: Info },
]

function TasksPanel({
  processes,
  connected,
  onCommand,
}: {
  processes: LiveProcesses
  connected: boolean
  onCommand: (text: string) => void
}) {
  const now = useNow(processes.processes.length > 0)
  const [armed, setArmed] = useState<string | null>(null)
  const drift = (now - processes.receivedAt) / 1000
  return (
    <div className="tasks">
      <div className="panel-toolbar">
        <span className="muted small">{processes.running} running · {processes.finished} finished</span>
        <span className="grow" />
        <IconButton icon={ListTree} size="sm" label="List all (/ps)" disabled={!connected} onClick={() => onCommand('/ps')} />
      </div>
      {processes.processes.length === 0 ? <Empty icon={Activity}>No background processes.</Empty> : null}
      {processes.processes.map((process) => (
        <div className="task glass-soft" key={process.process_id}>
          <div className="task-head">
            <span className={`proc-dot ${process.watching ? 'watching' : ''}`} data-tip={process.watching ? 'Wakes the agent on output' : 'Runs in background'} />
            <span className="task-label clamp">{process.label}</span>
            <span className="muted small">{duration(process.elapsed_seconds + Math.max(0, drift))}</span>
          </div>
          {process.last_line ? <pre className="task-line">{process.last_line}</pre> : null}
          <div className="task-actions">
            <span className="mono muted small">#{process.process_id}</span>
            <span className="grow" />
            <IconButton icon={Eye} size="sm" label="Show log" disabled={!connected} onClick={() => onCommand(`/ps ${process.process_id}`)} />
            <IconButton
              icon={Square}
              size="sm"
              tone="danger"
              active={armed === process.process_id}
              label={armed === process.process_id ? 'Click again to stop' : 'Stop'}
              disabled={!connected}
              onClick={() => {
                if (armed === process.process_id) {
                  setArmed(null)
                  onCommand(`/ps stop ${process.process_id}`)
                } else {
                  setArmed(process.process_id)
                  window.setTimeout(() => setArmed((current) => (current === process.process_id ? null : current)), 3000)
                }
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

function InfoPanel({ summary, info, commands }: { summary: SessionSummary; info: Record<string, string>; commands: Record<string, string> }) {
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
      <div className="list-caption">Commands</div>
      <div className="command-list">
        {Object.entries(commands).sort(([a], [b]) => a.localeCompare(b)).map(([name, description]) => (
          <div className="command-row" key={name}>
            <code>/{name}</code>
            <span className="muted">{description}</span>
          </div>
        ))}
      </div>
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
  commands,
  connected,
  filesRefresh,
  onOpenFile,
  onJumpRound,
  onCommand,
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
  commands: Record<string, string>
  connected: boolean
  filesRefresh: number
  onOpenFile: (path: string) => void
  onJumpRound: (round: RoundStats) => void
  onCommand: (text: string) => void
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
        {tab === 'files' ? <FileBrowser sessionId={sessionId} onOpen={onOpenFile} refreshKey={filesRefresh} /> : null}
        {tab === 'stats' ? (
          <StatsPanel rounds={rounds} active={busy} context={context} operationSince={operationSince} onJump={onJumpRound} />
        ) : null}
        {tab === 'tasks' ? <TasksPanel processes={processes} connected={connected} onCommand={onCommand} /> : null}
        {tab === 'info' ? <InfoPanel summary={summary} info={info} commands={commands} /> : null}
      </div>
    </aside>
  )
}
