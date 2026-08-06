# Ene documentation

The documentation site is built from Markdown in `source/` with the custom npm static-site
builder in this directory.

## Build locally

```bash
npm ci
npm run typecheck
npm run build
npm run serve
```

The preview is available at `http://127.0.0.1:4174/ene/`, matching the GitHub Pages project
subpath. Use `npm run dev` to rebuild automatically while editing.

Navigation and metadata are configured in `site.config.ts`. The generated site is written to
`dist/`.

## Deployment

`.github/workflows/docs.yaml` builds and deploys the documentation when a GitHub release is
created. It can also be started manually with `workflow_dispatch`; ordinary pushes do not
publish documentation. GitHub Pages must use **GitHub Actions** as its publishing source.
