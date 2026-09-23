# Ene documentation

- Source: Markdown in `source/`.
- Builder: custom npm static site.
- Navigation and metadata: `site.config.ts`.
- Generated output: `dist/`.

## Build locally

```bash
npm ci
npm run typecheck
npm run build
npm run serve
```

- Preview: `http://127.0.0.1:4174/ene/` (matches the GitHub Pages subpath).
- Live rebuild: `npm run dev`.
- Writing style: short bullets, focused examples, and minimal prose; see `../AGENTS.md`.

## Deployment

- Workflow: `.github/workflows/docs.yaml`.
- Triggers: GitHub release creation or manual `workflow_dispatch`, not ordinary pushes.
- GitHub Pages publishing source: **GitHub Actions**.
