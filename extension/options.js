// The panel. Two settings, three buttons, and the last forty things that
// happened — which is the only part anybody actually reads.

const $ = (id) => document.getElementById(id);

const DEFAULTS = { server: "http://localhost:8000", worker: "", running: false, logs: [] };

async function load() {
  const s = await chrome.storage.local.get(DEFAULTS);
  $("server").value = s.server;
  $("worker").value = s.worker;
  $("log").textContent = (s.logs || []).slice().reverse().join("\n") || "Hali hech narsa yo‘q.";
  await refresh(s);
}

async function refresh(s) {
  const state = s || (await chrome.storage.local.get(DEFAULTS));
  const server = String(state.server || "").replace(/\/+$/, "");
  let queue = "—";
  try {
    const resp = await fetch(`${server}/api/flow/tasks`);
    queue = resp.ok ? `${(await resp.json()).waiting} ta kutilyapti` : `xato ${resp.status}`;
  } catch {
    // The usual cause is a server address that is wrong or not running, and
    // saying so beats a red console message the user will never see.
    queue = "ulanib bo‘lmadi";
  }
  $("status").textContent = `${state.running ? "Ishlayapti" : "To‘xtatilgan"} · navbat: ${queue}`;
}

async function save() {
  await chrome.storage.local.set({
    server: $("server").value.trim(),
    worker: $("worker").value.trim(),
  });
  await refresh();
}

$("server").addEventListener("change", save);
$("worker").addEventListener("change", save);

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
  const tabs = await chrome.tabs.query({ url: "https://labs.google/*" });
  const tab = tabs.find((t) => (t.url || "").includes("/fx/"));
  if (!tab) {
    $("log").textContent = "Flow varag‘i ochiq emas — avval labs.google/fx ni oching.";
    return;
  }
  try {
    const out = await chrome.tabs.sendMessage(tab.id, { kind: "sarideo:probe" });
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

load();
