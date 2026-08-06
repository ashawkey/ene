# Getting Started

## Install

Ene requires Python 3.10 or newer.

```bash
pip install -U ene
```

Confirm that the CLI is available:

```bash
ene --help
```

## Configure a model

Ene reads model and global settings from `~/.ene.yaml`:

### OpenAI-compatible API keys

Use the API key and base URL supplied by your service. For example:

```yaml
openai:
  fast:
    model: deepseek-v4-flash
    api_key: replace-with-your-api-key
    base_url: https://api.deepseek.com

  pro:
    model: deepseek-v4-pro
    api_key: replace-with-your-api-key
    base_url: https://api.deepseek.com
    reasoning_effort: high # optional
```

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

Choose one of the offered browser, manual redirect, or device-code flows. 
OAuth credentials are stored in `~/.ene/auth.json`.


## List available models

```bash
List the resolved aliases and settings:

```bash
ene list
```

When `--model` is omitted, the first configured entry is used.

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

Tool calls execute automatically. Ene has the same permission as the shell user, use at your own risk.

See [CLI](commands.md) and [Tools](tools.md) for the complete interfaces.

## Resume a session

Sessions and project state are stored under `./.ene/`. It's self-gitignored so nothing will be committed accidentally.
Choose a previous session interactively:

```bash
ene --resume
```

Or resume a known session directly:

```bash
ene --resume SESSION_ID
```

Within a running session, `/rewind` can restore conversation, changed files, or both.