# Bundled Skills

- Bundled skills update with Ene and take precedence over project/personal copies.
- Each linked `SKILL.md` is the authoritative usage guide.

| Skill | Use it for | Instructions |
|---|---|---|
| `batch` | Transform independent text/images in parallel; structured output and resume support. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/batch/SKILL.md) |
| `browser` | Operate your own running Chrome or Chromium through CDP when you ask. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/browser/SKILL.md) |
| `code-review` | Find actionable risks in code changes or designs. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/code-review/SKILL.md) |
| `lean` | Get terse answers and minimal implementations. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/lean/SKILL.md) |
| `library` | Manage Git-backed skills and personas. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/library/SKILL.md) |
| `monitor` | Keep watching long-running jobs, services, or health checks. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/monitor/SKILL.md) |
| `pdf-reading` | Parse and analyze PDFs with MinerU. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/pdf-reading/SKILL.md) |
| `persona-creator` | Create and validate custom Ene personas. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/persona-creator/SKILL.md) |
| `plan` | Produce a repository-grounded implementation plan. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/plan/SKILL.md) |
| `project-info` | Create or refine concise repository instructions in `AGENTS.md`. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/project-info/SKILL.md) |
| `reflection` | Save verified conversation lessons as project skills. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/reflection/SKILL.md) |
| `skill-creator` | Create, revise, or validate Agent Skills packages. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/skill-creator/SKILL.md) |
| `subagent` | Delegate substantial independent tasks to fresh Ene agents running as background processes. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/subagent/SKILL.md) |
| `web-verify` | Verify web pages and UI changes with tests, HTTP checks, and headless Chrome. | [source](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/web-verify/SKILL.md) |

## Invoke a skill

Matching requests load skills automatically. Use a slash command to select one directly:

```text
/plan Add support for JSON output mode.
```

- `/reflection` saves verified, reusable lessons only under `.ene/skills/`; no durable lessons means no changes. Run `/skills reload` afterward.
- `browser` requires Chrome/Chromium with remote debugging enabled.
- `web-verify` needs a local Chrome or Chromium for headless checks; it never uses your browser profile.
- `pdf-reading` requires the MinerU CLI and runtime resources.
- Check the linked instructions for requirements before first use.