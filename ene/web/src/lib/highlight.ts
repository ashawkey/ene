import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import css from 'highlight.js/lib/languages/css'
import diff from 'highlight.js/lib/languages/diff'
import go from 'highlight.js/lib/languages/go'
import ini from 'highlight.js/lib/languages/ini'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

const languages = { bash, c, cpp, css, diff, go, ini, java, javascript, json, markdown, python, rust, sql, typescript, xml, yaml }
for (const [name, language] of Object.entries(languages)) hljs.registerLanguage(name, language)
hljs.registerAliases(['sh', 'shell', 'zsh', 'console'], { languageName: 'bash' })
hljs.registerAliases(['py'], { languageName: 'python' })
hljs.registerAliases(['js', 'jsx', 'mjs', 'cjs'], { languageName: 'javascript' })
hljs.registerAliases(['ts', 'tsx'], { languageName: 'typescript' })
hljs.registerAliases(['html', 'svg', 'vue'], { languageName: 'xml' })
hljs.registerAliases(['toml', 'cfg', 'conf'], { languageName: 'ini' })
hljs.registerAliases(['yml'], { languageName: 'yaml' })
hljs.registerAliases(['md'], { languageName: 'markdown' })
hljs.registerAliases(['rs'], { languageName: 'rust' })
hljs.registerAliases(['h', 'hpp', 'cc', 'cu', 'cuh'], { languageName: 'cpp' })

const EXTENSIONS: Record<string, string> = {
  py: 'python', pyi: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
  ts: 'typescript', tsx: 'typescript', json: 'json', jsonl: 'json', yaml: 'yaml', yml: 'yaml',
  toml: 'ini', ini: 'ini', cfg: 'ini', html: 'xml', xml: 'xml', svg: 'xml', css: 'css', scss: 'css',
  md: 'markdown', sh: 'bash', bash: 'bash', zsh: 'bash', sql: 'sql', rs: 'rust', go: 'go',
  java: 'java', c: 'c', h: 'cpp', cpp: 'cpp', hpp: 'cpp', cc: 'cpp', cu: 'cpp', diff: 'diff', patch: 'diff',
}

export function languageForPath(path: string): string | null {
  const name = path.split('/').at(-1)?.toLowerCase() ?? ''
  if (name === 'dockerfile' || name === 'makefile') return 'bash'
  const ext = name.includes('.') ? name.split('.').at(-1)! : ''
  return EXTENSIONS[ext] ?? null
}

// Highlighting very large inputs blocks the main thread; fall back to plain.
const MAX_HIGHLIGHT = 200_000

/** Highlighted HTML (hljs escapes the source) or null for plain rendering. */
export function highlight(code: string, language: string | null | undefined): string | null {
  if (!language || code.length > MAX_HIGHLIGHT) return null
  const name = hljs.getLanguage(language) ? language : null
  if (!name) return null
  try {
    return hljs.highlight(code, { language: name, ignoreIllegals: true }).value
  } catch {
    return null
  }
}
