# Web UI

Ene's Web UI turns `ene hub` into a standalone entry point: start the hub, then create, attach to, and detach from live sessions entirely in the browser.

## Architecture

The Web UI uses a **hub + live worker** design:

- One `ene hub` process owns the HTTP port, serves the UI, authenticates browsers, and manages live sessions.
- Each session runs in its own detached worker process and working directory, exactly as it does for the terminal.
- The hub attaches to a worker the same way a terminal does. A session has **one owner at a time**: either a terminal or the hub, never both.
- Every attached session appears as a separate browser tab. Sessions owned by a terminal, or not attached at all, are listed separately in the sidebar.

The hub binds to `0.0.0.0`, so devices on the same network can reach it using the machine's hostname or IP address.

## Start the hub

Choose a private access token in `~/.ene.yaml`:

```yaml
ene_web_token: replace-with-a-long-random-secret
```

Start the hub in a dedicated terminal:

```bash
ene hub --web-port 8765
```

The command prints browser URLs using both the machine's local IPv4 address and fully qualified domain name (FQDN), followed by the access token. If `ene_web_token` is absent, Ene generates a temporary token for that hub process. Starting a second hub on a port that already has one is refused.

Open either printed URL—such as `http://192.168.1.25:8765` or `http://ene.example.com:8765`—and enter the token. A successful login creates an httponly browser session cookie; signing out invalidates it. Keep the hub terminal running while using the Web UI.

## Create a session

Select **+ New session** in the sidebar to open the session dialog:

- **Working directory** — type a path or browse the filesystem. Recently used workspaces are offered as shortcuts, and hidden directories can be shown with a toggle.
- **Name** — an optional live-session name, as with `ene new NAME`.
- **Model**, **Persona**, **Reasoning effort** — the same choices as the corresponding command-line flags. Personas are read from the selected workspace, so its project personas are included.
- **Resume conversation** — optionally continue a saved conversation from that workspace instead of starting a new one. Conversations that are already live cannot be selected twice.

The hub starts the worker, attaches to it, and focuses the new tab. Failures—an unknown model, a duplicate session name—are reported in the dialog.

## Attach and detach

The sidebar lists every live session on the machine:

- Sessions owned by this hub appear as tabs with a state dot and a **×** control.
- Detached sessions can be attached with a single click.
- Sessions attached in a terminal are marked `terminal` and cannot be selected. Detach them there first (`/detach`, or `Ctrl+D`).

Selecting **×** detaches the session: the worker keeps running and the tab disappears, leaving the session available to a terminal or another browser. Detaching never stops a session.

To stop a session, send `/exit` (or `/quit`) in the composer. The worker shuts down and the session leaves the list.

Attaching from a terminal to a session the hub owns is refused immediately with a message pointing back to the Web UI; run **×** in the browser first.

## Browser behavior

The browser shows the conversation, tool activity, interactive selections, queued message, and process status of every attached session. Only one message can be queued while a round is active; a message the agent refuses is reported rather than silently dropped.

Typing `/` at the start of an empty composer opens slash-command suggestions, including discovered skills. Continue typing to filter them, use Up/Down to select, and press Tab or Enter to complete; press Enter again to run the completed command.

If the browser loses its hub connection, it shows a reconnecting state, preserves draft input, and disables actions until the connection recovers. Reloading the page or closing the tab does not detach a session—only **×** does. Stopping the hub releases its attachments, leaving every session running and available to a terminal.

## Network and remote access

The hub listens on all network interfaces for access from trusted devices on the same network. Browser access requires the Ene token, but a signed-in browser can browse the filesystem and start agents in any directory, so treat the token as equivalent to shell access. Use a long random token and only run the hub on trusted networks.

For access beyond the local network, use a tunnel or reverse proxy and protect it as well.

For example, with Cloudflare Tunnel:

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
