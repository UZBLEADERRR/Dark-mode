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
/* Opaque, not translucent. Their cards are stacked inside one another, so a
   card at 85% opacity sits on top of another one and comes out grey — which is
   how a "dark" panel ends up looking washed out rather than black. */
:root {{
  color-scheme: dark;
  --bg: #06070a !important;
  --surface: #0b0d12 !important;
  --card: #101219 !important;
  --card-hover: #171a23 !important;
  --border: rgba(255, 255, 255, .07) !important;
  --accent: #ff5c47 !important;
  --green: #3ddc91 !important;
  --red: #ff5c47 !important;
  --yellow: #ffbd52 !important;
  --text: #eef1f7 !important;
  --text-dim: #b3bbcc !important;
  --muted: #7c8497 !important;
}}
html, body {{ background: var(--bg) !important; }}
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
  background: #06070a !important;
}}
.select-choice:hover {{ background: var(--card-hover) !important; }}
.settings-card input {{ background: #06070a !important; }}
.settings-card input:focus {{ background: #101219 !important; }}
/* The largest pale surface on the panel was not a background *colour* at all —
   `.quick-panel` paints a white-to-white gradient, which no amount of setting
   background-color reaches. Gradients are dropped wholesale; none of them is
   carrying meaning, and any that stayed would be a white block on a black page. */
.quick-panel, .tabs, .monitor-card, .monitor-actions, .settings-card,
.result-card, .media-item, .history-head, header, main, section, .tab-panel {{
  background-image: none !important;
}}
.quick-panel {{ background-color: var(--surface) !important; }}
/* Their shadows are near-black spread over a white ground. On a black ground
   they are invisible at best and a grey halo at worst. */
* {{ box-shadow: none !important; }}
/* Everything that separated a card from a white page has to do it against a
   black one instead, which is a border rather than a shadow. */
.monitor-card, .settings-card, .result-card, .media-item, .header-badge {{
  border: 1px solid var(--border) !important;
}}
</style>
"""


KEY_MARK = "/* sarideo-key */"

# Flow Agent's REST endpoints sit behind `SERVER_API_KEY`, and the extension
# never sends an Authorization header — it only sends `X-Client-Id`, and its
# panel has no field for a key. So a protected backend answers its credits and
# models calls with 401 and the panel fills up with "Invalid or missing API
# key", while image generation carries on working, because that goes over the
# WebSocket, which is not behind the key.
#
# One wrapper around `fetch`, added at the top of each file that talks to the
# server, rather than an edit at every call site: upstream can move its calls
# around and this still holds. Scoped to our own host by name, so the Google
# requests the extension makes with Google's own credentials are untouched.
KEY_SHIM = """{mark}
(() => {{
  const HOST = {host};
  const KEY = {key};
  const real = globalThis.fetch;
  if (!real || globalThis.__sarideoKeyed) return;
  globalThis.__sarideoKeyed = true;
  globalThis.fetch = function (input, init) {{
    let url = "";
    try {{ url = typeof input === "string" ? input : (input && input.url) || ""; }} catch (e) {{}}
    if (url.indexOf(HOST) !== -1) {{
      const next = Object.assign({{}}, init || {{}});
      const headers = new Headers(
        (init && init.headers)
        || (typeof input !== "string" && input && input.headers)
        || {{}});
      if (!headers.has("Authorization")) headers.set("Authorization", "Bearer " + KEY);
      next.headers = headers;
      return real.call(this, input, next);
    }}
    return real.call(this, input, init);
  }};
}})();
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
        # Whether the backend is protected, and therefore whether the file being
        # handed out carries the key that opens it. Said, never shown: the key
        # itself is the one thing this panel must not put on screen.
        "keyed": bool(config.FLOW_AGENT_KEY),
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


# The two files that talk to the backend over HTTP. Everything else in the
# extension either talks to Google or talks to these.
KEYED = ("background.js", "popup.js")

# Where the extension's WebSocket handler decides what a message from the
# backend means. A branch is added just before this one.
WS_ANCHOR = "} else if (msg.type === 'callback_config') {"

# Switching Flow projects has to move the browser too: Google files a picture
# partly by what the tab is showing, so a backend pointed at one project while
# the tab shows another puts pictures somewhere nobody asked for. Upstream can
# open Flow, but only the bare tool and only when the panel's button is pressed
# — there is no way to say "open this project", and no way to say it remotely.
OPEN_PROJECT = """} else if (msg.type === 'open_project') {
        /* sarideo-open */
        const wanted = 'https://labs.google/fx/tools/flow/project/' + msg.project_id;
        chrome.tabs.query({
          url: ['https://labs.google/fx/tools/flow*',
                'https://labs.google/fx/*/tools/flow*'],
        }).then((tabs) => {
          console.log('[Flow Agent] open_project ->', wanted, 'tabs:', tabs.length);
          if (tabs.length) return chrome.tabs.update(tabs[0].id, { url: wanted, active: true });
          return chrome.tabs.create({ url: wanted });
        }).catch((e) => console.error('[Flow Agent] open_project:', e));
      %s""" % WS_ANCHOR

OPEN_MARK = "/* sarideo-open */"


def build(url: str | None = None, *, theme: bool = True,
          key: str | None = None) -> bytes:
    """The extension as a zip, pointed at this app's Flow Agent.

    When a `SERVER_API_KEY` is configured for that backend, the same key is
    carried into the extension — otherwise its own panel cannot read credits or
    models from a backend it is perfectly able to draw pictures through. The
    file therefore contains the key, which is why it is served from this app and
    not from anywhere a stranger can reach.
    """
    scheme, host = address(url)
    if not FOLDER.is_dir():
        raise NotReady(f"Kengaytma papkasi yo'q: {FOLDER}")
    secret = key if key is not None else (config.FLOW_AGENT_KEY or "")
    shim = KEY_SHIM.format(mark=KEY_MARK, host=json.dumps(host),
                           key=json.dumps(secret)) if secret else ""

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
            elif inside == "background.js":
                text = data.decode("utf-8")
                if OPEN_MARK not in text and WS_ANCHOR in text:
                    text = text.replace(WS_ANCHOR, OPEN_PROJECT, 1)
                data = (shim + text).encode("utf-8")
            elif inside in KEYED and shim:
                data = (shim + data.decode("utf-8")).encode("utf-8")
            elif inside == "popup.html" and theme:
                markup = data.decode("utf-8")
                if MARK not in markup and "</head>" in markup:
                    markup = markup.replace("</head>", f"{THEME}</head>", 1)
                data = markup.encode("utf-8")
            archive.writestr(inside, data)
    return buffer.getvalue()
