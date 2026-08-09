"""Cross-platform subprocess launch helpers."""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def windows_hidden_process_kwargs(creationflags: int = 0) -> dict[str, Any]:
    """Return Windows subprocess options that suppress transient console windows."""
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": creationflags | subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }
