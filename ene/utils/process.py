"""Cross-platform subprocess launch helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


def windows_utf8_powershell_command(command: str) -> str:
    """Wrap a PowerShell command so native and redirected output is UTF-8."""
    prefix = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "$OutputEncoding = [Console]::OutputEncoding; "
    )
    return f"{prefix}{command}"


def windows_utf8_process_env() -> dict[str, str]:
    """Return an environment that makes Python children write UTF-8 streams."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


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
