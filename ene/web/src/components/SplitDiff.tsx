import { ChevronsUpDown } from 'lucide-react'
import { memo, useEffect, useMemo, useState } from 'react'

import { highlight, languageForPath, splitHighlightedLines } from '../lib/highlight'
import type { DiffCell, SplitLine } from '../lib/splitDiff'
import { foldContext, splitLines } from '../lib/splitDiff'
import { useMedia } from './ui'

/** Per-line highlighted HTML for one side of the diff, or null for plain text. */
type SideHtml = string[] | null

function highlightSide(text: string, language: string | null): SideHtml {
  const html = highlight(text, language)
  return html === null ? null : splitHighlightedLines(html)
}

// Deleted lines and the left side of context come from the old file; added
// lines and the right side of context from the new one.
function htmlFor(cell: DiffCell, old: boolean, oldHtml: SideHtml, newHtml: SideHtml) {
  const side = old ? oldHtml : newHtml
  return side?.[cell.no - 1] ?? null
}

function Code({ cell, html }: { cell: DiffCell; html: string | null }) {
  return html !== null
    ? <code className="hljs" dangerouslySetInnerHTML={{ __html: html || ' ' }} />
    : <code>{cell.text || ' '}</code>
}

function Side({ cell, html }: { cell: DiffCell | null; html: string | null }) {
  if (!cell) return <><span className="ln empty" /><span className="cell empty" /></>
  return (
    <>
      <span className={`ln ${cell.kind}`}>{cell.no}</span>
      <span className={`cell ${cell.kind}`}><Code cell={cell} html={html} /></span>
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

/** One folded or unfolded block; memoized so expanding a gap re-renders only that gap. */
const Block = memo(function Block({
  lines,
  narrow,
  oldHtml,
  newHtml,
}: {
  lines: SplitLine[]
  narrow: boolean
  oldHtml: SideHtml
  newHtml: SideHtml
}) {
  if (narrow) {
    return (
      <>
        {unify(lines).map((row, at) => (
          <div className={`u-row ${row.cell.kind}`} key={at}>
            <span className="ln">{row.old ?? ''}</span>
            <span className="ln">{row.new ?? ''}</span>
            <span className={`cell ${row.cell.kind}`}>
              <Code cell={row.cell} html={htmlFor(row.cell, row.old !== null, oldHtml, newHtml)} />
            </span>
          </div>
        ))}
      </>
    )
  }
  return (
    <>
      {lines.map((line, at) => (
        <div className="s-row" key={at}>
          <Side cell={line.left} html={line.left ? htmlFor(line.left, true, oldHtml, newHtml) : null} />
          <Side cell={line.right} html={line.right ? htmlFor(line.right, false, oldHtml, newHtml) : null} />
        </div>
      ))}
    </>
  )
})

/** Git-style diff: two columns (old | new), unified on narrow screens. */
export const SplitDiff = memo(function SplitDiff({
  path,
  oldText,
  newText,
  lines,
}: {
  path: string
  oldText: string
  newText: string
  /** Precomputed `splitLines(oldText, newText)`, when the caller already has it. */
  lines?: SplitLine[]
}) {
  const language = languageForPath(path)
  const narrow = useMedia('(max-width: 760px)')
  const blocks = useMemo(() => foldContext(lines ?? splitLines(oldText, newText)), [lines, oldText, newText])
  // Highlight each side once, as a whole file, so multi-line strings and
  // comments keep their colors and expanding a gap costs no re-highlighting.
  const oldHtml = useMemo(() => highlightSide(oldText, language), [oldText, language])
  const newHtml = useMemo(() => highlightSide(newText, language), [newText, language])
  const [opened, setOpened] = useState<Set<number>>(new Set())
  useEffect(() => setOpened(new Set()), [oldText, newText])

  return (
    <div className={`split-diff ${narrow ? 'unified' : ''}`}>
      {blocks.map((block, index) =>
        block.kind === 'gap' && !opened.has(index) ? (
          <button
            type="button"
            className="diff-gap"
            key={index}
            onClick={() => setOpened((current) => new Set([...current, index]))}
          >
            <ChevronsUpDown size={13} />
            {block.lines.length} unchanged lines
          </button>
        ) : (
          <Block key={index} lines={block.lines} narrow={narrow} oldHtml={oldHtml} newHtml={newHtml} />
        ),
      )}
    </div>
  )
})
