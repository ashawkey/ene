# Library

`ene lib` synchronizes project skills and personas through Git.

- Local resources: `./.ene/skills/` and `./.ene/personas/`.
- For agent-assisted management, use the [library skill](https://github.com/ashawkey/ene/blob/main/ene/bundled_skills/library/SKILL.md).

## Configure the repository

Set an accessible repository URL in `~/.ene.yaml`:

```yaml
ene_lib: git@github.com:username/ene-resources.git
```

- Branch: `main`; directories: `skills/<name>/` and `personas/<name>/`.
- Authentication: your existing Git/SSH credentials.
- Empty repositories initialize on first upload.

## Typical workflow

If you create a skill in project A and want to use it in project B:

```bash
# project A
cd /path/to/A
ene lib upload <my-skill>

# project B
cd /path/to/B
ene lib install <my-skill>

# synchronize later changes in either direction
ene lib update
```

## Commands

| Command | Effect |
|---|---|
| `ene lib list [pattern]` | List remote names/descriptions; `--local` lists project copies |
| `ene lib install <name> ...` | Install without overwriting existing local resources |
| `ene lib update [name ...]` | Synchronize selected installed resources, or all when omitted |
| `ene lib upload <name> ...` | Publish project resources; `--force` replaces remote copies |
| `ene lib remove <name> ...` | Delete remote resources; `--local` deletes only project copies |

- Default kind: skills. Add `--kind persona` for personas.
- Add `--verbose` for Git operation details.

Examples:

```bash
ene lib list --kind persona
ene lib install my-coder --kind persona
ene lib upload my-coder --kind persona
```

- Remote resources must be installed before an agent can use them.
- After install/update, run `/skills reload` or `/persona reload`.

## Synchronization and conflicts

- `.ene-lib.json` records each resource's last synchronized tree to detect conflicts across machines.
- Normal updates upload local-only changes and download remote-only changes.
- `local` is the project copy; `remote` is the library. Arrows show the destination; `up-to-date` means both match:

```text
gitlab-mr              up-to-date
gitlab-review-service  local --> remote
another-skill          local <-- remote
```

If both copies changed, Ene leaves them untouched:

1. Merge the desired changes into the project copy and validate it.
2. Force synchronization:

   ```bash
   ene lib update <name> --force
   ```

Repository checkouts are cached under `~/.ene/library/`.

