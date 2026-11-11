export type JsonTokenKind = 'key' | 'string' | 'number' | 'literal' | 'punct' | 'plain'
export type JsonToken = { kind: JsonTokenKind; text: string }

/** Longer lines are left plain: parsing and spans would cost more than they help. */
const MAX_JSON_LINE = 64 * 1024
const TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}[\],:]|\s+/g

/** Where a line's JSON payload may start: a whole-line value, or an object after a log prefix. */
function jsonStarts(line: string): number[] {
  const lead = line.length - line.trimStart().length
  const starts = line[lead] === '{' || line[lead] === '[' ? [lead] : []
  const brace = line.indexOf('{')
  if (brace >= 0 && brace !== lead) starts.push(brace)
  return starts
}

function isJsonContainer(text: string): boolean {
  const last = text.at(-1)
  if (last !== '}' && last !== ']') return false
  try {
    const value: unknown = JSON.parse(text)
    return !!value && typeof value === 'object'
  } catch {
    return false
  }
}

/**
 * Tokens for a log line carrying a JSON object or array (JSON Lines output,
 * optionally after a prefix such as a timestamp), or null when it is not one.
 */
export function jsonLineTokens(line: string): JsonToken[] | null {
  if (line.length > MAX_JSON_LINE || line.includes('\x1b')) return null
  const start = jsonStarts(line).find((index) => isJsonContainer(line.slice(index).trimEnd()))
  if (start === undefined) return null
  const body = line.slice(start).trimEnd()
  const tokens: JsonToken[] = start ? [{ kind: 'plain', text: line.slice(0, start) }] : []
  for (const match of body.matchAll(TOKEN)) {
    const [text, string, colon] = match
    if (string !== undefined) {
      tokens.push({ kind: colon ? 'key' : 'string', text: string })
      if (colon) tokens.push({ kind: 'punct', text: colon })
    } else if (/^\s/.test(text)) tokens.push({ kind: 'plain', text })
    else if (/^[{}[\],:]$/.test(text)) tokens.push({ kind: 'punct', text })
    else if (text === 'true' || text === 'false' || text === 'null') tokens.push({ kind: 'literal', text })
    else tokens.push({ kind: 'number', text })
  }
  const trailing = line.slice(start + body.length)
  if (trailing) tokens.push({ kind: 'plain', text: trailing })
  return tokens
}
