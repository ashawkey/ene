# Web UI

Start `ene hub` to create and manage live sessions from your browser.

## Architecture

- **Hub:** serves HTTP, authenticates browsers, and manages sessions.
- **Workers:** one detached process and working directory per session, shared with the terminal interface.
- **Ownership:** one terminal or hub per session, never both.
- **Tabs:** attached sessions appear as UI tabs; others remain in the sidebar.
- **Network:** binds to `0.0.0.0`, reachable by hostname or IP from other devices.

## Start the hub

Choose a private access token in `~/.ene.yaml`:

```yaml
ene_web_token: replace-with-a-long-random-secret
```

Start the hub in a dedicated terminal:

```bash
ene hub --web-port 8765
```

1. Open a printed URL (local IPv4 or fully qualified hostname) and enter the printed token.
2. Keep the hub terminal running while using the UI.

- Without `ene_web_token`, Ene generates a temporary token for that hub process.
- A second hub cannot use an occupied hub port.
- Login creates an httponly cookie. Logout or expiry closes that login's WebSocket connections across tabs; other authenticated browsers stay signed in.

## Create a session

Select **+ New session** in the sidebar to open the session dialog:

- **Working directory:** type or browse a path; recent-workspace shortcuts and a hidden-directory toggle are available.
- **Name:** optional, as with `ene new NAME`.
- **Model / Persona / Reasoning effort:** CLI-equivalent choices, including the selected workspace's personas.
- **Resume conversation:** continue a saved conversation; already-live conversations cannot be selected twice.

The hub starts and attaches the worker, then focuses its tab. Errors appear in the dialog.

## Attach and detach

The sidebar lists every live session on the machine:

- **Attach:** click a detached session.
- **Detach:** select **×** on its tab. The worker keeps running and becomes available elsewhere.
- **Terminal-owned:** marked `terminal`; run `/detach` or Ctrl+D there before attaching in the browser.
- **Hub-owned:** terminal attachment is refused; select **×** in the browser first.
- **Stop:** send `/exit` or `/quit`. The worker shuts down and leaves the list.

## Browser behavior

- **Activity:** conversation, tools, selections, queued input, and process status are visible per session.
- **Process dock:** stays above the composer; scrollable rows show ID, label, runtime, and latest output.
- **Queue:** one message per active round; refusals are reported, not silently dropped.

### Composer shortcuts

- `/` at an empty composer's start: suggest commands and skills.
- `@` at a word's start: search workspace paths and filenames.
- With suggestions open: type to filter, Up/Down to select, Tab/Enter to complete; Enter again sends.
- Without suggestions: Up/Down recalls browser-session input history, restoring the draft after the newest entry.

### Connection lifecycle

- Disconnect: show reconnecting status, preserve drafts, and disable actions until recovery.
- Reload or close the browser tab: sessions stay attached. Use **×** to detach.
- Stop the hub: release attachments; workers keep running and can be attached from a terminal.

## Network and remote access

- **Treat the token as shell access:** signed-in browsers can browse files and start agents in any directory.
- Use a long random token and run the hub only on trusted networks.
- For remote access, use a protected tunnel or reverse proxy.

Cloudflare Tunnel example:

```bash
# One-time authentication and tunnel creation.
cloudflared tunnel login
cloudflared tunnel create ene
cloudflared tunnel route dns ene ene.example.com
```

Run the hub and tunnel in separate terminals:

```bash
ene hub --web-port 8765
```

```bash
cloudflared tunnel run --url http://127.0.0.1:8765 ene
```

Then open `https://ene.example.com` and sign in with the Ene Web UI token.
