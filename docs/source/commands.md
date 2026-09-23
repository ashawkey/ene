# CLI reference

## Start a chat

```bash
ene                           # same as ene new
ene api-refactor --model gpt   # optional name and model
```

The default model is the first entry in `~/.ene.yaml`.

| Option | Meaning |
|---|---|
| `--model ALIAS` | Select a model alias or unique alias prefix from `~/.ene.yaml`; exact matches take priority. |
| `--persona NAME` | Start with a discovered persona. |
| `--verbose` | Show detailed output. |
| `--stream` / `--no-stream` | Enable or disable response-token streaming; streaming is the default. |
| `--reasoning-effort LEVEL` | Override effort with `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`. |

## Top-level commands

| Command | Purpose |
|---|---|
| `ene [NAME] [OPTIONS]` / `ene new [NAME] [OPTIONS]` | Start a new, optionally named session. |
| `ene resume [SESSION_ID] [OPTIONS]` (`r`) | Resume a specific conversation, or omit the ID to choose interactively. |
| `ene models` | List configured model aliases and resolved defaults. |
| `ene list` (`ls`, `l`) | List live sessions and whether each is working or done. |
| `ene attach [NAME_OR_ID]` (`a`) | Attach by unique name or ID prefix; omit it to open the picker. |
| `ene kill [NAME_OR_ID]` (`k`) | Terminate one live session, or omit the identifier to select multiple sessions interactively. |
| `ene status` | Show disk usage for entries under the current project's `.ene/`. |
| `ene clean [--history] [ENTRY ...]` | Remove disposable project state or selected entries; `--history` also removes sessions. |
| `ene hub --web-port PORT` | Start the [Web UI](web-ui.md); default port: `8765`. |
| `ene update` | Update Ene from its editable checkout or reinstall the latest GitHub source. |
| `ene lib --help` | Manage the Git-backed library of skills and personas. |

See [Library](library.md) for `ene lib` workflows.

### Attach and replay

- Replay shows the full conversation's prompts and final assistant responses, with omitted-message counts in place. `/resume` uses the same display.
- Picker states: `● working`, `✓ done · needs review`, and `○ waiting`. Completed sessions appear first, newest status change first.
- One owner per session: a terminal attachment waits briefly for another terminal to release it; Web UI ownership is refused immediately. Detach in the browser first.

### Clean project state

- Check `ene status` before deleting state you may need.
- `ene clean` removes tool results, scratch data, caches, and unrecognized entries.
- It preserves instructions, authored skills/personas, sessions, batch results, and orchestrator state.
- `--history` also removes saved sessions.
- **Explicit entry names are deleted regardless of these defaults.**

## Interactive slash commands

| Command | Description |
|---------|-------------|
| `/help` | Show help message for all slash commands |
| `/context [user\|assistant\|id]` | List clipped messages, filter by role, or inspect one in full; `-1` is newest |
| `/system_prompt` | Print the current full system prompt |
| `/compact` | Force context compaction via LLM summarization |
| `/recap` | Summarize the conversation's task in one sentence, focusing on user requests |
| `/export <path/filename>` | Export the last assistant response as UTF-8 text; relative paths are resolved from the working directory |
| `/continue` | Resume an unfinished round without a new user message; warn if already complete |
| `/usage` | Show token usage for this session |
| `/ps [label\|process-id] [tail-chars]`; `/ps stop <label\|process-id>` | List managed background processes, inspect recent output, or stop one process |
| `/agents` | List Ene agents working in this workspace, including this session |
| `/model [name]` | Show or switch LLM model mid-session; accepts an exact alias or unique alias prefix |
| `/login [provider\|model-alias]` | Authenticate an OAuth provider; defaults to the current provider |
| `/logout [provider\|model-alias]` | Remove stored OAuth credentials |
| `/auth [provider\|model-alias]` | Show authentication status |
| `/effort [level]` | Show or set reasoning effort (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`) |
| `/rewind` | In an attached terminal, restore conversation, tracked files, or both at an earlier prompt; edit and branch |
| `/fork [name]` | In an attached terminal, fork an earlier prompt into a new session; leave tracked files unchanged |
| `/skills` | List installed skills; `/skills reload` to re-scan; `/skills <name>` to load one |
| `/<skill-name> [task]` | Invoke a skill for an optional task; without one, run its declared default or ask what to do |
| `/persona` | List personas; `/persona <name>` to switch (restarts the conversation) |
| `/wait <duration> <prompt>` | Queue a prompt for later using seconds, minutes, or hours, e.g. `/wait 1h check whether the other agent finished` |
| `/detach` | Detach the terminal without stopping the live session (also Ctrl+D) |
| `/switch` | Switch live sessions (also Ctrl+S); Cancel or Ctrl+C keeps the current attachment |
| `/new [name]` | Detach and start a new live session, optionally with a name |
| `/clear` | Stop this session and attach to a new unnamed one; the old conversation remains resumable |
| `/resume [session_id]` | In an attached terminal, save this conversation and activate a stopped one; omit the ID for a named picker |
| `/name [name]` | Show or set the live session name; use `/name` to inspect it |
| `/exit` or `/quit` | Exit the agent and stop the live session (also Ctrl+K) |

- `/context` renders assistant content as Markdown when inspecting a message.
- Output-limit, missing-terminal, and empty responses continue automatically. Truncation mid-tool-call stops instead; those calls are recorded as never executed.

### Summary models

Set aliases in `~/.ene.yaml` to use smaller models for summaries:

```yaml
recap_model: fast    # model used by /recap
summary_model: fast  # model used by automatic and /compact compaction
```

- Values name entries under `openai`; omitted settings use the active model.
- Both operations count toward token usage. Recaps are not added to conversation history.

## Bash shortcut

Prefix a command with `!` to run it directly without involving the model:

```text
!ls -la
!git diff
```

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Escape` → `Enter` | Insert a newline |
| `Ctrl+Z` / `Cmd+Z` (macOS, see below) | Undo an edit in the current input (does not retract sent messages) |
| `Ctrl+R` | Search input history backward |
| `Ctrl+S` (persistent session) | Switch sessions |
| `Ctrl+C` (non-empty prompt) | Clear the current input, even while Ene is working; do not cancel the operation |
| `Ctrl+C` (empty prompt, idle, twice) | Exit the agent |
| `Ctrl+C` (empty prompt, while Ene is working) | Cancel the current operation |
| `Esc` (while Ene is working) | Cancel the current operation, regardless of input |
| `Up` (empty prompt with a queued message) | Move the queued message back into the editor |

- Undo affects only unsent input.
- On macOS, `Ctrl+Z` works directly; `Cmd+Z` requires terminal forwarding as CSI-u (`ESC [ 122 ; 9 u`) or a mapping to `Ctrl+Z`.
- In iTerm2, map `Cmd+Z` to **Send Hex Code** → `0x1a`. Ene cannot receive shortcuts intercepted by the terminal or OS.

## Working while a round is active

- Submit one queued message, shown as `pending: … · runs next`; it starts after the current round.
- Press `Up` at an empty prompt to edit the queued message.
- Read-only commands such as `/usage`, `/context`, `/ps`, `/agents`, `/sa`, and `/auth` run immediately, as does `/name [name]`.
- Commands that change conversation or provider state wait.

## Concurrent agents in one workspace

- Agents discover peers through `.ene/agents/`; `/agents` lists them.
- A once-per-session conversation notice tells agents to expect unrelated edits and transient test/build failures. It leaves the system prompt and its cache unchanged.
- **Records are advisory, not locks.** Keep concurrent tasks on separate files.
- Dead-process records are removed when noticed. `ene clean` may delete records; live agents republish them next round.

## Tool execution

- Tools run automatically: no permission modes, confirmation prompts, or command screening.
- Ene is **not a security boundary**. Use an OS sandbox or container to constrain commands.
- Review tasks before handing them to an autonomous agent.
