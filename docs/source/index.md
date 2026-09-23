# Getting Started

## Install

Requires Python 3.10+.

```bash
pip install ene-agent
ene --help
```

## Configure a model

Ene reads model and global settings from `~/.ene.yaml`:

### OpenAI-compatible APIs

Add one entry under `openai` for each model alias. Use the model ID, API key, and base URL supplied by your service:

```yaml
openai:
  fast:
    model: deepseek-v4-flash
    api_key: replace-with-your-api-key
    base_url: https://api.deepseek.com

  my_model:
    model: provider-model
    api_key: replace-with-your-api-key
    base_url: https://provider.example/v1
    reasoning_effort: high # optional; defaults to high
```

- **Select an alias:** `--model fast`, `/model fast`, or `run_agent(model_alias="fast")`.
- **Unique prefixes work:** `gpt-6` selects `gpt-6-astra` if unambiguous. Matching is case-sensitive; exact aliases win, and ambiguous prefixes list their matches.
- **Defaults follow the model ID**, not the alias. Model-family matching is case-insensitive and accepts provider prefixes.
- Omitting `model` sends the alias as the ID; unknown IDs use conservative defaults.
- **Override budgets** per model with `context_length` and `max_output_tokens`.

#### GPT-6 budgets

- [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [Sol](https://developers.openai.com/api/docs/models/gpt-6-sol), and [Luna](https://developers.openai.com/api/docs/models/gpt-6-luna): **1,050,000 total context tokens**, **128,000 output tokens**, and 922,000 maximum input tokens.
- Ene reserves output headroom; default compaction starts near **892,500 input tokens**.
- For Sol/Luna reasoning with tool calls, use `api: responses`. Their Chat Completions function calling requires `reasoning_effort: none`.

#### Responses API

API-key models default to `api: chat_completions`. To use Responses instead:

```yaml
openai:
  gpt-6:
    provider: openai
    api: responses # optional; defaults to chat_completions
    model: azure/openai/gpt-6-astra
    api_key: replace-with-your-api-key
    base_url: https://inference-api.nvidia.com
    reasoning_effort: high
```

- Use your service's model ID and base URL; Ene appends `/responses`. For OpenAI, use `https://api.openai.com/v1` and `model: gpt-6-astra`.
- Supported `api` values: `chat_completions` and `responses`. The choice also applies to model switches, Python, batch, recap, and compaction.
- Responses supports streaming, images, function calls, and structured JSON; gateway support varies.
- Ene sends `store: false` and saves returned items locally, including supplied encrypted reasoning. Replay preserves API field names without adding unreturned SDK defaults.
- `openai-codex` always uses Responses, independently of this setting.

### ChatGPT Plus/Pro subscription

The `openai-codex` provider authenticates through a ChatGPT subscription instead of an API key:

```yaml
openai:
  codex:
    provider: openai-codex
    model: gpt-5.6-sol
    reasoning_effort: high # optional

  gpt-6-astra:
    provider: openai-codex
    model: gpt-6-astra
    reasoning_effort: high # optional
```

Start Ene and authenticate from the chat prompt:

```bash
ene --model codex

# in the chat prompt, run
/login openai-codex
```

- Choose browser, manual-redirect, or device-code authentication.
- Credentials are stored in `~/.ene/auth.json`.
- Use `/auth` to check status and `/logout` to remove credentials.

## List configured models

List resolved aliases, providers, APIs, context windows, and reasoning settings:

```bash
ene models
```

- Without `--model`, Ene uses the first configured entry.
- Subagents launched through `exec_command` or `start_process` inherit the session's model and effort via `ENE_MODEL_ALIAS` and `ENE_REASONING_EFFORT`, unless overridden.

## CLI

Run Ene in the current directory:

```bash
ene
```

Useful commands during a session include:

| Command | Purpose |
|---|---|
| `/help` | Show interactive commands. |
| `/model` | Show or switch the active model. |
| `/persona` | List or switch personas. |
| `/skills` | List reusable skills. |
| `/rewind` | Return conversation or code to an earlier prompt. |
| `/exit` | Save and exit. |

Prefix a shell command with `!` to run it directly without asking the model:

```text
!git status
!pytest -q
```

**Tools execute automatically with your shell permissions.** Ene is not a security boundary; use an OS sandbox or container to constrain commands.

See [CLI](commands.md) and [Tools](tools.md) for the complete interfaces.

## Python API

Use `run_agent()` for a single non-interactive task with the configured model:

```python
from ene import run_agent

result = run_agent("Review the changes in this project", work_dir=".")
if result.success:
    print(result.response)
else:
    print(result.outcome, result.error)
```

- Truncated tool calls, content-filtered responses, and exhausted automatic continuations return `TurnOutcome.FAILED`, `success=False`, and an explanatory `error`.
- Content filtering stops tool execution and automatic continuation.
- Any final partial text remains in `response`.

## Persistent live sessions

Interactive sessions run in detached workers and survive closing the terminal or shell.

- Start with an optional name using `ene [name]` (equivalent to `ene new [name]`).
- Detach without interruption using `/detach` or Ctrl+D.
- Rename the session with `/name [name]`.
- List workers with `ene ls` (`ene l`).
- Reattach with `ene attach [name]`, or choose with bare `ene attach` (`ene a`).
- Switch sessions using `/switch` or Ctrl+S; cancelling the picker leaves the current attachment untouched.
- Use `/resume` to activate a stopped conversation in the current live worker.

### Session lifecycle

- **Stop:** Ctrl+K, double Ctrl+C at an idle prompt, or `/exit` / `/quit`. For multiple sessions, use the `ene kill` (`ene k`) picker.
- Workers do not restart after reboot.
- **Force-close:** work continues; the attachment releases after about 20 seconds of silence. Immediate reattachment waits for release.
- **Terminal title:** `◐` / `◑ ene [name]` while working; `✓ ene [name]` when ready. Unnamed sessions use the workspace name; terminal profiles may suppress titles.
- See [session attachment and replay](commands.md#attach-and-replay) for ownership and history display.

## Resume a session

```bash
ene resume             # choose interactively
ene resume SESSION_ID  # resume directly
```

- **Storage:** `./.ene/`; Ene maintains a `.gitignore` containing `*` to exclude local state.
- **`/rewind`:** preview an earlier prompt and restore conversation, tracked files, or both. Edit the restored prompt before branching.
- **`/fork [name]`:** start a new session at an earlier prompt without changing the old session or tracked files.
- **Symlinks:** rewind restores targets written by `write_file`, `edit_file`, or `multi_edit`, preserving symlinks—even through symlinked directories or outside the workspace.
