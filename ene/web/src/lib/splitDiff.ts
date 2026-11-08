import { diffLines } from 'diff'

export type DiffCell = { no: number; text: string; kind: 'ctx' | 'del' | 'add' }
export type SplitLine = { left: DiffCell | null; right: DiffCell | null }
export type SplitBlock =
  | { kind: 'lines'; lines: SplitLine[] }
  | { kind: 'gap'; lines: SplitLine[] }

/** Unchanged lines kept around each change; longer runs fold into a gap. */
export const CONTEXT = 3

const split = (value: string) => value.replace(/\n$/, '').split('\n')

/** Side-by-side rows: removals on the left paired with additions on the right. */
export function splitLines(oldText: string, newText: string): SplitLine[] {
  const lines: SplitLine[] = []
  let oldNo = 1
  let newNo = 1
  let removed: DiffCell[] = []
  const flush = (added: DiffCell[] = []) => {
    for (let i = 0; i < Math.max(removed.length, added.length); i++) {
      lines.push({ left: removed[i] ?? null, right: added[i] ?? null })
    }
    removed = []
  }
  for (const part of diffLines(oldText, newText)) {
    const texts = split(part.value)
    if (part.removed) {
      flush()
      removed = texts.map((text) => ({ no: oldNo++, text, kind: 'del' as const }))
    } else if (part.added) {
      flush(texts.map((text) => ({ no: newNo++, text, kind: 'add' as const })))
    } else {
      flush()
      for (const text of texts) {
        lines.push({
          left: { no: oldNo++, text, kind: 'ctx' },
          right: { no: newNo++, text, kind: 'ctx' },
        })
      }
    }
  }
  flush()
  return lines
}

const unchanged = (line: SplitLine) => line.left?.kind === 'ctx' && line.right?.kind === 'ctx'

/** Group rows, folding unchanged runs that are far from any change. */
export function foldContext(lines: SplitLine[], context = CONTEXT): SplitBlock[] {
  const blocks: SplitBlock[] = []
  let index = 0
  while (index < lines.length) {
    if (!unchanged(lines[index])) {
      const start = index
      while (index < lines.length && !unchanged(lines[index])) index++
      blocks.push({ kind: 'lines', lines: lines.slice(start, index) })
      continue
    }
    const start = index
    while (index < lines.length && unchanged(lines[index])) index++
    const run = lines.slice(start, index)
    const head = start === 0 ? 0 : context
    const tail = index === lines.length ? 0 : context
    if (run.length <= head + tail + 1) {
      blocks.push({ kind: 'lines', lines: run })
      continue
    }
    if (head) blocks.push({ kind: 'lines', lines: run.slice(0, head) })
    blocks.push({ kind: 'gap', lines: run.slice(head, run.length - tail) })
    if (tail) blocks.push({ kind: 'lines', lines: run.slice(run.length - tail) })
  }
  return blocks
}
