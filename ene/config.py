"""User configuration for ene.

Only ``~/.ene.yaml`` is loaded.  Missing, unreadable, malformed, and non-mapping
files produce an empty configuration so commands such as ``ene --help`` remain
available without setup.
"""

from pathlib import Path

import yaml


HOME_CONFIG_PATH = Path.home() / ".ene.yaml"
CONFIG_PATH = HOME_CONFIG_PATH


def _load_config() -> dict:
    try:
        loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


conf: dict = _load_config()

__all__ = ["CONFIG_PATH", "HOME_CONFIG_PATH", "conf"]
