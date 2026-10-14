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
| `schedule_wakeup` | Deliver an inbox notification after a delay, without blocking |
| `glob_files` | Find files matching a glob pattern (gitignore-aware) |
| `grep_files` | Search file contents using ripgrep regex (gitignore-aware) |
| `web_search` | Search the web via DuckDuckGo |
| `web_fetch` | Fetch URL content (HTML as readable text, other text as-is, binary refused); interruptible 30-second timeout; honors proxy environment variables |
| `remove_file` | Remove a file or directory |
| `load_skill` | Load the full prompt instructions for a skill by name |
| `start_process` | Start a managed background process with file-backed output, live status, and inbox notifications |
| `inspect_processes` | Inspect one or all managed background processes, with an optional bounded log tail for one process |
| `stop_process` | Stop a managed background process and its child process tree, without notifying |

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

Use the bundled `monitor` skill for active monitoring.

## Inbox notifications

The agent never blocks waiting for background work. Notifications queue in an inbox and wake the session:

- **Idle:** after a 0.5-second batching window, a new round starts with every queued notification in one message.
- **Working:** notifications are inserted before the next model call.
- User messages run before queued notifications. Terminal and Web UI input keeps working throughout.

| Source | Notifies when |
|---|---|
| `start_process(notify="exit")` (default) | The process exits on its own; `stop_process` never notifies |
| `start_process(notify="output", pattern=REGEX)` | An output line matches, then on exit |
| `start_process(notify="jsonl")` | An output line is a JSON object, then on exit |
| `schedule_wakeup(seconds, note)` | The delay elapses (at most 100 pending) |
| `ene notify NAME_OR_ID [TEXT]` | Another program sends a message ([command](commands.md#top-level-commands)) |

- Pending wakeups appear in the process status with a countdown (`w1`), and in the `wakeups` list of `inspect_processes`.
- Use `notify="none"` for processes whose exit does not matter.
- The inbox keeps at most 50 events per source and 200 overall. Overflow counts are bounded and may be grouped under `[other sources]` to fit a delivery.
- JSONL objects larger than 8 KiB arrive as `process_output_file` notifications. Read their `path` for the complete JSON, saved under `.ene/processes/`. Lines over 1 MiB produce an explicit size-limit notice; the process log may contain more, subject to its own limit.
- JSON notification files have a separate 100 MiB budget per process. Once exhausted, large objects produce a storage-limit notice; small objects still arrive normally.
- Delivered notifications are inbox messages, not user prompts: `/context inbox` lists them, and `/rewind` to a notification round leaves the editor empty.
- Keep `pattern` simple; it runs on every output line.
- A line rewritten with carriage returns, such as a progress bar, notifies with its final redraw.
- Starting, resuming, or forking a session stops managed processes and wakeups and discards queued notifications.

### One-shot runs

- `run_agent()` stays open while a process or wakeup can still notify, and delivers each notification as another round.
- The last delivery asks the model to restate its complete answer, which becomes the result; a round with no reply keeps the earlier answer.
- After `notify_timeout` seconds without a notification, remaining processes and wakeups stop and the model gives its final answer. Already queued notifications are delivered even if their batching window has not elapsed.
- ESC or Ctrl+C stops the wait and returns `TurnOutcome.USER_INTERRUPTED` with the latest answer.

Set the default timeout in `~/.ene.yaml`, or pass `run_agent(..., notify_timeout=SECONDS)`:

```yaml
notify_timeout: 3600  # seconds; the default
```

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
