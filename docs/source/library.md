# Library

`ene lib` synchronizes reusable skills and personas through a Git repository. 
It manages authored resources under the current project's `./.ene/skills/` and `./.ene/personas/`.

The bundled [library skill](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/library/SKILL.md) lets the agent operate this feature for you.

## Configure the repository

Set an accessible repository URL in `~/.ene.yaml`:

```yaml
ene_lib: git@github.com:username/ene-resources.git
```

The repository uses its `main` branch and stores resources under `skills/<name>/` and `personas/<name>/`. 
Ene uses your existing Git and SSH credentials. An empty repository is initialized by the first upload.

## Typical Use

Let say you created a skill when working on project A, and want to use it in project B, you can simply:
```bash
# project A
cd /path/to/A
ene lib upload <my-skill>

# project B
cd /path/to/B
ene lib install <my-skill>

# if you updated the skill, the update command will do the sync:
ene lib update # sync local changes to the remote and download any remote changes
```

## Commands

Skills are the default resource kind:

```bash
ene lib list [pattern]
ene lib list [pattern] --local
ene lib install <name> [<name> ...]
ene lib update [<name> ...]
ene lib update <name> --force
ene lib upload <name> [<name> ...]
ene lib upload <name> --force
ene lib remove <name> [<name> ...]
ene lib remove <name> --local
```

Add `--kind persona` to operate on personas:

```bash
ene lib list --kind persona
ene lib install my-coder --kind persona
ene lib upload my-coder --kind persona
```

- `list` shows remote resources and descriptions; `--local` lists project resources.
- `install` copies a remote resource into the project and never overwrites an existing local resource.
- `update` synchronizes selected installed resources, or all installed resources when no names are supplied.
- `upload` publishes project resources. `--force` replaces an existing remote resource.
- `remove` deletes remote resources. `--local` deletes only project copies.

Remote resources are not available to an agent until installed. 
After installing or updating a skill, run `/skills reload`; for a persona, run `/persona reload`.

## Synchronization and conflicts

Each installed resource records its last synchronized tree in `.ene-lib.json`.
This base allows Ene to distinguish local-only changes, remote-only changes, and conflicts even on another machine.

A normal update uploads local-only changes and downloads remote-only changes. 
If both copies changed, it stops and asks you to merge both versions into the project-local resource. 
Validate that merged copy, then run:

```bash
ene lib update <name> --force
```

Repository checkouts are cached under `~/.ene/library/`. 

