from pathlib import Path

ENE_DIR_NAME = ".ene"
ENE_GITIGNORE_NAME = ".gitignore"
ENE_GITIGNORE_CONTENT = "*\n"


def get_ene_dir(cwd: str | Path | None = None) -> Path:
    """Return the self-ignored .ene directory, creating it if needed."""
    base = Path(cwd) if cwd else Path.cwd()
    ene_dir = base / ENE_DIR_NAME
    ene_dir.mkdir(parents=True, exist_ok=True)
    gitignore = ene_dir / ENE_GITIGNORE_NAME
    if (
        not gitignore.exists()
        or gitignore.read_text(encoding="utf-8") != ENE_GITIGNORE_CONTENT
    ):
        gitignore.write_text(ENE_GITIGNORE_CONTENT, encoding="utf-8")
    return ene_dir
