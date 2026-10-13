---
name: monitor
description: Run and actively monitor long-lived jobs, services, or health checks.
---

# Background Processes and Monitoring

These built-in tools are available when the active persona permits them:

- `start_process`: launch a managed background process with file-backed combined output; the harness notifies you when it exits or emits selected output;
- `schedule_wakeup`: deliver a notification after a delay, for checks with no process to watch;
- `inspect_processes`: return one immediate status snapshot and an optional bounded log tail;
- `stop_process`: terminate one managed process and its process tree without notifying.

Loading this skill adds the active-monitoring workflow below.

Use `exec_command` for non-interactive foreground commands expected to finish reliably. Its default timeout is 300 seconds; set `timeout=null` only when no timeout is intentionally required. Use managed processes for servers, long-running commands, and observers.

## How notifications work

Notifications arrive as an inbox message that lists each event with its source (`process:<id> <label>`, `wakeup:<id>`, or an external client). If you are idle, a notification starts a new round; if you are working, it is inserted before your next model call. User messages keep working while you wait, and several sources can notify at once.

Never block or poll to wait. After starting or scheduling the work you depend on, end your turn with a short status. You resume when a notification arrives.

## Choose a notify mode

| `notify` | Notifies on | Use for |
|---|---|---|
| `exit` (default) | exit | finite jobs, servers that should keep running |
| `output` + `pattern` | matching output lines, then exit | failures or milestones in a log, e.g. `pattern="^ERROR"` |
| `jsonl` | output lines that are JSON objects, then exit | observers that emit one JSON event per line |
| `none` | never | helper processes whose exit does not matter |

- `stop_process` never produces a notification; only unexpected exits do.
- `output` without `pattern` notifies for every line. Use it only for observers that print solely on meaningful changes.
- A noisy source keeps only its newest queued events; the message reports how many were dropped.

## Active monitoring loop

1. Establish the target, authorized corrective action, and stopping condition. Ask only when a missing detail blocks safe action.
2. Check the authoritative current state immediately. A quiet log or running observer does not prove that the target is healthy.
3. Arrange the next notification:
   - for a managed job, `start_process` with the mode above;
   - for an external scheduler or service, start a lightweight observer that polls locally and exits, or prints one JSON line, on a meaningful change (`notify=jsonl`);
   - for a periodic check with nothing to observe, `schedule_wakeup(seconds, note)` with a note that says what to check.
4. End your turn with a one-line status. Do not end monitoring by asking the user to request another check.
5. On each notification:
   - if a process exited, inspect its relevant final log and authoritative state;
   - if output notified, inspect only the relevant new output;
   - if a wakeup fired, run the check its note describes;
   - diagnose meaningful changes or failures;
   - recheck current state immediately before any relaunch or mutation;
   - apply only the requested corrective action and verify its result.
6. If the stopping condition is not met, make sure something will notify you again (a running notifying process or a new wakeup), then end your turn.

## Stopping and failures

- Treat “continuously,” “keep watching,” “every N minutes,” and “until I stop it” as open-ended monitoring.
- Continue until the explicit completion condition is met or the user stops it. To stop, `stop_process` observers and let pending wakeups fire without action.
- If a monitored process exits and no restart was requested, report the result.
- If an observer or managed job fails unexpectedly, inspect its complete relevant error, diagnose it, and restart only when consistent with the user's request.
- Preserve exact job names, IDs, namespaces, users, clusters, and launch arguments. Recheck before remediation to avoid duplicate actions.

## Completion output

Report the terminal condition, corrective actions taken, final verified state, and whether any managed process or wakeup remains pending. If blocked, report the concrete failure and relevant log path.
