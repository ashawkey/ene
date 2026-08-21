"""Cross-platform subprocess launch helpers."""

from __future__ import annotations

import base64
import locale
import os
import select
import subprocess
import sys
import time
from typing import Any


def _output_fallback_encoding() -> str:
    """Best-guess encoding for subprocess output that is not valid UTF-8.

    On Windows, PowerShell host errors (reported before the UTF-8 launch
    wrapper runs) are written in the console's OEM codepage, e.g. cp936/GBK on
    Chinese Windows. ``locale.getpreferredencoding`` is not a reliable proxy
    there (Python may report utf-8 regardless), so read the real codepage.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            codepage = ctypes.windll.kernel32.GetOEMCP()
            if codepage:
                return f"cp{codepage}"
        except Exception:
            pass
    return locale.getpreferredencoding(False) or "cp1252"


def decode_output_text(raw: bytes) -> str:
    """Decode one output line as UTF-8 or, when appropriate, the host codepage."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        utf8 = raw.decode("utf-8", errors="surrogateescape")
        invalid_bytes = sum("\udc80" <= char <= "\udcff" for char in utf8)
        has_valid_unicode = any(
            ord(char) > 127 and not "\udc80" <= char <= "\udcff" for char in utf8
        )
        # Preserve valid UTF-8 around a split or damaged byte. A localized
        # PowerShell host error contains many legacy-codepage bytes instead.
        if (
            exc.end == len(raw) and exc.reason == "unexpected end of data"
        ) or (invalid_bytes <= 3 and has_valid_unicode):
            return raw.decode("utf-8", errors="replace")
        return raw.decode(_output_fallback_encoding(), errors="replace")


def windows_utf8_powershell_command(command: str) -> str:
    """Wrap a PowerShell command so all of its output is UTF-8.

    The user command is decoded and parsed only after output encoding is set.
    This matters for parser errors: concatenating the command directly onto the
    setup script makes PowerShell parse the whole script first, so a syntax
    error can be emitted in the legacy console codepage before setup runs.
    """
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    prefix = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "$OutputEncoding = [Console]::OutputEncoding; "
    )
    # Appending the status check inside the delayed script preserves the normal
    # nonzero result for a final failed native command. The escaped ``$?`` is
    # kept literal while PowerShell constructs that script.
    delayed = (
        f"$eneCommand = [System.Text.Encoding]::UTF8.GetString("
        f"[System.Convert]::FromBase64String('{encoded}')); "
        '$eneScript = $eneCommand + "`nif (-not `$?) { exit 1 }"; '
        "Invoke-Expression $eneScript"
    )
    return prefix + delayed


def windows_utf8_process_env() -> dict[str, str]:
    """Return an environment that makes Python children write UTF-8 streams."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def agent_process_env(
    model_alias: str | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, str]:
    """Return the launch environment for agent-spawned subprocesses.

    On Windows this keeps the UTF-8 settings of :func:`windows_utf8_process_env`;
    elsewhere it is a plain copy so POSIX children keep their inherited
    locale-based output decoding. When a model identity is known it is stamped
    into ``ENE_MODEL_ALIAS`` / ``ENE_REASONING_EFFORT``, which the subagent
    runner inherits so a delegated agent runs with the same model and effort as
    the parent session.
    """
    env = windows_utf8_process_env() if sys.platform == "win32" else os.environ.copy()
    # Do not accidentally inherit an outer agent's identity when this executor
    # has no owning model (or intentionally uses a different partial identity).
    env.pop("ENE_MODEL_ALIAS", None)
    env.pop("ENE_REASONING_EFFORT", None)
    if model_alias:
        env["ENE_MODEL_ALIAS"] = model_alias
    if reasoning_effort:
        env["ENE_REASONING_EFFORT"] = reasoning_effort
    return env


def process_exited(pid: int, timeout: float = 0.0) -> bool:
    """Whether *pid* has exited, waiting up to *timeout* seconds for it to.

    Uses a stable per-process handle where the platform offers one, so a reused
    PID cannot be mistaken for the original process. ``timeout=0`` polls once.
    An unknown or invalid PID reports not-exited: the caller cannot distinguish
    it from a live process it simply may not query.
    """
    if pid <= 0:
        return False

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER
        try:
            return kernel32.WaitForSingleObject(
                handle, max(0, int(timeout * 1000))
            ) == 0  # WAIT_OBJECT_0
        finally:
            kernel32.CloseHandle(handle)

    pidfd_open = getattr(os, "pidfd_open", None)
    if pidfd_open is not None:
        try:
            pidfd = pidfd_open(pid)
        except ProcessLookupError:
            return True
        except OSError:
            pass
        else:
            try:
                ready, _, _ = select.select([pidfd], [], [], timeout)
                return bool(ready)
            finally:
                os.close(pidfd)

    # Checked before the deadline test so timeout=0 still probes once.
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


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
