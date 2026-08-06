import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

import { renderNote, type RenderedNote } from '../render/pipeline.ts'
import { detectLanguage, type NoteLanguage } from './language.ts'

export interface PageNode {
  slug: string
  title: string
  navLabel?: string
  lang: NoteLanguage
  rendered: RenderedNote
}

export interface NavigationSectionNode {
  title: string
  pages: PageNode[]
}

export interface LoadResult {
  pages: PageNode[]
  sections: NavigationSectionNode[]
}

interface NavigationPageConfig {
  slug: string
  label?: string
}

interface NavigationSectionConfig {
  title: string
  pages: readonly NavigationPageConfig[]
}

function prettify(name: string): string {
  const words = name.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

async function loadPage(sourceDir: string, config: NavigationPageConfig): Promise<PageNode> {
  const filename = config.slug ? `${config.slug}.md` : 'index.md'
  const source = await readFile(join(sourceDir, filename), 'utf8')
  const rendered = renderNote(source, config.slug ? prettify(config.slug) : 'Ene', {
    assetPrefix: config.slug ? '../' : '',
  })
  return {
    slug: config.slug,
    title: rendered.title,
    navLabel: config.label,
    lang: detectLanguage(source),
    rendered,
  }
}

export async function loadDocumentation(
  sourceDir: string,
  navigation: readonly NavigationSectionConfig[]
): Promise<LoadResult> {
  const markdown = (await readdir(sourceDir, { withFileTypes: true }))
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith('.md'))
    .map((entry) => entry.name)

  if (!markdown.includes('index.md')) throw new Error('docs/source/index.md is required')

  const configured = navigation.flatMap((section) => section.pages.map((page) => page.slug))
  const duplicates = configured.filter((slug, index) => configured.indexOf(slug) !== index)
  if (duplicates.length > 0) {
    throw new Error(`Duplicate page(s) in site navigation: ${[...new Set(duplicates)].join(', ')}`)
  }

  const discovered = markdown.map((name) => (name === 'index.md' ? '' : name.slice(0, -3)))
  const missing = configured.filter((slug) => !discovered.includes(slug))
  const unlisted = discovered.filter((slug) => !configured.includes(slug))
  if (missing.length > 0 || unlisted.length > 0) {
    const details = [
      missing.length > 0 ? `missing source: ${missing.join(', ')}` : '',
      unlisted.length > 0 ? `not in site navigation: ${unlisted.join(', ')}` : '',
    ].filter(Boolean)
    throw new Error(`Documentation navigation is incomplete (${details.join('; ')})`)
  }

  const pageConfigs = navigation.flatMap((section) => section.pages)
  const pages = await Promise.all(pageConfigs.map((config) => loadPage(sourceDir, config)))
  const bySlug = new Map(pages.map((page) => [page.slug, page]))
  const sections = navigation.map((section) => ({
    title: section.title,
    pages: section.pages.map((config) => bySlug.get(config.slug)!),
  }))

  return { pages, sections }
}
