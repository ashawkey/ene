# Tools

The agent has access to the following tools:

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
| `web_fetch` | Fetch and parse content from a URL with an interruptible 30-second overall timeout; honors `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `SOCKS_PROXY`, and `NO_PROXY` from the environment |
| `remove_file` | Remove a file or directory |
| `load_skill` | Load the full prompt instructions for a skill by name |
| `start_process` | Start a managed background process with file-backed output |
| `inspect_processes` | Inspect one or all managed background processes, with an optional bounded log tail for one process |
| `wait_processes` | Block until a selected managed process exits, optionally writes output, or an optional timeout expires; omit the timeout for ordinary finite jobs |
| `stop_process` | Stop a managed background process and its child process tree |

Managed background process tools are built into ene so permitted model calls,
the `/ps` command, and the live terminal/web status use the same process registry.
Like other built-in model tools, their advertisement is subject to the active
persona's tool policy; `/ps` and live status remain available to the UI.

The status bar shows `(Proc: N running [M finished])` while jobs are active.
Use `/ps` to list jobs and `/ps <process-id>` for details and recent output.
Processes are terminated on `/clear`, session switch, and exit. The bundled
`monitor` skill adds an active-monitoring workflow. For periodic monitoring,
call the core `wait` tool first and put the inspection or status calls after it
in the same sequential tool-call batch; do not group the wait and checks in
parallel.

## Skill-provided tools

A skill may ship a `tools.py` at its root (a module-level `TOOLS` list of
`{schema, run, describe, describe_output}` entries; both descriptors are
optional). `describe(arguments)` returns a `ToolCallDescription` for the call
label. `describe_output(result)` returns a concise string for the successful
result; failures use the standard error formatter. This keeps each skill's
call and result semantics beside its tools while the shared UI owns rendering.
The full result still goes to the model, while the concise output is persisted
for consistent live and replay display. Those tools are registered and
advertised to the model only while the skill is loaded, and removed when it is
unloaded.

The bundled **batch** skill follows the same split: the agent owns the
context-isolated turn, while the skill owns everything around it. See its
[batch instructions](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/batch/SKILL.md)
for the workflow.

| Tool | Description |
|------|-------------|
| `run_batch` | Run one task per item in a fresh context, appending per-item results to a JSONL file |
