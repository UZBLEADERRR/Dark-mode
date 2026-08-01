// The bridge. Sarideo has prompts and no browser; this browser has a Google
// account and no prompts. All this does is carry one to the other.
//
// It deliberately holds nothing sensitive. The Google session lives where it
// already lived — in the browser's own cookies for labs.google — and is never
// read, copied or sent anywhere. What crosses this file is a prompt going one
// way and a PNG coming back.
//
// The loop, in full:
//
//   1. ask Sarideo for a prompt that nobody is working on
//   2. find or open a Flow tab, and give the prompt to the content script
//   3. wait for the content script to hand back an image
//   4. POST it to Sarideo, which writes it into the scene that was waiting
//
// Everything about *how step 2 and 3 actually work* is in `flow.js`, because
// that is the part that Google can change without warning. This file only knows
// that it sends a message and gets a picture back — so when Flow's page changes,
// there is exactly one file to fix.

const DEFAULTS = {
  server: "http://localhost:8000",
  worker: "",
  running: false,
  // Between tasks. Flow is doing real work in there; hammering it helps nobody
  // and makes the tab unusable for anything else.
  gapSeconds: 6,
  // When the queue is empty, ask less often.
  idleSeconds: 15,
};

let state = { ...DEFAULTS };
let busy = false;

async function settings() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  // A worker name that is stable per install, so the queue can say who took
  // what when two browsers are helping.
  if (!stored.worker) {
    stored.worker = `chrome-${Math.random().toString(36).slice(2, 8)}`;
    await chrome.storage.local.set({ worker: stored.worker });
  }
  return stored;
}

function base(server) {
  return String(server || "").replace(/\/+$/, "");
}

async function log(message) {
  const line = `${new Date().toLocaleTimeString()} — ${message}`;
  const { logs = [] } = await chrome.storage.local.get({ logs: [] });
  await chrome.storage.local.set({ logs: [...logs, line].slice(-40) });
}

// --- Sarideo ----------------------------------------------------------------

/** `fetch`, with the failure named after what it was doing.
 *
 *  A bare "Failed to fetch" is the same three words whether the server address
 *  is wrong, the laptop is offline, or the picture came back malformed — and it
 *  is the only thing the user ever sees. Every call here says which side it was
 *  talking to.
 */
async function ask(what, url, options) {
  let resp;
  try {
    resp = await fetch(url, options);
  } catch (exc) {
    throw new Error(`${what}: ulanib bo'lmadi (${base(state.server)}) — ${exc.message}`);
  }
  if (!resp.ok) throw new Error(`${what}: ${resp.status} ${(await resp.text()).slice(0, 120)}`);
  return resp;
}

async function claim() {
  const url = `${base(state.server)}/api/flow/next?worker=${encodeURIComponent(state.worker)}`;
  const body = await (await ask("Navbatni so'rash", url, { method: "POST" })).json();
  return body.task || null;
}

async function deliver(taskId, blob) {
  const body = new FormData();
  body.append("image", blob, "flow.png");
  await ask("Rasmni yuborish", `${base(state.server)}/api/flow/tasks/${taskId}/image`,
            { method: "POST", body });
}

async function giveUp(taskId, reason, retry) {
  await fetch(`${base(state.server)}/api/flow/tasks/${taskId}/fail`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: String(reason).slice(0, 280), retry: !!retry }),
  });
}

// --- the Flow tab -----------------------------------------------------------

const FLOW_URL = "https://labs.google/fx/tools/flow";

async function flowTab() {
  // Any Google Labs tab will do. Insisting on "/fx/" in the path meant that the
  // day Google moves Flow, the extension stops finding a tab that is sitting
  // right there in front of you.
  const tabs = await chrome.tabs.query({
    url: ["https://labs.google/*", "https://flow.google/*"],
  });
  if (tabs.length) return tabs[0];
  // Not focused: this is meant to work while you use the browser for something
  // else. An extension that steals the foreground every ninety seconds is one
  // you turn off by lunchtime.
  return chrome.tabs.create({ url: FLOW_URL, active: false });
}

/** Wait until the content script in this tab is answering.
 *
 *  A tab that has just been created exists before its page does, and messaging
 *  it in that gap fails with "receiving end does not exist" — which looked
 *  exactly like Flow refusing the prompt and sent the task back round the queue
 *  for no reason. A ping costs nothing and turns the first run after opening a
 *  tab from a guaranteed miss into a wait.
 */
async function ready(tabId, seconds = 40) {
  for (let i = 0; i < seconds; i++) {
    try {
      const pong = await chrome.tabs.sendMessage(tabId, { kind: "sarideo:ping" });
      if (pong?.ok) return;
    } catch {
      // Not up yet. That is the expected answer for the first second or two.
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("Flow varag'i yuklanmadi — sahifa ochiqmi?");
}

/** Ask the content script for one picture. Resolves to a Blob. */
async function drawInFlow(tab, task) {
  await ready(tab.id);
  const answer = await chrome.tabs.sendMessage(tab.id, {
    kind: "sarideo:make",
    prompt: task.prompt,
    aspect: task.aspect,
  });
  if (!answer) throw new Error("Flow varag'i javob bermadi");
  if (answer.error) throw new Error(answer.error);
  if (!answer.dataUrl) throw new Error("Rasm qaytmadi");
  return dataUrlToBlob(answer.dataUrl);
}

/** Decode a data: URL by hand rather than with `fetch`.
 *
 *  A service worker is not a document, and what it is allowed to fetch is not
 *  the same list. Decoding base64 is four lines and cannot be withdrawn.
 */
function dataUrlToBlob(dataUrl) {
  const [head, body] = String(dataUrl).split(",", 2);
  if (!body) throw new Error("Rasm noto'g'ri shaklda qaytdi");
  const type = (head.match(/data:([^;]+)/) || [, "image/png"])[1];
  const binary = atob(body);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type });
}

// --- the loop ---------------------------------------------------------------

/** Wake up later. An alarm, not a timer: a service worker is killed when idle,
 *  and a `setTimeout` dies with it. Chrome will not fire an alarm sooner than
 *  half a minute, which is why it is only used for *idle* — the busy case never
 *  waits on it. */
async function again() {
  if (!state.running) return;
  await chrome.alarms.create("tick",
                             { delayInMinutes: Math.max(state.idleSeconds, 30) / 60 });
}

async function handle(task) {
  await log(`${task.scene + 1}-sahna olindi`);
  let tab;
  try {
    tab = await flowTab();
  } catch (exc) {
    // No tab means nothing is wrong with the prompt, so it goes back in the
    // queue rather than leaving the scene without a picture.
    await giveUp(task.id, `Flow varag'i ochilmadi: ${exc.message}`, true);
    throw exc;
  }
  try {
    const blob = await drawInFlow(tab, task);
    await deliver(task.id, blob);
    await log(`${task.scene + 1}-sahna yuborildi`);
  } catch (exc) {
    // Retryable by default: almost everything that goes wrong here is the tab,
    // the network, or Flow being busy, and none of those are a reason to leave a
    // scene without a picture forever.
    await giveUp(task.id, exc.message, true);
    await log(`Xato: ${exc.message}`);
  }
}

/** Work the queue until it is empty. `force` does a single task even when the
 *  loop is stopped — which is what a "do one now" button means, and what it
 *  refused to do while it checked `running` first. */
async function once(force = false) {
  if (busy) return;
  busy = true;
  try {
    state = await settings();
    if (!state.running && !force) return;

    do {
      const task = await claim();
      if (!task) break;
      await handle(task);
      if (force) break;
      // Re-read rather than trust the copy taken at the top: "stop" pressed
      // during a five-minute generation should stop it, not be noticed an hour
      // later when the backlog runs out.
      state = await settings();
      if (state.running) await new Promise((r) => setTimeout(r, state.gapSeconds * 1000));
    } while (state.running);
  } catch (exc) {
    await log(`Xato: ${exc.message}`);
  } finally {
    busy = false;
    await again();
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "tick") once();
});

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg?.kind === "sarideo:start") {
    chrome.storage.local.set({ running: true }).then(() => {
      once();
      respond({ ok: true });
    });
    return true;
  }
  if (msg?.kind === "sarideo:stop") {
    chrome.storage.local.set({ running: false }).then(() => respond({ ok: true }));
    return true;
  }
  if (msg?.kind === "sarideo:once") {
    once(true).then(() => respond({ ok: true }));
    return true;
  }
  return false;
});

chrome.runtime.onStartup.addListener(() => once());
chrome.runtime.onInstalled.addListener(() => once());
