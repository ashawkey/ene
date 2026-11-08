import { ChevronsUpDown } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { highlight, languageForPath } from '../lib/highlight'
import type { DiffCell, SplitLine } from '../lib/splitDiff'
import { foldContext, splitLines } from '../lib/splitDiff'
import { useMedia } from './ui'

function Code({ cell, language }: { cell: DiffCell; language: string | null }) {
  const html = highlight(cell.text, language)
  return html !== null
    ? <code className="hljs" dangerouslySetInnerHTML={{ __html: html || ' ' }} />
    : <code>{cell.text || ' '}</code>
}

function Side({ cell, language }: { cell: DiffCell | null; language: string | null }) {
  if (!cell) return <><span className="ln empty" /><span className="cell empty" /></>
  return (
    <>
      <span className={`ln ${cell.kind}`}>{cell.no}</span>
      <span className={`cell ${cell.kind}`}><Code cell={cell} language={language} /></span>
    </>
  )
}

/** Unified rows for narrow screens: removals, then additions. */
function unify(lines: SplitLine[]) {
  const rows: Array<{ cell: DiffCell; old: number | null; new: number | null }> = []
  const dels: typeof rows = []
  const adds: typeof rows = []
  const flush = () => {
    rows.push(...dels.splice(0), ...adds.splice(0))
  }
  for (const line of lines) {
    if (line.left?.kind === 'ctx' && line.right) {
      flush()
      rows.push({ cell: line.left, old: line.left.no, new: line.right.no })
      continue
    }
    if (line.left) dels.push({ cell: line.left, old: line.left.no, new: null })
    if (line.right) adds.push({ cell: line.right, old: null, new: line.right.no })
  }
  flush()
  return rows
}

/** Git-style diff: two columns (old | new), unified on narrow screens. */
export function SplitDiff({ path, oldText, newText }: { path: string; oldText: string; newText: string }) {
  const language = languageForPath(path)
  const narrow = useMedia('(max-width: 760px)')
  const blocks = useMemo(() => foldContext(splitLines(oldText, newText)), [oldText, newText])
  const [opened, setOpened] = useState<Set<number>>(new Set())
  useEffect(() => setOpened(new Set()), [oldText, newText])

  return (
    <div className={`split-diff ${narrow ? 'unified' : ''}`}>
      {blocks.map((block, index) => {
        if (block.kind === 'gap' && !opened.has(index)) {
          return (
            <button
              type="button"
              className="diff-gap"
              key={index}
              onClick={() => setOpened(new Set([...opened, index]))}
            >
              <ChevronsUpDown size={13} />
              {block.lines.length} unchanged lines
            </button>
          )
        }
        if (narrow) {
          return unify(block.lines).map((row, at) => (
            <div className={`u-row ${row.cell.kind}`} key={`${index}-${at}`}>
              <span className="ln">{row.old ?? ''}</span>
              <span className="ln">{row.new ?? ''}</span>
              <span className={`cell ${row.cell.kind}`}><Code cell={row.cell} language={language} /></span>
            </div>
          ))
        }
        return block.lines.map((line, at) => (
          <div className="s-row" key={`${index}-${at}`}>
            <Side cell={line.left} language={language} />
            <Side cell={line.right} language={language} />
          </div>
        ))
      })}
    </div>
  )
}
