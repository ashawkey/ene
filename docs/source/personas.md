# Personas

A persona owns the agent's identity, complete system prompt, and advertised tool surface. Bundled personas live under `ene/bundled_personas/`; custom personas are discovered from `./.ene/personas/` and `~/.ene/personas/`. Bundled names are reserved, and project personas take precedence over personal personas.

| Persona | Tools | Purpose |
|---------|-------|---------|
| `coder` | all | The default coding agent (project-aware, full tool access) |
| `chat` | `web_search`, `web_fetch` | General chatbot without file/shell access |
| `reviewer` | selected file, shell, web, and skill tools | Evidence-grounded academic paper reviewer |
| `orchestrator` | selected file, process, and skill tools | One bounded work item, optionally grouping related small issues, with delegated implementation and independent review |

Each persona is a directory containing `PERSONA.md`:

```markdown
---
name: my-coder
description: A concise project coding assistant.
tools: all
skills:
  bundled:
    - code-review
  local: true
---
You are a terminal-based coding assistant.

{{ene:skills}}
{{ene:project-instructions}}
{{ene:current-context}}
```

`tools` is required and is either `all` or a YAML list of built-in tool names; use `[]` for no tools. `skills` is also required: `bundled` is either `all` or an explicit list of bundled skill names advertised through `{{ene:skills}}`, while `local` controls both project and personal `.ene/skills`. This skill policy limits automatic prompt discovery, not explicit user loads through `/skills <name>`. A persona must include `load_skill` in `tools` to load a skill or expose its tools.

Supported whole-line markers are `autonomous-mode`, `skills`, `project-instructions`, and `current-context`, each prefixed with `ene:` as above. Markers are expanded once, so marker-like text inside project instructions is not interpreted.

Project instructions normally come from `./AGENTS.md`. If `./.ene/AGENTS.md` exists, it replaces that file; a line containing exactly `@AGENTS.md` imports the root file at that position, allowing local instructions to extend it. No other import paths are supported.

```bash
ene --persona reviewer
```

| Command | Effect |
|---------|--------|
| `/persona` | List discovered personas, sources, and tool surfaces |
| `/persona <name>` | Switch persona and restart the conversation |
| `/persona reload` | Re-scan persona directories; restart if the active persona changed |

The active persona name and content digest are saved with the session. Resume warns if its content changed and fails clearly if it is no longer installed. Tool whitelists enforce which built-in tools are advertised to the model; interactive slash commands remain available.

Use the bundled [persona-creator skill](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/persona-creator/SKILL.md) to create and validate a persona. Use the [Library](library.md) to synchronize personas between projects.
