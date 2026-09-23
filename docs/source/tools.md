# Tools

Built-in tools (availability depends on the model and persona):

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents with optional offset/limit |
| `read_image` | Send a local PNG, JPEG, GIF, or WebP image to a multimodal model (not registered for text-only models) |
| `write_file` | Create or overwrite files, creating parent directories |
| `edit_file` | Surgical text replacement in files (whitespace-tolerant match) |
| `multi_edit` | Apply an ordered batch of edits to one file atomically (all-or-nothing) |
| `ls` | List a directory's immediate contents (gitignore-aware) |
| `exec_command` | Run foreground shell commands with real-time streaming output |
| `wait` | Pause before subsequent sequential tool calls, with an interruptible countdown |
| `glob_files` | Find files matching a glob pattern (gitignore-aware) |
| `grep_files` | Search file contents using ripgrep regex (gitignore-aware) |
| `web_search` | Search the web via DuckDuckGo |
| `web_fetch` | Fetch readable URL content; interruptible 30-second timeout; honors proxy environment variables |
| `remove_file` | Remove a file or directory |
| `load_skill` | Load the full prompt instructions for a skill by name |
| `start_process` | Start a managed background process with file-backed output and live status |
| `inspect_processes` | Inspect one or all managed background processes, with an optional bounded log tail for one process |
| `wait_processes` | Wait for process exit, optional output, or a timeout; omit timeout for finite jobs |
| `stop_process` | Stop a managed background process and its child process tree |

`web_fetch` honors `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `SOCKS_PROXY`, and `NO_PROXY`.

## Images

- `read_image` retains images across turns and saved sessions; the original file is not needed to resume.
- Images remain until compaction, rewind, or clearing removes their messages. They are not user prompts in previews or replay.
- Image-capable requests resend retained images and incur their token costs. Text-only models receive placeholders; switching back restores image access.

### Limits and recovery

- **24 MiB inline-image budget per request.** Older images become outgoing text placeholders; saved bytes remain intact.
- The newest image is kept. Resize or compress any image larger than 24 MiB.
- `content_length_limit` or HTTP 413 triggers one recovery attempt: shrink older image payloads, or compact context if they cannot shrink. The reduced budget persists in the live context, preserving the newest image.
- If recovery fails, reduce attachments or use `/clear`. Increasing `context_length` cannot raise a gateway's byte limit.
- Context sizing uses reported prompt usage plus a **2,048-token allowance** per added or unmeasured image. Actual costs vary; image costs are separate from text calibration and count toward compaction.

## Background processes

- Model tools, `/ps`, and terminal/Web UI status share one registry. Persona policy limits model tools, not `/ps` or live status.
- Processes survive detach and session switching; explicit exit or `ene kill` terminates them.
- Status shows each running process's session-local ID (`1`, `2`, …), label, runtime, and latest log line. The terminal also shows running/finished counts; the Web UI uses composer-dock rows.
- Logs: `.ene/processes/<process-id>-<unique-suffix>.log`.

| Command | Action |
|---|---|
| `/ps` | List jobs and latest output |
| `/ps <label\|process-id> [tail-chars]` | Inspect details and recent output |
| `/ps stop <label\|process-id>` | Stop a job |

Use the bundled `monitor` skill for active monitoring. For periodic checks, call `wait` **before** inspection in the same sequential tool-call batch, never in parallel.

## Skill-provided tools

**Install only trusted skills:** a root `tools.py` executes Python in Ene's process.

- Define a module-level `TOOLS` list with `{schema, run, describe, describe_output}` entries; both descriptors are optional.
- `describe(arguments)` returns a `ToolCallDescription` label.
- `describe_output(result)` returns a short success summary for live/replay display; failures use the standard formatter. The model receives the full result.
- Tools register on first skill load and remain for the session. A collision or broken `tools.py` fails the load without partial registration.
- `/skills reload` removes tools only for skills no longer discovered. Use `/clear` or restart to reload edited Python.

The [batch skill](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/batch/SKILL.md) adds parallel, tool-free requests through the configured provider, without exposing credentials to the skill. It supports structured output and resumable JSONL results.

| Tool | Description |
|------|-------------|
| `run_batch` | Map one direct LLM transformation over independent text or image items, appending results to a resumable JSONL file |
