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
- A sign-in expires after 12 hours without activity.

## Create a session

Select **+** in the sidebar (or **New session** on the empty screen):

- **Working directory:** type a path, browse folders, or pick a recent workspace. **New folder** in the browser creates a folder in the one shown; a typed path that does not exist offers to create it.
- **Name:** optional, as with `ene new NAME`.
- **Model / Persona / Reasoning:** CLI-equivalent choices, including the workspace's personas. Persona starts at `coder` and reasoning at the model's configured effort (else `high`), so both are always shown explicitly.
- **Conversation:** start fresh, or resume a saved conversation. Saved ones are grouped by date (newest first) and searchable by title, last request, or ID; live conversations cannot be selected twice.

The hub starts and attaches the worker, then opens it. The dialog remembers your last choices.

A worker has 60 seconds to start. If it fails or times out, the error names the startup step it reached and quotes its output.

## Sessions

The sidebar groups every live session on the machine:

| Group | Meaning | Action |
| --- | --- | --- |
| **Open here** | Attached by this hub | Click to switch |
| **Available** | Running, unattached | Click to attach |
| **In a terminal** | Owned by a terminal | Run `/detach` or Ctrl+D there first |

- **Title:** the sidebar, header, and browser tab show the session name, or the session ID if unnamed. Sidebar rows show the workspace folder under the title.
- **Scroll position:** each session keeps its place when you switch away; one that was following the latest output stays at the bottom.
- **Rename:** click the session name in the header; Enter saves (same as `/name <name>`), Escape cancels.
- **Fork:** `/fork [name]` opens the new session here and switches to it; the original stays open under **Open here**.
- **Detach:** the unlink icon in the session header or on an "Open here" sidebar row; one click. The worker keeps running.
- **Git branch:** in a Git workspace, a header chip next to the session name shows the current branch (or short commit when detached); it refreshes after each round.
- **End:** the power icon on a sidebar row; click twice to confirm. Terminal-owned sessions cannot be ended from the browser.
- **State icon:** spinner = working (model call or command); hourglass = idle in the foreground while background processes run or queued input remains; check = waiting for your input. A blue dot marks sessions that finished while you were elsewhere.
- **Sound:** a wind chime plays when a session gets that blue dot. Toggle it with the bell icon next to the theme button; the choice is remembered per browser. Browsers allow sound only after you click or type on the page once.
- **Hub name:** the sidebar header and browser tab title show the hub machine's hostname, so tabs from different hubs stay distinguishable.
- **Collapse:** the panel icon in the sidebar header (or Ctrl/Cmd+B) shrinks the sidebar to an icon rail; click a session there to switch or attach. The choice is remembered.
- **Resize:** drag the edge between the sidebar and the conversation; double-click it to reset. Widths are remembered.

## Conversation

- **Timeline:** messages, reasoning, and tool calls. A tool call holds its command output, diffs, and result.
- **History:** opening or reloading a session shows the newest turns; scrolling up loads earlier ones, back to the start of the conversation. Full detail covers the latest 2,000 events; before a marked point, only messages are kept, without tool activity.
- **Folding:** every step folds by its chevron. Results longer than 12 lines start folded; failures and running calls start open. Three or more consecutive tool calls fold into one row.
- **Long output:** a tool call keeps the newest 200,000 characters of its command output and says how many earlier lines it no longer shows.
- **Message times:** each message and notification shows when it was sent (replies: when they started), with the date for earlier days; hover for the full time. Sessions saved before message timestamps existed show none.
- **Reasoning:** folded; while streaming it previews the newest lines. Unfold it at any time to read the whole thought so far.
- **Links:** web URLs open in a new tab. File paths in replies (for example `src/app.py:42`) open in the file viewer; only paths that resolve to a readable file become links. Only the display changes, never the reply text.
- **Round summary:** next to the copy button under the reply that ends a round, a chip shows its time split into model and tool time, mean time to first token (TTFT), output speed (tokens/s), tokens, cache hit rate, and tool calls; click it for statistics.
- **Diffs:** click the file name to open the file.
- **Images:** a `read_image` call shows a thumbnail; click it to open the image in the file viewer. Unreadable or unsupported images show only the text result.
- **Reply images:** Markdown images with a local raster path (for example `![plot](out/fig.png)`) render inline. Remote, SVG, or unreadable images show their alt text.
- **Find:** Ctrl/Cmd+F; Enter / Shift+Enter step through matches in the loaded history.
- **Questions:** answer with a click or the choice's number key (when the composer is empty); Escape dismisses.

### Composer

- Enter sends; Shift+Enter adds a line. On touch devices, use the send button.
- `/` at the start: commands and skills. `@`: workspace files; lookup starts after a 50 ms typing pause. Up/Down select, Tab/Enter complete.
- Up/Down without suggestions: move between lines; on the first (or last) line, recall earlier (or later) input.
- Home/End: start or end of the current line; press again for the start or end of the message.
- `!` at the start runs the rest as a one-off shell command in the workspace, shown in the conversation. For an interactive shell, use the [Terminal](#terminal) panel.
- While the agent works, messages queue; the queued message can be pulled back for editing. Commands cannot join a queue.
- The queued chip shows a clock for a message that joins the running round and a list-end icon for a `/wait` message that runs after it, with any remaining delay in parentheses.
- Escape or the stop button stops the round.
- **Attachments:** drop files on the message box, paste them, or click the paperclip. See [attachments](#attachments).
- **Model:** the picker next to the send button shows the current model as `alias (model name)`; choosing another sends `/model <name>`. It is locked while a round runs.
- **Reasoning effort:** the picker beside it sends `/effort <level>` and applies from the next model request, even mid-round. It is hidden for models without effort control.
- **Status line:** above the composer while the agent works: activity, elapsed time, session tokens, and, while a reply streams, its time to first token (`TTFT 1.8s`) and estimated output speed (`~42 tok/s`).
- **Background processes:** an "N running" chip in the status line stays visible, even while idle, until every background process exits; click it to open Processes.

### Attachments

- Each file becomes a chip with upload progress; × removes it (and deletes the uploaded file). Send waits until every chip has finished.
- Sending appends `Attached: @.ene/uploads/<conversation>/<id>/<name>` to the message, so the agent can read the files with its file tools.
- Up to 10 files per message, 100 MB each. Spaces in names become `_`.
- **Cleanup:** attachments never sent are deleted when the session ends or the hub stops. Sent ones are kept for resumed conversations and swept after 7 days. `.ene/` is ignored by Git.

## Inspector

Open the side panel with the panel icon in the session header (it reopens the last tab), or jump to a tab from the context gauge (Statistics), the folder or Git branch chip (Files), or the "N running" chip above the composer (Processes):

| Panel | Contents |
| --- | --- |
| **Files** | A folder tree of the session workspace with Git-changed files marked, and a changed-only view. See [Files](#files). |
| **Terminal** | Interactive shells in the session workspace; see [Terminal](#terminal). |
| **Statistics** | Rounds, agent time, model and tool time, tokens (input / cached / output), speed (tokens/s and TTFT), context usage, and per-round charts. Click a round to jump to it. |
| **Processes** | Running and recently exited background processes with runtime, exit code, and latest output. Open one to view its output, or stop it (click twice). |
| **Info** | Model, context, reasoning, persona, skills, and IDs. |

- **Resize:** drag the panel's left edge, up to half the window; double-click it to reset. The conversation keeps a minimum readable width, and on narrow windows the panel floats over it instead.
- See [process output](#process-output) for the viewer opened from **Processes**.
- Statistics are recorded by the agent for every session, including terminal-started ones, and saved in its history: they survive resume and follow `/rewind` and `/fork`. Sessions saved before this feature have none.
- A running round counts in the totals and appears as a faded bar and highlighted row. It updates after each model response and tool batch.
- TTFT and tokens/s need streamed responses; rounds without them show "—". Speed counts output tokens (including reasoning) after the first token.

## Files

- **Tabs:** **Files** (the tree), **Changes** (Git-changed files, with their count), and **Search** (text search).
- **Tree:** click a folder to expand it in place; open folders are remembered per session. **Browse into folder** makes a folder the tree's root; the breadcrumbs above the tree lead back.
- **Sort:** the sort button cycles name, last modified, and size; folders stay first.
- **Keys:** Down from the filter enters the list; Up/Down move, Right/Left expand or collapse (Left on a file goes to its folder), Enter opens.
- **Mention:** hover a row and click `@` to insert its workspace-relative path into the message box.
- **Changes:** lists changed files in **Staged**, **Changes** (including partially staged), and **Untracked** groups, with lines added and removed. The caption shows commits ahead (↑) and behind (↓) the upstream branch.
- **Select:** Ctrl/Cmd+click toggles rows, Shift+click selects a range; Escape clears. Right-click a selected row to act on all of them.
- Listing, find, search, diffs, history, and downloads stay inside the workspace; opening a file link or image may preview any file the hub user can read. [Uploads](#uploading-and-downloading) are the only writes.

### File actions

Right-click a row, press the context-menu key, or click the `…` button that appears when hovering it. Rows in the tree, the changed-files view, find results, and text search results all have the menu.

| Action | Effect |
| --- | --- |
| Open, Open changes, File history | Open the viewer on the file, its diff against `HEAD`, or its Git history. |
| Reveal in file tree | Expand the tree to a find, search, or changed-file result. |
| Mention in message | Insert `@path`. |
| Ask to explain / review changes / write tests | Put a prompt naming the file in the message box; nothing is sent. |
| Copy relative path, Copy absolute path, Copy contents | Copy to the clipboard (contents: text files, first 512 KB). |
| Download | Save the file through the browser. On a folder or a selection: **Download as zip**. |
| Upload files here | On a folder: pick files to upload into it. |
| Open in terminal | Open a new [terminal](#terminal) in the folder (a file's parent). |

### Uploading and downloading

- **Upload:** drop files or folders on a folder row, the list (the open folder), or a breadcrumb. The toolbar's upload button picks files for the open folder.
- **Existing files:** a dialog offers **Replace**, **Skip existing**, or **Cancel**. Folders are never replaced.
- **Progress:** the **Uploads** list under the tree shows each file's progress, speed, and time left, with a cancel button; **Cancel all** stops the batch. Two files upload at a time.
- **Finished:** saved files stay listed with a check mark; `@` mentions one in the message box. × dismisses a row; the header's × closes the list.
- Uploads continue while you switch panels or sessions; closing the page asks first, since it cancels them.
- **Safety:** a file is written under a hidden temporary name and renamed into place only when complete. A cancelled or failed upload leaves nothing behind.
- **Downloads** use the browser's download manager, which shows progress and can cancel. Zips are built while they download, so they show no total size.

| Limit | Value |
| --- | --- |
| Upload size | 100 MB per file (a proxy may allow less) |
| Upload batch | 50 files, 1 GB |
| Zip download | 1,000 files, 2 GB before compression; symlinks leaving the workspace are skipped |

### File viewer

- **Views:** tabs under the path switch between **Changes** (a side-by-side diff against `HEAD`, unified on narrow screens), **File** (syntax highlighting, raster images), **Preview** and **Source** for Markdown, and **History** in a Git repository. A changed file opens on **Changes**.
- **Actions:** `@` mentions the file; `…` copies its path or contents, downloads it, or reloads it. Tools for the current view (find, wrap, change navigation) sit at the right of the tabs.
- **Diff navigation:** the arrow buttons or Alt+Up/Down jump between changed blocks.
- **History:** the file's last 100 commits, following renames. Click one to see what it changed in the file.
- **Line references:** click a line number (Shift+click extends), or select text, then **Mention** inserts `@path:12-20`. **Copy lines** copies them.
- **Find:** Ctrl/Cmd+F finds text in the source or in rendered Markdown (case-insensitive unless the query has capitals); Enter and Shift+Enter step through matches. Type `:120`, or press Ctrl/Cmd+G, to go to a line of the source.
- **Wrap:** the wrap button toggles long-line wrapping; the choice is remembered.
- Previews are capped: text shows the first 512 KB, highlighting stops above 200,000 characters, and images over 16 MB are not shown.

### Finding files and text

| Action | How |
| --- | --- |
| Find files | Type in the **Files** filter (or press Ctrl/Cmd+P). It filters the open folder and fuzzy-matches paths across the whole workspace; Enter opens the first match. |
| Search text | The **Search** tab in **Files** (or Ctrl/Cmd+Shift+F). Toggle match case, whole word, and regular expressions; the `…` button adds comma-separated include/exclude globs. Click a hit to open the file at that line. |

- Both skip ignored files and `.git`; text search does not follow symlinks.
- Text search needs `rg` (ripgrep) on the hub host and is search-only; ask the agent to make replacements.
- Limits keep large workspaces responsive: 50 path matches; 1,000 text matches across at most 300 files, 50 per file; files over 1 MB are skipped; searches stop after 5 seconds. A capped result says so, and a file with more than 50 matching lines shows `50+`.

## Terminal

The **Terminal** panel runs your login shell (`$SHELL -l`) on a pseudo-terminal, starting in the session workspace.

- **New:** **+** in the terminal tab bar; each tab is a separate shell. Close a tab with its **×**. **Open in terminal** in the [file actions](#file-actions) starts one in a workspace folder.
- **Persistence:** shells belong to the hub, not the browser. Closing the panel, switching sessions, reloading, or signing in again keeps them running.
- **Reconnecting:** replays the last 256 KB of output and restores terminal modes such as the alternate screen. A full-screen program (tmux, vim) is briefly resized so it redraws; inline ones (Claude Code, Codex) are not.
- **Tabs:** every open tab stays connected, so switching tabs is instant.
- **Exit:** an exited shell shows its exit code; **Restart** replaces it with a new shell.
- **Lifetime:** detaching or ending the session, the worker exiting, or stopping the hub closes its shells.
- **Keys:** a focused terminal receives every key, including the page shortcuts below. On macOS, **⌘←** / **⌘→** jump to the line's start / end and **⌘⌫** deletes to its start.
- **New lines:** **Shift+Enter** sends Ctrl+J, a new line in Claude Code and Codex prompts.
- **Browser keys:** browsers keep **Ctrl+W**, **Ctrl+T**, and **Ctrl+N**. Closing the page from a focused terminal asks first.
- **Copy and links:** programs can copy to the clipboard with OSC 52 (tmux `set-clipboard`, editors); clipboard reads are refused. URLs open in a new tab when clicked.
- **Environment:** `TERM=xterm-256color`. Variables naming the hub's own terminal (`TMUX`, `TERM_PROGRAM`, `CLAUDECODE`, …) are removed, so tmux and Claude Code start normally.
- **Resizing:** the shell is resized once, when a panel drag ends. Narrowing re-wraps lines at a shell prompt; while a program runs, lines are clipped instead so its redraw leaves no copies, and widening restores them.
- **Limits:** 8 shells per session, 32 per hub.
- **Typing ahead:** input that a busy program has not read yet waits for it, up to 1 MB.
- Linux and macOS hubs only.

## Process output

Open a process from **Processes** or from the link under its `start_process` call in the conversation.

- **Log:** the full log, tail first, following live output (including a progress line still being redrawn). Load earlier output from the top, filter lines, toggle wrapping, or copy the loaded text.
- **JSON lines:** lines holding a JSON object or array (JSONL output, optionally after a prefix such as a timestamp) are syntax-highlighted.
- **Conversation:** a [subagent](bundled-skills.md) opens as a read-only conversation: its task, replies, tool calls, and final result. Switch to **Log** for the raw output.
- Output follows live while scrolled to the bottom. The viewer fetches only new bytes, when the pushed process status reports growth; there is no polling.
- The last 20 exited processes stay listed. Reading requires the session to be attached here.

## Keyboard

| Keys | Action |
| --- | --- |
| Ctrl/Cmd+K | Search sessions (Enter opens or attaches the first match) |
| Ctrl/Cmd+B | Collapse or expand the sidebar (open or close it on phones) |
| Ctrl/Cmd+F | Find in conversation |
| Ctrl/Cmd+P | Find files in the workspace |
| Ctrl/Cmd+Shift+F | Search text in workspace files |
| Ctrl/Cmd+F, Ctrl/Cmd+G | In the file viewer: find in the file, go to a line |
| Alt+Up/Down | In the file viewer: previous or next change |
| Escape | Close dialogs and suggestions, dismiss a question, or stop the round |

On macOS the shortcuts use Cmd; Ctrl+B, Ctrl+F, Ctrl+K, and Ctrl+P keep their text-editing meaning.

## Connection handling

- Each tab keeps its sockets alive with heartbeats and reconnects automatically with backoff, immediately when the network or tab returns.
- While disconnected, a banner appears, sending is disabled, and drafts are kept (also across reloads).
- A message whose delivery was not confirmed is re-sent after reconnecting; the hub recognizes it and never delivers it twice.
- A message the agent refuses returns to the composer.
- A message or answer over the hub's 256 KB frame limit is refused before sending and stays where you typed it.
- A tab that falls too far behind the live output (for example on a slow link) restarts at the newest page instead of skipping events.
- Reloading or closing the tab keeps sessions attached. Stopping the hub releases them for terminals.

## Network and remote access

- **Treat the token as shell access:** signed-in browsers get an interactive shell as the hub's user, can read workspace files, and start agents in any directory. Terminals start in the workspace but are not confined to it.
- Use a long random token and run the hub only on trusted networks.
- For remote access, use a protected tunnel or reverse proxy.
- Behind a reverse proxy, forward the original `Host` header. Otherwise the hub refuses the page's connections, and the page and the hub's log say why.

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
