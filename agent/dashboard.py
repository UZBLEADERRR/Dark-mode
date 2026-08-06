"""What the agent has been doing, on a page you can open from anywhere.

The agent used to say everything it did to stdout and nowhere else. That is fine
on a laptop with a terminal open and useless on Railway, where reading it means
finding the service, opening the log pane, and scrolling — for the answer to
"has it drawn anything in the last hour".

So it keeps a journal, and serves it: what was asked for, what came back, how
long each one took, and the picture itself. Everything on the page is downloadable,
because a record you cannot take with you is a record you are renting.

The journal is written to disk as it goes, so a redeploy does not erase the
history — the agent service has a volume, and this is the second thing worth
putting on it after the browser profile.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import secrets
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

# How many entries to keep. Each one holds a PNG, so this is a disk budget as
# much as a scrollback: two hundred pictures at ~1 MB is a fifth of a gigabyte.
KEEP = 200


class Journal:
    """Everything the agent has done, newest last, with the pictures."""

    def __init__(self, folder: Path, keep: int = KEEP) -> None:
        self.folder = folder
        self.images = folder / "images"
        self.keep = keep
        self.entries: list[dict[str, Any]] = []
        self.started = time.time()
        # What it is doing *right now* — not an entry, because it has not
        # happened yet and might never.
        self.current: dict[str, Any] | None = None
        self.state = "boshlanmoqda"
        self.note = ""
        self._load()

    # --- storage ------------------------------------------------------------

    @property
    def _file(self) -> Path:
        return self.folder / "journal.json"

    def _load(self) -> None:
        try:
            self.entries = json.loads(self._file.read_text(encoding="utf-8"))[-self.keep:]
        except Exception:  # noqa: BLE001 - a missing or broken journal is empty
            self.entries = []

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            self._file.write_text(json.dumps(self.entries, ensure_ascii=False),
                                  encoding="utf-8")
        except Exception:  # noqa: BLE001 - never let bookkeeping stop the work
            pass

    # --- recording ----------------------------------------------------------

    def begin(self, *, scene: int, prompt: str, source: str) -> None:
        self.current = {"scene": scene, "prompt": prompt, "source": source,
                        "at": time.time()}
        self.state = "ishlayapti"
        self.note = f"{scene + 1}-sahna"

    def finish(self, *, ok: bool, image: bytes = b"", error: str = "") -> dict:
        started = self.current or {"scene": -1, "prompt": "", "source": "",
                                   "at": time.time()}
        self.current = None
        entry = {
            "id": secrets.token_hex(8),
            "scene": started["scene"],
            "prompt": started["prompt"],
            "source": started["source"],
            "at": started["at"],
            "seconds": round(time.time() - started["at"], 1),
            "ok": bool(ok),
            "error": error[:400],
            "bytes": len(image),
        }
        if image:
            self.images.mkdir(parents=True, exist_ok=True)
            (self.images / f"{entry['id']}.png").write_bytes(image)
        self.entries.append(entry)
        self._prune()
        self._save()
        self.state = "kutyapti"
        self.note = "yuborildi" if ok else (error[:80] or "xato")
        return entry

    def say(self, state: str, note: str = "") -> None:
        """Status without an entry — waiting, reconnecting, that sort of thing."""
        self.state = state
        self.note = note

    def _prune(self) -> None:
        while len(self.entries) > self.keep:
            gone = self.entries.pop(0)
            (self.images / f"{gone['id']}.png").unlink(missing_ok=True)

    # --- reading ------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        done = sum(1 for e in self.entries if e["ok"])
        return {"done": done, "failed": len(self.entries) - done,
                "total": len(self.entries)}

    def state_out(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "note": self.note,
            "since": round(time.time() - self.started),
            "current": self.current and {
                "scene": self.current["scene"],
                "prompt": self.current["prompt"],
                "seconds": round(time.time() - self.current["at"], 1),
            },
            "counts": self.counts(),
            # Newest first: the page reads top-down and the interesting end is
            # the recent one.
            "entries": list(reversed(self.entries)),
        }

    def image_path(self, entry_id: str) -> Path:
        # Names come from the journal, never from the request — a path with a
        # slash in it must not become a directory traversal.
        safe = "".join(c for c in entry_id if c.isalnum())
        return self.images / f"{safe}.png"

    # --- taking it with you -------------------------------------------------

    def zip_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in self.entries:
                path = self.image_path(entry["id"])
                if not path.exists():
                    continue
                when = time.strftime("%Y%m%d-%H%M%S", time.localtime(entry["at"]))
                # Named so the pile sorts into the order it was made in, and so
                # each file says which scene it belongs to without opening it.
                zf.writestr(f"{when}_sahna-{entry['scene'] + 1:03d}_{entry['id']}.png",
                            path.read_bytes())
            zf.writestr("journal.json",
                        json.dumps(self.entries, ensure_ascii=False, indent=2))
        return buffer.getvalue()

    def csv_text(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["vaqt", "sahna", "manba", "soniya", "holat", "xato",
                         "bayt", "prompt"])
        for entry in self.entries:
            writer.writerow([
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["at"])),
                entry["scene"] + 1, entry["source"], entry["seconds"],
                "ok" if entry["ok"] else "xato", entry["error"], entry["bytes"],
                entry["prompt"],
            ])
        return out.getvalue()


# ── the page ──────────────────────────────────────────────────────────────────

PAGE = """<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sarideo agent</title>
<!-- Inline, so the browser never asks for one. Every other path on this server
     needs a token, and a favicon request without one is a 403 in the console of
     a page that is working perfectly. -->
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='13' fill='%23ff5c47'/%3E%3C/svg%3E">
<style>
  /* Two themes, and the one you last chose is the one you get. Dark is the
     default because this is a thing you check at night, from a phone, to see
     whether the machine at home is still working. */
  :root {
    --bg:#08090c; --surface:#101219; --raised:#171a23;
    --line:rgba(255,255,255,.08); --line-2:rgba(255,255,255,.14);
    --text:#eef1f7; --muted:#7c8497; --accent:#ff5c47;
    --ok:#3ddc91; --warn:#ffbd52; --r:14px;
  }
  :root[data-theme="light"] {
    --bg:#f6f7fa; --surface:#fff; --raised:#eef0f5;
    --line:rgba(0,0,0,.08); --line-2:rgba(0,0,0,.16);
    --text:#12141a; --muted:#5b6373; --accent:#e5432c;
    --ok:#12a05f; --warn:#a9720b;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
    font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;
    -webkit-font-smoothing:antialiased; }
  .wrap { max-width:820px; margin:0 auto; padding:16px 14px 40px; }

  header { display:flex; align-items:center; gap:10px; margin-bottom:14px; }
  .dot { width:9px; height:9px; border-radius:50%; background:var(--accent); flex:0 0 auto; }
  h1 { font-size:16px; font-weight:700; margin:0; letter-spacing:-.01em; }
  .pill { margin-left:auto; font-size:12px; padding:5px 10px; border-radius:99px;
    background:var(--raised); color:var(--muted); border:1px solid var(--line);
    white-space:nowrap; }
  .pill.on { color:var(--ok); border-color:rgba(61,220,145,.35); }
  .pill.busy { color:var(--warn); border-color:rgba(255,189,82,.35); }
  .pill.bad { color:var(--accent); border-color:rgba(255,92,71,.35); }
  button, a.btn { font:inherit; font-size:13px; padding:8px 12px; cursor:pointer;
    color:var(--text); background:var(--raised); border:1px solid var(--line-2);
    border-radius:10px; text-decoration:none; display:inline-block; }
  button:hover, a.btn:hover { border-color:var(--accent); }
  #theme { padding:8px 10px; }

  .card { background:var(--surface); border:1px solid var(--line);
    border-radius:var(--r); padding:14px; margin-bottom:12px; }
  .nums { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; text-align:center; }
  .nums b { display:block; font-size:22px; font-weight:700; }
  .nums span { font-size:11.5px; color:var(--muted); }
  .now { margin-top:12px; padding-top:12px; border-top:1px solid var(--line);
    font-size:13px; color:var(--muted); }
  .now b { color:var(--text); }

  .acts { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }

  .row { display:flex; gap:12px; padding:11px 0; border-top:1px solid var(--line); }
  .row:first-child { border-top:0; }
  .row img { width:88px; height:56px; object-fit:cover; border-radius:8px;
    background:var(--raised); flex:0 0 auto; }
  .row .miss { width:88px; height:56px; border-radius:8px; background:var(--raised);
    flex:0 0 auto; display:flex; align-items:center; justify-content:center;
    color:var(--accent); font-size:18px; }
  .row .body { min-width:0; flex:1; }
  .row .head { display:flex; gap:8px; align-items:baseline; font-size:12px;
    color:var(--muted); }
  .row .head b { color:var(--text); font-size:13px; }
  .row p { margin:3px 0 0; font-size:12.5px; color:var(--muted);
    display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
    overflow:hidden; }
  .row .err { color:var(--accent); }
  .empty { color:var(--muted); font-size:13px; text-align:center; padding:22px 0; }
  #live { width:100%; border-radius:10px; display:block; border:1px solid var(--line); }
  details summary { cursor:pointer; font-size:13px; color:var(--muted); }
</style></head><body>
<div class="wrap">
  <header>
    <i class="dot"></i>
    <h1>Sarideo agent</h1>
    <span class="pill" id="pill">…</span>
    <button id="theme" title="Mavzu">◐</button>
  </header>

  <div class="card">
    <div class="nums">
      <div><b id="n-done">0</b><span>yuborildi</span></div>
      <div><b id="n-failed">0</b><span>xato</span></div>
      <div><b id="n-up">0</b><span>ishlayapti</span></div>
    </div>
    <div class="now" id="now">…</div>
  </div>

  <div class="acts">
    <a class="btn" id="dl-zip" href="#">⬇ Hamma rasm (.zip)</a>
    <a class="btn" id="dl-json" href="#">⬇ Jurnal (.json)</a>
    <a class="btn" id="dl-csv" href="#">⬇ Jadval (.csv)</a>
  </div>

  <details class="card" id="livewrap" hidden>
    <summary>Brauzer oynasi</summary>
    <img id="live" alt="" style="margin-top:10px">
  </details>

  <div class="card"><div id="list"><p class="empty">Hali hech nima qilinmadi.</p></div></div>
</div>
<script>
const token = new URLSearchParams(location.search).get("t") || "";
const q = (p) => p + (p.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(token);
const el = (id) => document.getElementById(id);

// Theme: remembered, and following the phone when nothing has been chosen.
const saved = localStorage.getItem("sarideo-theme");
if (saved) document.documentElement.dataset.theme = saved;
el("theme").addEventListener("click", () => {
  const now = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = now;
  localStorage.setItem("sarideo-theme", now);
});

["zip", "json", "csv"].forEach((kind) => {
  el("dl-" + kind).href = q("/export/" + (kind === "zip" ? "images.zip" : "journal." + kind));
});

const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
  (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));
const clock = (s) => {
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h}s ${m}d` : m ? `${m}d` : `${s}s`;
};
const when = (t) => new Date(t * 1000).toLocaleTimeString();

async function tick() {
  let s;
  try { s = await (await fetch(q("/api/state"))).json(); }
  catch { el("pill").textContent = "ulanmadi"; el("pill").className = "pill bad"; return; }

  const pill = el("pill");
  pill.textContent = s.state + (s.note ? " · " + s.note : "");
  pill.className = "pill " + (s.state === "ishlayapti" ? "busy"
    : s.state === "xato" ? "bad" : "on");

  el("n-done").textContent = s.counts.done;
  el("n-failed").textContent = s.counts.failed;
  el("n-up").textContent = clock(s.since);
  el("now").innerHTML = s.current
    ? `Hozir: <b>${s.current.scene + 1}-sahna</b> · ${s.current.seconds}s<br>
       <span style="opacity:.8">${esc(s.current.prompt.slice(0, 160))}</span>`
    : "Hozir bo‘sh — navbat kutilyapti.";

  el("livewrap").hidden = !s.live;
  if (s.live && el("livewrap").open) el("live").src = q("/shot") + "&n=" + Date.now();

  el("list").innerHTML = s.entries.length ? s.entries.map((e) => `
    <div class="row">
      ${e.ok && e.bytes
        ? `<a href="${q("/img/" + e.id)}" target="_blank" rel="noopener">
             <img src="${q("/img/" + e.id)}" alt="" loading="lazy"></a>`
        : '<div class="miss">!</div>'}
      <div class="body">
        <div class="head">
          <b>${e.scene + 1}-sahna</b>
          <span>${when(e.at)}</span>
          <span>${e.seconds}s</span>
          <span>${esc(e.source)}</span>
        </div>
        <p class="${e.ok ? "" : "err"}">${esc(e.ok ? e.prompt : (e.error || "xato"))}</p>
      </div>
    </div>`).join("") : '<p class="empty">Hali hech nima qilinmadi.</p>';
}
setInterval(tick, 2000); tick();
</script></body></html>"""


async def serve(journal: Journal, *, host: str, port: int, token: str,
                shot: Callable[[], Any] | None = None) -> Any:
    """Start the dashboard. Returns the aiohttp runner so it can be stopped.

    `shot` is an optional coroutine returning JPEG bytes of whatever the browser
    is looking at — present in `run`, absent in `bridge`, which has no browser of
    its own to show.
    """
    from aiohttp import web

    def allowed(request) -> bool:
        # The journal holds your prompts and your pictures. Same door as the
        # login view, for the same reason.
        return secrets.compare_digest(request.query.get("t", ""), token)

    def guard(handler):
        async def wrapped(request):
            if not allowed(request):
                return web.Response(status=403, text="token noto'g'ri")
            return await handler(request)
        return wrapped

    @guard
    async def index(_request):
        return web.Response(text=PAGE, content_type="text/html")

    @guard
    async def state(_request):
        body = journal.state_out()
        body["live"] = shot is not None
        return web.json_response(body)

    @guard
    async def image(request):
        path = journal.image_path(request.match_info["entry"])
        if not path.exists():
            return web.Response(status=404, text="yo'q")
        return web.Response(body=path.read_bytes(), content_type="image/png")

    @guard
    async def live(_request):
        if shot is None:
            return web.Response(status=404, text="brauzer yo'q")
        return web.Response(body=await shot(), content_type="image/jpeg")

    @guard
    async def zip_all(_request):
        # Built off the event loop: zipping two hundred pictures is real work,
        # and the agent is drawing while you press the button.
        body = await asyncio.to_thread(journal.zip_bytes)
        return web.Response(body=body, content_type="application/zip",
                            headers={"Content-Disposition":
                                     'attachment; filename="sarideo-rasmlar.zip"'})

    @guard
    async def as_json(_request):
        return web.Response(
            text=json.dumps(journal.entries, ensure_ascii=False, indent=2),
            content_type="application/json",
            headers={"Content-Disposition":
                     'attachment; filename="sarideo-jurnal.json"'})

    @guard
    async def as_csv(_request):
        # Bytes rather than `text=`: aiohttp refuses a content type that carries
        # its own charset, and a spreadsheet opening a UTF-8 CSV needs to be told.
        return web.Response(
            body=journal.csv_text().encode("utf-8"),
            content_type="text/csv", charset="utf-8",
            headers={"Content-Disposition":
                     'attachment; filename="sarideo-jurnal.csv"'})

    async def favicon(_request):
        # Answered without a token: it is a browser habit, not a request for
        # anything of yours, and refusing it only fills the console with 403s.
        return web.Response(status=204)

    async def elsewhere(request):
        # A token typed as a path rather than a query — the shape a link takes
        # when it is copied by hand from a log into a phone.
        guess = request.path.strip("/")
        if guess and secrets.compare_digest(guess, token):
            raise web.HTTPFound(f"/?t={token}")
        return web.Response(status=403, text="token kerak: .../?t=<token>")

    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.get("/api/state", state),
        web.get("/img/{entry}", image),
        web.get("/shot", live),
        web.get("/export/images.zip", zip_all),
        web.get("/export/journal.json", as_json),
        web.get("/export/journal.csv", as_csv),
        web.get("/favicon.ico", favicon),
        web.get("/{rest:.*}", elsewhere),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner


def public_url(port: int, token: str) -> str:
    """Where to open it — the platform's own address when it has one."""
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    base = f"https://{domain}" if domain else f"http://localhost:{port}"
    return f"{base}/?t={token}"
