# Project

Ene is a Python 3.10+ terminal-first AI coding agent. It provides an interactive CLI, a one-shot Python API, reusable skills and personas, rewindable sessions, and a FastAPI-served React Web UI.

- `pyproject.toml` is authoritative for Python metadata, runtime dependencies, package data, and the `ene` console entry point. Use an editable install for development: `python -m pip install -e .`.
- `ene/frontend/package-lock.json` is authoritative for frontend dependencies; use npm rather than another JavaScript package manager.
- The supported public Python imports are exported from `ene/__init__.py`: `run_agent`, `AgentRunResult`, and `TurnOutcome`.

# Repository map

- `ene/backend/`: `LLMAgent`, turn execution, commands, batch operation, and session integration.
- `ene/context.py`: token estimation, context trimming/compaction, and tool-result retention policy.
- `ene/tools/`: built-in tool schemas, registry/dispatch, execution, process management, file/search/web implementations, and result formatting.
- `ene/providers/`: provider interfaces and the OpenAI-compatible and OpenAI Codex implementations.
- `ene/skills.py`, `ene/personas.py`: discovery, validation, precedence, and prompt rendering. Built-in definitions live in `ene/bundled_skills/` and `ene/bundled_personas/`.
- `ene/session_store.py`, `ene/library.py`: persisted sessions and the Git-backed skill/persona library.
- `ene/cli.py`, `ene/terminal.py`, `ene/ui.py`: CLI entry point and terminal interaction/rendering.
- `ene/hub.py`, `ene/hubclient.py`: synchronized Web UI server/client protocol, authentication, and session registry.
- `ene/frontend/src/`: React and TypeScript Web UI. `ene/frontend/dist/` is the generated runtime UI served by FastAPI and included in Python packages.
- `tests/`: pytest suite, generally organized as `test_<subsystem>.py`.
- `docs/source/`: Markdown documentation rendered by the custom npm static-site build in `docs/src/` and `docs/scripts/`.

# Important contracts

- User configuration is loaded only from `~/.ene.yaml`; malformed, unreadable, or non-mapping content intentionally behaves as empty configuration. Tests should monkeypatch `ene.config.conf` or `CONFIG_PATH`, not read a developer's real config.
- Built-in tool wire schemas are defined in `ene/tools/schemas.py`; `ene/tools/registry.py` is the source of truth for advertisement and dispatch. Keep schema names, registry entries, executor handlers, call/output descriptions, and focused tests synchronized when changing a tool.
- Skills use Agent Skills-style `SKILL.md` frontmatter and progressive disclosure. Discovery precedence is bundled, then project `.ene/skills/`, then personal `~/.ene/skills/`. Bundled definitions must remain compatible with their instructions and any `tools.py` resources.
- Personas are declarative `PERSONA.md` files. Discovery uses the same bundled/project/personal precedence, with project personas under `.ene/personas/`. Preserve marker validation and persona tool/skill filtering.
- Project instructions are loaded by `ene/personas.py` from root `AGENTS.md`, or from `.ene/AGENTS.md` when present; a line containing exactly `@AGENTS.md` includes the root file.
- The frontend production assets are committed because Python package builds do not invoke Node.js. After changing `ene/frontend/src/`, run the frontend checks and `npm run build`, then commit the updated `ene/frontend/dist/` output.
- Keep Markdown raw HTML disabled and preserve the hub's strict Content Security Policy. Web authentication relies on an httponly login cookie plus a CSRF token supplied in state frames and required for logout.

# Verification

Install Python test tooling separately; the project does not define a development extra.

- Focused Python test: `python -m pytest tests/test_<area>.py -q`
- Full Python suite: `python -m pytest -q`
- Frontend tests: `cd ene/frontend && npm test`
- Frontend typecheck: `cd ene/frontend && npm run typecheck`
- Frontend production build: `cd ene/frontend && npm run build`
- Documentation checks matching CI: `cd docs && npm ci && npm run typecheck && npm run build`
- Documentation subpath preview: `cd docs && npm run serve` (serves at `/ene/`)

Run the smallest tests covering the changed subsystem. For cross-cutting changes to the agent loop, context policy, tools, sessions, or hub protocol, run all directly affected test modules; use the full suite when the impact is broad. Frontend changes require tests, typechecking, and a production build. Documentation dependencies are locked in `docs/package-lock.json`.

# Change discipline

- Match the existing split modules and avoid duplicating registry or schema data in new sources of truth.
- Preserve Python 3.10 compatibility and the public exports in `ene/__init__.py` unless an API change is intentional.
- Update focused tests with behavior changes. Update `docs/source/` when user-facing CLI, configuration, skill/persona, API, tool, rewind, or Web UI behavior changes.
- Do not hand-edit hashed files in `ene/frontend/dist/`; regenerate them with the frontend build.
