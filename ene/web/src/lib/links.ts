/**
 * Display-only link detection for model output: workspace file paths open in
 * the file viewer, and web URLs that GFM autolinking misses (for example right
 * after CJK text) become links. The Markdown source itself is never changed.
 */

// Extensionless names worth linking when they appear on their own.
const BARE_NAMES = new Set([
  'Makefile', 'Dockerfile', 'Containerfile', 'Justfile', 'Procfile', 'Gemfile', 'Rakefile',
  'LICENSE', 'README', 'CHANGELOG', 'AGENTS.md',
])

// A bare `name.ext` (no directory) links only for these extensions, so
// prose like `os.path` or `self.value` stays plain.
const KNOWN_EXTENSIONS = new Set([
  'py', 'pyi', 'ipynb', 'ts', 'tsx', 'js', 'jsx', 'mjs', 'cjs', 'json', 'jsonl', 'md', 'mdx', 'rst', 'txt',
  'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'env', 'lock', 'html', 'htm', 'css', 'scss', 'less', 'svg',
  'c', 'h', 'cc', 'cpp', 'hpp', 'cu', 'cuh', 'rs', 'go', 'java', 'kt', 'swift', 'rb', 'php', 'lua', 'sh',
  'bash', 'zsh', 'fish', 'ps1', 'sql', 'proto', 'vue', 'svelte', 'cmake', 'gradle', 'xml', 'csv', 'tsv', 'log',
  'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf', 'diff', 'patch',
])

const PATH_CHARS = /^[\w.@+~-]+(?:\/[\w.@+~-]+)*\/?$/
// `path:12`, `path:12:3`, `path:12-20`, `path#L12`, `path#L12-L20`.
const LOCATION = /(?::\d+(?::\d+)?(?:-\d+)?|#L\d+(?:-L?\d+)?)$/

export type FileTarget = { path: string; line: number | null }

/** The workspace path *text* names, or null when it does not look like one. */
export function fileTarget(text: string): FileTarget | null {
  let value = text.trim()
  if (!value || value.length > 400 || /\s/.test(value)) return null
  if (/^[a-z][a-z\d+.-]*:\/\//i.test(value)) {
    if (!value.toLowerCase().startsWith('file://')) return null
    value = decodeURIComponentSafe(value.slice('file://'.length))
  }
  const location = LOCATION.exec(value)
  const line = location ? Number(/\d+/.exec(location[0])?.[0] ?? '') || null : null
  if (location) value = value.slice(0, location.index)
  const absolute = value.startsWith('/')
  const body = absolute ? value.slice(1) : value
  if (!body || !PATH_CHARS.test(body) || body.endsWith('/')) return null
  if (body.split('/').some((part) => part === '' || /^\.{3,}$/.test(part))) return null
  const name = body.split('/').at(-1)!
  const dot = name.lastIndexOf('.')
  const extension = dot > 0 ? name.slice(dot + 1) : ''
  const hasDir = body.includes('/') || absolute
  if (BARE_NAMES.has(name)) return { path: value, line }
  if (!/^[A-Za-z\d_]{1,10}$/.test(extension) || /^\d+$/.test(extension)) return null
  if (!hasDir && !KNOWN_EXTENSIONS.has(extension.toLowerCase())) return null
  return { path: value, line }
}

function decodeURIComponentSafe(value: string) {
  try {
    return decodeURIComponent(value)
  } catch {
    return value
  }
}

/** True for hrefs with a scheme (or protocol-relative), i.e. not workspace paths. */
export const isExternalHref = (href: string) => /^([a-z][a-z\d+.-]*:|\/\/)/i.test(href)

export const isWebUrl = (href: string) => /^(https?:\/\/|mailto:)/i.test(href)

// URLs: stop at whitespace, angle brackets, quotes, and CJK punctuation.
const URL_SOURCE = String.raw`https?:\/\/[^\s<>"'\`　-〿＀-￯]+`
// Paths: segments ending in a name with an extension (a slash is checked after).
const PATH_SOURCE = String.raw`(?:~|\.{1,2})?\/?(?:[\w.@+-]+\/)*[\w@+-][\w.@+-]*\.[A-Za-z\d_]{1,10}(?::\d+(?::\d+)?(?:-\d+)?|#L\d+(?:-L?\d+)?)?`
const TOKEN = new RegExp(`(${URL_SOURCE})|(?<![\\w/.~@+-])(${PATH_SOURCE})(?![\\w/@+-])`, 'g')

export type TextPiece = { kind: 'text'; text: string } | { kind: 'url' | 'file'; text: string; href: string }

/** Split plain prose into text, URL, and file-path pieces. */
export function splitLinks(text: string): TextPiece[] {
  const pieces: TextPiece[] = []
  let last = 0
  const push = (piece: TextPiece) => {
    const previous = pieces.at(-1)
    if (piece.kind === 'text' && previous?.kind === 'text') previous.text += piece.text
    else if (piece.text) pieces.push(piece)
  }
  for (const match of text.matchAll(TOKEN)) {
    const start = match.index ?? 0
    let token = match[0]
    let kind: 'url' | 'file'
    if (match[1]) {
      kind = 'url'
      // Sentence punctuation after a URL is not part of it; keep balanced parens.
      while (/[.,;:!?'"\])}*_~]$/.test(token)) {
        if (token.endsWith(')') && (token.match(/\(/g)?.length ?? 0) >= (token.match(/\)/g)?.length ?? 0)) break
        token = token.slice(0, -1)
      }
      if (!/^https?:\/\/[^/]+\.[^/]/i.test(token) && !/^https?:\/\/localhost/i.test(token)) continue
    } else {
      kind = 'file'
      // In prose only paths with a directory link; `Node.js` is not a file.
      if (!token.includes('/') || !fileTarget(token)) continue
    }
    push({ kind: 'text', text: text.slice(last, start) })
    push({ kind, text: token, href: token })
    last = start + token.length
  }
  push({ kind: 'text', text: text.slice(last) })
  return pieces
}

type MdNode = { type: string; value?: string; url?: string; children?: MdNode[]; data?: Record<string, unknown> }

// Inside these, text is already a link, or is code handled by the renderer.
const SKIP = new Set(['link', 'linkReference', 'definition', 'code', 'inlineCode', 'html'])

/** Remark plugin: turn URLs and file paths in plain text into link nodes. */
export function remarkLinks() {
  const walk = (node: MdNode) => {
    if (!node.children) return
    const next: MdNode[] = []
    let changed = false
    for (const child of node.children) {
      if (child.type === 'text' && child.value) {
        const pieces = splitLinks(child.value)
        if (pieces.length === 1 && pieces[0].kind === 'text') {
          next.push(child)
          continue
        }
        changed = true
        for (const piece of pieces) {
          next.push(
            piece.kind === 'text'
              ? { type: 'text', value: piece.text }
              : { type: 'link', url: piece.href, children: [{ type: 'text', value: piece.text }] },
          )
        }
        continue
      }
      if (!SKIP.has(child.type)) walk(child)
      next.push(child)
    }
    if (changed) node.children = next
  }
  return (tree: MdNode) => walk(tree)
}
