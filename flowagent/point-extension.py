#!/usr/bin/env python3
"""Point Flow Agent's Chrome extension at your own backend, and darken it.

Flow Agent ships an extension that talks to the author's server. Once the backend
is running somewhere of your own — Railway, say — the extension has to be told,
and there is no field in its panel for it: the address is compiled into
`config.js` and repeated in the manifest's permissions.

This edits a copy on your own machine. It does not fork Flow Agent, does not
change what it does, and can be re-run after every upstream update. Anything it
overwrites is backed up once, next to the original.

    python flowagent/point-extension.py ~/flow-extension https://flow.up.railway.app
    python flowagent/point-extension.py ~/flow-extension --restore

The theme is a block of CSS variables appended to the panel. Flow Agent's panel
is white; every other window in this workflow is not.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

MARK = "/* sarideo-theme */"

# Sarideo's palette, mapped onto the variable names Flow Agent's panel already
# uses. Only the variables are touched — no rule of theirs is rewritten, so an
# upstream redesign inherits the colours instead of fighting them.
THEME = f"""
{MARK}
:root {{
  color-scheme: dark;
  --bg: #08090c !important;
  --surface: #101219 !important;
  --card: rgba(23, 26, 35, .85) !important;
  --card-hover: rgba(29, 33, 43, .95) !important;
  --border: rgba(255, 255, 255, .08) !important;
  --border-glow: rgba(255, 92, 71, .25) !important;
  --accent: #ff5c47 !important;
  --accent-soft: #ff7a68 !important;
  --accent-bg: rgba(255, 92, 71, .12) !important;
  --green: #3ddc91 !important;
  --red: #ff5c47 !important;
  --yellow: #ffbd52 !important;
  --cyan: #ff5c47 !important;
  --text: #eef1f7 !important;
  --text-dim: #b3bbcc !important;
  --muted: #7c8497 !important;
}}
/* Their header sits on a white-to-tint gradient and their logo is multiplied
   against it; both disappear on a dark ground. */
header {{ background: var(--surface) !important; }}
header img {{ mix-blend-mode: normal !important; }}
input, textarea, select {{
  background: #171a23 !important;
  color: var(--text) !important;
  border-color: rgba(255, 255, 255, .14) !important;
}}
"""


def load(folder: Path) -> tuple[Path, Path, Path]:
    config = folder / "config.js"
    manifest = folder / "manifest.json"
    popup = folder / "popup.html"
    missing = [p.name for p in (config, manifest, popup) if not p.is_file()]
    if missing:
        raise SystemExit(
            f"{folder} does not look like Flow Agent's extension — "
            f"missing {', '.join(missing)}.")
    return config, manifest, popup


def keep(path: Path) -> None:
    """Back a file up once, and only once: re-running must not overwrite the
    original with an already-patched copy."""
    backup = path.with_suffix(path.suffix + ".sarideo-bak")
    if not backup.exists():
        shutil.copy2(path, backup)


def restore(folder: Path) -> int:
    done = 0
    for backup in folder.glob("*.sarideo-bak"):
        shutil.copy2(backup, backup.with_suffix(""))
        backup.unlink()
        done += 1
    return done


def point(folder: Path, url: str, theme: bool) -> list[str]:
    config, manifest, popup = load(folder)
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc or parsed.path
    if not host:
        raise SystemExit(f"'{url}' is not an address.")
    scheme = parsed.scheme or "https"
    done: list[str] = []

    # 1. the address it dials
    text = config.read_text(encoding="utf-8")
    patched = re.sub(r'(DEFAULT_SERVER_HOST\s*:\s*)"[^"]*"', rf'\1"{host}"', text)
    if patched != text:
        keep(config)
        config.write_text(patched, encoding="utf-8")
        done.append(f"config.js → {host}")
    elif f'"{host}"' in text:
        done.append("config.js already points there")
    else:
        raise SystemExit("config.js has no DEFAULT_SERVER_HOST to change — "
                         "upstream moved it, and this script is out of date.")

    # 2. permission to reach it. Chrome refuses the connection outright without
    #    this, and the panel reports it as the server being down.
    data = json.loads(manifest.read_text(encoding="utf-8"))
    hosts = data.get("host_permissions") or []
    wanted = f"{scheme}://{host}/*"
    if wanted not in hosts:
        keep(manifest)
        data["host_permissions"] = hosts + [wanted]
        manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        done.append(f"manifest.json → {wanted}")
    else:
        done.append("manifest.json already allows it")

    # 3. the look
    if theme:
        markup = popup.read_text(encoding="utf-8")
        if MARK in markup:
            done.append("panel already themed")
        elif "</head>" not in markup:
            done.append("panel left alone — no <head> to add the theme to")
        else:
            keep(popup)
            popup.write_text(
                markup.replace("</head>", f"<style>{THEME}</style>\n</head>", 1),
                encoding="utf-8")
            done.append("popup.html → Sarideo theme")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path,
                        help="Flow Agent's flow-extension folder on this machine")
    parser.add_argument("url", nargs="?", default="",
                        help="where your backend lives, e.g. https://flow.up.railway.app")
    parser.add_argument("--no-theme", action="store_true",
                        help="change the address but leave the panel white")
    parser.add_argument("--restore", action="store_true",
                        help="put every file back the way it was")
    args = parser.parse_args()

    folder = args.folder.expanduser().resolve()
    if not folder.is_dir():
        raise SystemExit(f"{folder} is not a folder.")

    if args.restore:
        count = restore(folder)
        print(f"{count} fayl tiklandi." if count else "Tiklaydigan narsa yo'q.")
        return 0
    if not args.url:
        raise SystemExit("Manzilni ayting — masalan https://flow.up.railway.app")

    for line in point(folder, args.url, not args.no_theme):
        print(f"  {line}")
    print("\nEndi chrome://extensions da kengaytmani Reload qiling.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
