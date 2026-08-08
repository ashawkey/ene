---
name: reflection
description: Reflect on conversation outcomes and persist verified, reusable lessons in project-local skills for future sessions.
---

# Reflection

Turn evidence from the current conversation into concise project skill instructions that improve future work. Treat skills as durable procedural memory, not as a transcript or a store for arbitrary facts.

Load `skill-creator` before creating or modifying any skill, and follow its format and validation instructions.

## Workflow

1. Review the conversation context available in this session, including any compaction summary. If the invocation includes a focus, restrict the review to that focus. Do not inspect other saved sessions unless the user explicitly asks.
2. Extract candidate lessons only from concrete evidence, such as:
   - an explicit user correction or stable preference;
   - a failed approach whose cause and prevention were established;
   - a verified solution, command, constraint, or workflow likely to recur;
   - repeated friction that a specific instruction would prevent.
3. Reject candidates that are transient task state, generic advice, speculative conclusions, unverified claims, or already captured accurately. Never persist credentials, secrets, personal information, or incidental machine-specific details.
4. Inspect existing skills under `<work_dir>/.ene/skills/`. Update the most relevant project skill when one exists; otherwise create one focused project skill with a distinct kebab-case name and coherent responsibility. Do not create a miscellaneous session-memory or catch-all lessons skill.
5. Write the smallest instruction that would have changed the earlier behavior. Preserve useful existing content and user changes. Prefer operational directions, concrete constraints, and verification steps over narrative history; do not record the conversation itself.
6. Validate every created or modified skill using the validator specified by `skill-creator`. Re-read the resulting files and confirm each retained lesson is supported by the conversation.
7. Report the lessons retained, files changed, and checks run. If no candidate meets the evidence and reuse threshold, make no filesystem changes and say so. After any change, tell the user to run `/skills reload` for the active session.

## Persistence boundaries

- Write only inside `<work_dir>/.ene/skills/` and only to skill instructions or resources necessary for the retained lesson.
- Never modify bundled skills, installed package files, or personal skills under `~/.ene/skills/`.
- Never modify source code, tests, documentation, configuration, personas, or `AGENTS.md` as part of reflection.
- Bundled skill names cannot be overridden. If a lesson relates to a bundled skill, create or update a distinctly named project skill only when the lesson is project-specific and independently useful.
- Prefer no change over low-confidence or duplicative memory. Keep the number and size of edits minimal.

## Default invocation

When invoked as `/reflection` without additional context, review the full conversation available in the current session and execute the workflow above. The explicit invocation authorizes immediate, reversible edits to qualifying project-local skills; do not ask for confirmation unless a consequential ambiguity prevents a safe choice. Stop after applying and validating the smallest set of high-confidence changes, or after determining that there is nothing durable to retain.
