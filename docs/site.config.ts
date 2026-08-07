export interface NavigationPage {
  slug: string
  label?: string
}

export interface NavigationSection {
  title: string
  pages: readonly NavigationPage[]
}

export const siteConfig = {
  title: 'Ene Documentation',
  description: 'Documentation for Ene, a terminal-first AI coding agent.',
  navigation: [
    {
      title: 'Getting Started',
      pages: [{ slug: '' }],
    },
    {
      title: 'Skills',
      pages: [{ slug: 'skills' }, { slug: 'bundled-skills' }, { slug: 'library' }],
    },
    {
      title: 'Usage',
      pages: [
        { slug: 'commands' },
        { slug: 'tools' },
        { slug: 'personas' },
        { slug: 'web-ui' },
      ],
    },
  ] satisfies readonly NavigationSection[],
}
