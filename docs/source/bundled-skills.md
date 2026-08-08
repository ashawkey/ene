# Bundled Skills

Ene ships common workflows directly in the installed package. They are updated with Ene and always take precedence over project or personal skills of the same name.

The source `SKILL.md` is the authoritative introduction and usage guide for each skill:

| Skill | Use it for | Instructions |
|---|---|---|
| `batch` | Apply one repeated agentic task to many independent items without growing conversation context. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/batch/SKILL.md) |
| `browser` | Control an existing Chrome or Chromium browser through CDP. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/browser/SKILL.md) |
| `code-review` | Review code, diffs, commits, pull requests, or designs for actionable risks. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/code-review/SKILL.md) |
| `lean` | Request terse answers and minimal, YAGNI implementations. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/lean/SKILL.md) |
| `library` | Operate the Git-backed Ene library of skills and personas. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/library/SKILL.md) |
| `monitor` | Run and actively monitor long-lived jobs, services, and health checks. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/monitor/SKILL.md) |
| `pdf-reading` | Parse and analyze PDFs with MinerU. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/pdf-reading/SKILL.md) |
| `persona-creator` | Create and validate custom Ene personas. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/persona-creator/SKILL.md) |
| `plan` | Inspect a repository and produce an implementation-ready plan. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/plan/SKILL.md) |
| `project-info` | Create or refine concise repository instructions in `AGENTS.md`. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/project-info/SKILL.md) |
| `reflection` | Persist verified lessons from the current conversation in project-local skills. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/reflection/SKILL.md) |
| `skill-creator` | Create, revise, or validate Agent Skills packages. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/skill-creator/SKILL.md) |
| `subagent` | Delegate substantial independent tasks to fresh Ene agents. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/subagent/SKILL.md) |

The model loads a skill automatically when your request matches its description. You can also invoke one explicitly:

```text
Please make a plan for JSON output mode.
/plan Add support for JSON output mode.
```

The first request should cause Ene to load `plan`; the second selects it directly.

Invoking `/reflection` reviews the available conversation and directly creates or updates focused skills under `.ene/skills/` when it finds verified, reusable lessons. It never writes to bundled or personal skills, and leaves the filesystem unchanged when there is nothing durable to retain. Run `/skills reload` afterward when it reports a change.

Some skills have external requirements. In particular, `browser` needs Chrome or Chromium with remote debugging enabled, while `pdf-reading` needs the external MinerU CLI and its runtime resources. Check each linked `SKILL.md` before first use.