# Skills

Skills are reusable prompt packages that give Ene focused procedures, domain knowledge, and optional tools. 
They're compatible with the open [Agent Skills](https://agentskills.io) format and has some custom extensions.

Ene advertises only each skill's name and description in the system prompt. 
When a task matches, the model calls `load_skill` to bring the complete instructions into context. 
This progressive disclosure keeps the normal prompt small while making detailed workflows available on demand.

## Find and invoke skills

Skills are discovered from three locations, in this precedence order:

1. bundled skills in the installed `ene` package;
2. project skills under `./.ene/skills/`;
3. personal skills under `~/.ene/skills/`.

Bundled names therefore cannot be overridden. Malformed skills and lower-precedence copies shadowed by the same name are reported at startup and by `/skills reload`.

| Command | Effect |
|---|---|
| `/skills` | List discovered skills, sources, warnings, and load counts. |
| `/skills reload` | Re-scan skill directories after a change. |
| `/skills <name>` | Load a skill's instructions without starting a model turn. |
| `/<skill-name> <task>` | Apply a skill to the supplied task. |
| `/<skill-name>` | Run its declared default invocation, or ask for a task. |

Built-in slash commands take precedence over skills with the same name. Loaded-skill state is saved with the session and included in usage summaries.

See [Bundled Skills](bundled-skills.md) for the workflows included with Ene.

## Create a custom skill

A skill is a directory containing `SKILL.md`. It may also include scripts, references, assets, or native tools:

```text
.ene/skills/
  my-workflow/
    SKILL.md
    scripts/       # optional deterministic programs
    references/    # optional documentation loaded on demand
    assets/        # optional templates or static data
    tools.py       # optional skill-provided tools
```

Use `./.ene/skills/` for a project-specific skill or `~/.ene/skills/` for a personal skill shared across local projects.

Every `SKILL.md` starts with YAML frontmatter followed by Markdown instructions:

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

`name` and `description` are required.  
The description should state what the skill does and when it should activate because it is the metadata the model uses to select the skill. 
Optional Agent Skills fields such as `license`, `compatibility`, and `metadata` are parsed but not used.

Keep the common workflow in `SKILL.md`. Put lengthy conditional material in `references/`, repeatable deterministic operations in `scripts/`, and templates in `assets/`. Reference these files by paths relative to the skill directory.

A root `tools.py` can define structured tools that are registered only while the skill is loaded. 
See [Skill-provided tools](tools.md#skill-provided-tools) for the runtime behavior.

Add a `## Default invocation` section only when `/<skill-name>` without task text can safely run one clearly defined workflow. 
Otherwise Ene loads the skill and asks the user what to do.

The bundled [skill-creator instructions](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/skill-creator/SKILL.md) provide the complete authoring and validation workflow:

```text
/skill-creator Create a project skill for validating release artifacts.
```

After adding or editing a skill, run `/skills reload`. To share custom skills and personas through Git, use the [Library](library.md).
