/**
 * Caret geometry for a plain textarea, so key handling can tell whether the
 * caret sits on the first or last *visual* row (soft wraps included).
 */

const COPIED = [
  'boxSizing', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
  'fontFamily', 'fontSize', 'fontStyle', 'fontVariant', 'fontWeight', 'fontStretch',
  'letterSpacing', 'lineHeight', 'textTransform', 'textIndent', 'wordSpacing',
  'tabSize', 'wordBreak', 'overflowWrap', 'direction',
] as const

/** Top offset (px) of the row where `position` renders in `element`. */
function rowTop(element: HTMLTextAreaElement, position: number): number {
  const style = getComputedStyle(element)
  const mirror = document.createElement('div')
  for (const key of COPIED) mirror.style[key] = style[key]
  Object.assign(mirror.style, {
    position: 'absolute',
    visibility: 'hidden',
    top: '0',
    left: '-9999px',
    border: '0',
    boxSizing: 'border-box',
    width: `${element.clientWidth}px`,
    whiteSpace: 'pre-wrap',
    overflowWrap: style.overflowWrap || 'break-word',
  })
  const value = element.value
  mirror.textContent = value.slice(0, position)
  const marker = document.createElement('span')
  // The rest of the text keeps wrapping identical; a zero-width space
  // stands in at the very end so an empty tail still has a row.
  marker.textContent = value.slice(position) || '​'
  mirror.appendChild(marker)
  document.body.appendChild(mirror)
  const top = marker.offsetTop
  mirror.remove()
  return top
}

/** Where the collapsed caret sits relative to the text's first and last rows. */
export function caretRows(element: HTMLTextAreaElement): { first: boolean; last: boolean } {
  const value = element.value
  const caret = element.selectionEnd ?? value.length
  // Logical lines decide first; layout then catches soft-wrapped rows.
  let first = !value.slice(0, caret).includes('\n')
  let last = !value.slice(caret).includes('\n')
  if (first || last) {
    const top = rowTop(element, caret)
    if (first) first = top <= rowTop(element, 0)
    if (last) last = top >= rowTop(element, value.length)
  }
  return { first, last }
}

/** Start and end offsets of the logical line containing `position`. */
export function lineBounds(value: string, position: number): [number, number] {
  const start = position > 0 ? value.lastIndexOf('\n', position - 1) + 1 : 0
  const newline = value.indexOf('\n', position)
  return [start, newline < 0 ? value.length : newline]
}
