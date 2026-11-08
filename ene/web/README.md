# ene Web UI

React + TypeScript app built with Vite. The hub (`ene hub`) serves the generated `dist/`; Node.js is needed only to develop or rebuild it.

## Development

```bash
ene hub            # serves the UI on :8765 and prints the token
cd ene/web
npm install
npm run dev        # proxies /api (HTTP + WebSocket) to 127.0.0.1:8765
```

## Checks

```bash
npm test
npm run typecheck
npm run build      # writes dist/; commit it (package builds do not run Node)
```

## Layout

| Path | Role |
| --- | --- |
| `src/lib/protocol.ts` | Wire types shared with `ene/hub/server.py` |
| `src/lib/session.ts` | Pure reducer: server frames → timeline + live state + signals |
| `src/lib/useSession.ts` | Per-session socket, draft, and exactly-once outbox |
| `src/lib/socket.ts` | Reconnecting WebSocket (heartbeat, backoff, online/visibility) |
| `src/components/` | Shell, timeline, composer, inspector (files, stats, tasks, info) |

## Reliability rules

- Every submit carries an `action_id`; unacknowledged submits are re-sent after a reconnect and the hub drops duplicates.
- A rejected submit or withdrawn queued message returns its text to the composer.
- Drafts persist per session in `localStorage`.
- Markdown never renders raw HTML; no inline scripts, so the hub's CSP stays strict.
