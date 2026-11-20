"""The GUI server's memory budget: how much the server and everything it starts (jobs and
their children, the ffmpeg runs behind frames and audio previews) may use together.

The budget is about our own processes, whatever else the machine runs: by default the
machine's memory less 8 GB (40 GB on a 48 GB Mac), at least half of it; set
VAC_MEMORY_BUDGET_GB to choose. Work waits to start until it fits, and a watchdog
(gui/app.py) stops work if the total still goes over.

Memory is the processes' physical footprint (what Activity Monitor shows as Memory,
including what the GPU holds for Demucs on MPS), read with proc_pid_rusage on macOS and
from RSS elsewhere.
"""
import asyncio
import ctypes
import ctypes.util
import os
import subprocess
import sys
import time

GB = 2**30
RESERVE_GB = 8.0


def total_bytes():
    """The machine's memory in bytes, or None."""
    try:
        if sys.platform == "darwin":
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            total = ctypes.c_uint64(0)
            size = ctypes.c_size_t(ctypes.sizeof(total))
            if libc.sysctlbyname(b"hw.memsize", ctypes.byref(total), ctypes.byref(size), None, 0):
                return None
            return total.value
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, AttributeError):
        return None


def budget_bytes():
    env = os.environ.get("VAC_MEMORY_BUDGET_GB")
    if env:
        return float(env) * GB
    total = total_bytes()
    if not total:
        return None
    return max(total / 2, total - RESERVE_GB * GB)


# ------------------------------------------------------------------ our processes
_RUSAGE_INFO_V2 = 2
_FOOTPRINT_OFFSET = 16 + 7 * 8   # rusage_info_v2: uuid[16], then ri_phys_footprint is the 8th uint64
_libproc = None


def _footprint(pid):
    """A process's physical footprint in bytes (macOS), or None."""
    global _libproc
    if _libproc is None:
        _libproc = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
    buf = ctypes.create_string_buffer(512)
    if _libproc.proc_pid_rusage(ctypes.c_int(pid), ctypes.c_int(_RUSAGE_INFO_V2), buf) != 0:
        return None
    return ctypes.c_uint64.from_buffer(buf, _FOOTPRINT_OFFSET).value


def _table():
    """{pid: (ppid, rss_bytes)} for every process."""
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True,
                         check=False).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            table[int(parts[0])] = (int(parts[1]), int(parts[2]) * 1024)
    return table


def tree(root=None, table=None):
    """The pids of `root` (default: this process) and all its descendants."""
    root = os.getpid() if root is None else root
    table = _table() if table is None else table
    children = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    found, todo = [], [root]
    while todo:
        pid = todo.pop()
        found.append(pid)
        todo.extend(children.get(pid, []))
    return found


def usage_of(pids, table=None):
    """Bytes the given processes use together."""
    table = _table() if table is None else table
    total = 0
    for pid in pids:
        size = None
        if sys.platform == "darwin":
            try:
                size = _footprint(pid)
            except (OSError, AttributeError):
                size = None
        if size is None:
            size = table.get(pid, (0, 0))[1]
        total += size
    return total


_cache = {"at": 0.0, "value": None}


def our_usage(max_age=1.0):
    """Bytes the server and everything it started use now (cached for a second), or None."""
    now = time.monotonic()
    if now - _cache["at"] < max_age and _cache["value"] is not None:
        return _cache["value"]
    try:
        table = _table()
        value = usage_of(tree(table=table), table)
    except (OSError, ValueError):
        value = None
    _cache.update(at=now, value=value)
    return value


def forget_usage():
    _cache["at"] = 0.0


# ------------------------------------------------------------------ estimates
# Peak memory of cleaning a stretch, measured on an M3 (Demucs on MPS + DeepFilterNet +
# hiss reduction + loudness): 3.4 GB for 3 minutes, 6.3 GB for 10. It grows with the
# stretch because whole-stretch arrays are kept at several sample rates.
CLEAN_BASE_GB = 2.2
CLEAN_PER_MIN_GB = 0.45
LIGHT_JOB_GB = 1.5      # ffmpeg decoding a 4K original (preparing)
EXPORT_GB = 3.0         # ffmpeg decoding several 4K originals and encoding
PICTURE_GB = 0.6        # one frame of a 4K original


# Subtitles (measured on an M5 Pro, docs/subtitles-plan.md §0.5): whisper.cpp large-v3 on
# the GPU, and the translation model held by our own Ollama (gemma3:27b: 20.6 GB peak).
SUBTITLES_GB = 5.0
TRANSLATE_GB = 22.0


def clean_estimate_gb(seconds):
    return CLEAN_BASE_GB + CLEAN_PER_MIN_GB * max(0.0, float(seconds or 0.0)) / 60.0


def room_for(need_gb):
    """Whether starting something that needs `need_gb` keeps us within the budget.
    True when usage cannot be read."""
    used, budget = our_usage(), budget_bytes()
    if used is None or not budget:
        return True
    return used + need_gb * GB <= budget


def never_fits(need_gb):
    """Whether `need_gb` alone is more than the budget."""
    budget = budget_bytes()
    return bool(budget) and need_gb * GB > budget


async def wait_for_room(need_gb, timeout=None, poll=0.5):
    """Wait until `need_gb` fits in the budget. True once it does, False on timeout."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while not room_for(need_gb):
        if deadline is not None and time.monotonic() >= deadline:
            return False
        await asyncio.sleep(poll)
        forget_usage()
    return True


def machine_used_fraction():
    """The share of the whole machine's memory in use (macOS: the kernel's own figure,
    100 - kern.memorystatus_level), or None."""
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        level = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(level))
        if libc.sysctlbyname(b"kern.memorystatus_level", ctypes.byref(level), ctypes.byref(size), None, 0):
            return None
        return max(0.0, min(1.0, 1.0 - level.value / 100.0))
    except (OSError, AttributeError):
        return None
