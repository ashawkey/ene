import { Check, Copy } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { createContext, memo, useContext, useMemo, useState } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { rawFileUrl } from '../lib/api'
import { highlight } from '../lib/highlight'
import { fileTarget, isExternalHref, isWebUrl, remarkLinks } from '../lib/links'
import { copyText } from './ui'

/** *text* may be a function, to build large text only when copied. */
export function CopyButton({ text, label = 'Copy', icon: Icon = Copy }: {
  text: string | (() => string)
  label?: string
  icon?: LucideIcon
}) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      className="copy-btn"
      aria-label={label}
      data-tip={done ? 'Copied' : label}
      onClick={async (event) => {
        event.stopPropagation()
        if (await copyText(typeof text === 'function' ? text() : text)) {
          setDone(true)
          window.setTimeout(() => setDone(false), 1200)
        }
      }}
    >
      {done ? <Check size={14} /> : <Icon size={14} />}
    </button>
  )
}

// Memoized: a streaming reply re-renders its Markdown on every delta, and
// finished code blocks above the cursor must not be re-highlighted each time.
export const CodeBlock = memo(function CodeBlock({ code, language }: { code: string; language?: string | null }) {
  const html = useMemo(() => highlight(code, language), [code, language])
  return (
    <div className="code-block">
      <div className="code-head">
        <span>{language || 'text'}</span>
        <CopyButton text={code} label="Copy code" />
      </div>
      {html !== null
        ? <pre><code className="hljs" dangerouslySetInnerHTML={{ __html: html }} /></pre>
        : <pre><code>{code}</code></pre>}
    </div>
  )
})

/** Opens a workspace file in the viewer; absent outside a session timeline. */
export const OpenFileContext = createContext<((path: string) => void) | null>(null)

/** The session whose workspace serves images; null disables previews. */
export const SessionIdContext = createContext<string | null>(null)

// Raster formats the hub's read-only `/raw` endpoint serves.
const RASTER_IMAGE = /\.(png|jpe?g|gif|webp|bmp|ico|avif)$/i

/**
 * A Markdown image. Local raster paths render through the hub; remote URLs
 * (which would leak the viewer's address) and failures show the alt text.
 */
function MarkdownImage({ src, alt }: { src: string; alt: string }) {
  const sessionId = useContext(SessionIdContext)
  const openFile = useContext(OpenFileContext)
  const [failed, setFailed] = useState(false)
  const local = !isExternalHref(src) || /^file:/i.test(src)
  const target = local ? fileTarget(decode(src)) : null
  if (!sessionId || !target || target.line !== null || !RASTER_IMAGE.test(target.path) || failed) {
    return <span className="md-img">[{alt || 'image'}]</span>
  }
  const img = <img src={rawFileUrl(sessionId, target.path)} alt={alt} loading="lazy" onError={() => setFailed(true)} />
  return openFile ? (
    <button type="button" className="md-image" onClick={() => openFile(target.path)} data-tip={`Open ${target.path}`}>{img}</button>
  ) : <span className="md-image">{img}</span>
}

function SmartLink({ href, children }: { href: string; children: ReactNode }) {
  const openFile = useContext(OpenFileContext)
  if (isWebUrl(href) || href.startsWith('#')) {
    return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
  }
  const target = !isExternalHref(href) || /^file:/i.test(href) ? fileTarget(decode(href)) : null
  if (target && openFile) {
    return (
      <button type="button" className="file-link" data-tip={`Open ${target.path}`} onClick={() => openFile(target.path)}>
        {children}
      </button>
    )
  }
  return <>{children}</>
}

function decode(href: string) {
  try {
    return decodeURI(href)
  } catch {
    return href
  }
}

// Keep file: links for the viewer; every other scheme uses the safe default.
const urlTransform = (url: string) => (/^file:/i.test(url) ? url : defaultUrlTransform(url))

const components: Components = {
  code({ className, children, ...props }) {
    const match = /language-([\w+-]+)/.exec(className || '')
    const text = String(children ?? '')
    // Fenced blocks end with a newline; inline code does not.
    if (match || text.includes('\n')) {
      return <CodeBlock code={text.replace(/\n$/, '')} language={match?.[1] ?? null} />
    }
    const inline = <code className="inline-code" {...props}>{children}</code>
    // Code naming a file or URL gets a link around it; the text is unchanged.
    if (isWebUrl(text) && !/\s/.test(text)) return <SmartLink href={text}>{inline}</SmartLink>
    return fileTarget(text) ? <SmartLink href={text}>{inline}</SmartLink> : inline
  },
  pre({ children }) {
    return <>{children}</>
  },
  a({ href, children }) {
    return <SmartLink href={href ?? ''}>{children}</SmartLink>
  },
  table({ children }) {
    return <div className="table-wrap"><table>{children}</table></div>
  },
  img({ src, alt }) {
    return <MarkdownImage src={typeof src === 'string' ? src : ''} alt={alt ?? ''} />
  },
}

const FENCE = /^ {0,3}(`{3,}|~{3,})/

/**
 * Split Markdown at blank lines between top-level blocks (never inside a
 * fenced code block, nor before an indented continuation line). Earlier
 * blocks of a growing text come out byte-identical, so they can be memoized.
 */
export function splitBlocks(text: string): string[] {
  const lines = text.split('\n')
  const blocks: string[] = []
  let start = 0
  let fence: string | null = null
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index]
    const marker = FENCE.exec(line)?.[1]
    if (marker) {
      if (fence === null) fence = marker
      else if (marker[0] === fence[0] && marker.length >= fence.length && !line.trim().slice(marker.length)) fence = null
      continue
    }
    if (fence !== null || line.trim() !== '' || index === start) continue
    const following = lines[index + 1]
    if (following === undefined || following === '' || /^\s/.test(following)) continue
    blocks.push(lines.slice(start, index).join('\n'))
    start = index + 1
  }
  blocks.push(lines.slice(start).join('\n'))
  return blocks
}

const REMARK_PLUGINS = [remarkGfm, remarkLinks]

const MarkdownBlock = memo(function MarkdownBlock({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={components} urlTransform={urlTransform} skipHtml>
      {text}
    </ReactMarkdown>
  )
})

/**
 * Markdown with raw HTML disabled (react-markdown never renders it).
 *
 * While *streaming*, the text is rendered block by block so each delta
 * re-parses only the last block instead of the whole reply. The final text
 * is parsed as one document (lists or references spanning blocks).
 */
export const Markdown = memo(function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return (
    <div className="markdown">
      {streaming
        ? splitBlocks(text).map((block, index) => <MarkdownBlock key={index} text={block} />)
        : <MarkdownBlock text={text} />}
    </div>
  )
})
