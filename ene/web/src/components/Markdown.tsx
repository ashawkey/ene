import { Check, Copy } from 'lucide-react'
import { memo, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { highlight } from '../lib/highlight'
import { copyText } from './ui'

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      className="copy-btn"
      aria-label={label}
      data-tip={done ? 'Copied' : label}
      onClick={async (event) => {
        event.stopPropagation()
        if (await copyText(text)) {
          setDone(true)
          window.setTimeout(() => setDone(false), 1200)
        }
      }}
    >
      {done ? <Check size={14} /> : <Copy size={14} />}
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

const components: Components = {
  code({ className, children, ...props }) {
    const match = /language-([\w+-]+)/.exec(className || '')
    const text = String(children ?? '')
    // Fenced blocks end with a newline; inline code does not.
    if (match || text.includes('\n')) {
      return <CodeBlock code={text.replace(/\n$/, '')} language={match?.[1] ?? null} />
    }
    return <code className="inline-code" {...props}>{children}</code>
  },
  pre({ children }) {
    return <>{children}</>
  },
  a({ href, children }) {
    const safe = href && /^(https?:|mailto:|#)/i.test(href) ? href : undefined
    return <a href={safe} target="_blank" rel="noopener noreferrer">{children}</a>
  },
  table({ children }) {
    return <div className="table-wrap"><table>{children}</table></div>
  },
  img({ alt }) {
    // Remote images would leak the viewer's address; show the alt text.
    return <span className="md-img">[{alt || 'image'}]</span>
  },
}

/** Markdown with raw HTML disabled (react-markdown never renders it). */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {text}
      </ReactMarkdown>
    </div>
  )
})
