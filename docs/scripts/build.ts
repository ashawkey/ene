import { cp, mkdir, readdir, readFile, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { extname, join, posix } from 'node:path'
import { fileURLToPath } from 'node:url'

import { loadDocumentation, type PageNode } from '../src/site/content.ts'
import { buildTheme } from '../src/site/theme.ts'
import { renderPage } from '../src/shell/page.ts'
import { renderToc } from '../src/shell/toc.ts'
import { renderTree, rootPrefix } from '../src/shell/tree.ts'
import { siteConfig } from '../site.config.ts'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const sourceDir = join(root, 'source')
const ABSOLUTE = /^(?:[a-z][a-z0-9+.-]*:|\/\/|\/|#|data:|mailto:)/i

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

async function readAssets(): Promise<{ js: string; css: string }> {
  const manifestPath = join(dist, '.vite/manifest.json')
  if (!existsSync(manifestPath)) {
    throw new Error('Vite manifest is missing; run the complete documentation build.')
  }
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8')) as Record<
    string,
    { file: string; css?: string[] }
  >
  const entry = manifest['src/shell/shell.ts']
  if (!entry?.file || !entry.css?.[0]) throw new Error('Vite manifest has no shell entry.')
  return { js: entry.file, css: entry.css[0] }
}

function pagefindAttrs(title: string): string {
  return ` data-pagefind-body data-pagefind-meta="title:${escapeHtml(title)}"`
}

async function copyAssets(from: string, to: string): Promise<number> {
  let copied = 0
  async function walk(dir: string, out: string): Promise<void> {
    for (const entry of await readdir(dir, { withFileTypes: true })) {
      if (entry.name.startsWith('.')) continue
      const source = join(dir, entry.name)
      if (entry.isDirectory()) {
        await walk(source, join(out, entry.name))
      } else if (entry.isFile() && entry.name.toLowerCase() !== 'index.md' && extname(entry.name).toLowerCase() !== '.md') {
        await mkdir(out, { recursive: true })
        await cp(source, join(out, entry.name))
        copied += 1
      }
    }
  }
  await walk(from, to)
  return copied
}

function excerpt(text: string): string {
  const trimmed = text.slice(0, 180).trim()
  return trimmed.length < text.length ? `${trimmed}…` : trimmed
}

function internalTarget(value: string): { slug: string; fragment: string } | undefined {
  if (!value || ABSOLUTE.test(value)) return undefined
  const [pathname = '', fragment = ''] = value.split('#', 2)
  if (!pathname.toLowerCase().endsWith('.md')) return undefined
  const normalized = posix.normalize(pathname)
  if (normalized.startsWith('../') || normalized.includes('/')) {
    throw new Error(`Documentation links must target a page in docs/source: ${value}`)
  }
  const basename = normalized.slice(0, -3)
  return { slug: /^(?:index|readme)$/i.test(basename) ? '' : basename, fragment }
}

function rewriteAndValidateLinks(pages: readonly PageNode[]): void {
  const bySlug = new Map(pages.map((page) => [page.slug, page]))
  for (const page of pages) {
    const depth = page.slug ? 1 : 0
    page.rendered.html = page.rendered.html.replace(
      /href="([^"]+)"/g,
      (match, encoded: string) => {
        const value = encoded.replace(/&amp;/g, '&')
        const target = internalTarget(value)
        if (!target) return match
        const destination = bySlug.get(target.slug)
        if (!destination) throw new Error(`${page.slug || 'index'}.md links to missing page: ${value}`)
        if (
          target.fragment &&
          !destination.rendered.headings.some((heading) => heading.id === target.fragment)
        ) {
          throw new Error(`${page.slug || 'index'}.md links to missing heading: ${value}`)
        }
        const href = `${rootPrefix(depth)}${target.slug ? `${encodeURIComponent(target.slug)}/` : ''}` +
          (target.fragment ? `#${target.fragment}` : '')
        return `href="${escapeHtml(href)}"`
      }
    )
  }
}

async function main(): Promise<void> {
  const assets = await readAssets()
  const loaded = await loadDocumentation(sourceDir, siteConfig.navigation)
  const pages = loaded.pages
  rewriteAndValidateLinks(pages)

  const copiedAssets = await copyAssets(sourceDir, dist)
  await writeFile(join(dist, 'theme.css'), await buildTheme(root), 'utf8')

  const katexDist = join(root, 'node_modules/katex/dist')
  await mkdir(join(dist, 'katex'), { recursive: true })
  await cp(join(katexDist, 'katex.min.css'), join(dist, 'katex/katex.min.css'))
  await cp(join(katexDist, 'fonts'), join(dist, 'katex/fonts'), { recursive: true })

  const languages = [...new Set(pages.map((page) => page.lang))].sort()

  for (const page of pages) {
    const depth = page.slug ? 1 : 0
    const html = renderPage({
      title: page.title,
      siteTitle: siteConfig.title,
      description: excerpt(page.rendered.text) || siteConfig.description,
      root: rootPrefix(depth),
      assets,
      nav: renderTree(loaded.sections, page.slug),
      toc: renderToc(page.rendered.headings),
      body: `<div id="write" class="typora-editor"${pagefindAttrs(page.title)}>\n${page.rendered.html}</div>`,
      hasMath: page.rendered.html.includes('katex'),
      lang: page.lang,
      languages,
    })
    const output = page.slug ? join(dist, page.slug) : dist
    await mkdir(output, { recursive: true })
    await writeFile(join(output, 'index.html'), html, 'utf8')
  }

  const failures = pages.flatMap((page) => page.rendered.mathFailures.map((failure) => ({ page, failure })))
  const localLinks = pages.flatMap((page) => page.rendered.unresolvableAssets.map((link) => ({ page, link })))
  if (failures.length > 0 || localLinks.length > 0) {
    for (const { page, failure } of failures) console.error(`formula in ${page.slug || 'index'}: ${failure.message}`)
    for (const { page, link } of localLinks) console.error(`local link in ${page.slug || 'index'}: ${link}`)
    throw new Error('Documentation contains rendering errors or local filesystem links.')
  }

  const unknown = new Set(pages.flatMap((page) => page.rendered.unknownLanguages))
  if (unknown.size > 0) console.warn(`no grammar for code fences tagged: ${[...unknown].sort().join(', ')}`)
  console.log(`${pages.length} documentation pages, ${copiedAssets} copied assets`)
}

await main()
