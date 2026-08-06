import type { NavigationSectionNode } from '../site/content.ts'

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function rootPrefix(depth: number): string {
  return depth === 0 ? './' : '../'.repeat(depth)
}

export function hrefTo(slug: string, fromDepth: number): string {
  const prefix = rootPrefix(fromDepth)
  return slug ? `${prefix}${encodeURIComponent(slug)}/` : prefix
}

const CHEVRON =
  '<svg class="nav-chevron" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
  '<path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.75" ' +
  'stroke-linecap="round" stroke-linejoin="round"/></svg>'

export function renderTree(
  sections: readonly NavigationSectionNode[],
  current: string
): string {
  const depth = current ? 1 : 0
  const groups = sections
    .map((section, index) => {
      const containsCurrent = section.pages.some((page) => page.slug === current)
      const items = section.pages
        .map((page) => {
          const selected = page.slug === current
          return (
            '<li class="nav-item">' +
            `<a class="nav-link${selected ? ' is-current' : ''}" ` +
            `href="${escapeHtml(hrefTo(page.slug, depth))}"` +
            `${selected ? ' aria-current="page"' : ''}>${escapeHtml(page.navLabel ?? page.title)}</a>` +
            '</li>'
          )
        })
        .join('')

      return (
        '<li class="nav-item nav-item--folder">' +
        `<details class="nav-folder" open data-path="section-${index}">` +
        `<summary class="nav-summary">${CHEVRON}` +
        `<span class="nav-link nav-link--folder${containsCurrent ? ' is-current-section' : ''}">` +
        `${escapeHtml(section.title)}</span></summary>` +
        `<ul class="nav-list">${items}</ul></details></li>`
      )
    })
    .join('')

  return `<ul class="nav-list nav-list--root">${groups}</ul>`
}
