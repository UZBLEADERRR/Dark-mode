"""Start the Sarideo server inside the phone, and hand the browser its address.

Everything the server needs from a machine it gets here instead: a folder it may
write to, two executables it is allowed to run, a folder of fonts because the
phone has no font database, and numbers for how much of the phone it may use at
once. Nothing is fetched, nothing is uploaded, and no address outside this
device is ever bound — the listener is on the loopback interface, which no other
program on the Wi-Fi can reach.

The three services the cloud version leans on are all optional in `app/` already,
which is why none of them had to be torn out: Postgres is skipped when
`DATABASE_URL` is unset, Supabase when `STORAGE_BACKEND` is `local`, and the
keys come from the keyring in SQLite — on this phone, in this app's private
storage.
"""

from __future__ import annotations

import os
import socket
import sys
import traceback
from pathlib import Path

# The loopback port. Fixed rather than random so a reload, a rotation or a
# return from the background lands on the same page.
DEFAULT_PORT = 8731


def _memory_budget() -> int:
    """Bytes this app may spend on renders, read from the phone's own total.

    `MEMORY_LIMIT` is what decides how many scenes animate at once, and the
    reading it normally comes from — a cgroup quota — is not readable on a
    phone. A third of installed RAM is deliberately timid: Android kills the
    process that is holding memory when the foreground app wants some, and a
    render killed at scene sixty costs more than one that took longer.
    """
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(int(line.split()[1]) * 1024 * 0.33)
    except (OSError, ValueError, IndexError):
        pass
    return 1024 * 1024 * 1024


def _workers() -> int:
    """How many scenes to animate side by side.

    On a laptop this is `min(cores, 8)`. A phone has as many cores on paper, but
    half of them are the small ones, they share one memory bus, and the whole
    package is throttled the moment it gets warm — so asking for eight buys
    heat, not speed.
    """
    cores = os.cpu_count() or 4
    return max(1, min(cores // 2, 4))


def configure(files_dir: str, native_lib_dir: str) -> dict[str, str]:
    """Set the environment `app.config` reads, and return it for the log."""
    files = Path(files_dir)
    native = Path(native_lib_dir)

    data = files / "data"
    tmp = files / "tmp"
    for folder in (data, tmp):
        folder.mkdir(parents=True, exist_ok=True)

    settings = {
        # Videos, images, narration, heroes and the key ring, all under the
        # app's private storage. Uninstalling is the only thing that removes it.
        "DATA_DIR": str(data),

        # Android refuses to execute a file that did not arrive in the package's
        # library folder, so the two binaries live there under `lib*.so` names.
        "FFMPEG_BIN": str(native / "libffmpeg.so"),
        "FFPROBE_BIN": str(native / "libffprobe.so"),

        # libass asks the system for a font family and a phone has no font
        # database to ask; without this the captions burn in blank.
        "SUBTITLE_FONTSDIR": str(files / "fonts"),

        # This folder is not rebuilt under the app, so the app should not warn
        # that it is.
        "LOCAL_DEVICE": "1",

        # No bucket, no database. Both are already optional in `app/`; naming
        # them here is what makes that explicit rather than accidental.
        "STORAGE_BACKEND": "local",

        "TMPDIR": str(tmp),
        "MEMORY_LIMIT": str(_memory_budget()),
        "RENDER_WORKERS": str(_workers()),
        # Half the encoding time per scene, and on a screen this size the
        # difference is not visible. Changeable in the app like any other.
        "RENDER_SPEED": "fast",
    }

    for name, value in settings.items():
        # `setdefault`: a value already in the environment was put there on
        # purpose, and this should not overrule it.
        os.environ.setdefault(name, value)

    # Leftovers from a cloud deployment have no meaning here, and a half-set
    # `DATABASE_URL` would make the app try to reach a Postgres that is not
    # there and fail its health check.
    for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "PORT"):
        os.environ.pop(name, None)

    return settings


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def serve(files_dir: str, native_lib_dir: str, port: int = DEFAULT_PORT) -> None:
    """Run the server. Blocks until the process ends — call it on its own thread."""
    files = Path(files_dir)
    pyroot = files / "pyroot"

    # First on the path, ahead of anything Chaquopy carries: `app` here is a real
    # directory of real files, which is what `StaticFiles` and every
    # `Path(__file__).parent` in the tree expect to find.
    sys.path.insert(0, str(pyroot))

    configure(files_dir, native_lib_dir)

    import pydantic_v1_bridge
    pydantic_v1_bridge.install()

    import uvicorn
    from app.main import app

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        # Named rather than left to be discovered: the fast implementations are
        # C extensions that are not in this build, and uvicorn's autodetection
        # would spend its startup finding that out.
        loop="asyncio",
        http="h11",
        log_level="info",
        access_log=False,
        # Nothing proxies this, and trusting a forwarded header on a loopback
        # socket is how a local server starts believing a page about where a
        # request came from.
        proxy_headers=False,
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    except BaseException:
        # Chaquopy prints a Java stack trace for anything that escapes, which
        # says nothing about which line of Python failed.
        traceback.print_exc()
        raise
