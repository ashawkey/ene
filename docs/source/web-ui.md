# Web UI

Ene's optional Web UI mirrors terminal sessions in a browser. 
It does not replace the terminal agent: a hub serves the browser, while each agent continues to run in its own working directory and terminal process.

## Architecture

The Web UI uses a **hub + agents** design:

- One `ene hub` process owns the HTTP port, serves the UI, authenticates browsers, and multiplexes connected agents.
- Each ordinary `ene` process checks for a local hub at startup and links to it automatically.
- Terminal and browser input operate the same live session. Agents continue terminal-only when no hub is available.
- Agents from different directories and terminals appear as separate browser tabs.

The hub binds to `127.0.0.1`, so it is local-only unless you explicitly add a tunnel or proxy.

## Start the hub

Choose a private access token in `~/.ene.yaml`:

```yaml
ene_web_token: replace-with-a-long-random-secret
```

Start the hub in a dedicated terminal:

```bash
ene hub --web-port 8765
```

The command prints the URL and access token. If `ene_web_token` is absent, Ene generates a new token for that hub process.

Open the printed URL—normally <http://127.0.0.1:8765>—and enter the token. Keep the hub terminal running while using the Web UI.

## Connect agents

Start each agent in its own terminal after the hub is running:

```bash
# Terminal 2
cd ~/projects/project-a
ene --model gpt
```

```bash
# Terminal 3
cd ~/projects/project-b
ene --model gpt
```

The hub writes discovery information to `~/.ene/hub.json`. Agents on the same machine read this file and verify that the recorded hub is reachable. 
A stale file from a crashed process is ignored. Agents that were already running before the hub started must be restarted to connect.

## Browser behavior

The browser shows the same conversation, tool activity, prompts, and process status as the terminal. Input from either interface is sent to the same agent session.

If the browser loses its hub connection, it shows a reconnecting state, preserves draft input, and disables actions until the connection recovers. Stopping the hub does not stop terminal agents; they continue running without the browser connection.

## Remote access

Because the hub listens only on loopback, use an authenticated tunnel or reverse proxy rather than exposing it directly. 
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