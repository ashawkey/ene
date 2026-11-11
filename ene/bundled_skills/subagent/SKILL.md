---
name: subagent
description: Delegate substantial independent work to fresh Ene agents running as managed background processes, in parallel when tasks are independent.
---

# Subagent Delegation

Run a fresh ene agent through `scripts/run_subagent.py` as a managed background
process. The child has its own conversation and token usage, but shares the
selected working directory and writes directly to it. It does not inherit this
conversation, loaded skills, or session/rewind history.

Use a subagent when a task is substantial and can be specified independently.
Handle small steps directly, and use the `batch` skill instead when one identical
operation repeats over many items.

## Prepare the task

Make the delegated task self-contained. Include:

- the exact goal and expected deliverable;
- relevant paths, constraints, and known evidence from this conversation;
- whether it may edit files or must only investigate;
- the checks it should run and the desired response shape.

Do not assume the child can see prior messages. It discovers project instructions
and available skills from its working directory, but if it needs a particular
skill, explicitly tell it to load that skill. The child runs in autonomous
execution mode, whose system prompt forbids invoking `subagent` or launching
another agent through `run_agent`; delegation depth is always one.

Subagents share the live filesystem. Their edits are already present when they
return; do not ask them for patches to reapply. Do not run parallel writers
against the same files or otherwise coupled state.

## Launch subagents in the background

Always start the runner with `start_process`, never as a foreground
`exec_command`. A foreground run blocks this session for the child's whole
lifetime: you cannot launch siblings, answer the user, or react to other
notifications until it returns. A managed child runs alongside you, and the user
can follow its conversation live from `/ps` or the Web UI's background tasks.

1. For each child, write its complete prompt to a unique, descriptively named
   file under `.ene/scratch/subagents/` and pass it with `--task-file`, even when
   the prompt is short. Never place credentials in a delegated prompt.
2. Resolve `scripts/run_subagent.py` against the absolute skill directory shown
   when this skill was loaded, and start it with `start_process` in the intended
   project `cwd`. Run it with `$ENE_PYTHON`, the interpreter running Ene
   (`& $env:ENE_PYTHON` in PowerShell), because the runner imports Ene:

   ```bash
   "$ENE_PYTHON" <skill-dir>/scripts/run_subagent.py --task-file .ene/scratch/subagents/parser-root-cause.md
   ```

   Give each child a concise, distinct `label` describing the delegated task, so
   it is identifiable in `/ps` and the status bar. Keep the default
   `notify=exit`. Retain each process ID, log path, and task-file path. If
   `start_process` itself fails, do not count that child as active.
3. Start every independent child before waiting, then end your turn with a short
   status. Each child's exit arrives as an inbox notification that starts your
   next round, and you stay responsive to the user meanwhile.
4. When a child exits, inspect that process with a bounded log tail and parse its
   final `ENE_SUBAGENT_RESULT=` line. Read the log file directly only if needed.
   Remove its task file once the result is read, and end your turn again while
   other children are still running.
5. If abandoning a child because of a deadline, failure, or user request, call
   `stop_process` for it, confirm it stopped, then remove its task file.

Do not poll with `inspect_processes`, do not start children with `notify=none`,
and do not start routine timer processes. An immediate inspection requested by
the user is fine.

Useful runner options:

```text
--task-file PATH
--persona NAME
--work-dir PATH
```

The child automatically runs with the same model and reasoning effort as you
(inherited through `ENE_MODEL_ALIAS` / `ENE_REASONING_EFFORT`), so no model
flags are needed. Omitting `--persona` uses the default `coder` persona.
Normally set the process `cwd` and omit `--work-dir`; use `--work-dir` only
when it should differ. Shell-quote every argument.

## Read the result

After its live log, the runner prints one final machine-readable line beginning
with `ENE_SUBAGENT_RESULT=`. Parse the JSON after that prefix; it contains:

- `success`, `outcome`, `response`, and `error`;
- `token_usage` for the independent run.

Exit code `0` means completed, `1` means failed or could not start, and `130`
means interrupted. Read the marked result even after a nonzero exit because it
contains the child error when one was available. A stopped child may exit before
emitting the result; report it as stopped.

## Validate and integrate

Treat a successful child response as evidence, not automatic proof. Inspect the
claimed files and run the smallest relevant parent-side check, especially when
multiple children wrote files. Resolve conflicting recommendations yourself.

Report the delegated outcome, material findings or edits, verification actually
performed, and any failed/interrupted child. Include token usage only when it is
useful or requested.
