# Web UI

Start `ene hub` to create and manage live sessions from your browser.

## Architecture

- **Hub:** serves HTTP, authenticates browsers, and manages sessions.
- **Workers:** one detached process and working directory per session, shared with the terminal interface.
- **Ownership:** one terminal or hub per session, never both.
- **Sessions:** attached sessions open in the browser; others remain available in the sidebar.
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

Select **+** in the sidebar (or **New session** on the empty screen):

- **Working directory:** type a path, browse folders, or pick a recent workspace.
- **Name:** optional, as with `ene new NAME`.
- **Model / Persona / Reasoning:** CLI-equivalent choices, including the workspace's personas.
- **Resume:** continue a saved conversation; live conversations cannot be selected twice.

The hub starts and attaches the worker, then opens it. The dialog remembers your last choices.

## Sessions

The sidebar groups every live session on the machine:

| Group | Meaning | Action |
| --- | --- | --- |
| **Open here** | Attached by this hub | Click to switch |
| **Available** | Running, unattached | Click to attach |
| **In a terminal** | Owned by a terminal | Run `/detach` or Ctrl+D there first |

- **Rename:** click the session name in the header; Enter saves (same as `/name <name>`), Escape cancels.
- **Detach:** the unlink icon in the session header. The worker keeps running.
- **Git branch:** in a Git workspace, a header chip shows the current branch (or short commit when detached); it refreshes after each round.
- **End:** the power icon on a sidebar row; click twice to confirm. Terminal-owned sessions cannot be ended from the browser.
- **State icon:** spinner = working (model call or command); hourglass = idle in the foreground while background processes run or queued input remains; check = waiting for your input. A blue dot marks sessions that finished while you were elsewhere.
- **Hub name:** the sidebar header and browser tab title show the hub machine's hostname, so tabs from different hubs stay distinguishable.
- **Collapse:** the panel icon in the sidebar header (or Ctrl/Cmd+B) shrinks the sidebar to an icon rail; click a session there to switch or attach. The choice is remembered.

## Conversation

- **Timeline:** messages, reasoning, and tool calls. A tool call holds its command output, diffs, and result.
- **Folding:** every step folds by its chevron. Results longer than 12 lines start folded; failures and running calls start open. Three or more consecutive tool calls fold into one row.
- **Round summary:** next to the copy button under the reply that ends a round, a chip shows its time, tokens, cache hit rate, and tool calls; click it for statistics.
- **Diffs:** click the file name to open the file.
- **Find:** Ctrl/Cmd+F; Enter / Shift+Enter step through matches.
- **Questions:** answer with a click or the choice's number key (when the composer is empty); Escape dismisses.

### Composer

- Enter sends; Shift+Enter adds a line. On touch devices, use the send button.
- `/` at the start: commands and skills. `@`: workspace files. Up/Down select, Tab/Enter complete.
- Up/Down without suggestions: recall earlier input.
- Shell mode (terminal button): input runs as a `!` shell command in the workspace; Up/Down recall earlier shell commands. Backspace in an empty composer or the button switches back. The mode is remembered per session.
- While the agent works, messages queue; the queued message can be pulled back for editing. Commands cannot join a queue.
- Escape or the stop button stops the round.

## Inspector

Open the side panel with the panel icon in the session header (it reopens the last tab), or jump to a tab from the context gauge (Statistics), the folder or Git branch chip (Files), or the "N running" chip shown while background processes run (Tasks):

| Panel | Contents |
| --- | --- |
| **Files** | Browse the session workspace; Git-changed files are marked, with a changed-only view. A changed file opens as a side-by-side diff against `HEAD` (unified on narrow screens); toggle to the file preview (syntax highlighting, rendered Markdown, raster images) or mention it in the composer. |
| **Statistics** | Rounds, agent time, tokens (input / cached / output), tool calls, context usage, and per-round charts. Click a round to jump to it. |
| **Tasks** | Background processes with runtime and latest output; show a log (`/ps <id>`) or stop one (click twice). |
| **Info** | Model, context, reasoning, persona, skills, and IDs. |

- File browsing is read-only and confined to the workspace; paths outside it, including through symlinks, are refused.
- Statistics are recorded by the agent for every session, including terminal-started ones, and saved in its history: they survive resume and follow `/rewind` and `/fork`. Sessions saved before this feature have none.

## Keyboard

| Keys | Action |
| --- | --- |
| Ctrl/Cmd+K | Search sessions (Enter opens or attaches the first match) |
| Ctrl/Cmd+B | Collapse or expand the sidebar (open or close it on phones) |
| Ctrl/Cmd+F | Find in conversation |
| Escape | Close dialogs and suggestions, dismiss a question, or stop the round |

## Connection handling

- Each tab keeps its sockets alive with heartbeats and reconnects automatically with backoff, immediately when the network or tab returns.
- While disconnected, a banner appears, sending is disabled, and drafts are kept (also across reloads).
- A message whose delivery was not confirmed is re-sent after reconnecting; the hub recognizes it and never delivers it twice.
- A message the agent refuses returns to the composer.
- Reloading or closing the tab keeps sessions attached. Stopping the hub releases them for terminals.

## Network and remote access

- **Treat the token as shell access:** signed-in browsers can read workspace files and start agents in any directory.
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
