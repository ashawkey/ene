# CLI reference

## Start a chat

`ene` is shorthand for `ene new`. Both accept an optional session name and use the first model configured in `~/.ene.yaml` by default:

```bash
ene
ene api-refactor --model gpt
ene new
ene new api-refactor --model gpt
```

Session options are:

| Option | Meaning |
|---|---|
| `--model ALIAS` | Select a model alias from `~/.ene.yaml`. |
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
| `ene attach [NAME_OR_ID]` (`a`) | Attach to a live session and replay its full conversation as prompts and final assistant responses, with omitted-message counts interleaved in their original positions; a unique name or ID prefix is accepted, and omitting the identifier opens an interactive picker. The picker marks sessions as `● working`, `✓ done · needs review`, or `○ waiting`, with completed sessions first by newest status change. Only one terminal may attach to a session at a time. |
| `ene kill [NAME_OR_ID]` (`k`) | Terminate one live session, or omit the identifier to select multiple sessions interactively. |
| `ene status` | Show disk usage for entries under the current project's `.ene/`. |
| `ene clean [--history] [ENTRY ...]` | Remove disposable project state or selected entries; `--history` also removes sessions. |
| `ene hub --web-port PORT` | Run the shared Web UI hub; the default port is `8765`. |
| `ene update` | Update Ene from its editable checkout or reinstall the latest GitHub source. |
| `ene lib --help` | Manage the Git-backed library of skills and personas. |

A default `ene clean` removes disposable state such as tool results, scratch data, caches, and other unrecognized entries. It preserves project instructions, authored skills and personas, conversation sessions, batch results, and orchestrator state. Use `ene clean --history` to also remove saved conversation sessions. Naming entries explicitly removes those entries regardless of the defaults. Check `ene status` before cleaning state you may need.

See [Library](library.md) for the complete `ene lib` workflow.

## Interactive slash commands

The agent supports the following slash commands while chatting:

| Command | Description |
|---------|-------------|
| `/help` | Show help message for all slash commands |
| `/context [user\|assistant\|id]` | List all context messages (one clipped line each), filter the list by user or assistant role, or show one message in full (assistant content is rendered as Markdown). A negative id counts back from the newest message, so `/context -1` shows the last one |
| `/system_prompt` | Print the current full system prompt |
| `/compact` | Force context compaction via LLM summarization |
| `/recap` | Summarize the conversation's task in one sentence, focusing on user requests |
| `/export <path/filename>` | Export the last assistant response as UTF-8 text; relative paths are resolved from the working directory |
| `/continue` | Resume an unfinished round without adding a user message; warns if the last round is complete (output-limit, missing-terminal, and empty responses continue automatically, except a response truncated mid tool call, whose calls are answered as never executed so the history stays valid) |
| `/usage` | Show token usage for this session |
| `/ps [label\|process-id] [tail-chars]`; `/ps stop <label\|process-id>` | List managed background processes, inspect recent output, or stop one process |
| `/agents` | List Ene agents working in this workspace, including this session |
| `/model [name]` | Show or switch LLM model mid-session |
| `/login [provider\|model-alias]` | Authenticate an OAuth provider; defaults to the current provider |
| `/logout [provider\|model-alias]` | Remove stored OAuth credentials |
| `/auth [provider\|model-alias]` | Show authentication status |
| `/effort [level]` | Show or set reasoning effort (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`) |
| `/rewind` | From an attached terminal, preview an earlier prompt boundary; restore conversation, tracked files, or both; then edit the restored prompt and branch |
| `/fork [name]` | From an attached terminal, pick an earlier prompt boundary and continue its conversation in a new, optionally named session without changing tracked files |
| `/skills` | List installed skills; `/skills reload` to re-scan; `/skills <name>` to load one |
| `/<skill-name> [task]` | Invoke a skill for an optional task; without one, run its declared default or ask what to do |
| `/persona` | List personas; `/persona <name>` to switch (restarts the conversation) |
| `/wait <duration> <prompt>` | Queue a prompt for later using seconds, minutes, or hours, e.g. `/wait 1h check whether the other agent finished` |
| `/detach` | Detach the terminal without stopping the live session (also Ctrl+D) |
| `/switch` | Detach and choose another live session (also Ctrl+S); choose Cancel or press Ctrl+C to return to the current session |
| `/new [name]` | Detach and start a new live session, optionally with a name |
| `/resume [session_id]` | From an attached terminal, save the current conversation, activate a stopped one, and replay its full conversation as prompts and final assistant responses, with omitted-message counts interleaved in their original positions (bare `/resume` picks interactively and shows saved names) |
| `/name [name]` | Show or set the live session name; use `/name` to inspect it |
| `/exit` or `/quit` | Exit the agent and stop the live session (also Ctrl+K) |




To use smaller models for summaries, set model aliases in `~/.ene.yaml`:

```yaml
recap_model: fast    # model used by /recap
summary_model: fast  # model used by automatic and /compact compaction
```

Both values refer to entries under `openai`. If either setting is omitted, that operation uses the active chat model. Recaps and compaction summaries count toward session token usage; recaps are not added to conversation history.

## Bash shortcut

Prefix a command with `!` to run it directly without involving the model:

```
!ls -la
!git diff
```

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Escape` → `Enter` | Insert a newline |
| `Ctrl+C` (non-empty prompt) | Clear the current input |
| `Ctrl+C` (empty prompt, twice) | Exit the agent |
| `Ctrl+C` / `Esc` (while Ene is working) | Cancel the current operation |
| `Up` (empty prompt with a queued message) | Move the queued message back into the editor |

## Working while a round is active

You may submit one message while Ene is working; it is shown as `pending: … · runs next` and starts after the current round. Press `Up` at an empty prompt to move it back into the editor. Commands that do not change conversation or provider state, such as `/usage`, `/context`, `/ps`, `/agents`, `/sa`, and `/auth`, run immediately. `/name [name]` also runs immediately because it changes only session metadata; commands that change conversation state wait for the current round.

## Concurrent agents in one workspace

Several Ene agents may work in the same directory at once — separate terminals, live sessions, or subagents. Each one publishes a small record under `.ene/agents/` and reads its peers' records from there, so they can tell they are not alone.

The first time an agent sees a peer, it receives one short conversation message stating that other agents are working in the workspace and that unrelated edits and transient test or build failures are expected rather than worth investigating. The notice appears once per session, not once per round, and the system prompt is left untouched so the provider's prompt cache stays valid. Use `/agents` to see the current list at any time.

The records are advisory: they carry no locks and grant no exclusivity, so agents that write the same files still conflict. Keep concurrent tasks on disjoint files. A record whose process is gone is removed by the next agent that notices it, so a force-killed session leaves nothing behind, and `ene clean` may delete `.ene/agents/` at any time because live agents republish their records on the next round.

## Tool execution

All tools execute automatically; Ene has no permission modes, confirmation prompts, or command screening. It is not a security boundary and does not attempt to contain what a model runs. Use an OS-level sandbox or container when commands must be constrained, and review a task before handing it to an autonomous agent.
