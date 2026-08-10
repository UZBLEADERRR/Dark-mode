"""What this container is actually using, right now.

Written because three rounds of guessing at an out-of-memory crash is two rounds
too many. The app can read its own process tree and its own project folder, and
a render that dies should die having said what it was holding — so the next
report comes with numbers instead of a screenshot of an email.

Everything here is best-effort and silent on failure: a measurement that can
take the render down with it is worse than no measurement.
"""

from __future__ import annotations

import os
from pathlib import Path

CGROUP = Path("/sys/fs/cgroup")


def _tree_rss(root: int) -> tuple[int, int]:
    """(bytes held by this process and its children, how many are ffmpeg)."""
    kids: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    name: dict[int, str] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return 0, 0
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)
            parent = int(stat[1].split()[1])
            rss[pid] = int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * 4096
            name[pid] = Path(f"/proc/{pid}/comm").read_text().strip()
        except (OSError, IndexError, ValueError):
            continue
        kids.setdefault(parent, []).append(pid)

    seen: set[int] = set()
    stack = [root]
    total = encoders = 0
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        total += rss.get(pid, 0)
        if name.get(pid, "").startswith(("ffmpeg", "ffprobe")):
            encoders += 1
        stack += kids.get(pid, [])
    return total, encoders


def _cgroup_bytes() -> int:
    """What the platform thinks this container is using, when it will say.

    This is the number that gets a container killed, and it counts more than the
    processes do — on a box with no volume, the files the render writes are part
    of it. Which is exactly why guessing from process memory alone was wrong.
    """
    for name in ("memory.current", "memory/memory.usage_in_bytes"):
        try:
            return int((CGROUP / name).read_text().strip())
        except (OSError, ValueError):
            continue
    return 0


def folder_bytes(path: Path) -> int:
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:  # noqa: PERF203 - a file that vanished is zero
                continue
    except OSError:
        return 0
    return total


def snapshot(workdir: Path | None = None) -> dict[str, int]:
    """Everything worth knowing about the moment before a crash."""
    held, encoders = _tree_rss(os.getpid())
    return {
        "held": held,
        "encoders": encoders,
        "container": _cgroup_bytes(),
        "project": folder_bytes(workdir) if workdir else 0,
    }


def size(nbytes: float) -> str:
    for unit in ("B", "KB", "MB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.0f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} GB"


def line(workdir: Path | None = None, limit: int = 0) -> str:
    """One human-readable line: what is held, and how close to the edge."""
    now = snapshot(workdir)
    parts = [f"xotira {size(now['held'])}"]
    if now["encoders"]:
        parts.append(f"{now['encoders']} ffmpeg")
    if now["project"]:
        parts.append(f"fayllar {size(now['project'])}")
    if now["container"]:
        share = f" ({now['container'] / limit * 100:.0f}%)" if limit else ""
        parts.append(f"konteyner {size(now['container'])}{share}")
    return " · ".join(parts)


def pressure(limit: int = 0) -> float:
    """How full the container is, 0 to 1. Zero when nothing will say."""
    if not limit:
        return 0.0
    used = _cgroup_bytes()
    return (used / limit) if used else 0.0
