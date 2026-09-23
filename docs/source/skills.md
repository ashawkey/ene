# Skills

Skills package reusable workflows, knowledge, and optional tools in the [Agent Skills](https://agentskills.io) format.

- The system prompt lists only names and descriptions; full instructions load on demand.
- The model reuses instructions already in context and calls `load_skill` when missing or incomplete, including after compaction.
- Repeated loads still return full instructions; reuse is prompt guidance, not enforced deduplication.
- Ene adds optional Python tools through `tools.py`.

## Find and invoke skills

Skills are discovered from three locations, in this precedence order:

1. bundled skills in the installed `ene` package;
2. project skills under `./.ene/skills/`;
3. personal skills under `~/.ene/skills/`.

- Bundled names cannot be overridden.
- Startup and `/skills reload` report malformed skills and shadowed copies.

| Command | Effect |
|---|---|
| `/skills` | List discovered skills, sources, warnings, and load counts. |
| `/skills reload` | Re-scan skill directories after a change. |
| `/skills <name>` | Load a skill's instructions without starting a model turn. |
| `/<skill-name> <task>` | Apply a skill to the supplied task. |
| `/<skill-name>` | Run its declared default invocation, or ask for a task. |

- Built-in slash commands win name conflicts.
- Sessions save loaded-skill state; usage summaries include it.
- See [Bundled Skills](bundled-skills.md) for included workflows.

## Create a custom skill

Create a directory under `./.ene/skills/` (project) or `~/.ene/skills/` (personal):

```text
.ene/skills/
  my-workflow/
    SKILL.md
    scripts/       # optional deterministic programs
    references/    # optional documentation loaded on demand
    assets/        # optional templates or static data
    tools.py       # optional skill-provided tools
```

Start `SKILL.md` with YAML frontmatter and Markdown instructions:

```markdown
---
name: my-workflow
description: Prepare and validate a release. Use when publishing a new version.
---

# Release workflow

1. Inspect the current version and changelog.
2. Run the focused test suite.
3. Build and validate the release artifacts.
```

### Authoring rules

- **Required:** `name` and `description`. Describe both the task and when to activate it.
- **Compatibility fields:** `license`, `compatibility`, and `metadata` are parsed but unused. `allowed-tools` is not enforced; personas control tools.
- **Keep instructions focused:** common workflow in `SKILL.md`, conditional detail in `references/`, deterministic programs in `scripts/`, templates in `assets/`. Use skill-relative paths.
- **Trust Python:** `tools.py` runs in-process when loaded. Install only trusted skills; see [Skill-provided tools](tools.md#skill-provided-tools).
- **Default invocation:** add `## Default invocation` only for a safe, unambiguous task-free workflow. Otherwise Ene asks what to do.

Use [skill-creator](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/skill-creator/SKILL.md) for authoring and validation:

```text
/skill-creator Create a project skill for validating release artifacts.
```

- Run `/skills reload` after adding or editing a skill.
- Share skills and personas through the [Library](library.md).
