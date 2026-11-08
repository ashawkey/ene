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
ene hub --port 8765
```

1. Open a printed URL (local IPv4 or fully qualified hostname) and enter the printed token.
2. Keep the hub terminal running while using the UI.

- `--port` defaults to `8765`.
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

- **Activity:** conversation, tools, selections, queued input, and process status are visible per session. The [model marker](commands.md#model-status-markers) stays first in the status bar.
- **Tool runs:** three or more consecutive tool calls fold into one summary row once the agent moves on; click to expand.
- **Copy:** hover a message or code block and select **⧉**.
- **Jump to latest:** appears when you scroll away from the newest output.
- **Tab title:** shows how many sessions finished and await review.
- **Queue:** follow-up messages append to the pending one; commands cannot join it. Refusals are reported, not silently dropped.

### Background tasks

- The status bar above the composer summarizes the session's background processes; select **Details** or the top-bar **⚙** (badge = process count).
- The task panel lists each process with ID, label, live runtime, and latest output.
- **Show log:** runs `/ps <id>`. **Stop:** click twice to run `/ps stop <id>`.

### Search

- **Conversation:** Ctrl/Cmd+F or **⌕** searches messages, tool calls, and diffs. Enter/Shift+Enter step through matches; folded blocks open to show them.
- **Sessions:** Ctrl/Cmd+K focuses the sidebar filter (name, last message, directory, model). Enter opens the first match, attaching a free session.

### Composer shortcuts

- `/` at an empty composer's start: suggest commands and skills.
- `@` at a word's start: search workspace paths and filenames.
- With suggestions open: type to filter, Up/Down to select, Tab/Enter to complete; Enter again sends.
- Without suggestions: Up/Down recalls browser-session input history, restoring the draft after the newest entry.
- Escape while the agent works: stop the round, as in the terminal.
- Prompts: press a choice's number key to answer; Escape or **Cancel** dismisses, as in the terminal.

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
ene hub --port 8765
```

```bash
cloudflared tunnel run --url http://127.0.0.1:8765 ene
```

Then open `https://ene.example.com` and sign in with the Ene Web UI token.
