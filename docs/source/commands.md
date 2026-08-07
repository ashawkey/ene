# CLI reference

## Start a chat

Running `ene` without a subcommand starts an interactive chat with the first model configured in `~/.ene.yaml`:

```bash
ene
ene --model gpt
```

The explicit form is equivalent:

```bash
ene chat --model gpt
```

Chat options are:

| Option | Meaning |
|---|---|
| `--model ALIAS` | Select a model alias from `~/.ene.yaml`. |
| `--persona NAME` | Start with a discovered persona. |
| `--verbose` | Show detailed output. |
| `--stream` / `--no-stream` | Enable or disable response-token streaming; streaming is the default. |
| `--reasoning-effort LEVEL` | Override effort with `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`. |
| `--resume [SESSION_ID]` | Resume a specific session, or omit the ID to choose interactively. |

## Top-level commands

| Command | Purpose |
|---|---|
| `ene chat [OPTIONS]` | Start an interactive chat. |
| `ene list` | List configured model aliases and resolved defaults. |
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
| `/context [id]` | List context messages, or show one message's exact content and metadata in full |
| `/system_prompt` | Print the current full system prompt |
| `/compact` | Force context compaction via LLM summarization |
| `/recap` | Summarize the conversation's task in one sentence, focusing on user requests |
| `/export <path/filename>` | Export the last assistant response as UTF-8 text; relative paths are resolved from the working directory |
| `/continue` | Resume an unfinished round without adding a user message; warns if the last round is complete (output-limit, missing-terminal, and empty responses continue automatically, except a response truncated mid tool call, whose calls are answered as never executed so the history stays valid) |
| `/usage` | Show token usage for this session |
| `/ps [process-id]` | List managed background processes, or show one process with recent output |
| `/model [name]` | Show or switch LLM model mid-session |
| `/login [provider\|model-alias]` | Authenticate an OAuth provider; defaults to the current provider |
| `/logout [provider\|model-alias]` | Remove stored OAuth credentials |
| `/auth [provider\|model-alias]` | Show authentication status |
| `/effort [level]` | Show or set reasoning effort (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or `max`) |
| `/rewind` | Preview an earlier prompt boundary; restore conversation, tracked files, or both; then edit the restored prompt and branch |
| `/skills` | List installed skills; `/skills reload` to re-scan; `/skills <name>` to load one |
| `/<skill-name> [task]` | Invoke a skill for an optional task; without one, run its declared default or ask what to do |
| `/persona` | List personas; `/persona <name>` to switch (restarts the conversation) |
| `/wait <duration> <prompt>` | Queue a prompt for later using seconds, minutes, or hours, e.g. `/wait 1h check whether the other agent finished` |
| `/clear` | Save the current session, stop its managed processes, and start a new session |
| `/resume [session_id]` | Save the current session, then resume a previous one (bare `/resume` picks interactively) |
| `/exit` or `/quit` | Exit the agent |




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

You may submit one message while Ene is working; it is shown as `pending: … · runs next` and starts after the current round. Press `Up` at an empty prompt to move it back into the editor. Read-only commands such as `/usage`, `/context`, `/ps`, and `/auth` run immediately; commands that change conversation state wait for the current round.

## Tool execution

All tools execute automatically; Ene has no permission modes, confirmation prompts, or command screening. It is not a security boundary and does not attempt to contain what a model runs. Use an OS-level sandbox or container when commands must be constrained, and review a task before handing it to an autonomous agent.
