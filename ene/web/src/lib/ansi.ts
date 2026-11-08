export type AnsiSpan = {
  text: string
  fg?: number
  bg?: number
  bold?: boolean
  dim?: boolean
  italic?: boolean
  underline?: boolean
}

// Matches SGR sequences (colors/styles); other CSI sequences are stripped.
const CSI = /\x1b\[([0-9;?]*)([A-Za-z])/g

/** Split ANSI-styled text into spans of the 16 standard colors and styles. */
export function parseAnsi(input: string): AnsiSpan[] {
  const spans: AnsiSpan[] = []
  let style: Omit<AnsiSpan, 'text'> = {}
  let last = 0
  const push = (text: string) => {
    if (!text) return
    const previous = spans.at(-1)
    if (previous && sameStyle(previous, style)) previous.text += text
    else spans.push({ text, ...style })
  }
  for (const match of input.matchAll(CSI)) {
    push(input.slice(last, match.index))
    last = (match.index ?? 0) + match[0].length
    if (match[2] !== 'm') continue
    const codes = match[1] === '' ? [0] : match[1].split(';').map((code) => Number(code) || 0)
    for (let i = 0; i < codes.length; i++) {
      const code = codes[i]
      if (code === 0) style = {}
      else if (code === 1) style = { ...style, bold: true }
      else if (code === 2) style = { ...style, dim: true }
      else if (code === 3) style = { ...style, italic: true }
      else if (code === 4) style = { ...style, underline: true }
      else if (code === 22) style = { ...style, bold: false, dim: false }
      else if (code === 23) style = { ...style, italic: false }
      else if (code === 24) style = { ...style, underline: false }
      else if (code >= 30 && code <= 37) style = { ...style, fg: code - 30 }
      else if (code >= 90 && code <= 97) style = { ...style, fg: code - 90 + 8 }
      else if (code === 39) style = { ...style, fg: undefined }
      else if (code >= 40 && code <= 47) style = { ...style, bg: code - 40 }
      else if (code >= 100 && code <= 107) style = { ...style, bg: code - 100 + 8 }
      else if (code === 49) style = { ...style, bg: undefined }
      else if ((code === 38 || code === 48) && codes[i + 1] === 5) {
        // 256-color: keep the 16-color subset, approximate the rest.
        const value = codes[i + 2] ?? 7
        const color = value < 16 ? value : 7
        style = code === 38 ? { ...style, fg: color } : { ...style, bg: color }
        i += 2
      } else if ((code === 38 || code === 48) && codes[i + 1] === 2) {
        i += 4
      }
    }
  }
  push(input.slice(last))
  return spans
}

function sameStyle(a: Omit<AnsiSpan, 'text'>, b: Omit<AnsiSpan, 'text'>) {
  return a.fg === b.fg && a.bg === b.bg && !!a.bold === !!b.bold && !!a.dim === !!b.dim &&
    !!a.italic === !!b.italic && !!a.underline === !!b.underline
}

export const stripAnsi = (input: string) => input.replace(CSI, '')
