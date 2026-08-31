"""采集 T0 可复现实验所需的主机、工具链和资源指纹。"""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Iterable


EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resource_hashes(paths: Iterable[str | Path], *, root: str | Path) -> dict[str, str]:
    base = Path(root).resolve()
    result: dict[str, str] = {}
    for raw_path in paths:
        path = Path(raw_path).resolve()
        try:
            label = path.relative_to(base).as_posix()
        except ValueError:
            label = path.as_posix()
        if label in result:
            raise ValueError(f"重复资源路径：{label}")
        result[label] = file_sha256(path)
    return dict(sorted(result.items()))


def _run_bytes(command: tuple[str, ...], *, cwd: Path) -> bytes | None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _run_text(command: tuple[str, ...], *, cwd: Path) -> str:
    value = _run_bytes(command, cwd=cwd)
    if value is None:
        return "unknown"
    return value.decode("utf-8", errors="replace").strip() or "unknown"


def git_state(root: str | Path) -> dict[str, object]:
    base = Path(root).resolve()
    commit = _run_text(("git", "rev-parse", "HEAD"), cwd=base)
    tracked = _run_bytes(("git", "diff", "--binary", "HEAD", "--"), cwd=base)
    untracked_raw = _run_bytes(
        ("git", "ls-files", "--others", "--exclude-standard", "-z"), cwd=base
    )
    if tracked is None or untracked_raw is None or commit == "unknown":
        return {"commit": commit, "dirty": None, "dirty_diff_sha256": "unknown"}
    digest = hashlib.sha256()
    digest.update(tracked)
    untracked = sorted(item for item in untracked_raw.split(b"\0") if item)
    for encoded_path in untracked:
        relative = encoded_path.decode("utf-8", errors="surrogateescape")
        path = base / relative
        digest.update(b"\0untracked\0")
        digest.update(encoded_path)
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError as error:
            digest.update(f"<unreadable:{error}>".encode("utf-8", errors="replace"))
    dirty = bool(tracked or untracked)
    return {
        "commit": commit,
        "dirty": dirty,
        "dirty_diff_sha256": digest.hexdigest() if dirty else EMPTY_SHA256,
    }


def _ram_bytes() -> int | None:
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
        return None
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def process_resident_memory_bytes() -> int | None:
    """返回当前 Python 进程驻留集；不可用时显式返回 None。"""

    if sys.platform == "win32":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        process = kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return None
    proc_statm = Path("/proc/self/statm")
    try:
        resident_pages = int(proc_statm.read_text(encoding="ascii").split()[1])
        return resident_pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (IndexError, OSError, ValueError):
        return None


def _webview2_version() -> str:
    if sys.platform != "win32":
        return "not_applicable"
    try:
        import winreg
    except ImportError:
        return "unknown"
    client = "{F1E7E1A8-EE00-4D55-A4F9-14CEB6A20C2D}"
    candidates = (
        (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{client}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"Software\Microsoft\EdgeUpdate\Clients\{client}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{client}"),
    )
    for hive, key_path in candidates:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if isinstance(value, str) and value:
            return value
    return "unknown"


def collect_environment_metadata(root: str | Path, *, command: str) -> dict[str, object]:
    base = Path(root).resolve()
    git = git_state(base)
    cpu = platform.processor().strip() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown")
    power_mode = (
        _run_text(("powercfg", "/getactivescheme"), cwd=base)
        if sys.platform == "win32"
        else "not_applicable"
    )
    return {
        "command": command,
        "commit": git["commit"],
        "cpu": cpu,
        "dirty": git["dirty"],
        "dirty_diff_sha256": git["dirty_diff_sha256"],
        "node": _run_text(("node", "--version"), cwd=base),
        "os": platform.platform(),
        "power_mode": power_mode,
        "python": platform.python_version(),
        "ram_bytes": _ram_bytes(),
        "rust": _run_text(("rustc", "--version"), cwd=base),
        "webview2": _webview2_version(),
    }
