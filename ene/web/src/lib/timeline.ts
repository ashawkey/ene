import type { TimelineItem } from './session'

type RoundItem = Extract<TimelineItem, { kind: 'round' }>

export type RenderNode =
  /** *round*: the round summary shown in this reply's footer row. */
  | { kind: 'item'; item: TimelineItem; round?: RoundItem }
  | { kind: 'group'; key: string; items: TimelineItem[]; live: boolean }

const groupable = (item: TimelineItem) =>
  // Edits stay visible: they are the work a reader most wants to review.
  (item.kind === 'tool' && item.diffs.length === 0) ||
  item.kind === 'thinking' ||
  item.kind === 'output' ||
  (item.kind === 'notice' && item.level === 'debug')

// Short runs read fine inline; folding them would only add clicks.
export const GROUP_MIN_TOOLS = 3

/** Fold long runs of tool activity between messages into collapsible groups. */
export function groupItems(items: TimelineItem[], live: boolean): RenderNode[] {
  const nodes: RenderNode[] = []
  let run: TimelineItem[] = []
  const flush = (atTail: boolean) => {
    const tools = run.filter((item) => item.kind === 'tool').length
    if (tools >= GROUP_MIN_TOOLS) {
      nodes.push({ kind: 'group', key: `g-${run[0].key}`, items: run, live: atTail && live })
    } else {
      for (const item of run) nodes.push({ kind: 'item', item })
    }
    run = []
  }
  for (const item of items) {
    if (groupable(item)) {
      run.push(item)
    } else {
      if (run.length) flush(false)
      nodes.push({ kind: 'item', item })
    }
  }
  if (run.length) flush(true)
  // A reply that ends a round carries that round's summary in its footer.
  const merged: RenderNode[] = []
  for (const node of nodes) {
    const previous = merged.at(-1)
    if (
      node.kind === 'item' && node.item.kind === 'round' &&
      previous?.kind === 'item' && previous.item.kind === 'assistant' && !previous.round
    ) {
      previous.round = node.item
      continue
    }
    merged.push(node)
  }
  return merged
}

export type GroupSummary = { tools: number; failed: number; names: Array<[string, number]>; latest: string }

export function summarize(items: TimelineItem[]): GroupSummary {
  const counts = new Map<string, number>()
  let tools = 0
  let failed = 0
  let latest = ''
  for (const item of items) {
    if (item.kind !== 'tool') continue
    tools += 1
    const name = item.name || item.text.split(/[\s:(]/, 1)[0] || 'tool'
    counts.set(name, (counts.get(name) ?? 0) + 1)
    if (item.result && !item.result.success) failed += 1
    latest = item.primary ? `${name} ${item.primary}` : name
  }
  return { tools, failed, names: [...counts.entries()].sort((a, b) => b[1] - a[1]), latest }
}

/** All visible text of an item, for in-conversation search. */
export function itemText(item: TimelineItem): string {
  switch (item.kind) {
    case 'tool':
      return [
        item.name, item.primary, item.text, ...item.qualifiers, item.result?.text ?? '', item.output?.text ?? '',
        ...item.diffs.flatMap((diff) => [diff.path, diff.oldText, diff.newText]),
      ].join('\n')
    case 'diff':
      return [item.path, item.oldText, item.newText].join('\n')
    case 'round':
      return item.round.prompt
    default:
      return item.text
  }
}
