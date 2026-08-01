"""Draw Sarideo's scenes in a browser that is not the user's phone.

The Chrome extension solves this for a laptop. A phone cannot run it — Chrome
on Android has no extensions — so the browser has to live somewhere that is
always on: a spare PC, a small VPS, or a second service next to Sarideo itself.
This is that browser, plus the two things it needs to be useful:

  `login`   opens Flow and hands you the controls, over the network, so you can
            sign in to Google **yourself** from your phone. Nothing here types a
            password, and none is stored: what is saved is the browser profile
            the login produced, exactly as if you had used that machine.

  `run`     polls Sarideo's queue and answers it — the same queue, the same
            endpoints, the same picture at the other end as the extension.

The page-driving code is not in this file. It is `extension/flow-dom.js`, read
and evaluated in the page, so the extension and the agent break and get fixed
together instead of drifting apart.

Two things to know before running it:

  * The Google session now lives on that machine. The extension was built the
    other way round on purpose — it never sees the account — and this gives that
    up in exchange for working without you present. Run it somewhere you control.
  * Google challenges sign-ins from datacenter addresses far more readily than
    from a home connection. A VPS may hit a verification screen that a PC at home
    never sees; the remote view will show it, and you answer it the same way you
    would anywhere else.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - the message is the whole point
    sys.exit("playwright kerak:  pip install -r requirements.txt "
             "&& playwright install chromium")

HERE = Path(__file__).resolve().parent
# Beside the repo checkout normally; the container image copies it to /extension,
# which is why this looks in two places rather than one.
FLOW_DOM = next((p for p in (HERE.parent / "extension" / "flow-dom.js",
                             Path("/extension/flow-dom.js"))
                 if p.exists()), HERE.parent / "extension" / "flow-dom.js")
FLOW_URL = "https://labs.google/fx/tools/flow"

# Where the signed-in browser profile is kept. A directory, not a cookie jar:
# Google's session is more than cookies, and a profile is what survives.
PROFILE = Path(os.environ.get("SARIDEO_PROFILE", HERE / "profile"))


def dom_source() -> str:
    """The shared page-driving code, ready to evaluate.

    It ends by assigning `window.sarideoFlow`, which is what makes it usable from
    here — the same object the extension's content script calls.
    """
    if not FLOW_DOM.exists():
        raise SystemExit(f"{FLOW_DOM} topilmadi — reponing ichidan ishga tushiring.")
    return FLOW_DOM.read_text(encoding="utf-8")


async def browser(play, *, headless: bool):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return await play.chromium.launch_persistent_context(
        str(PROFILE),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
        executable_path=os.environ.get("SARIDEO_CHROME") or None,
    )


# ── the remote view ───────────────────────────────────────────────────────────
# A screenshot and a tap relay. Not a general remote desktop — just enough to get
# through a Google sign-in from a phone, which is the one thing that cannot be
# automated and should not be.

PAGE = """<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sarideo — Flow'ga kirish</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;background:#111;color:#eee;font:14px system-ui,sans-serif}
 header{display:flex;gap:8px;padding:8px;align-items:center;flex-wrap:wrap}
 input,button{font:inherit;padding:8px 10px;border-radius:8px;border:1px solid #333;
   background:#1c1f24;color:#eee}
 button{cursor:pointer}
 button.go{background:#ff3b30;border-color:#ff3b30;font-weight:600}
 #wrap{position:relative;width:100%}
 img{width:100%;display:block;touch-action:manipulation}
 #msg{padding:6px 10px;color:#9aa0a6;font-size:12px}
</style></head><body>
<header>
  <button id="back">‹</button>
  <input id="keys" placeholder="matn yozib Enter bosing" style="flex:1;min-width:120px">
  <button id="enter">Enter</button>
  <button class="go" id="done">Kirdim — saqla</button>
</header>
<p id="msg">Sahifa har soniyada yangilanadi. Rasm ustiga bosing — o‘sha joyga bosiladi.</p>
<div id="wrap"><img id="shot" alt=""></div>
<script>
const token = new URLSearchParams(location.search).get("t") || "";
const send = (path, body) => fetch(path + "?t=" + encodeURIComponent(token),
  {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify(body||{})});
const shot = document.getElementById("shot");
let busy = false;
async function refresh(){
  if (busy) return;
  busy = true;
  try { shot.src = "/shot?t=" + encodeURIComponent(token) + "&n=" + Date.now(); }
  finally { busy = false; }
}
setInterval(refresh, 1000); refresh();
shot.addEventListener("click", (e) => {
  const r = shot.getBoundingClientRect();
  send("/tap", { x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height });
});
document.getElementById("keys").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  send("/type", { text: e.target.value }).then(() => { e.target.value = ""; });
});
document.getElementById("enter").addEventListener("click", () => send("/key", {key:"Enter"}));
document.getElementById("back").addEventListener("click", () => send("/back", {}));
document.getElementById("done").addEventListener("click", async () => {
  await send("/done", {});
  document.getElementById("msg").textContent = "Saqlandi. Bu oynani yopsangiz bo‘ladi.";
});
</script></body></html>"""


async def serve_login(page, host: str, port: int, token: str) -> None:
    """Hand the browser's controls to a phone until it says it is done."""
    from aiohttp import web

    finished = asyncio.Event()

    def allowed(request) -> bool:
        # The one endpoint set in this project that must be authenticated: it
        # drives a browser that is about to hold a Google session.
        return secrets.compare_digest(request.query.get("t", ""), token)

    async def index(request):
        if not allowed(request):
            return web.Response(status=403, text="token noto'g'ri")
        return web.Response(text=PAGE, content_type="text/html")

    async def shot(request):
        if not allowed(request):
            return web.Response(status=403)
        return web.Response(body=await page.screenshot(type="jpeg", quality=60),
                            content_type="image/jpeg",
                            headers={"cache-control": "no-store"})

    async def tap(request):
        if not allowed(request):
            return web.Response(status=403)
        body = await request.json()
        size = page.viewport_size or {"width": 1280, "height": 900}
        await page.mouse.click(float(body["x"]) * size["width"],
                               float(body["y"]) * size["height"])
        return web.json_response({"ok": True})

    async def type_text(request):
        if not allowed(request):
            return web.Response(status=403)
        await page.keyboard.type(str((await request.json()).get("text", "")), delay=30)
        return web.json_response({"ok": True})

    async def key(request):
        if not allowed(request):
            return web.Response(status=403)
        await page.keyboard.press(str((await request.json()).get("key", "Enter")))
        return web.json_response({"ok": True})

    async def back(request):
        if not allowed(request):
            return web.Response(status=403)
        await page.go_back()
        return web.json_response({"ok": True})

    async def done(request):
        if not allowed(request):
            return web.Response(status=403)
        finished.set()
        return web.json_response({"ok": True})

    app = web.Application()
    app.add_routes([
        web.get("/", index), web.get("/shot", shot),
        web.post("/tap", tap), web.post("/type", type_text), web.post("/key", key),
        web.post("/back", back), web.post("/done", done),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    # Printed rather than assumed: on a hosting platform the address is the
    # service's public URL, and on a home machine it is an address on the LAN.
    # Only the path and the token are ours to state.
    public = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("PUBLIC_URL", "")
    where = (f"https://{public.replace('https://', '').rstrip('/')}"
             if public else f"http://<shu-mashina>:{port}")
    print(f"\n  Telefoningizdan oching:  {where}/?t={token}\n"
          f"  Google'ga kirib bo'lgach «Kirdim — saqla» ni bosing.\n", flush=True)
    await finished.wait()
    await runner.cleanup()


async def login(args) -> None:
    async with async_playwright() as play:
        ctx = await browser(play, headless=args.headless)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.goto(args.url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001 - the view is the way to fix this
            # A first load that fails is exactly when the remote view is most
            # wanted: you can see the error, go back, try again. Dying with a
            # traceback instead leaves nothing to look at.
            print(f"Sahifa ochilmadi ({exc}). Oyna baribir ochiladi.", flush=True)
        if args.headless:
            await serve_login(page, args.host, args.port, args.token or secrets.token_urlsafe(9))
        else:
            print("Brauzer ochildi. Google'ga kiring, keyin shu yerda Enter bosing.")
            await asyncio.get_event_loop().run_in_executor(None, input)
        await ctx.close()
    print(f"Profil saqlandi: {PROFILE}")


# ── the worker ────────────────────────────────────────────────────────────────

class Sarideo:
    """The same three calls the extension makes."""

    def __init__(self, base: str, worker: str) -> None:
        self.base = base.rstrip("/")
        self.worker = worker

    async def claim(self, session):
        async with session.post(f"{self.base}/api/flow/next",
                                params={"worker": self.worker}) as resp:
            resp.raise_for_status()
            return (await resp.json()).get("task")

    async def deliver(self, session, task_id: str, png: bytes):
        import aiohttp
        data = aiohttp.FormData()
        data.add_field("image", png, filename="flow.png", content_type="image/png")
        async with session.post(f"{self.base}/api/flow/tasks/{task_id}/image",
                                data=data) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"{resp.status} {(await resp.text())[:160]}")

    async def give_up(self, session, task_id: str, reason: str, retry: bool):
        async with session.post(f"{self.base}/api/flow/tasks/{task_id}/fail",
                                json={"reason": reason[:280], "retry": retry}):
            pass


async def draw(page, prompt: str) -> bytes:
    """One picture, using the extension's own page code."""
    await page.evaluate(dom_source())
    data_url = await page.evaluate("(p) => window.sarideoFlow.make(p)", prompt)
    head, _, body = str(data_url).partition(",")
    if not body:
        raise RuntimeError("Rasm noto'g'ri shaklda qaytdi")
    return base64.b64decode(body)


async def run(args) -> None:
    import aiohttp

    api = Sarideo(args.server, args.worker)
    async with async_playwright() as play:
        ctx = await browser(play, headless=not args.headed)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    task = await api.claim(session)
                except Exception as exc:  # noqa: BLE001 - the server may be asleep
                    print(f"Sarideo javob bermadi: {exc}", flush=True)
                    await asyncio.sleep(args.idle)
                    continue
                if task is None:
                    await asyncio.sleep(args.idle)
                    continue

                print(f"{task['scene'] + 1}-sahna: {task['prompt'][:60]}…", flush=True)
                try:
                    png = await draw(page, task["prompt"])
                    await api.deliver(session, task["id"], png)
                    print(f"{task['scene'] + 1}-sahna yuborildi", flush=True)
                except Exception as exc:  # noqa: BLE001
                    # Retryable, for the same reason as in the extension: almost
                    # everything that goes wrong here is the page or the network,
                    # and neither is a reason to leave a scene without a picture.
                    print(f"Xato: {exc}", flush=True)
                    await api.give_up(session, task["id"], str(exc), True)
                    await page.goto(args.url, wait_until="domcontentloaded")
                await asyncio.sleep(args.gap)


async def probe(args) -> None:
    async with async_playwright() as play:
        ctx = await browser(play, headless=not args.headed)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await page.evaluate(dom_source())
        print(json.dumps(await page.evaluate("() => window.sarideoFlow.probe()"),
                         ensure_ascii=False, indent=2))
        await ctx.close()


def main() -> None:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--url", default=FLOW_URL)
    parent.add_argument("--headed", action="store_true",
                        help="ko'rinadigan brauzer (ekrani bor mashinada)")

    ap = argparse.ArgumentParser(description="Sarideo uchun Flow agenti")
    subs = ap.add_subparsers(dest="cmd", required=True)

    lg = subs.add_parser("login", parents=[parent],
                         help="Google'ga bir marta kirib, profilni saqlash")
    lg.add_argument("--headless", action="store_true", default=True)
    lg.add_argument("--host", default="0.0.0.0")
    # `PORT` is what every hosting platform hands a service, and this is the one
    # command that has to be reachable from outside — a login page on a port the
    # platform is not routing is a login page nobody can open.
    lg.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8777))
    # From the environment too, so the token can be set before the deploy rather
    # than fished out of the logs on a phone.
    lg.add_argument("--token", default=os.environ.get("SARIDEO_LOGIN_TOKEN", ""))
    lg.set_defaults(func=login)

    rn = subs.add_parser("run", parents=[parent], help="navbatni ishlash")
    rn.add_argument("--server", default=os.environ.get("SARIDEO_URL", "http://localhost:8000"))
    rn.add_argument("--worker", default=os.environ.get("SARIDEO_WORKER", "agent"))
    rn.add_argument("--gap", type=float, default=6.0)
    rn.add_argument("--idle", type=float, default=15.0)
    rn.set_defaults(func=run)

    pb = subs.add_parser("probe", parents=[parent],
                         help="Flow sahifasi tanilyaptimi — tekshirish")
    pb.set_defaults(func=probe)

    args = ap.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
