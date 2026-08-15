# Getting Started

## Install

Ene requires Python 3.10 or newer.

```bash
pip install ene-agent
```

Confirm that the CLI is available:

```bash
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

The alias (`fast` or `my_model` above) is what you pass to `--model`.

### ChatGPT Plus/Pro subscription

The `openai-codex` provider authenticates through a ChatGPT subscription instead of an API key:

```yaml
openai:
  codex:
    provider: openai-codex
    model: gpt-5.6-sol
    reasoning_effort: high # optional
```

Start Ene and authenticate from the chat prompt:

```bash
ene --model codex

# in the chat prompt, run
/login openai-codex
```

Choose one of the offered browser, manual-redirect, or device-code flows. OAuth credentials are stored in `~/.ene/auth.json`. Use `/auth` to check login status and `/logout` to remove the credentials.

## List configured models

List resolved aliases, providers, context windows, and reasoning settings:

```bash
ene models
```

When `--model` is omitted, the first configured entry is used.

Subagents launched through `exec_command` or `start_process` inherit the
session's model and reasoning effort automatically (via `ENE_MODEL_ALIAS` and
`ENE_REASONING_EFFORT` in the child environment), so a delegated agent runs
with the same configuration as the parent unless explicitly overridden.

## CLI

Run Ene in the current directory:

```bash
ene
```

Useful commands during a session include:

| Command | Purpose |
|---|---|
| `/help` | Show interactive commands. |
| `/context [id]` | List context messages, or inspect one in full. |
| `/usage` | Show token usage. |
| `/recap` | Summarize the current task in one sentence. |
| `/export <path/filename>` | Export the last assistant response to a file. |
| `/model` | Show or switch the active model. |
| `/persona` | List or switch personas. |
| `/skills` | List reusable skills. |
| `/rewind` | Return conversation or code to an earlier prompt. |
| `/fork [name]` | Start a new session from an earlier prompt boundary. |
| `/exit` | Save and exit. |

Prefix a shell command with `!` to run it directly without asking the model:

```text
!git status
!pytest -q
```

Tool calls execute automatically. Ene has the same permissions as the shell user and is not a security boundary. Use an OS-level sandbox or container when commands must be constrained.

See [CLI](commands.md) and [Tools](tools.md) for the complete interfaces.

## Persistent live sessions

Interactive sessions run in detached workers and survive closing the terminal or shell. While attached, the terminal tab shows `◐ ene [name]` / `◑ ene [name]` while Ene is working and `✓ ene [name]` when it is ready; unnamed sessions use the workspace directory name. Terminal profiles configured to suppress application titles will ignore these updates.

- Start with an optional name using `ene [name]` (equivalent to `ene new [name]`).
- Detach without interruption using `/detach` or Ctrl+D.
- Rename the session with `/name [name]`.
- List workers with `ene ls` (`ene l`).
- Reattach with `ene attach [name]`, or choose with bare `ene attach` (`ene a`).
- Switch sessions using `/switch` or Ctrl+S; cancelling the picker leaves the current attachment untouched.
- Terminate the attached session with Ctrl+K, or use the `ene kill` (`ene k`) multi-select picker.
- Use `/resume` to activate a stopped conversation in the current live worker.

Resume, attach, and switch replay the latest 10 user turns and final assistant responses, omitting historical tool activity and warnings. Ctrl+K, double Ctrl+C at an idle prompt, and explicit `exit`/`quit` terminate the live session. Workers are not restarted after a machine reboot.

## Resume a session

Sessions and other project-local Ene state are stored under `./.ene/`. Ene maintains `./.ene/.gitignore` with `*`, so this state is not committed accidentally.

Choose a previous session interactively:

```bash
ene resume
```

Or resume a known session directly:

```bash
ene resume SESSION_ID
```

Within a running session, `/rewind` previews an earlier prompt boundary and lets you restore the conversation, tracked file changes, or both. The selected prompt returns to the editor so you can revise it before branching. `/fork [name]` uses the same prompt picker but starts a new, optionally named session at that conversation state, leaving both the old session and tracked files unchanged.