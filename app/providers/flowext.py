"""Hand out Flow Agent's Chrome extension, already pointed at this server.

Flow Agent ships an extension whose address is compiled into `config.js` and
repeated in the manifest's permissions — there is no field in its panel to
change it. So connecting it to a backend of your own means editing two files by
hand, on a laptop, from written instructions, which is not a thing anybody
finishes.

This assembles the same folder into a zip with those two edits already made, so
the whole setup is: download, unzip, load unpacked in Chrome. The extension then
dials this app's Flow Agent over a WebSocket — outward, so no port needs opening
— and every Google request is made by the browser itself, from the machine that
is signed in.

Nothing here forks Flow Agent. The vendored folder is untouched; the edits are
made to the bytes on their way into the archive, so an upstream update needs no
re-patching.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from .. import config

# Where the unmodified extension lives. Vendored, not downloaded at request
# time: the whole point is that this works on a laptop with the app in front of
# it, not that it works when github is reachable.
FOLDER = Path(__file__).resolve().parents[2] / "flowagent" / "upstream" / "flow-extension"

MARK = "/* sarideo-theme */"

# Flow Agent's panel is white; every other window in this workflow is not. Only
# the variables their own stylesheet already uses are set, so an upstream
# redesign inherits the colours rather than fighting them.
THEME = f"""
<style>{MARK}
:root {{
  color-scheme: dark;
  --bg: #08090c !important;
  --surface: #101219 !important;
  --card: rgba(23, 26, 35, .85) !important;
  --card-hover: rgba(29, 33, 43, .95) !important;
  --border: rgba(255, 255, 255, .08) !important;
  --accent: #ff5c47 !important;
  --green: #3ddc91 !important;
  --red: #ff5c47 !important;
  --yellow: #ffbd52 !important;
  --text: #eef1f7 !important;
  --text-dim: #b3bbcc !important;
  --muted: #7c8497 !important;
}}
header {{ background: var(--surface) !important; }}
header img {{ mix-blend-mode: normal !important; }}
input, textarea, select {{
  background: #171a23 !important;
  color: var(--text) !important;
  border-color: rgba(255, 255, 255, .14) !important;
}}
/* Their stylesheet reaches past its own variables in about twenty places and
   writes `#fff` directly. Those are listed here rather than left half-dark: a
   panel that is dark at the top and white in the middle is worse than one that
   was never touched. If upstream renames one of these, that single element
   goes back to white — nothing breaks. */
.tabs, .settings-pop, .settings-card, .header-badge, .result-card,
.history-head, #panel-history.active, .media-item, .select-trigger,
.select-menu, #btn-settings, #refresh-media, #delete-media,
.monitor-card, .monitor-actions, .monitor-actions button {{
  background: var(--card) !important;
  color: var(--text) !important;
}}
.type-switch, .result-preview, .media-item img, .media-item video,
.metric-mini {{
  background: #0d0f15 !important;
}}
.select-choice:hover {{ background: var(--card-hover) !important; }}
.settings-card input {{ background: #171a23 !important; }}
.settings-card input:focus {{ background: #1d212b !important; }}
</style>
"""


class NotReady(Exception):
    """The extension cannot be built into something that would work."""


def address(url: str | None = None) -> tuple[str, str]:
    """The Flow Agent address as (scheme, host), as the extension needs it."""
    raw = (url or config.FLOW_AGENT_URL or "").strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc or parsed.path.strip("/")
    if not host:
        raise NotReady(
            "Flow Agent manzili sozlanmagan — FLOW_AGENT_URL ni Railway'dagi "
            "Flow Agent servisining manzili qilib qo'ying.")
    return (parsed.scheme or "https"), host


def reachable(host: str) -> bool:
    """Whether this address is one the phone in your pocket could also reach.

    A locally-run Flow Agent is a perfectly good setup, but only when the
    browser and the app are on the same machine. Said rather than discovered.
    """
    return not re.match(r"^(127\.0\.0\.1|localhost|0\.0\.0\.0|192\.168\.|10\.)", host)


def status() -> dict:
    """What the library panel needs to say about the download."""
    try:
        scheme, host = address()
    except NotReady as exc:
        return {"ready": False, "why": str(exc), "host": "", "local": False}
    return {
        "ready": FOLDER.is_dir(),
        "why": "" if FOLDER.is_dir() else "Kengaytma fayllari topilmadi.",
        "host": host,
        "scheme": scheme,
        # True means "this only works with the browser on this same machine".
        "local": not reachable(host),
    }


def _config_js(text: str, host: str) -> str:
    patched = re.sub(r'(DEFAULT_SERVER_HOST\s*:\s*)"[^"]*"', rf'\1"{host}"', text)
    if patched == text and f'"{host}"' not in text:
        raise NotReady("config.js da DEFAULT_SERVER_HOST topilmadi — "
                       "kengaytma yangilangan, bu yer eskirgan.")
    return patched


def _manifest(text: str, scheme: str, host: str) -> str:
    """Let Chrome reach the address. Without this it refuses the connection
    outright, and the panel reports it as the server being down."""
    data = json.loads(text)
    hosts = list(data.get("host_permissions") or [])
    for wanted in (f"{scheme}://{host}/*", f"wss://{host}/*" if scheme == "https"
                   else f"ws://{host}/*"):
        if wanted not in hosts:
            hosts.append(wanted)
    data["host_permissions"] = hosts
    return json.dumps(data, indent=2) + "\n"


def build(url: str | None = None, *, theme: bool = True) -> bytes:
    """The extension as a zip, pointed at this app's Flow Agent."""
    scheme, host = address(url)
    if not FOLDER.is_dir():
        raise NotReady(f"Kengaytma papkasi yo'q: {FOLDER}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(FOLDER.rglob("*")):
            if not path.is_file() or path.name.endswith(".sarideo-bak"):
                continue
            inside = path.relative_to(FOLDER).as_posix()
            data = path.read_bytes()
            if inside == "config.js":
                data = _config_js(data.decode("utf-8"), host).encode("utf-8")
            elif inside == "manifest.json":
                data = _manifest(data.decode("utf-8"), scheme, host).encode("utf-8")
            elif inside == "popup.html" and theme:
                markup = data.decode("utf-8")
                if MARK not in markup and "</head>" in markup:
                    markup = markup.replace("</head>", f"{THEME}</head>", 1)
                data = markup.encode("utf-8")
            archive.writestr(inside, data)
    return buffer.getvalue()
