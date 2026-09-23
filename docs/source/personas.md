# Personas

Personas define the agent's identity, system prompt, and advertised tools.

Discovery order (first match wins):

1. Bundled: `ene/bundled_personas/` — names are reserved.
2. Project: `./.ene/personas/`.
3. Personal: `~/.ene/personas/`.

| Persona | Tools | Purpose |
|---------|-------|---------|
| `coder` | all | The default coding agent (project-aware, full tool access) |
| `chat` | `web_search`, `web_fetch` | General chatbot without file/shell access |
| `reviewer` | selected file, shell, web, and skill tools | Evidence-grounded academic paper reviewer |
| `orchestrator` | selected file, managed-process, and skill tools | Delegate one bounded implementation/review task to fresh background agents until independent review passes |

An orchestrator task may group related small issues.

## Create a persona

Each persona directory contains `PERSONA.md`:

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

- **`tools` (required):** `all`, a list of built-in tool names, or `[]` for none. Include `load_skill` to load skills or expose their tools.
- **`skills` (required):** `bundled` accepts `all` or a name list; `local` controls project and personal skills. This filters automatic discovery, not explicit `/skills <name>` loads.
- **Markers:** whole-line `{{ene:autonomous-mode}}`, `{{ene:skills}}`, `{{ene:project-instructions}}`, and `{{ene:current-context}}`. Expansion happens once; embedded marker-like text is not interpreted.

### Project instructions

- Default: `./AGENTS.md`.
- Override: `./.ene/AGENTS.md`, when present.
- To extend rather than replace the root file, include a line containing exactly `@AGENTS.md` in the override. No other imports are supported.

## Select a persona

```bash
ene --persona o
```

| Command | Effect |
|---------|--------|
| `/persona` | List discovered personas, sources, and tool surfaces |
| `/persona <name-or-prefix>` | Switch persona and restart the conversation; a unique prefix such as `o` selects `orchestrator` |
| `/persona reload` | Re-scan persona directories; restart if the active persona changed |

- `--persona` also accepts unique prefixes: `o` selects `orchestrator`. Exact names win; ambiguous prefixes list matches.
- Sessions save the persona name and content digest. Resume warns on changed content and fails if the persona is missing.
- Tool restrictions apply to model tools, not interactive slash commands.
- Create and validate with [persona-creator](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/persona-creator/SKILL.md); synchronize through the [Library](library.md).
