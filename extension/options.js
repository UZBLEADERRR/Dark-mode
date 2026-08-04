// The panel. Two things this extension does, one tab each, and the last forty
// things that happened tucked away underneath — which is the only part anybody
// reads once it is working.

const $ = (id) => document.getElementById(id);

const DEFAULTS = {
  server: "http://localhost:8000", agent: "http://localhost:8001",
  worker: "", running: false, logs: [],
  // Remembered rather than re-picked every time: you send picture after picture
  // to the same project all afternoon.
  lastJob: "", lastMode: "auto", lastScope: "empty", lastSource: "page",
  sending: null,
};

const NOTES = {
  page: "Flow varag‘ini ochib, promptlarni o‘zingiz bajaring. Rasmlar sahifada "
      + "turganda shu tugmani bosasiz — sahifadagi hamma rasm loyihaga yuboriladi "
      + "va Sarideo har birini qaysi sahnaga tushishini o‘zi topadi.",
  agent: "Flow Agent o‘rnatilgan bo‘lsa, rasmlarni o‘sha yerda yasayvering — bu "
       + "tugma uning tarixidagi rasmlarni olib, loyihaga yuboradi. Flow Agent’ning "
       + "o‘zi o‘zgarmaydi: undan faqat tarix o‘qiladi.",
};

/** Opened as a file rather than installed as an extension.
 *
 *  Unzipping and double-clicking `options.html` is the obvious thing to do with
 *  a folder full of files, and on a `file://` page every `chrome.*` API is
 *  undefined — so the panel looked completely normal and not one button did
 *  anything. A page that cannot work should say so instead of sitting there.
 */
function looksInstalled() {
  return typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id;
}

if (!looksInstalled()) {
  document.body.innerHTML = `
    <div class="head"><i class="dot"></i><h1>Kengaytma o‘rnatilmagan</h1></div>
    <div class="card">
      <p class="note" style="margin:0">Bu sahifa oddiy fayl sifatida ochilgan,
        shuning uchun tugmalar ishlamaydi. Kengaytmani o‘rnatish kerak:</p>
      <ol>
        <li>Arxivni <b>doimiy papkaga</b> chiqaring (Temp emas — masalan
          <code>Hujjatlar\\sarideo-flow</code>).</li>
        <li>Chrome’da <code>chrome://extensions</code> ni oching.</li>
        <li>O‘ng yuqoridan <b>Developer mode</b> ni yoqing.</li>
        <li><b>Load unpacked</b> → o‘sha <b>papkani</b> tanlang (faylni emas).</li>
        <li>Chrome panelidagi kengaytma belgisini bosing — shu oyna qaytadan
          ochiladi va ishlaydi.</li>
      </ol>
    </div>`;
  throw new Error("not installed");
}

const base = (server) => String(server || "").replace(/\/+$/, "");

// ── tabs ─────────────────────────────────────────────────────────────

function showTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.tab === name)));
  $("tab-send").classList.toggle("hidden", name !== "send");
  $("tab-queue").classList.toggle("hidden", name !== "queue");
}

document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));

// ── settings ─────────────────────────────────────────────────────────

async function save() {
  await chrome.storage.local.set({
    server: $("server").value.trim(),
    agent: $("agent").value.trim(),
    worker: $("worker").value.trim(),
    lastJob: $("job").value,
    lastMode: $("mode").value,
    lastScope: $("scope").value,
    lastSource: $("source").value,
  });
}

["server", "agent", "worker"].forEach((id) =>
  $(id).addEventListener("change", async () => { await save(); await refresh(); }));
["mode", "scope"].forEach((id) => $(id).addEventListener("change", save));
$("source").addEventListener("change", async () => { await save(); await drawJob(); });
$("job").addEventListener("change", async () => { await save(); await drawJob(); });

// ── what is going on right now ────────────────────────────────────────

function say(where, text, kind = "") {
  const el = $(where);
  el.textContent = text;
  el.className = `msg ${kind}`;
  el.classList.toggle("hidden", !text);
}

async function refresh() {
  const s = await chrome.storage.local.get(DEFAULTS);
  const server = base(s.server);

  let queue = "ulanib bo‘lmadi";
  let reachable = false;
  try {
    const resp = await fetch(`${server}/api/flow/tasks`);
    if (resp.ok) {
      reachable = true;
      queue = `${(await resp.json()).waiting} ta prompt kutyapti`;
    } else {
      queue = `xato ${resp.status}`;
    }
  } catch {
    // The usual cause is a server address that is wrong or not running, and
    // saying so beats a red console message the user will never see.
  }

  $("queue-meta").innerHTML = `Navbat: <b>${queue}</b>`;
  const pill = $("pill");
  if (s.sending) {
    pill.textContent = `yuborilmoqda ${s.sending.done}/${s.sending.total}`;
    pill.className = "pill busy";
  } else {
    pill.textContent = s.running ? "ishlayapti" : reachable ? "ulandi" : "ulanmagan";
    pill.className = `pill${s.running || reachable ? " on" : ""}`;
  }
  return { ...s, server, reachable };
}

// ── the project list ─────────────────────────────────────────────────

let jobs = [];

async function loadJobs(state) {
  const picker = $("job");
  try {
    const resp = await fetch(`${base(state.server)}/api/jobs?limit=40`);
    if (!resp.ok) throw new Error(`${resp.status}`);
    jobs = await resp.json();
  } catch {
    picker.innerHTML = '<option value="">— Sarideoga ulanib bo‘lmadi —</option>';
    return;
  }
  // Only the ones a picture can actually be put into. A finished video will take
  // pictures too, but one still being written has no scenes to put them in.
  const usable = jobs.filter((j) => ["review", "done"].includes(j.status) && j.scene_count);
  if (!usable.length) {
    picker.innerHTML = '<option value="">— tayyor loyiha yo‘q —</option>';
    return;
  }
  picker.innerHTML = usable.map((j) =>
    `<option value="${j.id}">${(j.title || j.topic || j.id).slice(0, 46)}</option>`).join("");
  if (usable.some((j) => j.id === state.lastJob)) picker.value = state.lastJob;
  jobs = usable;
}

/** How many scenes of the chosen project are still without a picture. */
async function drawJob() {
  const chosen = jobs.find((j) => j.id === $("job").value);
  if (!chosen) {
    // No project to describe, but the page count is still worth showing — it is
    // how you tell whether the extension can see Flow at all.
    $("send-meta").dataset.job = "";
    await drawCount();
    return;
  }
  const state = await chrome.storage.local.get(DEFAULTS);
  let bare = null;
  try {
    const resp = await fetch(`${base(state.server)}/api/jobs/${chosen.id}`);
    if (resp.ok) {
      const job = await resp.json();
      bare = (job.scenes || []).filter((s) => s.needs_image || !s.image_url).length;
    }
  } catch {
    // Not worth a message of its own — the scene count below still tells the
    // user which project they are pointing at.
  }
  $("send-meta").dataset.job = chosen.scene_count
    ? `<b>${chosen.scene_count}</b> sahna${bare === null ? "" : ` · <b>${bare}</b> tasi rasm kutyapti`}`
    : "";
  await drawCount();
}

/** How many pictures the chosen source is holding right now. */
async function drawCount() {
  const meta = $("send-meta");
  const job = meta.dataset.job || "";
  const source = $("source").value;
  $("send-note").textContent = NOTES[source];
  let found = "";

  if (source === "agent") {
    const { agent } = await chrome.storage.local.get(DEFAULTS);
    try {
      const resp = await fetch(`${base(agent)}/v1/history`);
      if (!resp.ok) throw new Error(`${resp.status}`);
      const body = await resp.json();
      const images = (body.history || []).filter((i) => i.type !== "video" && i.url);
      found = `Flow Agent’da <b>${images.length}</b> ta rasm`;
    } catch {
      found = "Flow Agent ishlamayapti";
    }
  } else {
    try {
      const tabs = await chrome.tabs.query({
        url: ["https://labs.google/*", "https://flow.google/*"],
      });
      if (!tabs.length) {
        found = "Flow varag‘i ochiq emas";
      } else {
        const out = await chrome.tabs.sendMessage(tabs[0].id, { kind: "sarideo:count" });
        found = `Sahifada <b>${out?.count || 0}</b> ta rasm`;
      }
    } catch {
      found = "Flow varag‘i javob bermadi — sahifani yangilang";
    }
  }
  meta.innerHTML = [job, found].filter(Boolean).join(" &nbsp;·&nbsp; ");
}

// ── sending ──────────────────────────────────────────────────────────

$("send").addEventListener("click", async () => {
  const jobId = $("job").value;
  if (!jobId) { say("send-msg", "Avval loyihani tanlang.", "err"); return; }
  await save();

  const button = $("send");
  const bar = $("send-bar");
  button.disabled = true;
  button.textContent = "Yuborilmoqda…";
  bar.classList.remove("hidden");
  say("send-msg", "");

  // The service worker writes its progress to storage as it reads the page, so
  // the panel can follow along instead of sitting on a dead button for a minute.
  const watching = setInterval(async () => {
    const { sending } = await chrome.storage.local.get({ sending: null });
    if (!sending) return;
    bar.firstElementChild.style.width =
      `${Math.round(100 * sending.done / Math.max(sending.total, 1))}%`;
    button.textContent = `Yuborilmoqda… ${sending.done}/${sending.total}`;
  }, 400);

  try {
    const answer = await chrome.runtime.sendMessage({
      kind: "sarideo:send", jobId, mode: $("mode").value, scope: $("scope").value,
      source: $("source").value,
    });
    if (!answer?.ok) throw new Error(answer?.error || "Yuborilmadi");
    say("send-msg", `${answer.sent} ta rasm yuborildi — Sarideo joylashtirmoqda.`, "ok");
  } catch (exc) {
    say("send-msg", exc.message, "err");
  } finally {
    clearInterval(watching);
    await chrome.storage.local.set({ sending: null });
    bar.classList.add("hidden");
    bar.firstElementChild.style.width = "0";
    button.disabled = false;
    button.textContent = "Sarideoga yuborish";
    await load();
  }
});

// ── the queue ────────────────────────────────────────────────────────

$("start").addEventListener("click", async () => {
  await save();
  await chrome.runtime.sendMessage({ kind: "sarideo:start" });
  setTimeout(load, 500);
});

$("stop").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ kind: "sarideo:stop" });
  setTimeout(load, 300);
});

$("once").addEventListener("click", async () => {
  await save();
  await chrome.runtime.sendMessage({ kind: "sarideo:once" });
  setTimeout(load, 1500);
});

$("probe").addEventListener("click", async () => {
  $("settings").open = true;
  const tabs = await chrome.tabs.query({
    url: ["https://labs.google/*", "https://flow.google/*"],
  });
  if (!tabs.length) {
    $("log").textContent = "Flow varag‘i ochiq emas — avval labs.google/fx ni oching.";
    return;
  }
  try {
    const out = await chrome.tabs.sendMessage(tabs[0].id, { kind: "sarideo:probe" });
    $("log").textContent = [
      `sahifa: ${out.url}`,
      `prompt maydoni: ${out.prompt}`,
      `yuborish tugmasi: ${out.submit}`,
      `sahifadagi rasmlar: ${out.pictures}`,
    ].join("\n");
  } catch (exc) {
    $("log").textContent = `Varaq javob bermadi: ${exc.message}\n`
      + "Sahifani yangilab, qaytadan urinib ko‘ring.";
  }
});

// ── start ────────────────────────────────────────────────────────────

async function load() {
  const state = await refresh();
  $("server").value = state.server;
  $("agent").value = state.agent;
  $("worker").value = state.worker;
  $("mode").value = state.lastMode;
  $("scope").value = state.lastScope;
  $("source").value = state.lastSource;
  $("log").textContent =
    (state.logs || []).slice().reverse().join("\n") || "Hali hech narsa yo‘q.";
  await loadJobs(state);
  await drawJob();
}

load();
