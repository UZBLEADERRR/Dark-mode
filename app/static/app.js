/* Sarideo — frontend.
   Write a topic or a script, watch it build, edit the scenes, publish. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  health: null,
  heroes: [],
  music: [],
  sfx: [],
  assets: [],
  brand: null,
  jobs: [],
  models: null,
  voices: [],
  available: {},
  loadingModels: false,
  motions: [],
  transitions: [],
  format: '16:9',
  mode: 'topic',
  speed: 'balanced',
  pace: 'steady',
  paces: [],
  cores: 0,
  view: 'create',
  activeId: null,
  poll: null,
  drawn: null,      // stamp of the editor currently on screen
  sheet: null,      // the prompt sheet on screen, and the job state it was
  sheetKey: null,   // fetched for — it is refetched only when that moves
  space: null,      // what is taking up room, as of the last time it was asked
  agent: null,      // where the Flow Agent backend is, and whether the
                    // ready-made extension can be handed out for it
  reveal: false,
  mark: null,       // last `updated_at` we saw, and when we saw it —
  markAt: 0,        // measured here so a server clock offset cannot skew it
};

// A job that has said nothing for this long is waiting on a provider, not
// working. The first number puts a timer on screen; the second offers the exit.
const IDLE_WARN = 20;
const IDLE_STOP = 45;

const MOTIONS = {
  zoom_in: 'Zoom in', zoom_out: 'Zoom out',
  pan_left: 'Chapga', pan_right: "O'ngga",
  pan_up: 'Yuqoriga', pan_down: 'Pastga',
  zoom_in_pan_right: "Zoom in + o'ngga", zoom_out_pan_left: 'Zoom out + chapga',
  zoom_in_pan_left: 'Zoom in + chapga', zoom_out_pan_right: "Zoom out + o'ngga",
  diag_up_right: "Diagonal — yuqori o'ngga", diag_down_left: 'Diagonal — quyi chapga',
  pulse: 'Nafas', sway: 'Tebranish', still: 'Harakatsiz',
};

const TRANSITIONS = {
  fade: 'Yumshoq', fadeblack: 'Qora orqali', fadewhite: 'Oq orqali',
  dissolve: 'Erish', smoothleft: 'Silliq chapga', smoothright: "Silliq o'ngga",
  smoothup: 'Silliq yuqoriga', smoothdown: 'Silliq pastga',
  slideleft: 'Surish chapga', slideright: "Surish o'ngga",
  wipeleft: "Supurish chapga", wiperight: "Supurish o'ngga",
  circleopen: 'Doira ochilishi', circleclose: 'Doira yopilishi',
  radial: 'Radial', pixelize: 'Piksel',
};

const STATUS = {
  queued: 'navbatda', running: 'ishlayapti', rendering: 'render',
  script: "matnni o'qish", review: "ko'rib chiqish", done: 'tayyor',
  failed: 'xato',
};

const BUSY = ['queued', 'running', 'rendering'];
// `script` settles like the others: the job is waiting for a person, not for a
// machine, so polling it would only ask the same question every two seconds.
const SETTLED = ['done', 'failed', 'review', 'script'];

// One click fills the art-style field, which the Imagesmith skill turns into
// the style bible appended to every scene prompt. The 2D entries say what the
// picture is NOT, because image models drift back to photorealism otherwise.
const STYLE_PRESETS = [
  ['Kino', 'cinematic photorealistic, dramatic lighting, 35mm film grain, shallow depth of field'],
  ['2D animatsiya', '2D flat vector illustration, bold clean outlines, limited flat colour palette, '
    + 'cel-shaded animation still, drawn not photographed, no photorealism, no 3D render'],
  ['Anime', 'anime key visual, cel shading, crisp linework, expressive faces, hand-painted '
    + 'background, soft grain, drawn not photographed'],
  ['Akvarel', 'loose watercolour illustration, visible paper texture, soft bleeding edges, '
    + 'muted palette, hand-painted storybook, drawn not photographed'],
  ['3D', 'stylised 3D render, soft global illumination, subsurface scattering, clay-like '
    + 'materials, shallow depth of field'],
  ['Komiks', 'graphic novel panel, heavy ink linework, halftone shading, high contrast, '
    + 'limited spot colour, drawn not photographed'],
];

const STAGE_LABELS = {
  gemini_text: 'Gemini — skript', gemini_text_fallback: 'Gemini — zaxira skript',
  gemini_image: 'Gemini — rasm', gemini_tts: 'Gemini — ovoz',
  gemini_tts_fallback: 'Gemini — zaxira ovoz',
  anthropic_text: 'Claude — skript',
  fal_image: 'fal — rasm (referens bilan)', fal_text2img: 'fal — rasm (referenssiz)',
  openai_text: 'ChatGPT — skript',
  openai_image: 'OpenAI — rasm', openai_tts: 'OpenAI — ovoz',
  openai_transcribe: 'OpenAI — transkripsiya', elevenlabs_tts: 'ElevenLabs — ovoz',
};

// ── utils ─────────────────────────────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error(body?.error || body?.detail || `${res.status} ${res.statusText}`);
  return body;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const clock = (sec) => {
  const s = Math.max(0, Math.round(sec || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

const durationLabel = (s) =>
  s < 60 ? `${s} son` : `${(s / 60) % 1 ? (s / 60).toFixed(1) : s / 60} daq`;

let toastTimer;
function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('on'), 1800);
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // clipboard API needs a secure context; this works everywhere else.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  if (button) {
    const was = button.textContent;
    button.textContent = 'Nusxalandi';
    button.classList.add('done');
    setTimeout(() => { button.textContent = was; button.classList.remove('done'); }, 1400);
  }
  toast('Nusxalandi');
}

// ── modal ─────────────────────────────────────────────────────────
// One small dialog serves every "type this and confirm" moment, so adding a
// scene and repurposing a video do not each grow their own panel.
let modalResolve = null;

function ask({ title, html, ok = 'Qo‘shish', onOpen = null }) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = html;
  $('#modal-ok').textContent = ok;
  // Reset: one dialog that disabled its confirm button must not hand the next
  // one a button that cannot be pressed.
  $('#modal-ok').disabled = false;
  $('#modal').classList.remove('hidden');
  const first = $('#modal-body textarea, #modal-body input, #modal-body select');
  first?.focus();
  // Run after the markup is in the document, so a dialog that has to fetch its
  // own contents can wire itself up without the caller reaching in from outside.
  onOpen?.();
  return new Promise((resolve) => { modalResolve = resolve; });
}

function closeModal(value) {
  $('#modal').classList.add('hidden');
  modalResolve?.(value);
  modalResolve = null;
}

$('#modal [data-m="cancel"]').addEventListener('click', () => closeModal(null));
$('#modal [data-m="ok"]').addEventListener('click', () => closeModal(
  Object.fromEntries($$('#modal-body [name]').map((el) =>
    [el.name, el.type === 'checkbox' ? el.checked : el.value]))));
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(null); });
addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#modal').classList.contains('hidden')) closeModal(null);
});

// ── strips that scroll sideways ───────────────────────────────────
// Four projects with two on screen, or five sections across a phone, look
// exactly like four projects and five sections — there is nothing to say the
// rest exist. A fade on whichever side has more says it, and only there: a strip
// whose contents fit is not faded, and one scrolled to its end stops promising.

function markFades(strip) {
  if (!strip) return;
  const over = strip.scrollWidth - strip.clientWidth;
  strip.classList.toggle('fade-l', over > 2 && strip.scrollLeft > 4);
  strip.classList.toggle('fade-r', over > 2 && strip.scrollLeft < over - 4);
}

const FADED = ['#topnav', '#jobs-list', '#filmstrip', '#chan-row'];

function refreshFades() {
  FADED.forEach((sel) => markFades($(sel)));
}

function wireFades() {
  FADED.forEach((sel) => {
    const strip = $(sel);
    if (!strip) return;
    strip.addEventListener('scroll', () => markFades(strip), { passive: true });
  });
  addEventListener('resize', refreshFades, { passive: true });
  // Content arrives after the first paint, so measuring once at boot would only
  // ever measure an empty strip.
  new MutationObserver(refreshFades).observe(document.body,
    { childList: true, subtree: true });
  refreshFades();
}

// ── the library's folding sections ────────────────────────────────
// Six headings on one page put the model settings and the health list three
// thousand pixels below the fold — on a phone, past every hero, the whole brand
// kit, every layer picture and every track. They fold, and which are open is
// remembered per section, so coming back lands where you left it.

const LIB_KEY = 'studio.library.open';

function libCount(section, text, warn = false) {
  const badge = $(`#count-${section}`);
  if (!badge) return;
  badge.textContent = text || '';
  badge.classList.toggle('warn', !!warn);
}

function libOpenState() {
  try { return JSON.parse(localStorage.getItem(LIB_KEY) || '{}'); } catch { return {}; }
}

function wireLibrarySections() {
  const remembered = libOpenState();
  $$('.lib-sec').forEach((sec) => {
    if (remembered[sec.id] !== undefined) sec.open = !!remembered[sec.id];
    sec.addEventListener('toggle', () => {
      const now = libOpenState();
      now[sec.id] = sec.open;
      try { localStorage.setItem(LIB_KEY, JSON.stringify(now)); } catch { /* private mode */ }
    });
  });
}

// ── navigation ────────────────────────────────────────────────────
function go(view) {
  state.view = view;
  $$('.view').forEach((v) => v.classList.toggle('on', v.id === `view-${view}`));
  $$('#topnav button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.go === view));
  // Deliberately not scrolled into view. The nav fits at every width worth
  // supporting — short labels below 460px — so there is nothing to scroll to, and
  // sliding it under the user's finger on every tap was the whole complaint.
  closeSheet();
  drawDock();
  scrollTo({ top: 0, behavior: 'instant' in document.documentElement.style ? 'instant' : 'auto' });
  if (view === 'edit' || view === 'ready' || view === 'run') loadJobs();
  // A plan changes on its own — the loop builds it while nobody is looking — so
  // arriving here always asks rather than drawing what was true last time.
  if (view === 'plans') loadPlans();
  else followPlans();
  drawEditEmpty();
  drawRunEmpty();
  // The channels may have been added from another device, or the conversation
  // continued there — this screen is the same conversation wherever it is opened.
  if (view === 'chat') { loadProfiles(); growChatInput(); }
}
addEventListener('click', (e) => {
  const target = e.target.closest('[data-go]');
  if (target) go(target.dataset.go);
});

addEventListener('scroll', () => $('.bar').classList.toggle('stuck', scrollY > 8), { passive: true });

// ── the dock ──────────────────────────────────────────────────────
// One row of tools for whichever section you are in — the settings that used to
// be a column of fields below the fold. Each opens a sheet holding the real
// controls; nothing is duplicated, the panels are moved in and out.

const ICONS = {
  format: '<rect x="3" y="5" width="18" height="14" rx="2.5"/>',
  length: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 1.8"/>',
  style: '<path d="M12 3.5l2.4 5.3 5.6.6-4.2 3.9 1.2 5.7L12 16.2 6.9 19l1.2-5.7L4 9.4l5.6-.6z"/>',
  cast: '<circle cx="12" cy="8" r="3.4"/><path d="M5.5 19.5c.6-3.4 3.3-5.3 6.5-5.3s5.9 1.9 6.5 5.3"/>',
  voice: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  music: '<path d="M9 17.5V5l11-2v12.5"/><circle cx="6.5" cy="17.5" r="2.6"/><circle cx="17.5" cy="15.5" r="2.6"/>',
  subtitle: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M7 14.5h5M14.5 14.5h2.5"/>',
  more: '<circle cx="6" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18" cy="12" r="1.3"/>',
  play: '<path d="M8 5.5l11 6.5-11 6.5z"/>',
  text: '<path d="M5 6.5h14M12 6.5v11"/>',
  image: '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="M3 15l5-4 4 3 3-2 6 5"/>',
  actor: '<circle cx="12" cy="5.5" r="2.6"/><path d="M12 8.5v6M12 14.5l-3 5M12 14.5l3 5M7.5 11h9"/>',
  scene: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3 9.5h18M8 5v4M16 5v4"/>',
  layers: '<path d="M12 3.5l8.5 4.5L12 12.5 3.5 8zM3.5 12.5L12 17l8.5-4.5"/>',
  render: '<path d="M12 3.5v11M8 11l4 3.5 4-3.5M4.5 19.5h15"/>',
};

const SHEETS = {
  format: 'Kadr shakli va til',
  length: 'Uzunlik',
  style: 'Vizual uslub',
  cast: 'Herolar',
  voice: 'Ovoz',
  music: 'Fon musiqasi',
  subtitle: 'Subtitr',
  more: 'Boshqa',
};

const DOCK_LABELS = {
  format: 'Format', length: 'Uzunlik', style: 'Uslub', cast: 'Herolar',
  voice: 'Ovoz', music: 'Musiqa', subtitle: 'Subtitr', more: 'Boshqa',
};

const DOCK = {
  create: Object.keys(DOCK_LABELS)
    .map((id) => ({ id, label: DOCK_LABELS[id], icon: ICONS[id], sheet: id })),
  edit: [
    { id: 'play', label: 'Eshitish', icon: ICONS.play, act: () => togglePreview() },
    { id: 'text', label: 'Matn', icon: ICONS.text, act: () => addLayer(TEXT_DEFAULTS) },
    { id: 'image', label: 'Rasm', icon: ICONS.image, act: () => $('#layer-file').click() },
    { id: 'actor', label: 'Aktyor', icon: ICONS.actor, act: () => addActor() },
    { id: 'scene', label: 'Sahna', icon: ICONS.scene, act: () => showPanel('scene') },
    { id: 'layers', label: 'Qatlam', icon: ICONS.layers, act: () => showPanel('layers') },
    { id: 'subtitle', label: 'Subtitr', icon: ICONS.subtitle, act: () => showPanel('caption') },
    { id: 'render', label: 'Render', icon: ICONS.render, act: () => $('#render-btn').click(), hero: true },
  ],
};

// Dubbing decides nothing about the picture, so most of the create tools have
// nothing to act on — offering them would only invite fiddling with settings
// that will be ignored.
const DUB_TOOLS = new Set(['format', 'voice', 'more']);
// Subtitling has no voice to choose and no shape to set — the video already has
// both. What is left is how the words look.
const SUBTITLE_TOOLS = new Set(['subtitle', 'more']);

function drawDock() {
  let items = DOCK[state.view] || [];
  if (state.view === 'create' && state.mode === 'dub') {
    items = items.filter((item) => DUB_TOOLS.has(item.id));
  }
  if (state.view === 'create' && state.mode === 'subtitle') {
    items = items.filter((item) => SUBTITLE_TOOLS.has(item.id));
  }
  // A row of eight tools that cannot be pressed is not information, it is
  // furniture — and it takes the bottom of the screen away from the thing that
  // would have told you what to do instead.
  if (state.view === 'edit' && !ED.job) items = [];
  const dock = $('#dock');
  dock.classList.toggle('hidden', !items.length);
  // The page keeps a dock's worth of room at the bottom. On a screen that has no
  // dock that room is a hole, and a sticky composer sitting above it jumps up by
  // exactly that much as you reach the end of the page.
  document.body.classList.toggle('nodock', !items.length);
  if (!items.length) { dock.innerHTML = ''; return; }

  // In the editor the tools act on a scene, so they are dead until one is open.
  const idle = state.view === 'edit' && !ED.job;
  // In the editor three of the tools are really the inspector's tabs, so they
  // show which one you are looking at.
  const tabOf = { scene: 'scene', layers: 'layers', subtitle: 'caption' };
  dock.innerHTML = items.map((item) => `
    <button data-dock="${esc(item.id)}"${item.hero ? ' class="hero"' : ''}${idle ? ' disabled' : ''}${
      !idle && tabOf[item.id] && tabOf[item.id] === ED.tab ? ' aria-pressed="true"' : ''}>
      <svg viewBox="0 0 24 24">${item.icon}</svg><span>${esc(item.label)}</span>
    </button>`).join('');

  $$('#dock button').forEach((button) => button.addEventListener('click', () => {
    const item = items.find((i) => i.id === button.dataset.dock);
    if (!item) return;
    if (item.sheet) openSheet(item.sheet);
    else item.act();
  }));
}

function showPanel(tab) {
  ED.tab = tab;
  drawPanel();
  drawDock();
  $('.panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ── the sheet ─────────────────────────────────────────────────────
function openSheet(name) {
  const panel = $(`[data-panel="${name}"]`);
  if (!panel) return;
  closeSheet();
  $('#sheet-title').textContent = SHEETS[name] || name;
  $('#sheet-body').append(panel);
  $('#sheet').classList.remove('hidden');
  $$('#dock button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.dock === name));
}

function closeSheet() {
  const panel = $('#sheet-body > [data-panel]');
  if (panel) $('#bank').append(panel);
  $('#sheet').classList.add('hidden');
  $$('#dock button').forEach((b) => b.removeAttribute('aria-pressed'));
  drawSummary();
}

$('#sheet-close').addEventListener('click', closeSheet);
$('#sheet').addEventListener('click', (e) => { if (e.target.id === 'sheet') closeSheet(); });
addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#sheet').classList.contains('hidden')) closeSheet();
});

// ── the summary line ──────────────────────────────────────────────
// What the video will be, in one row, with every part of it tappable.
function drawSummary() {
  const scriptMode = state.mode === 'script';
  const dub = state.mode === 'dub';
  const uploading = $('#use_upload').checked;
  const heroes = $$('#hero-picker input:checked').length;
  const music = $('#music_id');
  const voice = $('#voice_id');
  const styleOn = $$('#style-presets button[aria-pressed="true"]')[0];
  const language = $('#language').selectedOptions[0]?.text.split(' ')[0] || '';
  const voiceName = uploading ? 'o‘z ovozim'
    : (voice.value ? voice.selectedOptions[0].text.split(' —')[0] : 'standart ovoz');

  // Dubbing keeps the original picture, so shape, length, style, cast and music
  // are all decided already — only the new language and voice are ours to pick.
  // Subtitling decides even less: the video is not touched except to be written
  // on, so the only choices are how the words look and whether they are burned.
  const chips = (state.mode === 'subtitle' ? [
    ['subtitle', $('#subtitle_style').selectedOptions[0]?.text || 'subtitr'],
    ['subtitle', $('#sub_burn').value === '1' ? 'rasmga yoziladi' : 'faqat fayl'],
  ] : dub ? [
    ['format', language && `${language}ga`],
    ['voice', voiceName],
  ] : [
    ['format', `${state.format} · ${language}`],
    ['length', scriptMode || uploading ? null : durationLabel(Number($('#duration').value))],
    ['style', styleOn ? styleOn.textContent : 'o‘z uslubim'],
    ['cast', heroes ? `${heroes} hero${$('#animate_actors').checked ? ' · multfilm' : ''}` : null],
    ['voice', voiceName],
    ['music', music.value ? music.selectedOptions[0].text : null],
    ['subtitle', $('#burn_subtitles').checked
      ? $('#subtitle_style').selectedOptions[0]?.text || 'subtitr' : 'subtitrsiz'],
    // Only worth a chip when it is not the default — a steady pace is what a
    // video has always done and says nothing about this one.
    ['more', state.pace !== 'steady'
      ? (state.paces.find((p) => p.id === state.pace)?.label || state.pace) : null],
  ]).filter(([, value]) => value);

  $('#summary').innerHTML = chips.map(([key, value]) =>
    `<button type="button" data-sheet="${key}">${esc(value)}</button>`).join('');
  $$('#summary [data-sheet]').forEach((b) =>
    b.addEventListener('click', () => openSheet(b.dataset.sheet)));
}

// ── health ────────────────────────────────────────────────────────
async function loadHealth() {
  const h = await api('/api/health');
  state.health = h;
  state.motions = h.motions || [];
  state.transitions = h.transitions || [];

  // format segmented control, each option drawn at its true aspect
  $('#format-seg').innerHTML = h.formats.map((f) => {
    const [a, b] = f.id.split(':').map(Number);
    const scale = 15 / Math.max(a, b);
    return `<button type="button" data-fmt="${esc(f.id)}" aria-pressed="${f.id === state.format}">
      <i style="width:${(a * scale).toFixed(1)}px;height:${(b * scale).toFixed(1)}px"></i>${esc(f.id)}</button>`;
  }).join('');
  $$('#format-seg button').forEach((b) => b.addEventListener('click', () => {
    state.format = b.dataset.fmt;
    $$('#format-seg button').forEach((x) => x.setAttribute('aria-pressed', x === b));
    drawSummary();
  }));

  $('#language').innerHTML = h.languages
    .map((l) => `<option value="${esc(l.id)}"${l.id === 'en' ? ' selected' : ''}>${esc(l.label)}</option>`)
    .join('');
  // A plan is written for a channel that is almost certainly in one language, and
  // Uzbek is the one this app is in, so that is where it starts.
  const planLang = $('#plan-language');
  if (planLang) {
    planLang.innerHTML = h.languages
      .map((l) => `<option value="${esc(l.id)}"${l.id === 'uz' ? ' selected' : ''}>${esc(l.label)}</option>`)
      .join('');
  }

  // A bare provider name says nothing about what choosing it costs you. The one
  // that needs saying most is `flow`, which is the only one that spends no money
  // and the only one that will not work unless a browser is helping.
  const PROVIDER_NOTE = { flow: ' — o‘z brauzeringiz, kalitsiz' };
  const fill = (sel, map, preferred, names = null) => {
    const pick = map[preferred] ? preferred : Object.keys(map).find((k) => map[k]);
    $(sel).innerHTML = Object.entries(map).map(([n, ok]) =>
      `<option value="${n}"${ok ? '' : ' disabled'}${n === pick ? ' selected' : ''}>${
        esc(names?.[n] || n)}${ok ? (names ? '' : PROVIDER_NOTE[n] || '') : ' — kalit yo‘q'}</option>`
    ).join('');
  };
  // Named rather than listed: "manual" is the one people actually want here —
  // voice and subtitles now, pictures later — and it is the one whose bare
  // name says least about what it does.
  fill('#image_provider', h.image_providers, h.defaults.image_provider,
       IMAGE_PROVIDER_LABELS);
  fill('#tts_provider', h.tts_providers, h.defaults.tts_provider);
  await loadVoices();

  $('#speed-seg').innerHTML = (h.speeds || []).map((sp) =>
    `<button type="button" data-speed="${esc(sp.id)}" aria-pressed="${sp.id === state.speed}">${esc(sp.label)}</button>`).join('');
  $$('#speed-seg button').forEach((b) => b.addEventListener('click', () => {
    state.speed = b.dataset.speed;
    $$('#speed-seg button').forEach((x) => x.setAttribute('aria-pressed', x === b));
    syncSpeedNote();
  }));
  syncSpeedNote(h.cores);

  // How often the picture changes. Every step up is more images to generate,
  // which is the real cost, so the hint says so rather than only naming a feel.
  state.paces = h.shot_paces || [];
  $('#pace-seg').innerHTML = state.paces.map((p) =>
    `<button type="button" data-pace="${esc(p.id)}" aria-pressed="${p.id === state.pace}">${esc(p.label)}</button>`).join('');
  $$('#pace-seg button').forEach((b) => b.addEventListener('click', () => {
    state.pace = b.dataset.pace;
    $$('#pace-seg button').forEach((x) => x.setAttribute('aria-pressed', x === b));
    syncPaceNote();
  }));
  syncPaceNote();

  const autoFirst = '<option value="">avtomatik aniqlansin</option>' +
    h.languages.map((l) => `<option value="${esc(l.id)}">${esc(l.label)}</option>`).join('');
  $('#dub_source').innerHTML = autoFirst;
  $('#sub_language').innerHTML = autoFirst;
  $('#mode-tabs [data-mode="dub"]').classList.toggle('hidden', !h.can_dub);
  // Subtitling needs a transcriber and nothing else — no voice, no pictures, no
  // script — so it is offered whenever the app can listen to a video at all.
  $('#mode-tabs [data-mode="subtitle"]').classList.toggle('hidden', !h.can_subtitle);

  $('#subtitle_style').innerHTML = (h.caption_templates || []).map((t) =>
    `<option value="${esc(t.id)}"${t.id === 'bold' ? ' selected' : ''}>${esc(t.label)}</option>`).join('');

  const checks = [
    ['ffmpeg', h.ffmpeg],
    [`skript — ${h.llm_provider}`, h.llm],
    ['transkripsiya', h.transcription],
    // `flow` and `manual` are left out on purpose: neither has a key, so both
    // are always "ready", and a green row saying so would mean nothing. Whether
    // anything is actually answering the queue's prompts is the Flow queue's
    // business, not this list's.
    ...Object.entries(h.image_providers)
      .filter(([n]) => n !== 'flow' && n !== 'manual')
      .map(([n, v]) => [`rasm — ${n}`, v]),
    ...Object.entries(h.tts_providers).map(([n, v]) => [`ovoz — ${n}`, v]),
    [`fayllar — ${h.storage}`, true],
    // Worth its own row, and worth being strict about: whether your work
    // outlives a deploy is something to learn before it happens, not after.
    [`baza — ${h.database?.backend || 'sqlite'}`, h.database?.ok !== false],
    ['deploydan keyin saqlanadi', h.database?.durable === true],
    [h.tts_rate_limit
      ? `ovoz limiti — ${h.tts_rate_limit}/daqiqa`
      : 'ovoz limiti — cheklanmagan', true],
  ];
  $('#health-list').innerHTML = checks.map(([label, ok]) =>
    `<div class="row"><span>${esc(label)}</span><span class="tag ${ok ? 'done' : 'failed'}">${ok ? 'bor' : 'yo‘q'}</span></div>`
  ).join('')
    + (h.database?.note ? `<p class="note">${esc(h.database.note)}</p>` : '')
    + Object.entries(h.models || {}).map(([stage, model]) =>
    `<div class="row"><span>${esc({ text: 'skript modeli', image: 'rasm modeli', tts: 'ovoz modeli' }[stage] || stage)}</span>
      <span class="model">${esc(model)}</span></div>`).join('');

  // A folded section still has to say what is inside it, otherwise folding it
  // hides the answer to "is anything wrong?" — which is the one thing this
  // section exists to answer.
  const bad = checks.filter(([, ok]) => !ok).length;
  libCount('health', bad ? `${bad} ta yetishmaydi` : 'hammasi joyida', bad > 0);

  // Same reasoning: a deployment with no image key at all is not set up, unless
  // it has deliberately chosen the provider that needs no key.
  const canDraw = Object.entries(h.image_providers || {})
    .some(([n, ok]) => ok && n !== 'flow') || h.defaults?.image_provider === 'flow';
  const core = h.ffmpeg && h.llm && canDraw;
  const voice = Object.values(h.tts_providers).some(Boolean);
  const pill = $('#health-pill');
  pill.className = `health-pill ${core && voice ? 'ok' : core ? 'part' : 'bad'}`;
  pill.textContent = core && voice ? 'tayyor' : core ? 'ovoz yo‘q' : 'sozlash kerak';
  pill.title = checks.map(([l, ok]) => `${l}: ${ok ? 'bor' : "yo'q"}`).join('\n');
}

// ── heroes ────────────────────────────────────────────────────────
async function loadHeroes() {
  state.heroes = await api('/api/heroes');
  libCount('heroes', state.heroes.length ? `${state.heroes.length} ta` : '');

  $('#hero-picker').innerHTML = state.heroes.map((h) => `
    <label class="hero-chip" title="${esc(h.name)}">
      <input type="checkbox" value="${esc(h.id)}" />
      <img src="${esc(h.url)}" alt="${esc(h.name)}" loading="lazy" />
    </label>`).join('');
  $$('#hero-picker .hero-chip').forEach((chip) => {
    const input = $('input', chip);
    input.addEventListener('change', () => {
      chip.classList.toggle('on', input.checked);
      syncAnimate();
    });
  });

  $('#hero-list').innerHTML = state.heroes.length
    ? state.heroes.map((h) => `
        <div class="lib-card">
          <img src="${esc(h.url)}" alt="${esc(h.name)}" loading="lazy" />
          <button class="x" data-del-hero="${esc(h.id)}" aria-label="O‘chirish">×</button>
          <b>${esc(h.name)}</b>
          <button class="voice-tag${h.voice_id ? ' on' : ''}" data-hero-voice="${esc(h.id)}">
            ${h.voice_id ? esc(voiceName(h)) : 'ovoz bermang'}
          </button>
        </div>`).join('')
    : '<p class="empty">Hali hero yo‘q.</p>';

  $$('[data-del-hero]').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('Bu heroni o‘chirasizmi?')) return;
    await api(`/api/heroes/${b.dataset.delHero}`, { method: 'DELETE' });
    loadHeroes();
  }));
  $$('[data-hero-voice]').forEach((b) =>
    b.addEventListener('click', () => giveVoice(b.dataset.heroVoice)));
}

/** What to call a character's voice when all we have is its id. */
function voiceName(hero) {
  const known = (state.voices || []).find((v) => v.id === hero.voice_id);
  return known ? known.label : hero.voice_id;
}

/** Give a character a voice of its own — or take it away again.
 *
 * A character with a voice speaks its own lines: the Director writes dialogue
 * for it and the voice stage records that line in this voice instead of the
 * narrator's. That is the whole of what makes a cartoon a cartoon rather than
 * a slideshow with someone explaining it.
 */
async function giveVoice(heroId) {
  const hero = (state.heroes || []).find((h) => h.id === heroId);
  if (!hero) return;
  const providers = Object.entries(state.health?.tts_providers || {})
    .filter(([, ready]) => ready).map(([name]) => name);
  if (!providers.length) { toast('Avval ovoz provayderiga kalit qo‘ying'); return; }

  const answer = await ask({
    title: `${hero.name} — ovozi`,
    ok: 'Saqlash',
    html: `
      <p class="hint">Ovoz berilgan qahramon o‘z gaplarini o‘zi aytadi.
        Ovozsiz qoldirsangiz uni diktor o‘qiydi.</p>
      <label class="f"><span>Provayder</span>
        <select name="tts_provider" id="hv-provider">
          ${providers.map((p) => `<option value="${esc(p)}"${
            p === (hero.tts_provider || $('#tts_provider').value) ? ' selected' : ''
          }>${esc(p)}</option>`).join('')}
        </select></label>
      <label class="f"><span>Ovoz</span>
        <span class="voice-row">
          <select name="voice_id" id="hv-voice"><option value="">yuklanmoqda…</option></select>
          <button type="button" class="btn ghost sm" id="hv-play" aria-label="Namunasini eshitish">▶</button>
        </span></label>
      <p class="voice-hint" id="hv-hint"></p>`,
    onOpen: () => {
      const load = async () => {
        const provider = $('#hv-provider').value;
        const select = $('#hv-voice');
        select.innerHTML = '<option value="">yuklanmoqda…</option>';
        try {
          const data = await api(`/api/voices?provider=${encodeURIComponent(provider)}`);
          select.innerHTML = '<option value="">— ovoz bermang (diktor o‘qiydi) —</option>' +
            (data.voices || []).map((v) => {
              const about = [v.hint, v.tone].filter(Boolean).join(' · ');
              return `<option value="${esc(v.id)}">${esc(v.label)}${
                about ? ` — ${esc(about)}` : ''}</option>`;
            }).join('');
          if (hero.voice_id && (data.voices || []).some((v) => v.id === hero.voice_id)) {
            select.value = hero.voice_id;
          }
          if (data.error || !(data.voices || []).length) {
            voiceTrouble($('#hv-hint'), provider,
                         data.error || 'Bu provayder uchun ovoz ro’yxati yo’q.');
          } else {
            $('#hv-hint').className = 'voice-hint';
            $('#hv-hint').textContent = `${data.voices.length} ta ovoz — ▶ bosib eshiting.`;
          }
        } catch (e) {
          voiceTrouble($('#hv-hint'), provider, e.message);
        }
      };
      $('#hv-provider').addEventListener('change', load);
      $('#hv-play').addEventListener('click', () =>
        playVoiceSample($('#hv-provider').value, $('#hv-voice').value, $('#hv-play')));
      load();
    },
  });
  if (!answer) return;

  await api(`/api/heroes/${heroId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      voice_id: answer.voice_id || '',
      tts_provider: answer.voice_id ? answer.tts_provider : '',
    }),
  });
  await loadHeroes();
  toast(answer.voice_id ? `${hero.name} endi o‘zi gapiradi` : `${hero.name} — diktor o‘qiydi`);
}

// ── overlay assets ────────────────────────────────────────────────
async function loadAssets() {
  state.assets = await api('/api/assets');
  libCount('assets', state.assets.length ? `${state.assets.length} ta` : '');
  $('#asset-list').innerHTML = state.assets.length
    ? state.assets.map((a) => `
        <div class="lib-card check">
          <img src="${esc(a.url)}" alt="${esc(a.name)}" loading="lazy" />
          <button class="x" data-del-asset="${esc(a.id)}" aria-label="O‘chirish">×</button>
          <b>${esc(a.name)}</b>
        </div>`).join('')
    : '<p class="empty">Hali qatlam rasmi yo‘q.</p>';

  $$('[data-del-asset]').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('Bu rasmni o‘chirasizmi? Undan foydalangan qatlamlar yo‘qoladi.')) return;
    await api(`/api/assets/${b.dataset.delAsset}`, { method: 'DELETE' });
    loadAssets();
  }));
  // The brand kit picks its logo from this same list, so it has to be redrawn
  // whenever the list changes — otherwise a just-uploaded logo is unpickable.
  if (state.brand) drawBrand();
  if (ED.job && ED.tab === 'layers') drawLayersPanel();
}

$('#asset-form').addEventListener('submit', (e) => {
  e.preventDefault();
  submitLibraryForm(e.target, '/api/assets', loadAssets);
});

// ── models ────────────────────────────────────────────────────────
// Providers ship and retire models on their own schedule, so the menu is
// whatever the provider returns for this key — and the field stays open text so
// a model released tomorrow can be typed in today.
async function loadModels() {
  state.models = await api('/api/models');
  drawModels();
  // Ask each provider what it offers straight away. Making that a button press
  // meant the fields stayed as free text on first sight, which is the one time
  // a menu would have helped most.
  refreshAvailable();
}

async function refreshAvailable(force = false) {
  const providers = [...new Set(Object.values(state.models?.stages || {})
    .map((meta) => meta.provider))].filter((p) => providersInPlay().has(p));
  const wanted = providers.filter((p) => force || !state.available[p]);
  if (!wanted.length) return;

  state.loadingModels = true;
  drawModels();
  await Promise.all(wanted.map(async (provider) => {
    state.available[provider] = await api(
      `/api/models/available?provider=${encodeURIComponent(provider)}`
    ).catch((e) => ({ models: [], error: e.message }));
  }));
  state.loadingModels = false;
  drawModels();
}

function providersInPlay() {
  const h = state.health || {};
  const on = new Set();
  if (h.llm) on.add(h.llm_provider);
  Object.entries(h.image_providers || {}).forEach(([n, ok]) => ok && on.add(n));
  Object.entries(h.tts_providers || {}).forEach(([n, ok]) => ok && on.add(n));
  if (h.transcription) on.add('openai');
  return on;
}

function drawModels() {
  const m = state.models;
  if (!m) return;
  const live = providersInPlay();
  const inUse = new Set(Object.values(m.in_use || {}));
  const rows = Object.entries(m.stages)
    .filter(([key, meta]) => live.has(meta.provider) || inUse.has(key));

  // Which provider writes the script, above the model rows — it decides which
  // of them is the one that matters, so it reads first.
  const chosen = m.text_choice || 'auto';
  const picker = `
    <label class="f model-pick"><span>Skriptni kim yozadi</span>
      <select id="text-provider">
        <option value="auto"${chosen === 'auto' ? ' selected' : ''}>avtomatik — kaliti bori</option>
        ${(m.text_providers || []).map((p) => `
          <option value="${esc(p.id)}"${p.ready ? '' : ' disabled'}${
            chosen === p.id ? ' selected' : ''}>${esc(TEXT_PROVIDER_LABELS[p.id] || p.id)}${
            p.ready ? '' : ' — kalit yo‘q'}</option>`).join('')}
      </select>
      <small>${chosen === 'auto'
        ? `Hozir: <b>${esc(TEXT_PROVIDER_LABELS[m.text_provider] || m.text_provider)}</b>`
        : 'Kalit bo‘lmasa video boshlanmaydi.'}</small>
    </label>`;

  // The same question for the pictures — and the one that has actually caught
  // people out, because the Flow switch stores a choice that outranks the
  // environment. When the two disagree, say so here rather than leaving it to
  // be discovered in a render log.
  const drawnBy = m.image_provider || '';
  const overridden = m.image_env && drawnBy && drawnBy !== m.image_env;
  const imagePicker = `
    <label class="f model-pick"><span>Rasmlarni kim yasaydi</span>
      <select id="image-provider">
        ${(m.image_providers || []).map((p) => `
          <option value="${esc(p.id)}"${p.ready ? '' : ' disabled'}${
            drawnBy === p.id ? ' selected' : ''}>${
            esc(IMAGE_PROVIDER_LABELS[p.id] || p.id)}${
            p.ready ? '' : ' — sozlanmagan'}</option>`).join('')}
      </select>
      <small>${overridden
        ? `Bu tanlov saqlangan va <code>IMAGE_PROVIDER=${esc(m.image_env)}</code>
           dan kuchli. Sozlamadagisiga qaytarish uchun
           «${esc(IMAGE_PROVIDER_LABELS[m.image_env] || m.image_env)}» ni tanlang.`
        : 'Har bir sahnaning rasmi shu yerdan chiqadi.'}</small>
    </label>`;

  $('#models-box').innerHTML = `
    ${picker}
    ${imagePicker}
    ${rows.map(([key, meta]) => modelRow(key, meta, m, inUse.has(key))).join('')}

    <div class="model-acts">
      <button class="btn" id="models-refresh">Ro‘yxatni yangilash</button>
      <button class="btn primary" id="models-save">Saqlash</button>
      <span class="saver" id="models-saver"></span>
    </div>
    <p class="hint" id="models-note">${esc(modelsNote(rows))}</p>`;

  // "Other" is the escape hatch: a model can exist before the listing endpoint
  // knows about it, and fal has no listing endpoint at all.
  $$('#models-box [data-msel]').forEach((select) => select.addEventListener('change', () => {
    const row = select.closest('.model-row');
    const other = select.value === OTHER_MODEL;
    $('[data-mtext]', row).classList.toggle('hidden', !other);
    if (other) $('[data-mtext]', row).focus();
  }));

  $('#models-refresh').addEventListener('click', async () => {
    const btn = $('#models-refresh');
    btn.disabled = true;
    btn.textContent = 'Yuklanmoqda…';
    await refreshAvailable(true);
    toast('Ro‘yxat yangilandi');
  });

  $('#models-save').addEventListener('click', async () => {
    const btn = $('#models-save');
    btn.disabled = true;
    try {
      state.models = await api('/api/models', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          models: readModelRows(),
          voices: state.models.voices || {},
          text_provider: $('#text-provider')?.value || null,
          image_provider: $('#image-provider')?.value ?? null,
        }),
      });
      $('#models-saver').textContent = 'saqlandi';
      $('#models-saver').className = 'saver ok';
      await loadHealth();
      drawModels();
      toast('Modellar saqlandi');
    } catch (e) {
      alert(e.message);
    } finally { btn.disabled = false; }
  });
}

/** Which provider is drawing this running job, and how to change your mind.
 *
 *  The editor is hidden while a job runs, and with it the control that would
 *  change this — so somebody watching "Flow'dan rasm kutilyapti" scroll past has
 *  no way to see which provider that is, and no way to find out that stopping is
 *  the first step of changing it. Both belong here, where they are looking.
 */
function drawnByNote(job) {
  const now = job.image_provider_now || '';
  if (!now) return '';
  const label = IMAGE_PROVIDER_LABELS[now] || now;
  const app = state.models?.image_provider || '';
  // Only worth explaining when this project has been left behind by the app's
  // own choice — otherwise it is just a label, and the card is not a form.
  const stale = app && app !== now;
  return `<span class="stage-drawnby">Rasmlar: <b>${esc(label)}</b>${
    stale ? ' — o‘zgartirish uchun avval To‘xtating' : ''}</span>`;
}

// The provider names are what the API calls them; these are what a person calls
// them. `openai` in a menu is not obviously "the one that made ChatGPT".
const TEXT_PROVIDER_LABELS = {
  anthropic: 'Claude (Anthropic)',
  openai: 'ChatGPT (OpenAI)',
  gemini: 'Gemini (Google)',
};

// Named by what they cost you and who does the work, because that is the
// difference that matters: two of these bill an API, one waits for your browser,
// one uses the Flow subscription without waiting for anybody.
const IMAGE_PROVIDER_LABELS = {
  gemini: 'Gemini (Google) — API',
  fal: 'fal.ai — API',
  openai: 'gpt-image-1 (OpenAI) — API',
  flow: 'Flow navbati — brauzeringiz yasaydi',
  flowagent: 'Flow Agent — o‘zi yasaydi',
  manual: 'Men o‘zim yasayman — promptlarni beradi',
};

const OTHER_MODEL = '__other';

/** One stage: a menu of what the provider offers, or a text box when it has none. */
function modelRow(key, meta, m, live) {
  const value = m.overrides[key] || '';
  const fallback = m.defaults[key] || '';
  const catalogue = state.available[meta.provider];
  // Each stage only shows models that can do its job — offering an image model
  // where the voice goes is worse than offering nothing.
  const options = (catalogue?.models || []).filter((x) => x.role === meta.role);
  const listed = options.some((x) => x.id === value);
  const head = `<span>${esc(STAGE_LABELS[key] || key)}${live ? '<b>ishlatilmoqda</b>' : ''}</span>`;

  if (!options.length) {
    return `<label class="f model-row${live ? ' live' : ''}" data-mkey="${esc(key)}">
      ${head}
      <input data-mtext value="${esc(value)}" placeholder="${esc(fallback)}" spellcheck="false" />
    </label>`;
  }

  return `<div class="f model-row${live ? ' live' : ''}" data-mkey="${esc(key)}">
    ${head}
    <select data-msel>
      <option value=""${value ? '' : ' selected'}>standart — ${esc(fallback)}</option>
      ${options.map((x) =>
        `<option value="${esc(x.id)}"${x.id === value ? ' selected' : ''}>${esc(x.id)}</option>`).join('')}
      ${value && !listed
        ? `<option value="${esc(value)}" selected>${esc(value)}</option>` : ''}
      <option value="${OTHER_MODEL}">boshqa nom yozaman…</option>
    </select>
    <input data-mtext class="hidden" value="${esc(value)}"
      placeholder="${esc(fallback)}" spellcheck="false" />
  </div>`;
}

function readModelRows() {
  return Object.fromEntries($$('#models-box [data-mkey]').map((row) => {
    const select = $('[data-msel]', row);
    const text = $('[data-mtext]', row);
    if (!select) return [row.dataset.mkey, text.value.trim()];
    return [row.dataset.mkey,
      select.value === OTHER_MODEL ? text.value.trim() : select.value];
  }));
}

function modelsNote(rows) {
  if (state.loadingModels) return 'Provayderlardan modellar ro‘yxati olinmoqda…';
  const providers = [...new Set(rows.map(([, meta]) => meta.provider))];
  const errors = providers
    .map((p) => state.available?.[p]?.error && `${p}: ${state.available[p].error}`)
    .filter(Boolean);
  if (errors.length) return errors.join(' · ');
  const counted = providers
    .map((p) => state.available?.[p] && `${p}: ${state.available[p].models.length} ta model`)
    .filter(Boolean);
  return counted.length
    ? `${counted.join(' · ')}. Ro‘yxatda yo‘q modelni «boshqa nom yozaman» orqali qo‘shasiz.`
    : 'Provayderdan mavjud modellar ro‘yxatini olish uchun «Ro‘yxatni yangilash».';
}

// ── brand kit ─────────────────────────────────────────────────────
async function loadBrand() {
  state.brand = await api('/api/brand');
  drawBrand();
  // The switch is only meaningful once a logo exists to stamp.
  $('#brand-logo-sw').classList.toggle('hidden', !state.brand.logo_asset_id);
}

function drawBrand() {
  const b = state.brand || {};
  const logo = (state.assets || []).find((a) => a.id === b.logo_asset_id);
  $('#brand-box').innerHTML = `
    <div class="brand-head">
      <div class="brand-logo">${logo
        ? `<img src="${esc(logo.url)}" alt="${esc(logo.name)}" />`
        : '<span>logo yo‘q</span>'}</div>
      <div class="f" style="flex:1">
        <span>Logotip — «Qatlam rasmlari» dan tanlanadi</span>
        <select data-b="logo_asset_id">
          <option value="">— yo‘q —</option>
          ${(state.assets || []).map((a) =>
            `<option value="${esc(a.id)}"${a.id === b.logo_asset_id ? ' selected' : ''}>${esc(a.name)}</option>`).join('')}
        </select>
      </div>
    </div>
    ${b.logo_asset_id ? `
    <!-- Two sliders and no picture is not a way to place anything. This is the
         frame the logo will sit in, with the logo in it: drag it where it goes,
         or tap a corner. It stays there for the whole video. -->
    <div class="f"><span>Logotip qayerda tursin — sudrab qo‘ying</span>
      <div class="logopad">
        <div class="seg tight logopad-shape" id="logo-shape">
          <button type="button" data-ar="1.7778" aria-pressed="${
            (b.logo_shape || '16:9') === '16:9'}">16:9</button>
          <button type="button" data-ar="0.5625" aria-pressed="${
            b.logo_shape === '9:16'}">9:16</button>
        </div>
        <div class="logopad-frame" id="logo-frame"
             style="--ar:${b.logo_shape === '9:16' ? '0.5625' : '1.7778'}">
          <img id="logo-ghost" src="${esc(logo?.url || '')}" alt="" draggable="false" />
        </div>
        <div class="logo-anchors" id="logo-anchors">
          ${[['0.08', '0.1', 'yuqori chap'], ['0.5', '0.1', 'yuqori o‘rta'],
             ['0.92', '0.1', 'yuqori o‘ng'],
             ['0.08', '0.5', 'chap'], ['0.5', '0.5', 'markaz'], ['0.92', '0.5', 'o‘ng'],
             ['0.08', '0.9', 'quyi chap'], ['0.5', '0.9', 'quyi o‘rta'],
             ['0.92', '0.9', 'quyi o‘ng']].map(([x, y, name]) =>
            `<button type="button" data-ax="${x}" data-ay="${y}" aria-label="${name}"
              class="${Math.abs(b.logo_x - Number(x)) < 0.02
                && Math.abs(b.logo_y - Number(y)) < 0.02 ? 'on' : ''}"></button>`).join('')}
        </div>
      </div></div>
    <div class="f2">
      ${brandSlider('logo_size', 'Logotip kattaligi', 0.03, 0.35, 0.01, b.logo_size)}
      ${brandSlider('logo_opacity', 'Shaffofligi', 0.1, 1, 0.05, b.logo_opacity)}
    </div>` : ''}

    <div class="f"><span>Brend rangi — hook va yozuvlar shu rangda chiqadi</span>
      <div class="sw-row" data-brand-swatch="accent">
        ${SWATCHES.map((c) => `<button type="button" style="background:${c}" data-c="${c}"
          class="${c.toLowerCase() === String(b.accent).toLowerCase() ? 'on' : ''}" aria-label="${c}"></button>`).join('')}
        <input type="color" data-b="accent" value="${esc(b.accent || '#FF3B30')}" />
      </div></div>

    <div class="f2">
      <label class="f"><span>Doimiy vizual uslub</span>
        <input data-b="art_style" value="${esc(b.art_style || '')}" placeholder="bo‘sh — har safar tanlayman" /></label>
      <label class="f"><span>Doimiy ohang</span>
        <input data-b="tone" value="${esc(b.tone || '')}" placeholder="bo‘sh — standart" /></label>
    </div>
    <div class="f2">
      <div class="f"><span>Doimiy ovoz</span>
        <div class="voice-row">
          <select data-b="voice_id">
            <option value="">— standart —</option>
            ${(state.voices || []).map((v) => {
              const about = [v.hint, v.tone].filter(Boolean).join(' · ');
              return `<option value="${esc(v.id)}"${v.id === b.voice_id ? ' selected' : ''}>${esc(v.label)}${about ? ` — ${esc(about)}` : ''}</option>`;
            }).join('')}
            ${b.voice_id && !(state.voices || []).some((v) => v.id === b.voice_id)
              ? `<option value="${esc(b.voice_id)}" selected>${esc(b.voice_id)}</option>` : ''}
          </select>
          <button type="button" class="hear" id="brand-hear" aria-label="Namunani eshitish">
            <svg viewBox="0 0 24 24"><path d="M8 5.5l11 6.5-11 6.5z"/></svg>
          </button>
        </div>
        <small>Ro‘yxat «${esc($('#tts_provider')?.value || '—')}» provayderidan.</small>
      </div>
      <label class="f"><span>Doimiy fon musiqasi</span>
        <select data-b="music_id"><option value="">— yo‘q —</option>
          ${(state.music || []).map((m) =>
            `<option value="${esc(m.id)}"${m.id === b.music_id ? ' selected' : ''}>${esc(m.name)}</option>`).join('')}
        </select></label>
    </div>
    <button class="btn primary" id="brand-save">Brendni saqlash</button>
    <span class="saver" id="brand-saver"></span>`;

  $$('[data-b]').forEach((el) => el.addEventListener('input', () => {
    state.brand[el.dataset.b] = el.type === 'range' ? Number(el.value) : el.value;
    if (['logo_asset_id', 'accent'].includes(el.dataset.b)) drawBrand();
    else if (el.dataset.b.startsWith('logo_')) placeLogoGhost();
  }));
  wireLogoPad();
  $$('[data-brand-swatch] button').forEach((btn) => btn.addEventListener('click', () => {
    state.brand.accent = btn.dataset.c;
    drawBrand();
  }));
  $('#brand-hear')?.addEventListener('click', () => playVoiceSample(
    $('#tts_provider').value, $('[data-b="voice_id"]').value, $('#brand-hear')));
  $('#brand-save').addEventListener('click', async () => {
    const btn = $('#brand-save');
    btn.disabled = true;
    try {
      state.brand = await api('/api/brand', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state.brand),
      });
      $('#brand-saver').textContent = 'saqlandi';
      $('#brand-saver').className = 'saver ok';
      $('#brand-logo-sw').classList.toggle('hidden', !state.brand.logo_asset_id);
      applyBrandToComposer();
      toast('Brend saqlandi');
    } catch (e) {
      alert(e.message);
    } finally { btn.disabled = false; }
  });
}

/** Put the ghost logo where the brand says it goes, in the frame's own terms.
 *
 * The same arithmetic the renderer and the editor's canvas use: `x`/`y` are the
 * centre as a fraction of the frame, `size` is the width as a fraction of it.
 * Anything else here would be a preview of a placement that never happens.
 */
function placeLogoGhost() {
  const ghost = $('#logo-ghost');
  if (!ghost) return;
  const b = state.brand || {};
  ghost.style.left = `${(b.logo_x ?? 0.9) * 100}%`;
  ghost.style.top = `${(b.logo_y ?? 0.1) * 100}%`;
  ghost.style.width = `${(b.logo_size ?? 0.11) * 100}%`;
  ghost.style.opacity = String(b.logo_opacity ?? 0.9);
  $$('#logo-anchors button').forEach((btn) => btn.classList.toggle('on',
    Math.abs((b.logo_x ?? 0) - Number(btn.dataset.ax)) < 0.02
    && Math.abs((b.logo_y ?? 0) - Number(btn.dataset.ay)) < 0.02));
}

function wireLogoPad() {
  const frame = $('#logo-frame');
  if (!frame) return;
  placeLogoGhost();

  // Dragging is on the frame, not on the logo: a logo at 3% of the width is a
  // target too small to grab on a phone, and dropping anywhere in the frame
  // meaning "put it there" is the behaviour people expect anyway.
  const put = (event) => {
    const box = frame.getBoundingClientRect();
    const clamp = (v) => Math.min(1, Math.max(0, v));
    state.brand.logo_x = Math.round(clamp((event.clientX - box.left) / box.width) * 100) / 100;
    state.brand.logo_y = Math.round(clamp((event.clientY - box.top) / box.height) * 100) / 100;
    placeLogoGhost();
  };
  frame.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    frame.setPointerCapture(e.pointerId);
    frame.classList.add('dragging');
    put(e);
  });
  frame.addEventListener('pointermove', (e) => {
    if (frame.hasPointerCapture(e.pointerId)) put(e);
  });
  frame.addEventListener('pointerup', () => frame.classList.remove('dragging'));
  frame.addEventListener('pointercancel', () => frame.classList.remove('dragging'));

  $$('#logo-anchors button').forEach((btn) => btn.addEventListener('click', () => {
    state.brand.logo_x = Number(btn.dataset.ax);
    state.brand.logo_y = Number(btn.dataset.ay);
    placeLogoGhost();
  }));

  // Which shape you are checking the placement against. A corner that looks
  // right on a wide frame can be under a phone's own furniture on a tall one.
  $$('#logo-shape button').forEach((btn) => btn.addEventListener('click', () => {
    $$('#logo-shape button').forEach((x) => x.setAttribute('aria-pressed', x === btn));
    frame.style.setProperty('--ar', btn.dataset.ar);
    state.brand.logo_shape = btn.dataset.ar === '0.5625' ? '9:16' : '16:9';
  }));
}

const brandSlider = (key, label, min, max, step, value) =>
  `<label class="f rng"><span>${esc(label)}<b>${Number(value ?? 0).toFixed(2)}</b></span>
    <input type="range" data-b="${key}" min="${min}" max="${max}" step="${step}" value="${value ?? min}" /></label>`;

// The brand is a starting point for the composer, never a lock — anything the
// user then types wins for that video.
function applyBrandToComposer() {
  const b = state.brand || {};
  if (b.art_style) {
    $('#art_style').value = b.art_style;
    $$('#style-presets button').forEach((x) =>
      x.setAttribute('aria-pressed', STYLE_PRESETS[Number(x.dataset.p)][1] === b.art_style));
  }
  if (b.tone) $('#tone').value = b.tone;
  if (b.voice_id && $(`#voice_id option[value="${CSS.escape(b.voice_id)}"]`))
    $('#voice_id').value = b.voice_id;
  if (b.music_id && $(`#music_id option[value="${b.music_id}"]`)) {
    $('#music_id').value = b.music_id;
    syncMusicStart();
  }
  if (b.caption_style?.template) $('#subtitle_style').value = b.caption_style.template;
}

// ── music & sound effects ─────────────────────────────────────────
async function loadMusic() {
  const all = await api('/api/music');
  state.music = all.filter((m) => m.kind !== 'sfx');
  state.sfx = all.filter((m) => m.kind === 'sfx');
  libCount('music', all.length ? `${all.length} ta` : '');

  $('#music_id').innerHTML = '<option value="">— yo‘q —</option>' +
    state.music.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');
  $('#music-list').innerHTML = all.length
    ? all.map((m) => `<div class="row">
        <span>${esc(m.name)} <small style="display:inline;margin-left:6px">${m.kind === 'sfx' ? 'effekt' : 'musiqa'}</small></span>
        <button class="x" data-del-music="${esc(m.id)}" aria-label="O‘chirish">×</button></div>`).join('')
    : '<p class="empty">Hali musiqa yo‘q.</p>';
  $$('[data-del-music]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/music/${b.dataset.delMusic}`, { method: 'DELETE' });
    loadMusic();
  }));
  syncMusicStart();
  if (state.brand) drawBrand();
  if (ED.job && ED.tab === 'scene') drawScenePanel();
}

// The trim offset is meaningless with no track chosen, so it only appears once
// there is something to trim.
function syncMusicStart() {
  const chosen = !!$('#music_id').value;
  $('#music-start-field').classList.toggle('hidden', !chosen);
  $('#music-start-label').textContent = clock(Number($('#music_start').value) || 0);
}
$('#music_id').addEventListener('change', () => { syncMusicStart(); drawSummary(); });
$('#music_start').addEventListener('input', syncMusicStart);

// Uploading a track from the composer, rather than making the user leave for the
// library and come back, is the difference between adding music and not.
$('#music-quick').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const file = $('input[type=file]', form).files[0];
  if (!file) return;
  const button = $('button', form);
  button.disabled = true;
  try {
    const body = new FormData();
    body.append('audio', file);
    body.append('name', file.name.replace(/\.[^.]+$/, ''));
    body.append('kind', 'music');
    const track = await api('/api/music', { method: 'POST', body });
    await loadMusic();
    $('#music_id').value = track.id;
    syncMusicStart();
    drawSummary();
    form.reset();
    $('.file', form).classList.remove('has');
    $('.file span', form).textContent = 'Qurilmadan musiqa yuklash';
    toast('Musiqa qo‘shildi');
  } catch (err) {
    alert(err.message);
  } finally {
    button.disabled = false;
  }
});

// Same for a hero: the cast is chosen here, so it can be added here.
$('#hero-quick').addEventListener('submit', async (e) => {
  e.preventDefault();
  await submitLibraryForm(e.target, '/api/heroes', loadHeroes);
});

// file inputs show the chosen name, keeping their own wording to go back to
$$('.file input').forEach((i) => i.addEventListener('change', () => {
  const label = i.closest('.file');
  const span = $('span', label);
  if (label.dataset.label === undefined) label.dataset.label = span.textContent;
  const name = i.files[0]?.name;
  label.classList.toggle('has', !!name);
  span.textContent = name || label.dataset.label;
}));

async function submitLibraryForm(form, url, reload) {
  const button = $('button', form);
  button.disabled = true;
  // Restoring each label's own wording is why it is read before the reset,
  // rather than guessed from the input's accept attribute afterwards.
  const labels = $$('.file', form);
  try {
    await api(url, { method: 'POST', body: new FormData(form) });
    form.reset();
    labels.forEach((label) => {
      label.classList.remove('has');
      if (label.dataset.label !== undefined) $('span', label).textContent = label.dataset.label;
    });
    await reload();
    toast('Qo‘shildi');
  } catch (err) {
    alert(err.message);
  } finally {
    button.disabled = false;
  }
}
$('#hero-form').addEventListener('submit', (e) => {
  e.preventDefault();
  submitLibraryForm(e.target, '/api/heroes', loadHeroes);
});
$('#music-form').addEventListener('submit', (e) => {
  e.preventDefault();
  submitLibraryForm(e.target, '/api/music', loadMusic);
});

// ── style presets & voice ─────────────────────────────────────────
$('#style-presets').innerHTML = STYLE_PRESETS.map(([label], i) =>
  `<button type="button" data-p="${i}" aria-pressed="${i === 0}">${esc(label)}</button>`).join('');

$$('#style-presets button').forEach((b) => b.addEventListener('click', () => {
  $('#art_style').value = STYLE_PRESETS[Number(b.dataset.p)][1];
  $$('#style-presets button').forEach((x) => x.setAttribute('aria-pressed', x === b));
  drawSummary();
}));

// Typing a style by hand means none of the presets describes it any more.
$('#art_style').addEventListener('input', () => {
  const value = $('#art_style').value.trim();
  $$('#style-presets button').forEach((b) =>
    b.setAttribute('aria-pressed', STYLE_PRESETS[Number(b.dataset.p)][1] === value));
  drawSummary();
});

const SPEED_NOTES = {
  fast: 'Eng tez — kuchli zoom paytida rasm biroz yumshoqroq chiqadi.',
  balanced: 'Tavsiya etiladi.',
  quality: 'Eng tiniq, lekin sezilarli sekinroq.',
};

function syncPaceNote() {
  const chosen = (state.paces || []).find((p) => p.id === state.pace);
  $('#pace-note').textContent = chosen?.hint || '';
  drawSummary();
}

function syncSpeedNote(cores) {
  if (cores) state.cores = cores;
  const note = SPEED_NOTES[state.speed] || '';
  $('#speed-note').textContent = state.cores
    ? `${note} Server ${state.cores} yadroli.` : note;
}

// ── voices ────────────────────────────────────────────────────────
// A voice id tells you nothing until you hear it, so the picker lists what the
// provider actually offers and every entry can be auditioned.
async function loadVoices() {
  const provider = $('#tts_provider').value;
  const select = $('#voice_id');
  const hint = $('#voice-hint');
  if (!provider) { select.innerHTML = '<option value="">standart ovoz</option>'; return; }

  const wanted = select.value;
  select.innerHTML = '<option value="">yuklanmoqda…</option>';
  select.disabled = true;
  try {
    const data = await api(`/api/voices?provider=${encodeURIComponent(provider)}`);
    state.voices = data.voices || [];
    select.innerHTML = `<option value="">standart — ${esc(data.default || '?')}</option>` +
      state.voices.map((v) => {
        const about = [v.hint, v.tone].filter(Boolean).join(' · ');
        return `<option value="${esc(v.id)}">${esc(v.label)}${about ? ` — ${esc(about)}` : ''}</option>`;
      }).join('');
    if (wanted && state.voices.some((v) => v.id === wanted)) select.value = wanted;
    if (data.error || !state.voices.length) {
      // A key that exists but is refused used to look like a working setup: the
      // provider stayed selected, the app looked configured, and the only sign
      // was a line of grey text. It is a failure, so it reads as one — and it
      // offers the way out, because otherwise you are stuck on a provider that
      // will refuse every scene.
      voiceTrouble(hint, provider,
                   data.error || 'Bu provayder uchun ovoz ro’yxati yo’q.');
    } else {
      hint.className = 'voice-hint';
      hint.textContent = `${state.voices.length} ta ovoz — ▶ bosib namunasini eshiting.`;
    }
  } catch (e) {
    select.innerHTML = '<option value="">standart ovoz</option>';
    voiceTrouble(hint, provider, e.message);
  } finally {
    select.disabled = false;
  }
  if (state.brand) drawBrand();
}

// The composer's voice loader writes into fixed ids; this one fills the copy
// living inside the re-record dialog, which can be open at the same time.
async function fillRevoice(provider, wanted) {
  const select = $('#revoice-voice');
  const hint = $('#revoice-hint');
  if (!select) return;
  const load = async () => {
    const chosen = $('#revoice-provider').value;
    select.innerHTML = '<option value="">yuklanmoqda…</option>';
    try {
      const data = await api(`/api/voices?provider=${encodeURIComponent(chosen)}`);
      select.innerHTML = `<option value="">standart — ${esc(data.default || '?')}</option>` +
        (data.voices || []).map((v) => {
          const about = [v.hint, v.tone].filter(Boolean).join(' · ');
          return `<option value="${esc(v.id)}">${esc(v.label)}${about ? ` — ${esc(about)}` : ''}</option>`;
        }).join('');
      if (wanted && (data.voices || []).some((v) => v.id === wanted)) select.value = wanted;
      if (data.error || !(data.voices || []).length) {
        voiceTrouble(hint, chosen,
                     data.error || 'Bu provayder uchun ovoz ro’yxati yo’q.');
        // Switching from inside the dialog has to move the dialog's own select,
        // not the composer's, or the choice would not be the one you confirm.
        $('[data-swap]', hint)?.addEventListener('click', () => {
          $('#revoice-provider').value = $('#tts_provider').value;
          wanted = '';
          load();
        });
      } else {
        hint.className = 'voice-hint';
        hint.textContent = `${data.voices.length} ta ovoz — ▶ bosib eshiting.`;
      }
    } catch (e) {
      select.innerHTML = '<option value="">standart ovoz</option>';
      voiceTrouble(hint, chosen, e.message);
    }
  };
  $('#revoice-provider').addEventListener('change', () => { wanted = ''; load(); });
  $('#revoice-hear').addEventListener('click', () =>
    playVoiceSample($('#revoice-provider').value, select.value, $('#revoice-hear')));
  await load();
}

// Say what went wrong, and give the one action that gets past it. Being told
// "401" is only useful next to a button that switches to a provider that works.
function voiceTrouble(hint, provider, message) {
  const others = Object.entries(state.health?.tts_providers || {})
    .filter(([name, ready]) => ready && name !== provider)
    .map(([name]) => name);

  hint.className = 'voice-hint bad';
  hint.innerHTML = esc(message) + (others.length
    ? ` <button type="button" class="linky" data-swap="${esc(others[0])}">${esc(others[0])}ga o‘tish</button>`
    : '');
  $('[data-swap]', hint)?.addEventListener('click', () => {
    $('#tts_provider').value = others[0];
    loadVoices();
    drawSummary();
    toast(`Ovoz provayderi ${others[0]} ga o‘zgartirildi`);
  });
}

let voiceAudio = null;
async function playVoiceSample(provider, voiceId, button) {
  if (!voiceId) { toast('Avval ovozni tanlang'); return; }
  voiceAudio?.pause();
  voiceAudio = new Audio(
    `/api/voices/preview?provider=${encodeURIComponent(provider)}` +
    `&voice_id=${encodeURIComponent(voiceId)}&language=${encodeURIComponent($('#language').value || 'en')}`);
  button?.classList.add('busy');
  try {
    await voiceAudio.play();
    voiceAudio.addEventListener('ended', () => button?.classList.remove('busy'), { once: true });
  } catch {
    button?.classList.remove('busy');
    // The endpoint answers with the actual reason; a generic "check your key"
    // is useless when the real problem is a missing permission or a busy
    // provider, so go and read it.
    try {
      const resp = await fetch(voiceAudio.src);
      const why = resp.ok ? '' : (await resp.json().catch(() => ({}))).error;
      toast(why || 'Namuna eshittirilmadi');
    } catch {
      toast('Namuna eshittirilmadi');
    }
  }
}

$('#voice-play').addEventListener('click', () =>
  playVoiceSample($('#tts_provider').value, $('#voice_id').value, $('#voice-play')));

$('#tts_provider').addEventListener('change', loadVoices);
$('#voice_id').addEventListener('change', drawSummary);
$('#language').addEventListener('change', drawSummary);
$('#subtitle_style').addEventListener('change', drawSummary);
$('#burn_subtitles').addEventListener('change', drawSummary);

// ── topic / script / dub mode ─────────────────────────────────────
$$('#mode-tabs button').forEach((b) => b.addEventListener('click', () => {
  state.mode = b.dataset.mode;
  $$('#mode-tabs button').forEach((x) => x.setAttribute('aria-pressed', x === b));
  const script = state.mode === 'script';
  const dub = state.mode === 'dub';
  const sub = state.mode === 'subtitle';
  // Both of these work on a video that already exists, so between them they hide
  // everything that is about inventing one.
  const onExisting = dub || sub;
  $('#script-box').classList.toggle('hidden', !script);
  $('#dub-box').classList.toggle('hidden', !dub);
  $('#subtitle-box').classList.toggle('hidden', !sub);
  // Dubbing has no picture to stage and no script to direct.
  $('#action-box').classList.toggle('hidden', onExisting);
  $('#animate-row').classList.toggle('hidden', onExisting);
  $('#animate-note').classList.toggle('hidden', onExisting);
  // Dubbing replaces the voice on a picture that already exists, so there is no
  // topic to write and nothing for the AI to imagine.
  $('.composer .prompt').classList.toggle('hidden', onExisting);
  $('#topic').closest('.prompt').classList.toggle('hidden', onExisting);
  // With a script supplied, length comes from the words, not from a slider.
  $('#duration-row').classList.toggle('hidden',
    script || onExisting || $('#use_upload').checked);
  $('#topic').placeholder = script
    ? 'Video nima haqida — bir qatorda (ixtiyoriy kontekst)'
    : "Ipak yo'li bo'ylab sayohat qilgan uch savdogarning haqiqiy tarixi…";
  $('#topic').rows = script ? 1 : 2;
  $('#submit-btn').textContent = dub ? 'Dublyaj qilish'
    : sub ? 'Subtitr qo‘shish' : 'Video yaratish';
  $('.composer h1').textContent = dub ? 'Qaysi videoni tarjima qilamiz?'
    : sub ? 'Qaysi videoga subtitr qo‘shamiz?' : 'Nima haqida video?';
  drawDock();
  drawSummary();
}));

// ── composer ──────────────────────────────────────────────────────
const duration = $('#duration');
const syncDuration = () => {
  $('#duration-label').textContent = durationLabel(Number(duration.value));
  drawSummary();
};
duration.addEventListener('input', syncDuration);
syncDuration();

$('#topic').addEventListener('input', (e) => {
  e.target.style.height = 'auto';
  e.target.style.height = `${Math.min(e.target.scrollHeight, 220)}px`;
  // The advice was about a different subject the moment the subject changes.
  $('#advise-out').classList.add('hidden');
});

// ── how long should this be ───────────────────────────────────────
// Length and shape were a slider with 180 on it and a format nobody moved.
// Neither can be guessed from the app's side, but both can be advised on from
// the subject — so it is asked here, and applied with one tap rather than
// described and left for the user to go and set by hand.

const setFormat = (id) => {
  state.format = id;
  $$('#format-seg button').forEach((x) => x.setAttribute('aria-pressed', x.dataset.fmt === id));
};

$('#advise-btn').addEventListener('click', async () => {
  const btn = $('#advise-btn');
  const out = $('#advise-out');
  const topic = $('#topic').value.trim();
  if (topic.length < 2) { toast('Avval mavzuni yozing'); $('#topic').focus(); return; }

  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = 'So‘ralmoqda…';
  try {
    const a = await api('/api/advise/length', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic, language: $('#language').value || '' }),
    });
    out.classList.remove('hidden');
    out.innerHTML = `
      <div class="advise-head">
        <b>${esc(a.label)} · ${durationLabel(a.seconds)}</b>
        <em>${durationLabel(a.low)} – ${durationLabel(a.high)}</em>
      </div>
      <p>${esc(a.why)}</p>
      ${a.title_note ? `<p class="advise-note">${esc(a.title_note)}</p>` : ''}
      <div class="advise-acts">
        <button type="button" class="btn sm primary" data-take="${a.seconds}"
          data-fmt="${esc(a.video_format)}">Shu tanlansin</button>
        ${a.both ? `<button type="button" class="btn sm" data-take="${a.other_seconds}"
          data-fmt="${esc(a.other_format)}">${esc(a.other_label)} — ${
            durationLabel(a.other_seconds)}</button>` : ''}
      </div>`;
    $$('#advise-out [data-take]').forEach((b) => b.addEventListener('click', () => {
      // Clamped to what the slider can hold, so applying advice never leaves
      // the control showing one number and the request carrying another.
      const secs = Math.max(Number(duration.min), Math.min(Number(duration.max),
        Number(b.dataset.take)));
      duration.value = String(secs);
      setFormat(b.dataset.fmt);
      syncDuration();
      toast(`${durationLabel(secs)} · ${b.dataset.fmt} tanlandi`);
    }));
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = was;
  }
});

/** Cartoon mode only works with characters, so it says so rather than failing.
 *
 * The check is against what is actually ticked in the cast picker, live: a
 * warning that appears after you have already pressed the button and waited two
 * minutes for a script is not a warning, it is a bill.
 */
function syncAnimate() {
  const on = $('#animate_actors').checked;
  const cast = $$('#hero-picker input:checked').length;
  const note = $('#animate-note');
  note.classList.toggle('hidden', !on);
  if (!on) { drawSummary(); return; }
  if (!cast) {
    note.className = 'note bad';
    note.textContent = 'Buning uchun kamida bitta qahramon tanlang — «Herolar» bo‘limidan.';
  } else {
    note.className = 'note';
    note.textContent = `${cast} ta qahramon fondan kesiladi va sahna ustida `
      + 'harakatlanadi. Fon esa ularsiz, alohida chiziladi.';
  }
  drawSummary();
}

$('#animate_actors').addEventListener('change', syncAnimate);
$('#action').addEventListener('input', drawSummary);

$('#use_upload').addEventListener('change', (e) => {
  const up = e.target.checked;
  $('#audio-field').classList.toggle('hidden', !up);
  $('#duration-row').classList.toggle('hidden', up || state.mode === 'script');
  $('#tts-field').classList.toggle('hidden', up);
  $('#voice-field').classList.toggle('hidden', up);
  drawSummary();
});

async function submitDub() {
  const err = $('#create-error');
  const file = $('#dub_file').files[0];
  if (!file) {
    err.textContent = 'Avval videoni tanlang.';
    err.classList.remove('hidden');
    return;
  }
  const body = new FormData();
  body.append('video', file);
  body.append('language', $('#language').value);
  body.append('source_language', $('#dub_source').value);
  body.append('original_volume', $('#dub_original').value);
  body.append('render_speed', state.speed);
  body.append('shot_pace', state.pace);
  body.append('topic', file.name.replace(/\.[^.]+$/, ''));
  if ($('#tts_provider').value) body.append('tts_provider', $('#tts_provider').value);
  if ($('#voice_id').value) body.append('voice_id', $('#voice_id').value);
  const job = await api('/api/dub', { method: 'POST', body });
  go('run');
  watch(job.id, { reveal: true });
}

async function submitSubtitle() {
  const err = $('#create-error');
  const file = $('#sub_file').files[0];
  if (!file) {
    err.textContent = 'Avval videoni tanlang.';
    err.classList.remove('hidden');
    return;
  }
  const body = new FormData();
  body.append('video', file);
  // Empty means "work it out from the audio", which is what the transcribers do
  // anyway — naming the language is an option, not a question to be answered.
  body.append('language', $('#sub_language').value);
  body.append('subtitle_style', $('#subtitle_style').value);
  body.append('burn_subtitles', $('#sub_burn').value === '1' ? 'true' : 'false');
  body.append('render_speed', state.speed);
  body.append('topic', file.name.replace(/\.[^.]+$/, ''));
  const job = await api('/api/videos/subtitle', { method: 'POST', body });
  go('run');
  watch(job.id, { reveal: true });
}

$('#submit-btn').addEventListener('click', async () => {
  const btn = $('#submit-btn');
  const err = $('#create-error');
  err.classList.add('hidden');

  // The two modes that work on a video the user already has share everything
  // except which endpoint they post to and what the button says afterwards.
  const onExisting = { dub: [submitDub, 'Dublyaj qilish'],
                       subtitle: [submitSubtitle, 'Subtitr qo‘shish'] }[state.mode];
  if (onExisting) {
    const [send, label] = onExisting;
    btn.disabled = true;
    btn.textContent = 'Yuborilmoqda…';
    try {
      await send();
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove('hidden');
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
    return;
  }

  const scriptMode = state.mode === 'script';
  const script = $('#script').value.trim();
  let topic = $('#topic').value.trim();

  if (scriptMode && script.length < 40) {
    err.textContent = 'Senariy juda qisqa — kamida bir necha jumla yozing.';
    err.classList.remove('hidden');
    return;
  }
  if (!scriptMode && topic.length < 2) {
    err.textContent = 'Avval mavzuni yozing.';
    err.classList.remove('hidden');
    return;
  }
  // The API always wants a topic; in script mode it is only context.
  if (!topic) topic = script.slice(0, 180);

  btn.disabled = true;
  btn.textContent = 'Yuborilmoqda…';
  const heroIds = $$('#hero-picker input:checked').map((i) => i.value);
  const uploading = $('#use_upload').checked;

  const common = {
    topic,
    video_format: state.format,
    language: $('#language').value,
    art_style: $('#art_style').value,
    tone: $('#tone').value,
    subtitle_style: $('#subtitle_style').value,
    burn_subtitles: $('#burn_subtitles').checked,
    render_speed: state.speed,
    shot_pace: state.pace,
    auto_hook: $('#auto_hook').checked,
    brand_logo: $('#brand_logo').checked,
    action: $('#action').value.trim(),
    animate_actors: $('#animate_actors').checked,
    review_script: $('#review_script').checked,
    auto_render: !$('#review_first').checked,
  };

  try {
    let job;
    if (uploading) {
      const file = $('#audio_file').files[0];
      if (!file) throw new Error('Audio fayl tanlanmagan.');
      const body = new FormData();
      Object.entries(common).forEach(([k, v]) => body.append(k, String(v)));
      body.append('hero_ids', heroIds.join(','));
      if ($('#image_provider').value) body.append('image_provider', $('#image_provider').value);
      if ($('#music_id').value) body.append('music_id', $('#music_id').value);
      body.append('audio', file);
      job = await api('/api/jobs/with-audio', { method: 'POST', body });
    } else {
      job = await api('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...common,
          script: scriptMode ? script : null,
          target_seconds: Number(duration.value),
          hero_ids: heroIds,
          image_provider: $('#image_provider').value || null,
          tts_provider: $('#tts_provider').value || null,
          voice_id: $('#voice_id').value.trim() || null,
          music_id: $('#music_id').value || null,
          music_start: Number($('#music_start').value) || 0,
        }),
      });
    }
    go('run');
    watch(job.id, { reveal: true });
  } catch (e) {
    err.textContent = e.message;
    err.classList.remove('hidden');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Video yaratish';
  }
});

// ── watch a job ───────────────────────────────────────────────────
// The one job this browser is following, remembered across a reload. Rendering
// happens on the server, so closing the tab never stopped the work — but until
// this was written down, coming back meant hunting for the job again.
const WATCH_KEY = 'studio.watching';

function remember(jobId) {
  try {
    if (jobId) localStorage.setItem(WATCH_KEY, jobId);
    else localStorage.removeItem(WATCH_KEY);
  } catch (e) { /* private mode: following just does not survive a reload */ }
}

function remembered() {
  try { return localStorage.getItem(WATCH_KEY) || ''; } catch (e) { return ''; }
}

// A render takes minutes, so nobody is going to sit and watch it. Permission is
// asked the first time one starts — not on page load, where a browser prompt out
// of nowhere is just an annoyance to dismiss.
function askToNotify() {
  try {
    if (window.Notification && Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
  } catch (e) { /* not every browser has it, and none of this is required */ }
}

function announce(job) {
  const done = job.status === 'done';
  const title = job.title || job.topic || 'Video';
  // On screen if you are here, as a system notification if you are not. The
  // toast alone is useless to someone who put the phone in their pocket.
  toast(done ? `${title} — tayyor` : `${title} — ${STATUS[job.status] || job.status}`);
  try {
    if (!window.Notification || Notification.permission !== 'granted') return;
    if (!document.hidden) return;
    new Notification(done ? 'Video tayyor' : 'Video tugadi', {
      body: done ? `${title} — ko‘rish uchun oching.`
                 : `${title} — ${job.error || STATUS[job.status] || job.status}`,
      tag: job.id,          // a second notice for the same job replaces the first
      icon: '/static/favicon.svg',
    });
  } catch (e) { /* notifying is a courtesy, never a failure */ }
}

function watch(jobId, { reveal = false } = {}) {
  // Marks belong to one revision of one script, not to the next project opened.
  SCRIPT_MARK = [];
  state.activeId = jobId;
  remember(jobId);
  // Opening a project is the moment the strip stops being what you want on
  // screen — unless you have said otherwise, in which case it is left alone.
  autoProjects();
  askToNotify();
  drawDock();
  state.drawn = null;
  state.reveal = reveal;
  $('#stage').classList.remove('hidden');
  // A different project's frame must not be left on screen while the new one's
  // first picture is still being fetched.
  $('#run-live').innerHTML = '';
  drawEditEmpty();
  drawRunEmpty();
  drawRunChips();
  if (state.poll) clearInterval(state.poll);
  tick();
  state.poll = setInterval(tick, 2500);
}

// Phones throttle timers hard once a tab is in the background — a two-and-a-half
// second poll can become a minute. Coming back should show the truth at once
// rather than the last frame from before the screen went off.
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && state.activeId) {
    // The stall counter measures silence from the server, and a throttled tab
    // is silence from us. Clearing the mark stops a backgrounded phone from
    // returning to a false "the provider is not answering".
    state.mark = null;
    state.markAt = 0;
    tick();
  }
});

async function tick() {
  if (!state.activeId) return;
  try {
    const job = await api(`/api/jobs/${state.activeId}`);
    drawLive(job);
    drawStage(job);
    syncEditor(job);
    if (SETTLED.includes(job.status)) {
      clearInterval(state.poll);
      state.poll = null;
      remember(null);
      announce(job);
      loadJobs();
    }
  } catch (e) {
    clearInterval(state.poll);
    state.poll = null;
    $('#stage').innerHTML = `<p class="msg err">${esc(e.message)}</p>`;
  }
}

async function stopJob(id, button) {
  button.disabled = true;
  button.textContent = 'To‘xtatilmoqda…';
  try {
    const job = await api(`/api/jobs/${id}/cancel`, { method: 'POST' });
    state.mark = null;
    state.markAt = 0;
    drawStage(job);
    syncEditor(job);
    if (state.poll) { clearInterval(state.poll); state.poll = null; }
    loadJobs();
    // Anything already made is kept, so say where it went rather than leaving
    // the user to guess whether the last ten minutes were wasted.
    toast(job.status === 'review'
      ? 'To‘xtatildi — tayyor sahnalar tahrirlash bo‘limida saqlandi.'
      : 'To‘xtatildi.');
  } catch (e) {
    button.disabled = false;
    button.textContent = 'To‘xtatish';
    toast(e.message);
  }
}

// ── the live frame ────────────────────────────────────────────────
// The one thing that should never leave the screen while a video is being made:
// what it looks like so far. It used to be buried under the progress card, and
// once finished the film sat inside markup that the poll rewrote wholesale —
// so a <video> two seconds into playing was replaced by a fresh one at zero.
// Built once, then only the attributes that changed are touched.

function drawLive(job) {
  const host = $('#run-live');
  if (!host) return;
  const shots = (job.scenes || []).filter((s) => s.image_url);
  const newest = shots.at(-1);
  const show = Boolean(job.video_url || newest);
  host.classList.toggle('hidden', !show);
  if (!show) return;

  if (!host.firstChild) {
    host.innerHTML = `<div class="live-frame">
        <img id="live-img" alt="" />
        <video id="live-vid" class="hidden" controls playsinline preload="metadata"></video>
      </div>
      <div class="live-say"><span id="live-what"></span>
        <button class="btn sm ghost" id="live-edit">Tahrirlash</button></div>`;
    $('#live-edit').addEventListener('click', () => {
      go('edit');
      if (state.activeId) watch(state.activeId, { reveal: true });
    });
  }

  const shape = (state.health?.formats || []).find((f) => f.id === job.video_format)
    || { width: 1920, height: 1080 };
  $('.live-frame').style.setProperty('--ar', (shape.width / shape.height).toFixed(4));

  const vid = $('#live-vid');
  const img = $('#live-img');
  if (job.video_url) {
    // `getAttribute`, not `.src`: the property is resolved to an absolute URL,
    // so comparing it to the relative one the server sends never matches and
    // the video would be reloaded on every poll.
    if (vid.getAttribute('src') !== job.video_url) vid.setAttribute('src', job.video_url);
    vid.classList.remove('hidden');
    img.classList.add('hidden');
    $('#live-what').textContent = 'Tayyor video';
  } else {
    if (newest && img.getAttribute('src') !== newest.image_url) img.src = newest.image_url;
    img.classList.toggle('hidden', !newest);
    vid.classList.add('hidden');
    $('#live-what').textContent = newest
      ? `Sahna ${newest.index + 1} — hozircha shu yerda` : '';
  }
}

/** The projects worth switching between while something is being made. */
function drawRunChips() {
  const host = $('#run-chips');
  if (!host) return;
  const live = state.jobs.filter((j) => BUSY.includes(j.status) || j.status === 'review');
  // The one being watched belongs here even when it has finished — otherwise
  // the chip you are standing on disappears the moment the render succeeds.
  const open = state.jobs.find((j) => j.id === state.activeId);
  const rows = open && !live.some((j) => j.id === open.id) ? [open, ...live] : live;
  host.classList.toggle('hidden', rows.length < 2);
  host.innerHTML = rows.map((j) => `
    <button class="runchip${j.id === state.activeId ? ' on' : ''}" data-run="${esc(j.id)}">
      <i class="dot ${esc(j.status)}"></i>${esc(j.title || j.topic || j.id)}
      <em>${j.progress ?? 0}%</em>
    </button>`).join('');
  $$('#run-chips [data-run]').forEach((b) =>
    b.addEventListener('click', () => watch(b.dataset.run, { reveal: true })));
}

/** What the progress screen shows when nothing is being made. */
function drawRunEmpty() {
  const empty = $('#run-empty');
  if (!empty) return;
  const show = state.view === 'run' && !state.activeId;
  empty.classList.toggle('hidden', !show);
  if (!show) return;
  const pick = state.jobs.find((j) => BUSY.includes(j.status))
    || state.jobs.find((j) => j.status === 'review');
  $('#run-empty-acts').innerHTML = [
    pick ? `<button class="btn primary" data-open-run="${esc(pick.id)}">${
      esc(pick.title || pick.topic || 'Loyiha')} — kuzatish</button>` : '',
    '<button class="btn" data-go="create">Yangi video</button>',
    '<button class="btn ghost" data-go="edit">Tahrirlashga o‘tish</button>',
  ].join('');
  $$('#run-empty-acts [data-open-run]').forEach((b) =>
    b.addEventListener('click', () => watch(b.dataset.openRun, { reveal: true })));
}

function drawStage(job) {
  const p = [];
  const busy = BUSY.includes(job.status);

  // The raw step name is only worth showing while something is moving —
  // once settled it just repeats the status word next to it.
  // While something is moving, the newest log line is the only thing worth
  // reading — "Voice-over 7/12" says more than the step name ever does, and it
  // was previously buried at the bottom of a scrolled box.
  const latest = (job.logs || []).at(-1)?.replace(/^\[[\d:]+\]\s*/, '') || '';
  const meta = [STATUS[job.status] || job.status];
  if (busy && latest) meta.push(latest);
  else if (busy && job.step && job.step !== job.status) meta.push(job.step);
  if (job.duration) meta.push(clock(job.duration));
  if (job.caption_count) meta.push(`${job.caption_count} ta satr`);
  else if (job.scene_count) meta.push(`${job.scene_count} sahna`);

  // How long since the job last said anything. A provider that accepts a
  // request and never answers used to look exactly like a frozen app, so the
  // wait is now on screen — with a way out of it once it stops being normal.
  const stamp = `${job.id}:${job.updated_at}`;
  if (!busy) { state.mark = null; state.markAt = 0; }
  else if (state.mark !== stamp) { state.mark = stamp; state.markAt = Date.now(); }
  // A job waiting for a slot is silent on purpose, so the silence must not be
  // reported as a provider that has stopped answering.
  const waiting = job.status === 'queued' && job.queue_place > 0;
  const idle = busy && !waiting && state.markAt
    ? Math.floor((Date.now() - state.markAt) / 1000) : 0;

  p.push(`<div class="stage-head">
      <h2>${esc(job.title || job.topic || 'Video')}</h2>
      <span class="stage-pct">${job.progress}%</span>
    </div>
    <div class="track ${esc(job.status)}${busy ? ' live' : ''}"><i style="width:${job.progress}%"></i></div>
    <p class="step${busy ? ' busy' : ''}">${esc(meta.join(' · '))}</p>
    ${busy ? `<div class="stage-stop">
      <!-- Available for the whole of a busy job, not only once it looks stuck.
           Changing your mind about a video is not an error condition, and a
           forty-scene render is a long time to have no way out of. -->
      <button class="btn ghost sm" data-stop="${esc(job.id)}">To‘xtatish</button>
      ${drawnByNote(job)}
    </div>` : ''}`);

  // Scenes that ended up with a grey rectangle. Offered here because this is
  // where you find out — the counters say 18/18 and the thumbnails are grey, and
  // the only way back used to be regenerating each scene by hand.
  if (!busy && job.placeholders) {
    p.push(`<div class="stage-redo">
      <button class="btn" data-redo="${esc(job.id)}">
        ${job.placeholders} ta rasm chiqmagan — qayta yasash</button>
      <small>Flow varag'i ochiq va kengaytma ishlab turganiga ishonch hosil qiling.</small>
    </div>`);
  }

  // Nothing has been drawn or recorded yet — the whole video is still just these
  // words, which is exactly why this is the moment to read them.
  if (job.status === 'script') {
    $('#stage').innerHTML = p.join('');
    drawScriptReview(job);
    return;
  }

  // Waiting for a slot is not the same as working, and a render that says
  // "rendering" while it sits behind another one is how the app looked frozen.
  if (job.status === 'queued' && job.queue_place) {
    p.push(`<div class="stall queue"><span>Navbatda ${job.queue_place}-chi — oldingi
      render tugagach o‘zi boshlanadi. Ilovani yopsangiz ham davom etadi.</span></div>`);
  }

  if (busy && idle >= IDLE_WARN) {
    p.push(`<div class="stall${idle >= IDLE_STOP ? ' long' : ''}">
      <span>${idle >= IDLE_STOP
        ? `Provayder ${idle} soniyadan beri javob bermayapti.`
        : `Kutilmoqda… ${idle} s`}</span>
    </div>`);
  }

  if (job.status === 'failed' && job.error) p.push(`<p class="msg err">${esc(job.error)}</p>`);
  (job.warnings || []).forEach((w) => p.push(`<p class="msg warn">${esc(w)}</p>`));

  // What has actually been made so far. While it is running this is the honest
  // answer to "is it working?", and after it stops it is the answer to "was any
  // of that worth keeping?" — which used to be invisible either way.
  const made = (job.scenes || []).filter((s) => s.image_url || s.audio_url);
  const left = job.progress_detail;
  if (made.length && job.status !== 'done') {
    // Counted per kind, not as one number. A draft in the middle of its
    // voice-over has ten recordings and no pictures yet, and reporting that as
    // "0/28 ready" reads as nothing having happened — next to a strip visibly
    // full of finished clips.
    const done = [];
    if (left) {
      const spoken = Math.max(0, (left.scenes_total || 0) - (left.voices_left || 0));
      const drawn = Math.max(0, (left.images_total || 0) - (left.images_left || 0));
      if (left.scenes_total) done.push(`Ovoz ${spoken}/${left.scenes_total}`);
      if (left.images_total) done.push(`Rasm ${drawn}/${left.images_total}`);
    }
    p.push(`<div class="made">
      <div class="made-head">
        <span>${esc(done.join(' · ') || `Tayyor · ${made.length}`)}</span>
        ${left?.left ? `<em>${left.left} ta qoldi</em>` : ''}
      </div>
      <div class="made-strip">${made.map((s) => `
        <figure${s.image_url ? '' : ' class="soundonly"'}>
          ${s.image_url
            ? `<img src="${esc(s.image_url)}" alt="Sahna ${s.index + 1}" loading="lazy" />`
            : '<span class="wave">♪</span>'}
          <figcaption>${s.index + 1}</figcaption>
          ${s.audio_url ? `<button class="hear" data-hear="${esc(s.audio_url)}"
            aria-label="Sahna ${s.index + 1} ovozini eshitish">▶</button>` : ''}
        </figure>`).join('')}</div>
    </div>`);
  }

  // A run that stopped can be carried on instead of redone. Offered whenever it
  // failed, not only when the counters say something is outstanding: a project
  // whose files vanished under it has a row for every scene and still cannot
  // render, and that is exactly the case where being left with no way forward
  // is worst. The resume checks the disk itself and remakes what is not there.
  if (!busy && job.status !== 'done' && (left?.left || job.status === 'failed')) {
    p.push(`<div class="acts">
      <button class="btn primary" data-resume="${esc(job.id)}">Davom ettirish</button>
      <small class="note">Tayyor bo‘lganlari qayta yaratilmaydi.</small>
    </div>`);
  }

  if (job.status === 'done' && job.video_url) {
    // No player here — the live frame above this card is holding it, and two
    // copies of the same film on one screen is one copy too many.
    p.push(`<div class="acts">
        <a class="btn primary" href="${esc(job.download_url || job.video_url)}" download>Videoni yuklab olish</a>
        <span class="subs">Subtitr:
          <a href="/api/jobs/${esc(job.id)}/subtitles.srt" download>.srt</a>
          <a href="/api/jobs/${esc(job.id)}/subtitles.vtt" download>.vtt</a>
          <a href="/api/jobs/${esc(job.id)}/subtitles.txt" download>matn</a>
        </span>
        <button class="btn ghost" data-go="ready">Matnlarni ko‘rish</button>
      </div>`);
  }

  if (job.transcript?.length) {
    p.push(`<details class="fold"><summary>Tarjima matni · ${job.transcript.length} qator</summary>
      <div class="fold-body"><div class="lines">${job.transcript.map((t) => `
        <div><b>${clock(t.start)}</b><span>${esc(t.text)}</span><em>${esc(t.translation || '')}</em></div>`).join('')}
      </div></div></details>`);
  }

  if (job.status === 'done' && job.thumbnails?.length) {
    p.push(`<div class="thumbs">${job.thumbnails.map((url, i) => `
      <figure><img src="${esc(url)}" alt="Muqova ${i + 1}" />
        <a class="btn" href="${esc(url)}" download="thumbnail-${i + 1}.png">Yuklab olish</a>
      </figure>`).join('')}</div>`);
  }

  // The prompts sit here, in the place the journal used to have to itself: this
  // is the bottom of the card, where you end up once you have read what state
  // the project is in, and the prompts are what you do next about it. Filled in
  // after the card is in the document — it is a second request, and the rest of
  // the progress card must not wait on it.
  const manual = (job.image_provider_now || '') === 'manual';
  const bare = (job.scenes || []).some((s) => s.needs_image || !s.image_url || s.placeholder);
  if (!busy && job.scene_count && (manual || bare)) {
    p.push('<div class="prompts" id="prompts"></div>');
  }

  if (job.logs?.length) {
    // Not open by default any more unless something is moving. A finished
    // project's journal is history, and it was sitting open above nothing.
    p.push(`<details class="fold"${busy ? ' open' : ''}><summary>Jurnal</summary>
      <div class="fold-body">
        <div class="logs">${esc(job.logs.join('\n'))}</div>
        <!-- Warnings are sticky on purpose, so a bad picture keeps saying so.
             The cost is that a project carried between providers keeps every
             complaint the old one made, which is not news about the new one. -->
        <button class="btn sm ghost" data-wipe="${esc(job.id)}">Jurnalni tozalash</button>
      </div></details>`);
  }

  $('#stage').innerHTML = p.join('');
  $$('#stage [data-wipe]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await api(`/api/jobs/${b.dataset.wipe}/logs`, { method: 'DELETE' });
      const fresh = await api(`/api/jobs/${b.dataset.wipe}`);
      drawStage(fresh);
      toast('Jurnal va eski ogohlantirishlar o‘chirildi');
    } catch (e) { b.disabled = false; toast(e.message); }
  }));
  $$('#stage [data-go]').forEach((b) => b.addEventListener('click', () => go(b.dataset.go)));
  $$('#stage [data-stop]').forEach((b) => b.addEventListener('click', () => stopJob(b.dataset.stop, b)));
  $$('#stage [data-redo]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    b.textContent = 'Boshlandi…';
    try {
      await api(`/api/jobs/${b.dataset.redo}/images/redo`, { method: 'POST' });
      state.drawn = null;
      watch(b.dataset.redo);
    } catch (e) {
      b.disabled = false;
      alert(e.message);
    }
  }));
  $$('#stage [data-resume]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await api(`/api/jobs/${b.dataset.resume}/resume`, { method: 'POST' });
      toast('Davom etmoqda — tayyorlari saqlanadi');
      watch(b.dataset.resume);
    } catch (err) { b.disabled = false; toast(err.message); }
  }));
  $$('#stage [data-hear]').forEach((b) => b.addEventListener('click', () => {
    // One preview at a time: several scenes playing over each other tells you
    // nothing about any of them.
    if (state.preview) { state.preview.pause(); state.preview = null; }
    const audio = new Audio(b.dataset.hear);
    state.preview = audio;
    audio.play().catch(() => toast('Brauzer ovozni to‘sdi'));
  }));

  if ($('#prompts')) loadSheet(job);

  // The log grows downwards, so without this the newest line is the one you
  // cannot see — which is the whole reason for watching it.
  const logs = $('#stage .logs');
  if (logs) logs.scrollTop = logs.scrollHeight;

  if (state.reveal) {
    state.reveal = false;
    // One frame later: the card was only just given its content, and scrolling
    // to an element the browser has not laid out yet lands in the wrong place.
    requestAnimationFrame(() =>
      $('#stage').scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }
}

// ── the prompt sheet ──────────────────────────────────────────────
// Making the pictures somewhere else is a loop: copy a prompt, draw it, save the
// file, next. The app used to hold up its end of that loop badly — the prompt
// for scene 14 lived behind two taps in a panel, and there was no way to see how
// far through the pile you were. This is the pile, flat and numbered, with the
// upload at the bottom of it.

async function loadSheet(job) {
  const host = $('#prompts');
  if (!host) return;
  // Refetched only when the pictures have actually moved. `tick` redraws this
  // card every couple of seconds and the prompts do not change that often.
  const key = `${job.id}:${job.scene_count}:${job.progress_detail?.images_left ?? ''}`;
  if (state.sheetKey === key && state.sheet) return drawSheet(job);
  host.innerHTML = '<p class="note">Promptlar o‘qilmoqda…</p>';
  try {
    state.sheet = await api(`/api/jobs/${job.id}/prompts`);
    state.sheetKey = key;
    drawSheet(job);
  } catch {
    host.innerHTML = '';
  }
}

function drawSheet(job) {
  const host = $('#prompts');
  const sheet = state.sheet;
  if (!host || !sheet || !sheet.items?.length) { if (host) host.innerHTML = ''; return; }
  const left = sheet.items.filter((i) => !i.done).length;

  host.innerHTML = `
    <details class="fold pr-fold"${left ? ' open' : ''}>
      <summary>Rasm promptlari · ${sheet.items.length} ta${
        left ? ` — ${left} tasi hali yasalmagan` : ' — hammasi joyida'}</summary>
      <div class="fold-body">
        <div class="pr-acts">
          <button class="btn sm" data-pr="all">Hammasini nusxalash</button>
          <a class="btn sm ghost" href="/api/jobs/${esc(job.id)}/prompts.txt"
             download>Matn fayli</a>
          <label class="btn sm primary pr-up">Rasmlarni yuklash
            <input type="file" accept="image/*" multiple hidden data-pr-up /></label>
        </div>
        <p class="note">Har bir promptni nusxalab Flow'da yasang, faylni o‘sha
          raqam bilan saqlang, so‘ng hammasini birdan yuklang.</p>
        <ol class="pr-list">
          ${sheet.items.map((it) => `
            <li${it.done ? ' class="done"' : ''}>
              <span class="pr-n">${it.n}</span>
              <div class="pr-body">
                <b>${esc(it.label)} · ${esc(it.filename)}</b>
                <p>${esc(it.prompt || 'prompt yozilmagan')}</p>
                ${it.heroes.length ? `<div class="pr-refs">${it.heroes.map((h) =>
                  `<a href="${esc(h.url)}" download="${esc(h.name)}.png">${esc(h.name)}</a>`
                ).join('')}</div>` : ''}
              </div>
              <button class="btn sm" data-pr-copy="${it.n}">Nusxa</button>
            </li>`).join('')}
        </ol>
      </div>
    </details>`;

  host.querySelector('[data-pr="all"]').addEventListener('click', (e) =>
    copyText(sheet.items.map((i) => `${i.n}. ${i.prompt}`).join('\n\n'), e.currentTarget));
  host.querySelectorAll('[data-pr-copy]').forEach((b) => b.addEventListener('click', () => {
    const item = sheet.items.find((i) => i.n === Number(b.dataset.prCopy));
    if (item) copyText(item.prompt, b);
  }));
  host.querySelector('[data-pr-up]').addEventListener('change', async (e) => {
    const files = [...e.target.files];
    e.target.value = '';
    await bulkUpload(files, job.id, sheet.items.length);
  });
}

// ── bulk picture upload ───────────────────────────────────────────
// Shared by the sheet above and the filmstrip in the editor: the same pile of
// files arriving by two doors is still one question — which picture goes where.

async function bulkUpload(rawFiles, jobId, wanted = 0) {
  if (!jobId || !rawFiles.length) return;
  // Sorted by name, numerically, before anything else looks at them. A browser
  // does not promise any particular order for a multiple selection, and the
  // prompt sheet asks for files saved as 001, 002, 003 — so "file order" has to
  // mean what those numbers say, not what the file picker felt like. `10` after
  // `9`, not between `1` and `2`.
  const files = [...rawFiles].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));

  // Beyond that the order is the one they were made in, which nothing on disk
  // records. Rather than ask people to rename a hundred files, the picker below
  // lets them be tapped into order — tapping being the one interaction a phone
  // is good at — or taken wholesale when the names already say it.
  const picked = [];
  const urls = files.map((f) => URL.createObjectURL(f));

  const answer = await ask({
    title: `${files.length} ta rasm`,
    ok: 'Joylashtirish',
    html: `<label class="f"><span>Qanday taqsimlansin</span>
        <select name="mode">
          <option value="pick">Men tanlagan tartibda — bosib raqamlayman</option>
          <option value="order">Fayl tartibida — 1-rasm 1-sahnaga</option>
          <option value="auto">AI o‘zi qarab joylashtirsin — sekin</option>
        </select></label>
      <!-- Said before the choice, not discovered after it. The first two modes
           are arithmetic and land instantly; the third sends every picture to a
           model and can sit at the same percentage for minutes. -->
      <p class="note">Birinchi ikki usul bir zumda ishlaydi. AI usuli har bir
        rasmni modelga yuboradi — bir necha daqiqa turishi mumkin.</p>
      <div class="pick-wrap" data-pickwrap>
        <!-- Tapping is right for reordering a handful. For a folder that was
             already saved in the right order — which is what the prompt sheet
             asks you to do — tapping forty thumbnails is the tax for a decision
             you already made. This takes the lot in file-name order. -->
        <div class="pick-acts">
          <button type="button" class="btn sm" data-pickall>Hammasini tanlash</button>
          <button type="button" class="btn sm ghost" data-picknone>Tozalash</button>
        </div>
        <p class="note" data-pickcount></p>
        <div class="pick-grid">${files.map((f, i) => `
          <button type="button" class="pick" data-pick="${i}">
            <img src="${urls[i]}" alt="" loading="lazy" />
            <em></em>
          </button>`).join('')}</div>
      </div>
      <label class="f"><span>Qaysi sahnalarga</span>
        <select name="scope">
          <option value="empty">Faqat rasmi yo‘qlariga</option>
          <option value="all">Hammasiga — borlarini ham almashtir</option>
        </select></label>`,
    onOpen: () => {
      const wrap = $('#modal-body [data-pickwrap]');
      const count = $('#modal-body [data-pickcount]');
      const mode = $('#modal-body [name="mode"]');
      const paint = () => {
        $$('#modal-body .pick').forEach((btn, i) => {
          const at = picked.indexOf(i);
          btn.classList.toggle('on', at >= 0);
          btn.querySelector('em').textContent = at >= 0 ? at + 1 : '';
        });
        count.textContent = picked.length
          ? `${picked.length} ta tanlandi${wanted ? ` — ${wanted} ta kerak` : ''}`
          : `Bosgan tartibingiz saqlanadi — birinchi bosganingiz 1-sahnaga tushadi.${
              wanted ? ` Jami ${wanted} ta rasm kerak.` : ''}`;
      };
      $$('#modal-body .pick').forEach((btn) => btn.addEventListener('click', () => {
        const i = Number(btn.dataset.pick);
        const at = picked.indexOf(i);
        if (at >= 0) picked.splice(at, 1); else picked.push(i);
        paint();
      }));
      $('#modal-body [data-pickall]').addEventListener('click', () => {
        picked.length = 0;
        files.forEach((_f, i) => picked.push(i));
        paint();
      });
      $('#modal-body [data-picknone]').addEventListener('click', () => {
        picked.length = 0;
        paint();
      });
      mode.addEventListener('change', () =>
        wrap.classList.toggle('hidden', mode.value !== 'pick'));
      paint();
    },
  });
  urls.forEach(URL.revokeObjectURL);
  if (!answer) return;

  // Tapped order is file order once the files are sent in that order, so the
  // server never has to learn a third arrangement mode.
  let sending = files;
  if (answer.mode === 'pick') {
    if (!picked.length) { toast('Hech qaysi rasm tanlanmadi'); return; }
    sending = picked.map((i) => files[i]);
  }

  const body = new FormData();
  sending.forEach((file) => body.append('images', file));
  body.append('mode', answer.mode === 'auto' ? 'auto' : 'order');
  body.append('scope', answer.scope || 'empty');
  try {
    if (ED.job?.id === jobId) await flush();
    await api(`/api/jobs/${jobId}/images`, { method: 'POST', body });
    state.drawn = null;
    state.sheetKey = null;
    watch(jobId);
    toast(`${sending.length} ta rasm qabul qilindi — joylashtirilmoqda`);
  } catch (err) {
    toast(err.message);
  }
}

// ══ studio editor ═════════════════════════════════════════════════
// Everything below edits one job in place: the scene, the layers sitting on it,
// and the caption look for the whole video. Edits land in `ED` immediately so
// the canvas can redraw at once, and are flushed to the server on a short timer
// — nobody should have to hunt for a save button after nudging a sticker.

const ED = {
  job: null, scenes: [], style: null, burn: true,
  i: 0, sel: null, tab: 'scene',
  shot: 0,          // which shot of the current scene the panel is editing
  dirty: new Set(), styleDirty: false, timer: null, saving: 0, busy: false,
  // A render is running over these rows, so saving is held until it settles.
  locked: false, held: false,
};

const LAYER_ANIMS = {
  none: 'yo‘q', fade: 'Yumshoq', pop: 'Sakrash', rise: 'Pastdan',
  slide_left: 'Chapdan', slide_right: "O‘ngdan", float: 'Suzish', drift: 'Siljish',
};

// Where a cut-out actor starts and ends, as a fraction of the frame width away
// from where it was placed. The same numbers live in `app/render/overlays.py`;
// they are repeated here so the preview walks the character along exactly the
// path the render will, instead of approximating it.
const ACTOR_MOVES = {
  walk_right: { from: -0.22, to: 0.22, label: "O‘ngga yuradi" },
  walk_left: { from: 0.22, to: -0.22, label: 'Chapga yuradi' },
  enter_left: { from: -0.85, to: 0, label: 'Chapdan kiradi' },
  enter_right: { from: 0.85, to: 0, label: "O‘ngdan kiradi" },
  exit_left: { from: 0, to: -0.85, label: 'Chapga chiqib ketadi' },
  exit_right: { from: 0, to: 0.85, label: "O‘ngga chiqib ketadi" },
  cross_right: { from: -0.85, to: 0.85, label: "Chapdan o‘ngga o‘tadi" },
  cross_left: { from: 0.85, to: -0.85, label: "O‘ngdan chapga o‘tadi" },
  hop: { from: 0, to: 0, label: 'Sakraydi' },
  sway: { from: 0, to: 0, label: 'Tebranadi' },
};

const TRAVELLING = new Set(['enter_left', 'enter_right', 'exit_left', 'exit_right',
  'cross_right', 'cross_left']);
const WALKING = new Set(['walk_right', 'walk_left', 'cross_right', 'cross_left']);

// The server is the authority on which moves exist, but it can only offer the
// ones this build knows how to preview.
function moveIds() {
  const served = (state.health?.overlay_animations?.actor || [])
    .map((m) => m.id).filter((id) => ACTOR_MOVES[id]);
  return served.length ? served : Object.keys(ACTOR_MOVES);
}

const actorLabel = (id) => (state.health?.overlay_animations?.actor || [])
  .find((m) => m.id === id)?.label || ACTOR_MOVES[id]?.label || id;

const CAP_ANIMS = { none: 'yo‘q', fade: 'Yumshoq', pop: 'Sakrash', rise: 'Pastdan' };
const BOX_MODES = { none: 'Yalang‘och', outline: 'Kontur', shadow: 'Soya', box: 'Fon' };
const POSITIONS = { top: 'Yuqorida', middle: "O‘rtada", bottom: 'Pastda' };

// A palette wide enough to cover what people actually reach for, short enough
// to scan in one glance.
const SWATCHES = ['#FFFFFF', '#000000', '#FFE94A', '#FF3B30', '#35F0A0',
  '#12E5FF', '#B36BFF', '#FF7AC8'];

const TEXT_DEFAULTS = {
  type: 'text', text: 'Yangi matn', x: 0.5, y: 0.2, size: 0.09,
  start: 0, end: 0, anim: 'fade', colour: '#FFFFFF', outline_colour: '#000000',
  box: false, box_colour: '#000000', box_opacity: 0.6, bold: true, italic: false,
  rotate: 0, opacity: 1,
};
const IMAGE_DEFAULTS = {
  type: 'image', text: '', x: 0.75, y: 0.3, size: 0.25,
  start: 0, end: 0, anim: 'fade', rotate: 0, opacity: 1,
  colour: '#FFFFFF', outline_colour: '#000000', box: false,
  box_colour: '#000000', box_opacity: 0.6, bold: true, italic: false,
};
// An actor stands on the ground and is tall enough to read as a character
// rather than as a sticker, so it lands lower and larger than a plain picture.
const ACTOR_DEFAULTS = { ...IMAGE_DEFAULTS, x: 0.5, y: 0.66, size: 0.34, anim: 'walk_right' };

const scene = () => ED.scenes[ED.i] || null;
const layers = () => scene()?.overlays || [];
const selected = () => layers().find((l) => l.id === ED.sel) || null;

function fmt() {
  const id = ED.job?.video_format || '16:9';
  return (state.health?.formats || []).find((f) => f.id === id)
    || { width: 1920, height: 1080, caption: { font_size: 96, max_chars: 42, margin: 0.08 } };
}

function capTemplates() {
  return state.health?.caption_templates || [];
}

// ── saving ────────────────────────────────────────────────────────
function setSaver(text, kind = '') {
  const el = $('#saver');
  el.textContent = text;
  el.className = `saver ${kind}`;
}

function touch(what = 'scene') {
  if (what === 'style') ED.styleDirty = true;
  else ED.dirty.add(ED.i);
  // A render is reading the rows this would write to, and the server refuses on
  // that basis. So the edit waits here — visible on the canvas, marked as
  // pending — and goes up on its own when the render settles.
  if (ED.locked) {
    ED.held = true;
    setSaver('render tugagach saqlanadi');
    return;
  }
  setSaver('saqlanmoqda…');
  clearTimeout(ED.timer);
  ED.timer = setTimeout(flush, 800);
}

function editorError(message) {
  const el = $('#editor-error');
  el.textContent = message || '';
  el.classList.toggle('hidden', !message);
}

async function flush() {
  if (!ED.job) return;
  // Held, not dropped: the server refuses writes while it is rendering these
  // rows, so the edits stay in `ED.dirty` and this runs again when it settles.
  if (ED.locked) { ED.held = ED.dirty.size > 0 || ED.styleDirty; return; }
  const id = ED.job.id;
  const pending = [...ED.dirty];
  const styleChanged = ED.styleDirty;
  ED.dirty.clear();
  ED.styleDirty = false;
  if (!pending.length && !styleChanged) return;

  ED.saving += 1;
  try {
    for (const index of pending) {
      const s = ED.scenes.find((x) => x.index === index);
      if (!s) continue;
      await api(`/api/jobs/${id}/scenes/${index}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          narration: s.narration,
          image_prompt: s.image_prompt,
          motion: s.motion,
          motion_strength: s.motion_strength,
          transition: s.transition || '',
          on_screen_text: s.on_screen_text || '',
          hero_ids: s.hero_ids || [],
          speaker: s.speaker || '',
          overlays: s.overlays || [],
          sfx_id: s.sfx_id || '',
          sfx_volume: s.sfx_volume ?? 1,
          sfx_offset: s.sfx_offset ?? 0,
          // The sid rides back untouched — it is what tells the server this is
          // the same shot, so a reorder does not redraw pictures it already has.
          shots: (s.shots || []).map((sh) => ({
            sid: sh.sid || '',
            prompt: sh.prompt || '',
            motion: sh.motion || 'zoom_in',
            motion_strength: sh.motion_strength ?? 1,
            transition: sh.transition || '',
            weight: sh.weight ?? 1,
          })),
        }),
      });
    }
    if (styleChanged) {
      await api(`/api/jobs/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ caption_style: ED.style, burn_subtitles: ED.burn }),
      });
    }
    editorError('');
    setSaver('saqlandi', 'ok');
  } catch (e) {
    setSaver('saqlanmadi', 'bad');
    editorError(e.message);
  } finally {
    ED.saving -= 1;
  }
}

// ── build ─────────────────────────────────────────────────────────
// ══ the script, before anything is made from it ═══════════════════
// Every stage after this one costs money — a picture per scene, a recording per
// line — and all of it is made from these words. So this is the one moment when
// changing your mind is free, and the panel is built for reading rather than for
// editing: the lines are the whole page, and the two things you can do about
// them sit underneath.

function scriptWords(scenes) {
  const chars = scenes.reduce((n, s) => n + (s.narration || '').length, 0);
  // Roughly fourteen characters a second read aloud. Not a promise — a sense of
  // scale, so a script that is twice as long as the video you wanted is obvious
  // before it is voiced.
  return { chars, seconds: Math.round(chars / 14) };
}

// Which lines the last revision touched. Held here rather than passed, because
// the panel is drawn by `drawStage` — so the note handler marks the lines by
// leaving a note for the next draw rather than by drawing a second panel on top
// of the first.
let SCRIPT_MARK = [];

function drawScriptReview(job) {
  const scenes = job.scenes || [];
  const changed = SCRIPT_MARK;
  const { seconds } = scriptWords(scenes);
  const marked = new Set(changed);

  const rows = scenes.map((s) => `
    <div class="line${marked.has(s.index) ? ' changed' : ''}" data-line="${s.index}">
      <span class="line-no">${s.index + 1}</span>
      <textarea data-narration="${s.index}" rows="2"
        aria-label="${s.index + 1}-sahna matni">${esc(s.narration || '')}</textarea>
    </div>`).join('');

  $('#stage').insertAdjacentHTML('beforeend', `
    <div class="script-gate">
      <p class="hint">Hozircha faqat matn — na rasm, na ovoz yaratilgan. O‘qib
        chiqing: qo‘lda tuzatasiz yoki nimani o‘zgartirish kerakligini aytasiz.
        Tasdiqlaganingizdan keyin ovoz yoziladi va rasmlar chiziladi.</p>
      <div class="script-top">
        <p class="script-sum">${scenes.length} sahna · ~${clock(seconds)} o‘qishga</p>
        <!-- Reading a long script is easier somewhere else — on paper, in a
             document, with somebody who is not looking at this phone. -->
        <a class="btn sm" href="/api/jobs/${esc(job.id)}/subtitles.txt"
           download>Matnni yuklab olish</a>
      </div>
      <div class="script-lines">${rows}</div>

      <label class="f"><span>Nimani tuzatish kerak?</span>
        <textarea id="script-note" rows="2" maxlength="2000"
          placeholder="Masalan: 3-sahna quruq chiqibdi, qiziqroq qil. Oxirini kuchaytir."></textarea></label>
      <div class="script-acts">
        <button class="btn" id="script-fix">AI tuzatsin</button>
        <button class="btn primary" id="script-ok">Tasdiqlash va davom etish</button>
      </div>
      <p class="note" id="script-said"></p>
    </div>`);

  // Grown to fit its line. A box that clips its own text is a poor thing to ask
  // somebody to read carefully, which is the only thing this panel is for.
  const grow = (box) => {
    box.style.height = 'auto';
    box.style.height = `${box.scrollHeight + 2}px`;
  };

  // A hand edit goes up when you leave the box, so the line you approve is the
  // line that gets recorded.
  $$('#stage [data-narration]').forEach((box) => {
    grow(box);
    box.addEventListener('input', () => grow(box));
    box.addEventListener('change', async () => {
      const index = Number(box.dataset.narration);
      const text = box.value.trim();
      if (!text) { box.value = scenes[index]?.narration || ''; return; }
      try {
        await api(`/api/jobs/${job.id}/scenes/${index}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ narration: text }),
        });
        const scene = scenes.find((x) => x.index === index);
        if (scene) scene.narration = text;
        box.closest('.line')?.classList.remove('changed');
      } catch (e) { toast(e.message); }
    });
  });

  $('#script-fix').addEventListener('click', async () => {
    const note = $('#script-note').value.trim();
    if (!note) { toast('Nimani tuzatish kerakligini yozing'); return; }
    const btn = $('#script-fix');
    btn.disabled = true;
    btn.textContent = 'Tuzatilyapti…';
    try {
      const out = await api(`/api/jobs/${job.id}/script/revise`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note }),
      });
      // Redrawn with the changed lines marked, so you can see what it did
      // without reading the whole thing again.
      SCRIPT_MARK = out.changed_indexes || [];
      drawStage({ ...job, scenes: out.scenes });
      if (out.note_back) $('#script-said').textContent = out.note_back;
      toast(out.changed
        ? `${out.changed} ta sahna o‘zgardi`
        : 'Hech narsa o‘zgarmadi — boshqacha aytib ko‘ring');
    } catch (e) {
      toast(e.message);
      btn.disabled = false;
      btn.textContent = 'AI tuzatsin';
    }
  });

  $('#script-ok').addEventListener('click', async () => {
    const btn = $('#script-ok');
    btn.disabled = true;
    btn.textContent = 'Boshlanmoqda…';
    try {
      // Whatever is still focused has not fired its change event yet, and
      // approving would then record the line you had just finished replacing.
      document.activeElement?.blur();
      await new Promise((r) => setTimeout(r, 120));
      await api(`/api/jobs/${job.id}/script/approve`, { method: 'POST' });
      watch(job.id);
      toast('Tasdiqlandi — ovoz va rasmlar boshlandi');
    } catch (e) {
      toast(e.message);
      btn.disabled = false;
      btn.textContent = 'Tasdiqlash va davom etish';
    }
  });
}

function syncEditor(job) {
  // A render used to take the editor down with it: the moment the status left
  // `review` the whole studio was unmounted, so pressing Render threw away the
  // scene you were looking at, the layer you had selected and the place you had
  // scrolled to — and gave back a progress bar. The work being rendered is the
  // work you were just editing, so it stays on screen while it runs.
  const usable = job.kind !== 'dub' && job.scenes?.length;
  const rendering = job.status === 'rendering' || job.status === 'queued';
  const on = usable && (job.status === 'review' || job.status === 'done'
                        || (rendering && ED.job?.id === job.id));
  $('#editor').classList.toggle('hidden', !on);
  if (!on) {
    state.drawn = null;
    if (ED.job) { ED.job = null; drawDock(); }
    return;
  }

  // Edits are held rather than refused while a render runs. The server rejects
  // them outright (the render is reading those very rows), so sending them
  // would only produce an error the user cannot act on; held here, they go up
  // by themselves the moment the render settles.
  ED.locked = rendering;
  $('#editor').classList.toggle('locked', rendering);
  // The open job's own metadata is kept current even when the studio is not
  // rebuilt below — status, progress and the finished video's URL all change
  // under it, and anything reading `ED.job` would otherwise still be answering
  // from before the render started. `ED.scenes` is the user's own copy and is
  // deliberately left alone.
  if (ED.job?.id === job.id) ED.job = job;

  $('#editor-title').textContent = `Sarideo · ${job.scenes.length} sahna`;
  $('#editor-note').textContent = rendering
    ? (job.status === 'queued'
        ? `Navbatda${job.queue_place ? ` ${job.queue_place}-chi` : ''} — shu yerda ishlashda davom eting.`
        : 'Render ketmoqda — ko‘rib turishingiz mumkin, o‘zgarishlar tugagach saqlanadi.')
    : job.status === 'review'
      ? 'Rasm ustidan sudrab joylashtiring. O‘zgarishlar o‘zi saqlanadi.'
      : 'Video tayyor. O‘zgartirsangiz qayta render qiling.';
  drawJobProvider(job, rendering);
  $('#render-btn').disabled = rendering;
  $('#render-btn').textContent = rendering
    ? (job.status === 'queued' ? 'Navbatda…' : 'Render ketmoqda…')
    : job.status === 'review' ? 'Render qilish' : 'Qayta render';

  // The render finished and there are edits waiting behind it.
  if (!rendering && ED.held) { ED.held = false; touch('scene'); }

  const stamp = `${job.id}:${job.updated_at}`;
  if (state.drawn === stamp) return;
  // Never redraw over work that has not reached the server yet, or over
  // someone who is mid-sentence in one of the fields. A render writes to the row
  // every few seconds, and rebuilding the studio under a playing preview each
  // time would make watching it back impossible.
  if (ED.dirty.size || ED.styleDirty || ED.saving) return;
  if (PREVIEW.chain || PREVIEW.raf) return;
  if (state.drawn?.startsWith(`${job.id}:`) && document.activeElement?.closest('.panel')) return;
  state.drawn = stamp;
  buildStudio(job);
}

/** Which provider draws this project's scenes — and moving it to another one.
 *
 *  A project keeps the provider it was started with, so turning the app over to
 *  something else strands every video already in progress: the setting changes,
 *  the running job carries on asking the old one, and nothing on screen explains
 *  the disagreement. This is where one project is brought across.
 */
function drawJobProvider(job, rendering) {
  const wrap = $('#job-provider-wrap');
  const select = $('#job-provider');
  const providers = state.health?.image_providers || {};
  const names = Object.keys(providers);
  if (!names.length) { wrap.hidden = true; return; }

  const app = state.models?.image_provider || '';
  const chosen = job.image_provider || '';
  const now = job.image_provider_now || app;
  const label = (n) => IMAGE_PROVIDER_LABELS[n] || n;

  // Rebuilt only when it would actually change: a select that is redrawn under
  // an open dropdown closes it, and this redraws on every poll.
  const signature = `${names.join()}|${chosen}|${app}`;
  if (select.dataset.sig !== signature) {
    select.dataset.sig = signature;
    select.innerHTML = [
      `<option value=""${chosen ? '' : ' selected'}>Ilovadagi — ${esc(label(app))}</option>`,
      ...names.map((n) => `<option value="${esc(n)}"${providers[n] ? '' : ' disabled'}${
        chosen === n ? ' selected' : ''}>${esc(label(n))}${
        providers[n] ? '' : ' — sozlanmagan'}</option>`),
    ].join('');
  }
  // While it runs, the server refuses the change anyway — saying so with a
  // disabled control beats an error after the click.
  select.disabled = !!rendering || ED.locked;
  select.title = `Hozir: ${label(now)}`;
  wrap.hidden = false;
}

$('#job-provider').addEventListener('change', async (e) => {
  if (!ED.job) return;
  const wanted = e.target.value;
  e.target.disabled = true;
  try {
    await api(`/api/jobs/${ED.job.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_provider: wanted }),
    });
    state.drawn = null;
    watch(ED.job.id);
    toast(wanted ? 'Rasm provayderi almashtirildi'
                 : 'Endi ilovadagi provayderdan foydalanadi');
  } catch (err) {
    editorError(err.message);
  } finally {
    e.target.disabled = false;
  }
});

function buildStudio(job) {
  const sameJob = ED.job?.id === job.id;
  ED.job = job;
  ED.scenes = job.scenes.map((s) => ({
    ...s,
    motion_strength: s.motion_strength ?? 1,
    overlays: (s.overlays || []).map((o) => ({ ...o })),
  }));
  ED.style = { ...(job.caption_style || {}) };
  ED.burn = job.burn_subtitles !== false;
  if (!sameJob) { ED.i = 0; ED.sel = null; ED.tab = 'scene'; ED.shot = 0; }
  ED.i = Math.max(0, Math.min(ED.i, ED.scenes.length - 1));
  if (!selected()) ED.sel = null;
  stopPreview();
  drawAll();
  // The editor's tools act on an open job, so the dock only comes alive here.
  drawDock();
}

function drawAll() {
  drawFilmstrip();
  drawCanvas();
  drawPanel();
}

// ── filmstrip ─────────────────────────────────────────────────────
function drawFilmstrip() {
  $('#filmstrip').innerHTML = ED.scenes.map((s, i) => {
    const stale = s.needs_image || s.needs_voice;
    const count = (s.overlays || []).length;
    return `<div class="frame${i === ED.i ? ' on' : ''}${stale ? ' stale' : ''}"
        data-scene="${i}" role="button" tabindex="0">
      ${s.image_url ? `<img src="${esc(s.image_url)}" alt="" loading="lazy" draggable="false" />` : '<i class="blank"></i>'}
      <b>${i + 1}</b>
      ${count ? `<em>${count}</em>` : ''}
      ${(s.shots || []).length > 1
        ? `<u class="cuts" title="${s.shots.length} kadr">${s.shots.length}</u>` : ''}
      ${s.sfx_id ? '<u class="cue" title="tovush effekti"></u>' : ''}
      <div class="move">
        <button data-move="-1"${i === 0 ? ' disabled' : ''} aria-label="Chapga surish">‹</button>
        <button data-move="1"${i === ED.scenes.length - 1 ? ' disabled' : ''} aria-label="O‘ngga surish">›</button>
      </div>
    </div>`;
  }).join('');

  $$('#filmstrip [data-scene]').forEach((b) => {
    const index = Number(b.dataset.scene);
    // Dragging is a mouse gesture here. On a touch screen a horizontal drag on
    // the strip is a scroll, and the browser claims it before we see the move —
    // which is what the arrows below are for.
    b.addEventListener('pointerdown', (e) => {
      if (e.pointerType === 'touch') return;
      startFrameDrag(e, index);
    });
    b.addEventListener('click', (e) => {
      if (e.target.closest('[data-move]')) return;
      if (ED.i === index) return;
      // Picking a scene while the whole video is playing is a seek, not a stop:
      // it carries on from there. Only a single-scene preview ends here.
      if (PREVIEW.chain && ED.scenes[index]?.audio_url) {
        ED.shot = 0;
        playFrom(index, { chain: true });
        return;
      }
      ED.i = index;
      ED.shot = 0;
      ED.sel = null;
      stopPreview();
      drawAll();
    });
  });

  $$('#filmstrip [data-move]').forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    const from = Number(b.closest('.frame').dataset.scene);
    moveScene(from, from + Number(b.dataset.move));
  }));
}

/** Move a scene — and everything that belongs to it — to another position. */
async function moveScene(from, to) {
  if (!ED.job || to < 0 || to >= ED.scenes.length || from === to) return;
  const order = ED.scenes.map((_, i) => i);
  order.splice(to, 0, ...order.splice(from, 1));

  // Show the new order at once; the server confirms a beat later. A scene is
  // one object — narration, voice, image, layers and timings all travel with
  // it — so nothing can end up on the wrong picture.
  ED.scenes = order.map((i) => ED.scenes[i]);
  ED.scenes.forEach((s, i) => { s.index = i; });
  ED.i = to;
  stopPreview();
  drawAll();

  try {
    await flush();
    mergeScenes(await api(`/api/jobs/${ED.job.id}/scenes/order`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order }),
    }));
    toast('Tartib o‘zgartirildi');
  } catch (e) {
    editorError(e.message);
  }
}

/** Click to select, drag past a threshold to move the scene in the running order. */
function startFrameDrag(event, index) {
  const strip = $('#filmstrip');
  const frames = $$('.frame', strip);
  const origin = event.clientX;
  let moved = false;
  let target = index;

  const move = (e) => {
    if (!moved && Math.abs(e.clientX - origin) < 8) return;
    if (!moved) {
      moved = true;
      frames[index].classList.add('dragging');
      strip.classList.add('sorting');
    }
    // Land on whichever frame the pointer is over; its own placeholder counts,
    // so dragging back to the start position is a no-op rather than a swap.
    const over = frames.findIndex((f) => {
      const r = f.getBoundingClientRect();
      return e.clientX >= r.left && e.clientX <= r.right;
    });
    if (over >= 0 && over !== target) {
      target = over;
      frames.forEach((f, i) => f.classList.toggle('drop', i === target && target !== index));
    }
  };

  const up = async () => {
    removeEventListener('pointermove', move);
    removeEventListener('pointerup', up);
    strip.classList.remove('sorting');
    frames.forEach((f) => f.classList.remove('dragging', 'drop'));
    // A click that never turned into a drag is handled by the click listener.
    if (moved && target !== index) await moveScene(index, target);
  };

  addEventListener('pointermove', move);
  addEventListener('pointerup', up);
}

/** Take the server's scene list, keeping the local editing state coherent. */
function mergeScenes(scenes) {
  ED.scenes = scenes.map((s) => ({
    ...s,
    motion_strength: s.motion_strength ?? 1,
    overlays: (s.overlays || []).map((o) => ({ ...o })),
  }));
  ED.i = Math.max(0, Math.min(ED.i, ED.scenes.length - 1));
  if (!selected()) ED.sel = null;
  state.drawn = null;
  drawAll();
}

$('#add-scene').addEventListener('click', async () => {
  if (!ED.job) return;
  const answer = await ask({
    title: 'Yangi sahna',
    ok: 'Qo‘shish',
    html: `<label class="f"><span>Matn — shu sahnada nima aytiladi</span>
      <textarea name="narration" rows="4" placeholder="Bir necha jumla yozing…"></textarea></label>
      <label class="f"><span>Qayerga</span>
        <select name="after">
          <option value="-1">Eng boshiga</option>
          ${ED.scenes.map((s, i) =>
            `<option value="${i}"${i === ED.i ? ' selected' : ''}>${i + 1}-sahnadan keyin</option>`).join('')}
        </select></label>
      <small class="note">AI unga rasm prompti yozadi, ovoz beradi va rasm yaratadi.</small>`,
  });
  if (!answer?.narration?.trim()) return;
  try {
    await flush();
    await api(`/api/jobs/${ED.job.id}/scenes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ after: Number(answer.after), narration: answer.narration.trim() }),
    });
    state.drawn = null;
    watch(ED.job.id);
  } catch (e) {
    editorError(e.message);
  }
});

// ── a folder of pictures at once ──────────────────────────────────
// The other half of making the images yourself. The prompts leave in scene
// order; the files come back named after whatever drew them, in whatever order
// the browser downloaded them. Rather than making the user rename a hundred
// files, the studio looks at them and works out where each one goes.

$('#bulk-file').addEventListener('change', async (e) => {
  const files = [...e.target.files];
  e.target.value = '';
  if (!ED.job) return;
  await bulkUpload(files, ED.job.id,
    ED.scenes.filter((s) => s.needs_image || !s.image_url).length);
});

// ── scene preview ─────────────────────────────────────────────────
// Playing the scene's own voice-over and driving the canvas from its clock is
// the only way to check that a layer lands on the word it is meant to land on.
// `chain` is what makes this a player rather than a scene auditioner: with it
// set, the end of one scene moves to the next and keeps going. `spare` is the
// element the *next* scene is loaded into while the current one is still
// playing, which is the whole trick — asking one element to change src at the
// moment of the handover is what puts a gap between every scene.
const PREVIEW = { audio: null, spare: null, raf: 0, index: -1, chain: false, ready: '' };

function stopPreview() {
  PREVIEW.audio?.pause();
  cancelAnimationFrame(PREVIEW.raf);
  PREVIEW.raf = 0;
  PREVIEW.index = -1;
  PREVIEW.chain = false;
  PREVIEW.ready = '';
  $('#play-icon').innerHTML = '<path d="M8 5.5l11 6.5-11 6.5z"/>';
  $('#play-btn span').textContent = 'Eshitish';
  $('#playall-icon').innerHTML = '<path d="M4 5.5l9 6.5-9 6.5z"/><path d="M17 5v14"/>';
  $('#playall-btn span').textContent = 'Hammasi';
  $('#playall-btn').classList.remove('on');
  $('#scrub-fill').style.width = '0%';
  if (ED.job) { drawLayers(); drawCaptionSample(); }
}

/** Load the scene after `i` into the spare element, so the handover is instant. */
function preload(i) {
  const next = ED.scenes[i + 1];
  if (!next?.audio_url) { PREVIEW.ready = ''; return; }
  PREVIEW.spare = PREVIEW.spare || new Audio();
  if (PREVIEW.spare.src !== next.audio_url) {
    PREVIEW.spare.src = next.audio_url;
    PREVIEW.spare.load();
  }
  PREVIEW.ready = next.audio_url;
}

/** The first scene at or after `from` that has a recording. */
function nextVoiced(from) {
  for (let i = from; i < ED.scenes.length; i += 1) {
    if (ED.scenes[i]?.audio_url) return i;
  }
  return -1;
}

function playFrom(i, { chain }) {
  const s = ED.scenes[i];
  if (!s?.audio_url) { stopPreview(); return; }

  ED.i = i;
  ED.sel = null;
  PREVIEW.index = i;
  PREVIEW.chain = chain;
  // Redrawing the whole studio between every scene would rebuild the panel and
  // steal focus mid-playback; the canvas and the strip are all that change.
  drawCanvas();
  drawFilmstrip();
  // A fifty-scene strip scrolls, so the frame being played has to be brought to
  // where it can be seen — otherwise the picture moves and the strip does not.
  $(`#filmstrip [data-scene="${i}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });

  PREVIEW.audio = PREVIEW.audio || new Audio();
  if (PREVIEW.audio.src !== s.audio_url) PREVIEW.audio.src = s.audio_url;
  PREVIEW.audio.currentTime = 0;
  if (chain) preload(i);

  PREVIEW.audio.play().then(() => {
    const step = () => {
      // Someone clicked another scene while it was playing. In chain mode that
      // is a seek, not a stop — carry on from wherever they went.
      if (PREVIEW.index !== ED.i) {
        if (PREVIEW.chain && ED.scenes[ED.i]?.audio_url) { playFrom(ED.i, { chain: true }); return; }
        stopPreview();
        return;
      }
      const t = PREVIEW.audio.currentTime;
      const span = Math.max(0.1, s.duration || PREVIEW.audio.duration || 1);
      $('#scrub-fill').style.width = `${Math.min(100, (t / span) * 100)}%`;
      drawLayers(t);
      drawCaptionSample(t);
      if (PREVIEW.audio.ended) {
        if (!PREVIEW.chain) { stopPreview(); return; }
        const following = nextVoiced(i + 1);
        if (following === -1) { stopPreview(); toast('Video tugadi'); return; }
        // Swap in the element that has already buffered, and hand the drained
        // one back to be the next spare.
        if (PREVIEW.ready === ED.scenes[following].audio_url) {
          const drained = PREVIEW.audio;
          PREVIEW.audio = PREVIEW.spare;
          PREVIEW.spare = drained;
          PREVIEW.ready = '';
        }
        playFrom(following, { chain: true });
        return;
      }
      PREVIEW.raf = requestAnimationFrame(step);
    };
    PREVIEW.raf = requestAnimationFrame(step);
  }).catch(() => {
    stopPreview();
    toast('Brauzer ovozni to‘sdi — kadrga bosing');
  });
}

function togglePreview() {
  const s = scene();
  if (!s?.audio_url) { toast('Bu sahnada hali ovoz yo‘q'); return; }
  if (PREVIEW.raf && PREVIEW.index === ED.i && !PREVIEW.chain) { stopPreview(); return; }
  stopPreview();
  $('#play-icon').innerHTML = '<path d="M8 5h3v14H8zM13 5h3v14h-3z"/>';
  $('#play-btn span').textContent = 'To‘xtatish';
  playFrom(ED.i, { chain: false });
}

function togglePlayAll() {
  if (PREVIEW.chain) { stopPreview(); return; }
  const start = nextVoiced(ED.i) === -1 ? nextVoiced(0) : nextVoiced(ED.i);
  if (start === -1) { toast('Hali hech qaysi sahnada ovoz yo‘q'); return; }
  stopPreview();
  $('#playall-icon').innerHTML = '<path d="M8 5h3v14H8zM13 5h3v14h-3z"/>';
  $('#playall-btn span').textContent = 'To‘xtatish';
  $('#playall-btn').classList.add('on');
  playFrom(start, { chain: true });
}

$('#play-btn').addEventListener('click', togglePreview);
$('#playall-btn').addEventListener('click', togglePlayAll);

// Space is what every player in the world uses, and reviewing a long video is
// exactly when reaching for a small button gets tiring. Never while typing.
addEventListener('keydown', (e) => {
  if (e.code !== 'Space' || !ED.job || $('#editor').classList.contains('hidden')) return;
  if (e.target.closest('input, textarea, select, [contenteditable]')) return;
  e.preventDefault();
  togglePlayAll();
});

/** How a layer looks `t` seconds into the scene, per its own animation. */
function layerAt(l, t, duration) {
  if (t === null) return { show: true, opacity: l.opacity ?? 1, dx: 0, dy: 0, scale: 1 };
  const start = l.start || 0;
  const end = l.end || duration;
  if (t < start || t > end) return { show: false };

  const into = t - start;
  const left = end - t;
  const ramp = Math.min(0.35, (end - start) / 3);
  const fade = Math.min(1, into / ramp) * Math.min(1, left / ramp);
  const p = Math.min(1, into / 0.42);
  const eased = p * p * (3 - 2 * p);

  const out = { show: true, opacity: (l.opacity ?? 1) * fade, dx: 0, dy: 0, fx: 0, fy: 0, scale: 1 };
  if (l.anim === 'none') { out.opacity = l.opacity ?? 1; return out; }
  if (ACTOR_MOVES[l.anim]) {
    // A travelling actor arrives from off-screen, so it must not also fade in
    // at the edge of the frame — the renderer makes the same exception.
    if (TRAVELLING.has(l.anim)) out.opacity = l.opacity ?? 1;
    const move = ACTOR_MOVES[l.anim];
    const span = Math.max(0.3, end - start);
    const p = Math.min(1, Math.max(0, into / span));
    if (move.from !== move.to) out.fx = move.from + (move.to - move.from) * (p * p * (3 - 2 * p));
    if (l.anim === 'hop') {
      out.fx = 0;
      out.fy = -0.05 * Math.abs(Math.sin((2 * Math.PI * into) / Math.max(0.9, span / 2)));
    } else if (l.anim === 'sway') {
      out.fx = 0.006 * Math.sin((2 * Math.PI * into) / 2.4);
    } else if (WALKING.has(l.anim)) {
      out.fy = -0.008 * Math.abs(Math.sin((2 * Math.PI * into) / 0.65));
    }
    return out;
  }
  if (l.anim === 'pop') out.scale = 0.72 + 0.28 * eased;
  else if (l.anim === 'rise') out.dy = (1 - eased) * 5;
  else if (l.anim === 'slide_left') out.dx = (1 - eased) * 8;
  else if (l.anim === 'slide_right') out.dx = -(1 - eased) * 8;
  else if (l.anim === 'float') out.dy = 1.4 * Math.sin((2 * Math.PI * into) / 3.2);
  else if (l.anim === 'drift') out.dx = (into / Math.max(0.5, end - start)) * 5;
  return out;
}

// ── canvas ────────────────────────────────────────────────────────
function drawCanvas() {
  const s = scene();
  const f = fmt();
  const canvas = $('#canvas');
  canvas.style.setProperty('--ar', (f.width / f.height).toFixed(4));
  $('#canvas-empty').classList.toggle('hidden', !!s);

  const img = $('#canvas-img');
  if (s?.image_url) { img.src = s.image_url; img.classList.remove('hidden'); }
  else { img.removeAttribute('src'); img.classList.add('hidden'); }

  $('#canvas-time').textContent = s
    ? `${clock(s.start)} · ${s.duration.toFixed(1)}s` : '';

  drawLayers();
  drawCaptionSample();
}

function layerBoxStyle(l, px) {
  if (!l.box) return '';
  const bg = hexToRgba(l.box_colour, l.box_opacity);
  return `background:${bg};padding:${(px * 0.22).toFixed(1)}px ${(px * 0.34).toFixed(1)}px;`;
}

function hexToRgba(hex, alpha) {
  const h = String(hex || '#000000').replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const n = parseInt(full.slice(0, 6) || '000000', 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${Number(alpha ?? 1)})`;
}

function drawLayers(time = null) {
  const host = $('#ov-layer');
  const s = scene();
  if (!s) { host.innerHTML = ''; return; }
  const h = $('#canvas').clientHeight || 1;
  const w = $('#canvas').clientWidth || 1;
  const playing = time !== null;
  host.classList.toggle('playing', playing);

  host.innerHTML = (s.overlays || []).map((l) => {
    const a = layerAt(l, time, s.duration || 0);
    if (!a.show) return '';
    // `fx`/`fy` are fractions of the frame, exactly as the renderer measures
    // them, so they belong in left/top rather than in the element-relative
    // translate the older animations use.
    const pos = `left:${((l.x + (a.fx || 0)) * 100).toFixed(2)}%;` +
      `top:${((l.y + (a.fy || 0)) * 100).toFixed(2)}%;` +
      `transform:translate(calc(-50% + ${a.dx}%),calc(-50% + ${a.dy}%)) ` +
      `rotate(${l.rotate || 0}deg) scale(${a.scale.toFixed(3)});opacity:${a.opacity.toFixed(3)};`;
    const on = !playing && l.id === ED.sel ? ' on' : '';
    if (l.type === 'image') {
      return `<div class="ov img${on}" data-id="${esc(l.id)}" style="${pos}width:${(l.size * 100).toFixed(2)}%">
        <img src="/api/assets/${esc(l.asset_id)}/image" alt="" draggable="false" />
        <i class="grab" data-grip="${esc(l.id)}"></i></div>`;
    }
    const px = l.size * h;
    const stroke = Math.max(0.6, px * 0.075);
    const text = `font-size:${px.toFixed(1)}px;color:${esc(l.colour)};` +
      `font-weight:${l.bold ? 800 : 500};font-style:${l.italic ? 'italic' : 'normal'};` +
      (l.box ? '' : `-webkit-text-stroke:${stroke.toFixed(2)}px ${esc(l.outline_colour)};`) +
      layerBoxStyle(l, px);
    return `<div class="ov txt${on}" data-id="${esc(l.id)}" style="${pos}max-width:${(w * 0.94).toFixed(0)}px">
      <span style="${text}">${esc(l.text)}</span>
      <i class="grab" data-grip="${esc(l.id)}"></i></div>`;
  }).join('');

  // While the scene is playing the layers are a picture, not a control surface.
  if (playing) return;
  $$('#ov-layer .ov').forEach((el) => {
    el.addEventListener('pointerdown', (e) => startDrag(e, el.dataset.id, 'move'));
    const grip = $('.grab', el);
    grip?.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      startDrag(e, el.dataset.id, 'size');
    });
  });
}

function startDrag(event, id, mode) {
  const l = (scene()?.overlays || []).find((x) => x.id === id);
  if (!l) return;
  event.preventDefault();
  if (ED.sel !== id) { ED.sel = id; drawLayers(); drawPanel(); }

  const canvas = $('#canvas');
  const rect = canvas.getBoundingClientRect();
  const from = { x: event.clientX, y: event.clientY, lx: l.x, ly: l.y, size: l.size };
  const target = event.currentTarget;
  target.setPointerCapture?.(event.pointerId);

  const move = (e) => {
    const dx = (e.clientX - from.x) / rect.width;
    const dy = (e.clientY - from.y) / rect.height;
    if (mode === 'move') {
      l.x = Math.max(0, Math.min(1, from.lx + dx));
      l.y = Math.max(0, Math.min(1, from.ly + dy));
    } else {
      // A text layer's size is a font height and an image's is a frame width,
      // so the same drag has to mean different amounts to each.
      const step = l.type === 'image' ? dx * 1.6 : dx * 0.5;
      l.size = Math.max(0.02, Math.min(l.type === 'image' ? 1 : 0.4, from.size + step));
    }
    drawLayers();
  };
  const up = () => {
    removeEventListener('pointermove', move);
    removeEventListener('pointerup', up);
    round(l);
    touch();
    drawPanel();
  };
  addEventListener('pointermove', move);
  addEventListener('pointerup', up);
}

const round = (l) => {
  l.x = Math.round(l.x * 1000) / 1000;
  l.y = Math.round(l.y * 1000) / 1000;
  l.size = Math.round(l.size * 1000) / 1000;
};

// The caption preview mirrors what libass will draw: same budget, same
// multiplier, same margins — scaled down by however wide the canvas happens
// to be on this screen.
/** Break a scene's timed words into caption lines the way the renderer does. */
function captionLines(s, budget) {
  const words = s.words || [];
  if (!words.length) return [];
  const lines = [];
  let current = [];
  const flush = () => { if (current.length) { lines.push(current); current = []; } };
  for (const word of words) {
    const joined = [...current.map((w) => w.text), word.text].join(' ');
    if (current.length && (joined.length > budget.max_chars || current.length >= budget.max_words)) flush();
    current.push(word);
  }
  flush();
  return lines.map((ws) => ({
    start: Number(ws[0].start) || 0,
    end: Number(ws[ws.length - 1].end) || 0,
    words: ws,
  }));
}

function drawCaptionSample(time = null) {
  const el = $('#cap-sample');
  const span = $('span', el);
  const s = scene();
  const st = ED.style || {};
  if (!s || !ED.burn) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');

  const f = fmt();
  const cw = $('#canvas').clientWidth || 1;
  const ch = $('#canvas').clientHeight || 1;
  const scale = cw / f.width;
  // Korean and its neighbours are written in square characters, so a line that
  // fits by English character count runs off the frame. The server works both
  // budgets out; here we only pick the one that matches the video's language, so
  // the preview breaks its line exactly where the render will.
  const dense = (state.health?.dense_scripts || []).includes(ED.job?.language);
  const budget = (dense ? f.caption_dense : f.caption)
    || { max_chars: 42, max_words: 7 };
  const px = (budget.font_size || f.caption?.font_size || 96)
    * (st.size ?? 1) * scale;

  // Playing: the line that is actually being spoken, word by word. Idle: as much
  // of the narration as one line can hold, so the look can be judged at rest.
  let parts = null;
  let sample = '';
  if (time !== null) {
    const lines = captionLines(s, budget);
    const line = lines.find((l) => time >= l.start - 0.15 && time <= l.end + 0.25);
    if (!line) { el.classList.add('hidden'); return; }
    parts = line.words.map((w) => ({ text: w.text, spoken: time >= Number(w.start) }));
  } else {
    const words = String(s.narration || 'Namuna matn').split(/\s+/);
    for (const word of words) {
      if ((sample + ' ' + word).trim().length > (budget.max_chars || 42)) break;
      sample = (sample + ' ' + word).trim();
    }
  }

  const landscape = f.width >= f.height;
  const marginV = ch * Math.max(0.02, Math.min(0.6,
    (landscape ? 0.09 : 0.20) + (st.margin ?? 0)));

  el.style.alignItems = st.position === 'top' ? 'flex-start'
    : st.position === 'middle' ? 'center' : 'flex-end';
  el.style.padding = st.position === 'middle' ? '0'
    : st.position === 'top' ? `${marginV}px 6% 0` : `0 6% ${marginV}px`;

  const boxed = st.box === 'box';
  span.style.cssText = [
    `font-size:${px.toFixed(1)}px`,
    `color:${st.karaoke ? st.highlight : st.colour}`,
    `font-weight:${st.bold ? 800 : 500}`,
    `font-style:${st.italic ? 'italic' : 'normal'}`,
    `text-transform:${st.uppercase ? 'uppercase' : 'none'}`,
    boxed
      ? `background:${hexToRgba(st.box_colour, st.box_opacity)};padding:${(px * 0.14).toFixed(1)}px ${(px * 0.26).toFixed(1)}px`
      : `-webkit-text-stroke:${((st.outline ?? 4) * scale * 2).toFixed(2)}px ${st.outline_colour}`,
    st.box === 'shadow' || st.box === 'outline'
      ? `text-shadow:0 ${((st.shadow ?? 1.6) * scale * 2).toFixed(1)}px ${((st.shadow ?? 1.6) * scale * 3).toFixed(1)}px rgba(0,0,0,.75)`
      : '',
  ].filter(Boolean).join(';');

  if (!parts) {
    span.textContent = sample || 'Namuna matn';
    return;
  }
  // A karaoke style lights each word as it is spoken; every other style just
  // shows the whole line, so the words are all painted the same.
  span.innerHTML = parts.map((w) => {
    const colour = !st.karaoke ? st.colour : (w.spoken ? st.highlight : st.colour);
    return `<i style="font-style:inherit;color:${esc(colour)}">${esc(w.text)}</i>`;
  }).join(' ');
}

new ResizeObserver(() => { if (ED.job) { drawLayers(); drawCaptionSample(); } })
  .observe($('#canvas'));

// ── panel ─────────────────────────────────────────────────────────
$$('#panel-tabs button').forEach((b) => b.addEventListener('click', () => {
  ED.tab = b.dataset.tab;
  drawPanel();
  drawDock();
}));

function drawPanel() {
  $$('#panel-tabs button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.tab === ED.tab));
  ['scene', 'layers', 'caption'].forEach((t) =>
    $(`#tab-${t}`).classList.toggle('hidden', t !== ED.tab));
  if (ED.tab === 'scene') drawScenePanel();
  else if (ED.tab === 'layers') drawLayersPanel();
  else drawCaptionPanel();
}

/** Wire every `[data-k]` control in `root` onto `target`, redrawing after. */
function wire(root, target, after) {
  $$('[data-k]', root).forEach((el) => {
    const key = el.dataset.k;
    const read = () => {
      if (el.type === 'checkbox') return el.checked;
      if (el.type === 'range' || el.type === 'number') return Number(el.value);
      return el.value;
    };
    el.addEventListener('input', () => {
      target[key] = read();
      after(key, el);
    });
  });
}

function slider(key, label, min, max, step, value, suffix = '', attr = 'data-k') {
  return `<label class="f rng"><span>${esc(label)}<b>${Number(value).toFixed(2).replace(/\.?0+$/, '')}${suffix}</b></span>
    <input type="range" ${attr}="${key}" min="${min}" max="${max}" step="${step}" value="${value}" /></label>`;
}

function swatches(key, value) {
  return `<div class="sw-row" data-swatch="${key}">
    ${SWATCHES.map((c) => `<button type="button" style="background:${c}" data-c="${c}"
      class="${c.toLowerCase() === String(value).toLowerCase() ? 'on' : ''}" aria-label="${c}"></button>`).join('')}
    <input type="color" data-k="${key}" value="${esc(value || '#ffffff')}" />
  </div>`;
}

function wireSwatches(root, target, after) {
  $$('[data-swatch]', root).forEach((row) => {
    const key = row.dataset.swatch;
    $$('button', row).forEach((b) => b.addEventListener('click', () => {
      target[key] = b.dataset.c;
      after(key, b);
    }));
  });
}

// ── panel: scene ──────────────────────────────────────────────────
// ── shots ─────────────────────────────────────────────────────────
// One line of narration can be covered by up to four pictures. An unsplit scene
// shows exactly what it always did — a prompt and a camera move — and only grows
// the strip once there is more than one picture to arrange.

const MAX_SHOTS = 4;

function shotList(s) {
  // The single implicit shot is built here rather than on the server, so the
  // editor can talk about "shot 1" before the scene has ever been split.
  if (s.shots?.length) return s.shots;
  return [{
    sid: '', prompt: s.image_prompt || '', motion: s.motion || 'zoom_in',
    motion_strength: s.motion_strength ?? 1, transition: '', weight: 1,
    seconds: s.duration || 0, image_url: s.image_url, needs_image: s.needs_image,
  }];
}

function shotBlock(s, motions, transitions) {
  const list = shotList(s);
  const split = list.length > 1;
  const i = Math.min(ED.shot || 0, list.length - 1);
  const shot = list[i];

  // Worked out here rather than read back from the server, so dragging a share
  // moves the numbers as you drag instead of on the next save.
  const share = list.reduce((sum, sh) => sum + (Number(sh.weight) || 1), 0);
  const seconds = (sh) => (s.duration || 0) * (Number(sh.weight) || 1) / (share || 1);

  const tabs = list.map((sh, j) => `
    <button type="button" class="shot-tab${j === i ? ' on' : ''}" data-shot="${j}">
      ${sh.image_url ? `<img src="${esc(sh.image_url)}" alt="" loading="lazy" />`
        : '<i class="shot-blank"></i>'}
      <b>${j + 1}</b>
      ${split ? `<em>${seconds(sh).toFixed(1)}s</em>` : ''}
    </button>`).join('');

  return `
    <div class="f shots">
      <span>Kadrlar — bitta matn ostida ${split ? `${list.length} ta rasm` : 'bitta rasm'}</span>
      <div class="shot-strip">
        ${tabs}
        ${list.length < MAX_SHOTS
          ? '<button type="button" class="shot-add" data-shot-add>+</button>' : ''}
      </div>
      ${split ? `<div class="shot-bar">${list.map((sh, j) => `
        <i class="${j === i ? 'on' : ''}" style="flex:${sh.weight || 1}"></i>`).join('')}</div>` : ''}
    </div>

    <label class="f"><span>${split ? `${i + 1}-kadr prompti` : 'Rasm prompti'}</span>
      <textarea data-sk="prompt" rows="3">${esc(shot.prompt || '')}</textarea></label>

    <div class="f2">
      <label class="f"><span>Kamera harakati</span>
        <select data-sk="motion">${motions.map((m) =>
          `<option value="${esc(m)}"${m === shot.motion ? ' selected' : ''}>${esc(MOTIONS[m] || m)}</option>`).join('')}</select></label>
      ${split ? `<label class="f"><span>Bu kadr qanday kiradi</span>
        <select data-sk="transition">
          <option value=""${shot.transition ? '' : ' selected'}>tez kesish</option>
          ${transitions.map((t) =>
            `<option value="${esc(t)}"${t === shot.transition ? ' selected' : ''}>${esc(TRANSITIONS[t] || t)}</option>`).join('')}
        </select></label>` : ''}
    </div>
    ${slider('motion_strength', 'Harakat kuchi', 0.3, 1.8, 0.05,
             shot.motion_strength ?? 1, '×', 'data-sk')}
    ${split ? `
      ${slider('weight', 'Ekranda turish ulushi', 0.25, 4, 0.25, shot.weight ?? 1, '×', 'data-sk')}
      <div class="sc-acts tight">
        <button class="btn ghost" data-shot-move="-1"${i === 0 ? ' disabled' : ''}>‹ chapga</button>
        <button class="btn ghost" data-shot-move="1"${i === list.length - 1 ? ' disabled' : ''}>o‘ngga ›</button>
        <button class="btn ghost" data-shot-drop>Kadrni o‘chirish</button>
      </div>` : ''}`;
}

function writeShots(s, list) {
  // One shot is not a split scene: send an empty list and the server folds it
  // back into a plain scene, so the editor never shows a lone tab.
  s.shots = list.length > 1 ? list : [];
  if (list.length === 1) {
    s.image_prompt = list[0].prompt;
    s.motion = list[0].motion;
    s.motion_strength = list[0].motion_strength;
  }
  ED.shot = Math.min(ED.shot || 0, Math.max(0, list.length - 1));
  drawScenePanel();
  drawFilmstrip();
  touch();
}

function wireShots(host, s) {
  $$('[data-shot]', host).forEach((b) => b.addEventListener('click', () => {
    ED.shot = Number(b.dataset.shot);
    drawScenePanel();
  }));

  $$('[data-sk]', host).forEach((el) => el.addEventListener('input', () => {
    const list = shotList(s).map((x) => ({ ...x }));
    const i = Math.min(ED.shot || 0, list.length - 1);
    const key = el.dataset.sk;
    list[i][key] = el.type === 'range' || key === 'weight' ? Number(el.value) : el.value;
    // A prompt edit changes what gets drawn, so redraw the strip to show the
    // picture is now stale; the rest can update in place without a rebuild.
    s.shots = list.length > 1 ? list : [];
    if (list.length === 1) {
      s.image_prompt = list[0].prompt;
      s.motion = list[0].motion;
      s.motion_strength = list[0].motion_strength;
    }
    if (key === 'weight') drawScenePanel();
    touch();
  }));

  $('[data-shot-add]', host)?.addEventListener('click', () => {
    const list = shotList(s).map((x) => ({ ...x }));
    if (list.length >= MAX_SHOTS) return;
    // A new shot inherits nothing but the scene's subject — the server fills in
    // a framing so it is a different angle rather than the same picture again.
    list.push({ sid: '', prompt: '', motion: 'zoom_out', motion_strength: 1,
                transition: '', weight: 1, seconds: 0, needs_image: true });
    ED.shot = list.length - 1;
    writeShots(s, list);
    toast('Kadr qo‘shildi — render qilganda rasmi chiziladi');
  });

  $('[data-shot-drop]', host)?.addEventListener('click', () => {
    const list = shotList(s).map((x) => ({ ...x }));
    if (list.length < 2) return;
    list.splice(Math.min(ED.shot || 0, list.length - 1), 1);
    ED.shot = Math.max(0, (ED.shot || 0) - 1);
    writeShots(s, list);
  });

  $$('[data-shot-move]', host).forEach((b) => b.addEventListener('click', () => {
    const list = shotList(s).map((x) => ({ ...x }));
    const from = Math.min(ED.shot || 0, list.length - 1);
    const to = from + Number(b.dataset.shotMove);
    if (to < 0 || to >= list.length) return;
    list.splice(to, 0, list.splice(from, 1)[0]);
    ED.shot = to;
    writeShots(s, list);
  }));
}

/** Who says this scene's line: the narrator, or a character with its own voice.
 *
 * Only characters that have been given a voice are offered. A character with no
 * voice would be read by the narrator anyway, so listing it would be a choice
 * that changes nothing — and the picker is silent entirely until at least one
 * character can speak, because until then there is nothing to choose between.
 */
function speakerPicker(s) {
  const talkers = (state.heroes || []).filter((h) => h.voice_id);
  if (!talkers.length || ED.job.uses_uploaded_audio) return '';
  return `<label class="f"><span>Kim gapiradi</span>
    <select data-k="speaker">
      <option value="">Diktor</option>
      ${talkers.map((h) => `<option value="${esc(h.id)}"${
        h.id === (s.speaker || '') ? ' selected' : ''}>${esc(h.name)}</option>`).join('')}
    </select>
    <small>Ovoz berilgan qahramonlar shu yerda chiqadi — Kutubxona → Herolar.</small></label>`;
}

// The browser writes whichever of these it can; the server accepts all of them,
// and ffmpeg reads all of them. Ordered by how widely they are supported.
const TAKE_TYPES = ['audio/webm', 'audio/ogg', 'audio/mp4'];
const TAKE_EXT = { 'audio/webm': '.webm', 'audio/ogg': '.ogg', 'audio/mp4': '.m4a' };

/** Record a scene's line yourself.
 *
 * Some lines are not readable by a synthesizer at all — an animal, a shout, a
 * voice you are doing. The take replaces that scene's audio and the timeline is
 * re-measured from it, so the picture and the captions follow what you actually
 * said rather than what was written.
 */
async function recordScene(s) {
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    toast('Bu brauzer mikrofonni qo‘llab-quvvatlamaydi');
    return;
  }

  let stream = null;
  let recorder = null;
  let chunks = [];
  let started = 0;
  let take = null;
  let timer = null;

  const stop = () => {
    if (recorder && recorder.state !== 'inactive') recorder.stop();
    stream?.getTracks().forEach((t) => t.stop());
    stream = null;
    clearInterval(timer);
  };

  const answer = await ask({
    title: `${s.index + 1}-sahna — o‘z ovozingiz`,
    ok: 'Ishlatish',
    html: `
      <p class="hint">${esc(s.narration)}</p>
      <div class="rec">
        <button type="button" class="rec-btn" id="rec-go">Yozishni boshlash</button>
        <span class="rec-time" id="rec-time">0.0s</span>
      </div>
      <audio id="rec-play" controls class="hidden"></audio>
      <p class="voice-hint" id="rec-hint">Mikrofonga ruxsat so‘raladi. Yozib bo‘lgach
        eshitib ko‘ring — yoqmasa qaytadan yozing.</p>`,
    onOpen: () => {
      $('#rec-go').addEventListener('click', async () => {
        if (recorder && recorder.state === 'recording') {
          stop();
          $('#rec-go').textContent = 'Qaytadan yozish';
          $('#rec-go').classList.remove('live');
          return;
        }
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (e) {
          $('#rec-hint').className = 'voice-hint bad';
          $('#rec-hint').textContent = 'Mikrofonga ruxsat berilmadi.';
          return;
        }
        const type = TAKE_TYPES.find((t) => MediaRecorder.isTypeSupported(t)) || '';
        recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
        chunks = [];
        recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
        recorder.onstop = () => {
          take = new Blob(chunks, { type: recorder.mimeType || type || 'audio/webm' });
          const player = $('#rec-play');
          player.src = URL.createObjectURL(take);
          player.classList.remove('hidden');
          $('#rec-hint').className = 'voice-hint';
          $('#rec-hint').textContent =
            `${(take.size / 1024).toFixed(0)} KB yozildi — «Ishlatish» bosing.`;
        };
        recorder.start();
        started = Date.now();
        $('#rec-go').textContent = 'To‘xtatish';
        $('#rec-go').classList.add('live');
        clearInterval(timer);
        timer = setInterval(() => {
          $('#rec-time').textContent = `${((Date.now() - started) / 1000).toFixed(1)}s`;
        }, 100);
      });
    },
  });

  stop();
  if (!answer) return;
  if (!take || !take.size) { toast('Hech narsa yozilmadi'); return; }

  const ext = TAKE_EXT[(take.type || '').split(';')[0]] || '.webm';
  const body = new FormData();
  body.append('audio', new File([take], `take${ext}`, { type: take.type }));
  try {
    const updated = await api(
      `/api/jobs/${ED.job.id}/scenes/${s.index}/voice`, { method: 'POST', body });
    // The take's length is almost never the synthesizer's, so every scene after
    // this one has moved. Reload rather than patch the one scene.
    Object.assign(s, updated);
    state.drawn = null;
    await tick();
    toast('Ovoz almashtirildi — vaqtlar qayta hisoblandi');
  } catch (err) {
    editorError(err.message);
  }
}

function drawScenePanel() {
  const s = scene();
  const host = $('#tab-scene');
  if (!s) { host.innerHTML = '<p class="empty">Sahna yo‘q.</p>'; return; }

  const motions = state.motions.length ? state.motions : Object.keys(MOTIONS);
  const transitions = state.transitions.length ? state.transitions : Object.keys(TRANSITIONS);
  const heroes = state.heroes;

  host.innerHTML = `
    <label class="f"><span>Matn — ovoz va subtitr shundan chiqadi</span>
      <textarea data-k="narration" rows="3">${esc(s.narration)}</textarea></label>

    ${shotBlock(s, motions, transitions)}

    <div class="f2">
      <label class="f"><span>O‘tish effekti — oldingi sahnadan</span>
        <select data-k="transition"><option value="">avtomatik</option>
          ${transitions.map((t) =>
            `<option value="${esc(t)}"${t === s.transition ? ' selected' : ''}>${esc(TRANSITIONS[t] || t)}</option>`).join('')}
        </select></label>
    </div>

    <label class="f"><span>Ekran yozuvi — sahna boshida chiqadi</span>
      <input data-k="on_screen_text" value="${esc(s.on_screen_text || '')}" placeholder="ixtiyoriy" /></label>

    ${speakerPicker(s)}

    ${heroes.length ? `<div class="f"><span>Bu sahnadagi herolar</span>
      <div class="cast-strip small" id="scene-heroes">${heroes.map((h) => `
        <label class="hero-chip${(s.hero_ids || []).includes(h.id) ? ' on' : ''}" title="${esc(h.name)}">
          <input type="checkbox" value="${esc(h.id)}"${(s.hero_ids || []).includes(h.id) ? ' checked' : ''} />
          <img src="${esc(h.url)}" alt="${esc(h.name)}" /></label>`).join('')}</div></div>` : ''}

    <label class="f"><span>Tovush effekti — sahna boshida bir marta yangraydi</span>
      <select data-k="sfx_id"><option value="">— yo‘q —</option>
        ${(state.sfx || []).map((x) =>
          `<option value="${esc(x.id)}"${x.id === s.sfx_id ? ' selected' : ''}>${esc(x.name)}</option>`).join('')}
      </select>
      ${state.sfx?.length ? '' : '<small>Kutubxonaga «Tovush effekti» sifatida audio yuklang.</small>'}</label>
    ${s.sfx_id ? `<div class="f2">
      ${slider('sfx_volume', 'Balandligi', 0.1, 3, 0.1, s.sfx_volume ?? 1, '×')}
      ${slider('sfx_offset', 'Kechikish', 0, Math.max(0.5, s.duration - 0.2), 0.1, s.sfx_offset ?? 0, 's')}
    </div>` : ''}

    <div class="sc-acts">
      <button class="btn" data-a="image">Rasmni qayta yaratish</button>
      ${ED.job.uses_uploaded_audio ? '' : '<button class="btn" data-a="voice">Ovozni qayta yozish</button>'}
      ${ED.job.uses_uploaded_audio ? '' : '<button class="btn" data-a="record">🎙 O‘zim aytaman</button>'}
      <label class="btn" data-a="upload">O‘z rasmim<input type="file" accept="image/*" hidden /></label>
      ${ED.scenes.length > 1 ? '<button class="btn ghost" data-a="drop-scene">Sahnani o‘chirish</button>' : ''}
    </div>`;

  wire(host, s, (key) => {
    if (['motion_strength', 'sfx_id', 'sfx_volume', 'sfx_offset'].includes(key)) drawScenePanel();
    if (key === 'sfx_id') drawFilmstrip();
    // A different speaker means a different recording, and the filmstrip shows
    // which scenes are waiting for one.
    if (key === 'speaker') { s.needs_voice = true; drawFilmstrip(); }
    touch();
  });
  wireShots(host, s);

  $('[data-a="drop-scene"]', host)?.addEventListener('click', async () => {
    if (!confirm(`${s.index + 1}-sahnani o‘chirasizmi?`)) return;
    try {
      clearTimeout(ED.timer);
      ED.dirty.delete(s.index);
      await flush();
      stopPreview();
      mergeScenes(await api(`/api/jobs/${ED.job.id}/scenes/${s.index}`, { method: 'DELETE' }));
      toast('Sahna o‘chirildi');
    } catch (e) {
      editorError(e.message);
    }
  });

  $$('#scene-heroes input', host).forEach((input) => input.addEventListener('change', () => {
    s.hero_ids = $$('#scene-heroes input:checked', host).map((i) => i.value);
    input.closest('.hero-chip').classList.toggle('on', input.checked);
    touch();
  }));

  const guard = async (fn) => {
    const controls = $$('.btn', host);
    controls.forEach((b) => (b.disabled = true));
    editorError('');
    try { await fn(); } catch (e) { editorError(e.message); }
    finally { controls.forEach((b) => (b.disabled = false)); }
  };

  const regen = (body) => guard(async () => {
    await flush();
    await api(`/api/jobs/${ED.job.id}/scenes/${s.index}/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    state.drawn = null;
    watch(ED.job.id);
  });

  // Drawing the same prompt again and hoping for a better roll is the expensive
  // way to fix "his jacket is the wrong colour". Saying what is wrong is the
  // cheap one, and the note sticks to the scene so it still applies the next
  // time anything draws it.
  $('[data-a="image"]', host).addEventListener('click', async () => {
    const answer = await ask({
      title: `${s.index + 1}-sahna rasmini qayta yasash`,
      ok: 'Qayta yasash',
      html: `<label class="f"><span>Nimasi noto‘g‘ri? (ixtiyoriy)</span>
          <textarea name="note" rows="3" maxlength="600"
            placeholder="Masalan: kurtkasi qizil bo‘lsin, ko‘k emas. Fonda odam ko‘p — kamaytiring."
            >${esc(s.fix || '')}</textarea></label>
        <small class="note">Yozganingiz promptga qo‘shiladi va shu sahnada
          saqlanadi — keyingi safar ham hisobga olinadi. Bo‘sh qoldirsangiz
          oldingi izoh o‘chadi.</small>`,
    });
    if (!answer) return;
    s.fix = (answer.note || '').trim();
    regen({ image: true, voice: false, note: s.fix });
  });

  $('[data-a="record"]', host)?.addEventListener('click', () => recordScene(s));

  // Re-recording is when you notice the voice was wrong, so the voice can be
  // chosen here rather than only at creation. It belongs to the whole video, so
  // the dialog says so and offers to re-record everything in one go.
  $('[data-a="voice"]', host)?.addEventListener('click', async () => {
    const providers = Object.entries(state.health?.tts_providers || {})
      .filter(([, ready]) => ready).map(([name]) => name);
    const current = ED.job.tts_provider || state.health?.defaults?.tts_provider || '';

    const answer = await ask({
      title: 'Ovozni qayta yozish',
      ok: 'Qayta yozish',
      html: `<label class="f"><span>Provayder</span>
          <select name="tts_provider" id="revoice-provider">${providers.map((p) =>
            `<option value="${esc(p)}"${p === current ? ' selected' : ''}>${esc(p)}</option>`).join('')}
          </select></label>
        <div class="f"><span>Ovoz</span>
          <div class="voice-pick">
            <select name="voice_id" id="revoice-voice"><option value="">yuklanmoqda…</option></select>
            <button type="button" class="hear" id="revoice-hear" aria-label="Namunani eshitish">▶</button>
          </div>
          <small id="revoice-hint"></small>
        </div>
        <label class="f"><span>Til</span>
          <select name="language" id="revoice-lang">${(state.health?.languages || [])
            .map((l) => `<option value="${esc(l.id)}"${l.id === ED.job.language ? ' selected' : ''}>${esc(l.label)}</option>`).join('')}
          </select></label>
        <div class="f"><span>Qaysi sahnalar</span>
          <select name="scope" id="revoice-scope">
            <option value="one">Faqat shu sahna (${s.index + 1})</option>
            <option value="range">Oraliq — masalan ${Math.min(s.index + 1, ED.scenes.length)} dan ${ED.scenes.length} gacha</option>
            <option value="all">Barchasi (${ED.scenes.length})</option>
          </select></div>
        <div class="f2 hidden" id="revoice-range">
          <label class="f"><span>Qaysi sahnadan</span>
            <input name="from_scene" type="number" min="1" max="${ED.scenes.length}"
              value="${s.index + 1}" /></label>
          <label class="f"><span>Qaysi sahnagacha</span>
            <input name="to_scene" type="number" min="1" max="${ED.scenes.length}"
              value="${ED.scenes.length}" /></label>
        </div>
        <small class="note">Ovoz butun videoga tegishli — o‘zgartirsangiz qolgan
          sahnalar ham render paytida qayta yoziladi. Oraliq tanlasangiz faqat
          o‘shalari hozir qayta yoziladi, ya‘ni yaxshi chiqqanlari uchun
          ikkinchi marta to‘lanmaydi.</small>
        <small class="note hidden" id="revoice-lang-note">Til o‘zgarsa matn shu
          tilga o‘giriladi va <b>butun video</b> qayta o‘qiladi — oraliq
          ishlamaydi. Subtitr ham o‘sha tilda bo‘ladi.</small>`,
      onOpen: () => {
        fillRevoice(current, ED.job.voice_id || '');
        // The two number boxes only mean anything when a range is what is wanted.
        const scope = $('#revoice-scope');
        const range = $('#revoice-range');
        const lang = $('#revoice-lang');
        const sync = () => {
          // A language change rewrites every line, so a range would be a promise
          // the app cannot keep. Say so, and take the choice away rather than
          // accepting it and quietly doing something else.
          const switching = lang.value !== ED.job.language;
          $('#revoice-lang-note').classList.toggle('hidden', !switching);
          if (switching) scope.value = 'all';
          scope.disabled = switching;
          range.classList.toggle('hidden', switching || scope.value !== 'range');
        };
        scope.addEventListener('change', sync);
        lang.addEventListener('change', sync);
        sync();
      },
    });
    if (!answer) return;
    const scope = answer.scope || 'one';
    // Shown one-based because that is what the filmstrip says; sent zero-based
    // because that is what a scene index is.
    const from = Math.max(1, Number(answer.from_scene) || 1) - 1;
    const to = Math.max(1, Number(answer.to_scene) || ED.scenes.length) - 1;
    const switching = answer.language && answer.language !== ED.job.language;
    regen({
      image: false,
      voice: true,
      tts_provider: answer.tts_provider || null,
      voice_id: answer.voice_id || null,
      language: switching ? answer.language : null,
      all_scenes: switching || scope === 'all',
      from_index: !switching && scope === 'range' ? Math.min(from, to) : null,
      to_index: !switching && scope === 'range' ? Math.max(from, to) : null,
    });
    if (switching) toast('Matn tarjima qilinib, butun video qayta o‘qiladi');
  });
  $('[data-a="upload"] input', host).addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    guard(async () => {
      await flush();
      const body = new FormData();
      body.append('image', file);
      await api(`/api/jobs/${ED.job.id}/scenes/${s.index}/image`, { method: 'POST', body });
      e.target.value = '';
      state.drawn = null;
      await tick();
      toast('Rasm almashtirildi');
    });
  });
}

// ── panel: layers ─────────────────────────────────────────────────
function drawLayersPanel() {
  const s = scene();
  const host = $('#tab-layers');
  if (!s) { host.innerHTML = '<p class="empty">Sahna yo‘q.</p>'; return; }

  const list = s.overlays || [];
  const l = selected();
  const anims = l?.type === 'image'
    ? (state.health?.overlay_animations?.image || ['none', 'fade', 'pop', 'rise', 'float', 'drift'])
    : (state.health?.overlay_animations?.text || ['none', 'fade', 'pop', 'rise', 'slide_left', 'slide_right']);

  host.innerHTML = `
    <div class="layer-list">
      ${list.length ? list.map((x) => `
        <button class="layer-row${x.id === ED.sel ? ' on' : ''}" data-pick="${esc(x.id)}">
          <i class="kind">${x.type === 'image' ? '🖼' : 'T'}</i>
          <span>${esc(x.type === 'image' ? (assetName(x.asset_id) || 'rasm') : x.text)}</span>
          <em data-del-layer="${esc(x.id)}" role="button" aria-label="O‘chirish">×</em>
        </button>`).join('')
        : '<p class="empty">Qatlam yo‘q. Yuqoridagi «Matn» yoki «Rasm» tugmasidan qo‘shing.</p>'}
    </div>

    ${l ? `
    <div class="layer-edit">
      ${l.type === 'text' ? `
        <label class="f"><span>Matn</span>
          <input data-k="text" value="${esc(l.text)}" maxlength="180" /></label>
        <div class="f"><span>Rang</span>${swatches('colour', l.colour)}</div>
        <label class="sw"><input type="checkbox" data-k="box"${l.box ? ' checked' : ''} /><i></i>
          <span>Orqa fon</span></label>
        ${l.box
          ? `<div class="f"><span>Fon rangi</span>${swatches('box_colour', l.box_colour)}</div>
             ${slider('box_opacity', 'Fon shaffofligi', 0, 1, 0.05, l.box_opacity ?? 0.6)}`
          : `<div class="f"><span>Kontur rangi</span>${swatches('outline_colour', l.outline_colour)}</div>`}
        <label class="sw"><input type="checkbox" data-k="bold"${l.bold ? ' checked' : ''} /><i></i>
          <span>Qalin</span></label>
      ` : `
        <div class="f"><span>Rasm</span>
          <div class="asset-pick" id="asset-pick">${(state.assets || []).map((a) => `
            <button type="button" data-asset="${esc(a.id)}"
              class="${a.id === l.asset_id ? 'on' : ''}"><img src="${esc(a.url)}" alt="${esc(a.name)}" /></button>`).join('')
            || '<p class="empty">Kutubxonaga rasm yuklang.</p>'}</div></div>
      `}

      ${slider('size', l.type === 'image' ? 'Kattaligi' : 'Shrift', 0.02, l.type === 'image' ? 1 : 0.4, 0.005, l.size)}
      ${slider('rotate', 'Burilish', -45, 45, 1, l.rotate || 0, '°')}
      ${slider('opacity', 'Shaffoflik', 0.1, 1, 0.05, l.opacity ?? 1)}

      <label class="f"><span>Animatsiya</span>
        <select data-k="anim">${anims.map((a) =>
          `<option value="${a}"${a === l.anim ? ' selected' : ''}>${esc(LAYER_ANIMS[a] || a)}</option>`).join('')}
          ${l.type === 'image' ? `<optgroup label="Harakat (aktyor)">${moveIds().map((m) =>
            `<option value="${esc(m)}"${m === l.anim ? ' selected' : ''}>${esc(actorLabel(m))}</option>`).join('')}</optgroup>` : ''}
        </select></label>

      <div class="f2">
        ${slider('start', 'Boshlanish', 0, Math.max(0.5, s.duration - 0.2), 0.1, l.start || 0, 's')}
        ${slider('end', 'Tugash', 0, Math.max(0.5, s.duration), 0.1, l.end || s.duration, 's')}
      </div>
      <small class="note">0 — sahna oxirigacha ko‘rinadi.</small>

      <div class="sc-acts">
        <!-- What a channel mark is: the same thing in the same corner from the
             first frame to the last. It is also how a watermark burned in by
             somebody else's generator gets covered. -->
        <button class="btn primary" data-a="everywhere">Hamma sahnaga qo‘yish</button>
        <button class="btn ghost" data-a="dup">Nusxalash</button>
        <button class="btn ghost" data-a="drop">O‘chirish</button>
      </div>
      <small class="note">«Hamma sahnaga qo‘yish» — shu qatlamni butun video
        davomida, shu joyda saqlaydi. Keyin istagan sahnada alohida surib
        qo‘yish mumkin.</small>
    </div>` : ''}`;

  $$('[data-pick]', host).forEach((b) => b.addEventListener('click', (e) => {
    if (e.target.closest('[data-del-layer]')) return;
    ED.sel = b.dataset.pick;
    drawLayers();
    drawLayersPanel();
  }));
  $$('[data-del-layer]', host).forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    dropLayer(b.dataset.delLayer);
  }));

  if (!l) return;

  wire(host, l, (key) => {
    if (key === 'start' || key === 'end') {
      if (l.end && l.end <= l.start) l.end = Math.min(s.duration, l.start + 0.5);
    }
    touch();
    drawLayers();
    if (['box', 'size', 'rotate', 'opacity', 'start', 'end'].includes(key)) drawLayersPanel();
  });
  wireSwatches(host, l, () => { touch(); drawLayers(); drawLayersPanel(); });

  $$('[data-asset]', host).forEach((b) => b.addEventListener('click', () => {
    l.asset_id = b.dataset.asset;
    touch();
    drawLayers();
    drawLayersPanel();
  }));

  $('[data-a="drop"]', host).addEventListener('click', () => dropLayer(l.id));

  $('[data-a="everywhere"]', host).addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      // Flushed first: the layer has to exist on the server before the server
      // can be asked to copy it, and it may have been dragged a second ago.
      await flush();
      const out = await api(
        `/api/jobs/${ED.job.id}/scenes/${s.index}/layers/${encodeURIComponent(l.id)}/everywhere`,
        { method: 'POST' });
      mergeScenes((await api(`/api/jobs/${ED.job.id}`)).scenes || []);
      toast(`${out.stamped} ta sahnaga qo‘yildi`);
    } catch (err) {
      editorError(err.message);
    } finally {
      btn.disabled = false;
    }
  });

  $('[data-a="dup"]', host).addEventListener('click', () => {
    const copy = { ...l, id: newLayerId(), x: Math.min(1, l.x + 0.06), y: Math.min(1, l.y + 0.06) };
    s.overlays.push(copy);
    ED.sel = copy.id;
    touch();
    drawAll();
  });
}

const assetName = (id) => (state.assets || []).find((a) => a.id === id)?.name;
const newLayerId = () => `ov${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

function dropLayer(id) {
  const s = scene();
  if (!s) return;
  s.overlays = (s.overlays || []).filter((x) => x.id !== id);
  if (ED.sel === id) ED.sel = null;
  touch();
  drawAll();
}

function addLayer(base) {
  const s = scene();
  if (!s) return;
  if ((s.overlays || []).length >= 8) { toast('Bir sahnada 8 tagacha qatlam'); return; }
  const layer = { ...base, id: newLayerId(), end: Math.min(s.duration, (base.start || 0) + 3) };
  s.overlays = [...(s.overlays || []), layer];
  ED.sel = layer.id;
  ED.tab = 'layers';
  touch();
  drawAll();
  drawDock();
}

$('.canvas-tools [data-add="text"]').addEventListener('click', () => addLayer(TEXT_DEFAULTS));
$('.canvas-tools [data-add="actor"]').addEventListener('click', () => addActor());

/** Cut a character out of its background and drop it onto the scene.
 *
 * Three ways in, because the character you want may already exist: a hero from
 * the library, a description of somebody new, or a picture from the phone. The
 * server does the keying either way and hands back a transparent PNG.
 */
async function addActor() {
  if (!scene()) { toast('Avval sahnani oching'); return; }
  const heroes = state.heroes || [];
  // The modal resolves to field values, and a file input has none worth having,
  // so the chosen file is caught as it is picked.
  let picked = null;
  const answer = await ask({
    onOpen: () => $('#modal-body [name="file"]').addEventListener(
      'change', (e) => { picked = e.target.files[0] || null; }),
    title: 'Aktyor qo‘shish',
    ok: 'Kesib olish',
    html: `
      <p class="hint">Fon kesiladi va qahramon sahna ustida yuradi.</p>
      ${heroes.length ? `<label class="f"><span>Herolardan</span>
        <select name="hero_id"><option value="">— yangi chizilsin —</option>
          ${heroes.map((h) => `<option value="${esc(h.id)}">${esc(h.name)}</option>`).join('')}
        </select></label>` : ''}
      <label class="f"><span>Yoki kim chizilsin</span>
        <textarea name="prompt" rows="2" placeholder="masalan: yashil dinozavr, og‘zi ochiq, yon tomondan"></textarea></label>
      <label class="f"><span>Yoki tayyor rasm</span>
        <input type="file" name="file" accept="image/*" /></label>
      <p class="hint">Yuklangan rasmning orqa foni tekis magenta (#FF00FF) bo‘lsa toza kesiladi.</p>`,
  });
  if (!answer) return;
  if (!picked && !answer.prompt?.trim() && !answer.hero_id) {
    toast('Hero tanlang, matn yozing yoki rasm yuklang');
    return;
  }

  const body = new FormData();
  if (picked) body.append('image', picked);
  if (answer.hero_id) body.append('hero_id', answer.hero_id);
  if (answer.prompt) body.append('prompt', answer.prompt.trim());

  toast('Aktyor tayyorlanmoqda…');
  try {
    const asset = await api('/api/actors', { method: 'POST', body });
    await loadAssets();
    addLayer({ ...ACTOR_DEFAULTS, asset_id: asset.id });
    toast('Aktyor qo‘shildi — harakatini tanlang');
  } catch (err) {
    editorError(err.message);
  }
}

$('#layer-file').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  e.target.value = '';
  try {
    const body = new FormData();
    body.append('image', file);
    body.append('name', file.name.replace(/\.[^.]+$/, ''));
    const asset = await api('/api/assets', { method: 'POST', body });
    await loadAssets();
    addLayer({ ...IMAGE_DEFAULTS, asset_id: asset.id });
  } catch (err) {
    editorError(err.message);
  }
});

// ── panel: caption ────────────────────────────────────────────────
function drawCaptionPanel() {
  const host = $('#tab-caption');
  const st = ED.style || {};
  const templates = capTemplates();

  host.innerHTML = `
    <div class="f"><span>Tayyor ko‘rinish</span>
      <div class="chips" id="cap-templates">${templates.map((t) =>
        `<button type="button" data-t="${esc(t.id)}" aria-pressed="${t.id === st.template}">${esc(t.label)}</button>`).join('')}</div></div>

    <label class="sw"><input type="checkbox" id="burn-sub"${ED.burn ? ' checked' : ''} /><i></i>
      <span>Subtitrni videoga yoqish</span></label>

    <div class="${ED.burn ? '' : 'dim'}" id="cap-knobs">
      <div class="f"><span>Matn rangi</span>${swatches('colour', st.colour)}</div>
      ${st.karaoke ? `<div class="f"><span>Aytilayotgan so‘z rangi</span>${swatches('highlight', st.highlight)}</div>` : ''}

      <label class="f"><span>Orqa fon</span>
        <select data-k="box">${Object.entries(BOX_MODES).map(([k, v]) =>
          `<option value="${k}"${k === st.box ? ' selected' : ''}>${esc(v)}</option>`).join('')}</select></label>

      ${st.box === 'box'
        ? `<div class="f"><span>Fon rangi</span>${swatches('box_colour', st.box_colour)}</div>
           ${slider('box_opacity', 'Fon shaffofligi', 0, 1, 0.05, st.box_opacity ?? 0.62)}
           ${slider('outline', 'Fon kengligi', 0, 8, 0.2, st.outline ?? 4)}`
        : `<div class="f"><span>Kontur rangi</span>${swatches('outline_colour', st.outline_colour)}</div>
           ${slider('outline', 'Kontur qalinligi', 0, 10, 0.2, st.outline ?? 4)}
           ${slider('shadow', 'Soya', 0, 10, 0.2, st.shadow ?? 1.6)}`}

      ${slider('size', 'O‘lcham', 0.5, 2.2, 0.05, st.size ?? 1, '×')}
      ${slider('margin', 'Chetdan masofa', -0.2, 0.3, 0.01, st.margin ?? 0)}

      <div class="f"><span>Joylashuvi</span>
        <div class="seg tight" id="cap-pos">${Object.entries(POSITIONS).map(([k, v]) =>
          `<button type="button" data-p="${k}" aria-pressed="${k === st.position}">${esc(v)}</button>`).join('')}</div></div>

      <label class="f"><span>Kirish animatsiyasi</span>
        <select data-k="animation">${Object.entries(CAP_ANIMS).map(([k, v]) =>
          `<option value="${k}"${k === st.animation ? ' selected' : ''}>${esc(v)}</option>`).join('')}</select></label>

      <div class="switches">
        <label class="sw"><input type="checkbox" data-k="bold"${st.bold ? ' checked' : ''} /><i></i>
          <span>Qalin</span></label>
        <label class="sw"><input type="checkbox" data-k="uppercase"${st.uppercase ? ' checked' : ''} /><i></i>
          <span>BOSH HARFLAR</span></label>
      </div>
    </div>`;

  $$('#cap-templates button', host).forEach((b) => b.addEventListener('click', () => {
    const t = templates.find((x) => x.id === b.dataset.t);
    if (!t) return;
    ED.style = { ...t.style };
    touch('style');
    drawCaptionSample();
    drawCaptionPanel();
  }));

  $('#burn-sub', host).addEventListener('change', (e) => {
    ED.burn = e.target.checked;
    touch('style');
    drawCaptionSample();
    drawCaptionPanel();
  });

  $$('#cap-pos button', host).forEach((b) => b.addEventListener('click', () => {
    ED.style.position = b.dataset.p;
    touch('style');
    drawCaptionSample();
    drawCaptionPanel();
  }));

  wire(host, ED.style, (key) => {
    touch('style');
    drawCaptionSample();
    if (['box', 'outline', 'shadow', 'size', 'margin', 'box_opacity'].includes(key)) drawCaptionPanel();
  });
  wireSwatches(host, ED.style, () => { touch('style'); drawCaptionSample(); drawCaptionPanel(); });
}

$('#render-btn').addEventListener('click', async () => {
  if (!state.activeId) return;
  const btn = $('#render-btn');
  btn.disabled = true;
  try {
    clearTimeout(ED.timer);
    await flush();
    await api(`/api/jobs/${state.activeId}/render`, { method: 'POST' });
    state.drawn = null;
    watch(state.activeId, { reveal: true });
  } catch (e) {
    editorError(e.message);
  } finally {
    btn.disabled = false;
  }
});

// Leaving with an edit still on the timer would silently lose it.
addEventListener('beforeunload', () => {
  if (ED.dirty.size || ED.styleDirty) flush();
});

/** What the editing screen shows when nothing is open in it. */
function drawEditEmpty() {
  const empty = $('#edit-empty');
  if (!empty) return;
  const show = state.view === 'edit' && !state.activeId;
  empty.classList.toggle('hidden', !show);
  if (!show) return;

  // The newest unfinished project is what somebody arriving here almost always
  // wants, so it is offered rather than described.
  const pick = state.jobs.find((j) => j.status === 'review')
    || state.jobs.find((j) => BUSY.includes(j.status))
    || state.jobs[0];
  $('#edit-empty-acts').innerHTML = [
    pick ? `<button class="btn primary" data-open="${esc(pick.id)}">${
      esc(pick.title || pick.topic || 'Oxirgi loyiha')} — ochish</button>` : '',
    '<button class="btn" data-go="create">Yangi video</button>',
    '<button class="btn ghost" data-go="chat">AI dan g‘oya so‘rash</button>',
  ].join('');
  $$('#edit-empty-acts [data-open]').forEach((b) =>
    b.addEventListener('click', () => watch(b.dataset.open, { reveal: true })));
}

// ── jobs + ready gallery ──────────────────────────────────────────
async function loadJobs() {
  state.jobs = await api('/api/jobs');

  // Two different counts, because they are now two different screens: how many
  // are moving, and how many there are to open.
  const busy = state.jobs.filter((j) => BUSY.includes(j.status)).length;
  $('#run-badge').textContent = busy;
  $('#run-badge').classList.toggle('hidden', !busy);

  const live = state.jobs.filter((j) => j.status === 'review').length;
  $('#jobs-badge').textContent = live;
  $('#jobs-badge').classList.toggle('hidden', !live);

  const done = state.jobs.filter((j) => j.status === 'done');
  $('#ready-badge').textContent = done.length;
  $('#ready-badge').classList.toggle('hidden', !done.length);

  drawEditEmpty();
  drawRunEmpty();
  drawRunChips();

  drawProjects();

  autoProjects();
  syncProjectsHead();
  drawReady(done);
}

// What this list is for changed when the progress card moved out of this screen:
// it is no longer "which video is running", it is "which finished video am I
// opening" — so the ones there is something to edit come first, and once there
// are enough of them to lose one in, they can be searched.
const EDITABLE_FIRST = { review: 0, done: 1, failed: 2, cancelled: 2 };

function drawProjects() {
  const find = ($('#projects-find')?.value || '').trim().toLowerCase();
  $('#projects-find').hidden = state.jobs.length < 6;

  const rows = state.jobs
    .filter((j) => !find || (j.title || j.topic || '').toLowerCase().includes(find))
    .slice()
    .sort((a, b) => (EDITABLE_FIRST[a.status] ?? 3) - (EDITABLE_FIRST[b.status] ?? 3));

  $('#jobs-list').innerHTML = rows.length
    ? rows.map((j) => `
        <button class="proj${j.id === state.activeId ? ' on' : ''}" data-job="${esc(j.id)}">
          <span class="tag ${esc(j.status)}">${esc(STATUS[j.status] || j.status)}</span>
          <b>${esc(j.title || j.topic || j.id)}</b>
          <small>${esc(j.video_format)}${j.duration ? ` · ${clock(j.duration)}` : ''}${
            j.scene_count ? ` · ${j.scene_count} sahna` : ''}</small>
          <i class="x" data-del-job="${esc(j.id)}" role="button" aria-label="O‘chirish">×</i>
        </button>`).join('')
    : `<p class="empty">${state.jobs.length
        ? 'Bunday nomli loyiha topilmadi.'
        : 'Hali loyiha yo‘q. «Yaratish» bo‘limidan boshlang.'}</p>`;

  $$('#jobs-list [data-job]').forEach((row) => row.addEventListener('click', (e) => {
    if (e.target.closest('[data-del-job]')) return;
    go('edit');
    watch(row.dataset.job, { reveal: true });
  }));

  $$('#jobs-list [data-del-job]').forEach((b) => b.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm('Loyihani o‘chirasizmi?')) return;
    await api(`/api/jobs/${b.dataset.delJob}`, { method: 'DELETE' });
    if (state.activeId === b.dataset.delJob) {
      state.activeId = null;
      ED.job = null;
      $('#stage').classList.add('hidden');
      $('#editor').classList.add('hidden');
      drawDock();
    }
    loadJobs();
  }));
}

$('#projects-find')?.addEventListener('input', () => drawProjects());

// The strip used to hold the top of the screen open whatever you were doing.
// It is a way back to another project, so it folds — and it names what is
// behind the fold, because a closed drawer you cannot label is just a mystery.
const SHUT_KEY = 'studio.projects.shut';

function syncProjectsHead() {
  const wrap = $('#projects-wrap');
  if (!wrap) return;
  const open = state.jobs.find((j) => j.id === state.activeId);
  const busy = state.jobs.filter((j) => BUSY.includes(j.status)).length;

  $('#projects-label').textContent = wrap.classList.contains('shut') && open
    ? (open.title || open.topic || 'Loyiha')
    : 'Loyihalar';
  const count = state.jobs.length;
  $('#projects-count').textContent = busy
    ? `${count} · ${busy} ishlayapti`
    : (count ? String(count) : '');
  $('#projects-toggle').setAttribute('aria-expanded', String(!wrap.classList.contains('shut')));
}

function setProjectsShut(shut, remember = true) {
  const wrap = $('#projects-wrap');
  if (!wrap) return;
  wrap.classList.toggle('shut', shut);
  if (remember) {
    try { localStorage.setItem(SHUT_KEY, shut ? '1' : '0'); } catch (e) { /* private mode */ }
  }
  syncProjectsHead();
}

function projectsChoice() {
  try { return localStorage.getItem(SHUT_KEY); } catch (e) { return null; }
}

/** The default, applied only while the user has not expressed one.
 *
 * Folded when a project is open, because then the strip is a way back to
 * something else rather than the thing you are looking at; open when there is
 * nothing below it, because then it is the whole screen's content. The markup
 * starts folded so a page that opens straight into a project never flashes it.
 */
function autoProjects() {
  if (projectsChoice() !== null) return;
  setProjectsShut(Boolean(state.activeId), false);
}

$('#projects-toggle')?.addEventListener('click', () =>
  setProjectsShut(!$('#projects-wrap').classList.contains('shut')));

// Older jobs stored one flat metadata pack; newer ones store a pack per
// platform. Normalise so the gallery renders both.
function platformPacks(m) {
  if (!m) return null;
  const yt = m.youtube || { title: m.title, description: m.description, tags: m.tags || [] };
  const tags = (yt.tags || []).map((t) => `#${t}`).join(' ');
  return [
    ['YouTube', '#ff4d4d', [
      ['Sarlavha', yt.title || ''],
      ['Tavsif', yt.description || ''],
      ['Teglar', (yt.tags || []).join(', ')],
    ]],
    ['TikTok', '#25f4ee', [
      ['Matn', m.tiktok?.caption || ''],
      ['Hashtaglar', (m.tiktok?.hashtags || []).map((t) => `#${t}`).join(' ')],
      ['Matn + hashtag', [m.tiktok?.caption, (m.tiktok?.hashtags || []).map((t) => `#${t}`).join(' ')]
        .filter(Boolean).join('\n\n')],
    ]],
    ['Instagram', '#e1306c', [
      ['Matn', m.instagram?.caption || ''],
      ['Hashtaglar', (m.instagram?.hashtags || []).map((t) => `#${t}`).join(' ')],
      ['Matn + hashtag', [m.instagram?.caption, (m.instagram?.hashtags || []).map((t) => `#${t}`).join(' ')]
        .filter(Boolean).join('\n\n')],
    ]],
  ].map(([name, colour, rows]) => [name, colour, rows.filter(([, v]) => v && v.trim())])
   .filter(([, , rows]) => rows.length || tags);
}

function drawReady(done = state.jobs.filter((j) => j.status === 'done')) {
  if (!done.length) {
    $('#ready-list').innerHTML =
      '<p class="empty">Hali tayyor video yo‘q. Video render bo‘lgach shu yerda chiqadi.</p>';
    return;
  }

  $('#ready-list').innerHTML = done.map((j) => {
    const packs = platformPacks(j.metadata) || [];
    return `
    <article class="ready-card">
      <video controls playsinline preload="metadata" src="${esc(j.video_url || '')}"></video>
      <div class="ready-body">
        <div class="ready-head">
          <h3>${esc(j.title || j.topic || j.id)}</h3>
          <small>${esc(j.video_format)} · ${clock(j.duration)} · ${
            j.caption_count ? `${j.caption_count} ta satr` : `${j.scene_count || 0} sahna`}</small>
        </div>
        <div class="acts" style="margin-top:0">
          <!-- A subtitled upload has no video of ours to download unless the
               words were burned in; with "faqat fayl" the deliverable is the
               subtitle, and a download button pointing at nothing is worse than
               no button. -->
          ${j.kind === 'subtitle' && !j.download_url ? '' :
            `<a class="btn primary" href="${esc(j.download_url || j.video_url || '')}" download>Videoni yuklab olish</a>`}
          <!-- The same cues in the shape you are about to use them in: an editor
               takes .srt, a web player only loads .vtt, and the description box
               wants the words with no timings at all. -->
          <span class="subs">Subtitr:
            <a href="/api/jobs/${esc(j.id)}/subtitles.srt" download>.srt</a>
            <a href="/api/jobs/${esc(j.id)}/subtitles.vtt" download>.vtt</a>
            <a href="/api/jobs/${esc(j.id)}/subtitles.txt" download>matn</a>
          </span>
          <!-- Everything below this line rebuilds the video from its scenes:
               a new music bed, a cover frame, a Short cut out of it, another
               format, another language. A subtitled upload has no scenes — the
               app never made that video — so it is offered none of them. -->
          ${j.kind === 'subtitle' ? '' : `
          <button class="btn" data-music="${esc(j.id)}" data-track="${esc(j.music_id || '')}"
            data-at="${esc(String(j.music_start || 0))}">${j.music_id ? 'Musiqani almashtirish' : 'Musiqa qo‘shish'}</button>
          <button class="btn" data-thumbs="${esc(j.id)}">Muqova yaratish</button>
          ${j.youtube?.url
            ? `<a class="btn ok" href="${esc(j.youtube.url)}" target="_blank" rel="noopener">
                 YouTube'da ochish${j.youtube.publish_at ? ' · rejalashtirilgan' : ''}</a>`
            : `<button class="btn yt" data-publish="${esc(j.id)}">YouTube'ga joylash</button>`}
          <!-- Offered whenever there is something to divide: three scenes is
               enough to take a piece out of, and a forty-second video can still
               hold a fifteen-second Short. Only a Short itself is excluded —
               cutting one of those again is cutting a cut. -->
          ${j.kind === 'short' || (j.scene_count || 0) < 3 ? '' :
            `<button class="btn" data-shorts="${esc(j.id)}">Shortsga bo‘lish</button>`}
          <button class="btn" data-repurpose="${esc(j.id)}" data-fmt="${esc(j.video_format)}">Boshqa formatga</button>
          ${j.kind === 'dub' ? '' :
            `<button class="btn" data-translate="${esc(j.id)}" data-lang="${esc(j.language)}">Boshqa tilga</button>`}
          `}
        </div>

        ${j.thumbnails?.length ? `<div class="thumbs">
          ${j.thumbnails.map((url, i) => `
            <figure><img src="${esc(url)}" alt="Muqova ${i + 1}" loading="lazy" />
              <a class="btn" href="${esc(url)}" download="thumbnail-${i + 1}.png">Yuklab olish</a>
            </figure>`).join('')}
        </div>` : ''}
        ${packs.length ? packs.map(([name, colour, rows]) => `
          <div class="plat">
            <div class="plat-head">
              <span><i class="logo-dot" style="background:${colour}"></i>${esc(name)}</span>
            </div>
            <div class="plat-body">
              ${rows.map(([label, value]) => `
                <div>
                  <div style="display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:4px">
                    <span style="font-size:.72rem;color:var(--muted)">${esc(label)}</span>
                    <button class="copy" data-copy="${esc(value)}">Nusxalash</button>
                  </div>
                  <pre>${esc(value)}</pre>
                </div>`).join('')}
            </div>
          </div>`).join('')
        : '<p class="empty">Matnlar yaratilmagan.</p>'}
      </div>
    </article>`;
  }).join('');

  $$('#ready-list [data-copy]').forEach((b) =>
    b.addEventListener('click', () => copyText(b.dataset.copy, b)));

  $$('#ready-list [data-publish]').forEach((b) => b.addEventListener('click', () => {
    const job = state.jobs.find((j) => j.id === b.dataset.publish);
    if (job) publishToYouTube(job);
  }));

  // Only the audio is rebuilt, so a track can be tried and changed again in
  // seconds. That is what makes this worth having on a finished video at all —
  // choosing music is a thing you do by ear, not once and for ever.
  $$('#ready-list [data-music]').forEach((b) => b.addEventListener('click', async () => {
    const beds = (state.music || []).filter((m) => (m.kind || 'music') !== 'sfx');
    const answer = await ask({
      title: 'Orqa fon musiqasi',
      ok: 'Qo‘shish',
      html: `<label class="f"><span>Trek</span>
          <select name="music_id">
            <option value="">— musiqasiz —</option>
            ${beds.map((m) => `<option value="${esc(m.id)}"${
              m.id === b.dataset.track ? ' selected' : ''}>${esc(m.name)}</option>`).join('')}
          </select></label>
        <label class="f"><span>Trekning qayeridan boshlansin (soniya)</span>
          <input type="number" name="music_start" min="0" step="1" value="${esc(b.dataset.at || '0')}" /></label>
        <small class="note">Ovoz ostida musiqa avtomatik pasayadi. Video qayta render
          qilinmaydi — faqat tovush yangilanadi, shuning uchun bir necha soniya oladi.
          ${beds.length ? '' : '<br />Kutubxonada hali trek yo‘q — avval yuklang.'}</small>`,
    });
    if (!answer) return;
    const label = b.textContent;
    b.disabled = true;
    b.textContent = 'Mikslanmoqda…';
    try {
      await api(`/api/jobs/${b.dataset.music}/music`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          music_id: answer.music_id || '',
          music_start: Number(answer.music_start) || 0,
        }),
      });
      go('run');
      watch(b.dataset.music, { reveal: true });
      toast(answer.music_id ? 'Musiqa mikslanmoqda' : 'Musiqa olib tashlanmoqda');
    } catch (e) {
      alert(e.message);
      b.disabled = false;
      b.textContent = label;
    }
  }));

  $$('#ready-list [data-thumbs]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    b.textContent = 'Yaratilmoqda…';
    try {
      await api(`/api/jobs/${b.dataset.thumbs}/thumbnails`, { method: 'POST' });
      go('run');
      watch(b.dataset.thumbs, { reveal: true });
      toast('Uchta muqova tayyorlanmoqda');
    } catch (e) {
      alert(e.message);
      b.disabled = false;
      b.textContent = 'Muqova yaratish';
    }
  }));

  // Same pictures, same cuts, a different language — the cheapest way to reach
  // a second audience with a video that already works.
  $$('#ready-list [data-translate]').forEach((b) => b.addEventListener('click', async () => {
    const languages = (state.health?.languages || []).filter((l) => l.id !== b.dataset.lang);
    const answer = await ask({
      title: 'Boshqa tilga',
      ok: 'Tarjima qilish',
      html: `<label class="f"><span>Qaysi tilga</span>
          <select name="language">${languages.map((l) =>
            `<option value="${esc(l.id)}">${esc(l.label)}</option>`).join('')}</select></label>
        <label class="f"><span>Ovoz</span>
          <select name="voice_id"><option value="">standart</option>
            ${(state.voices || []).map((v) =>
              `<option value="${esc(v.id)}">${esc(v.label)}${v.hint ? ` — ${esc(v.hint)}` : ''}</option>`).join('')}
          </select></label>
        <small class="note">Rasmlar, qatlamlar va kamera harakatlari o‘zgarmaydi —
          faqat matn tarjima qilinib, qaytadan ovozlanadi.</small>`,
    });
    if (!answer) return;
    b.disabled = true;
    b.textContent = 'Tarjima qilinmoqda…';
    try {
      const clone = await api(`/api/jobs/${b.dataset.translate}/translate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          language: answer.language,
          voice_id: answer.voice_id || null,
          tts_provider: $('#tts_provider').value || null,
        }),
      });
      await loadJobs();
      go('run');
      watch(clone.id, { reveal: true });
      toast('Tarjima tayyor — endi render qiling');
    } catch (e) {
      alert(e.message);
    } finally {
      b.disabled = false;
      b.textContent = 'Boshqa tilga';
    }
  }));

  $$('#ready-list [data-shorts]').forEach((b) => b.addEventListener('click', () => {
    const job = state.jobs.find((j) => j.id === b.dataset.shorts);
    if (job) cutShorts(job);
  }));

  $$('#ready-list [data-repurpose]').forEach((b) => b.addEventListener('click', async () => {
    const formats = (state.health?.formats || []).filter((f) => f.id !== b.dataset.fmt);
    const answer = await ask({
      title: 'Boshqa formatga',
      ok: 'Nusxa olish',
      html: `<label class="f"><span>Yangi format</span>
          <select name="video_format">${formats.map((f) =>
            `<option value="${esc(f.id)}">${esc(f.label)}</option>`).join('')}</select></label>
        <label class="sw"><input type="checkbox" name="regenerate_images" checked /><i></i>
          <span>Rasmlarni yangi format uchun qayta yaratish</span></label>
        <small class="note">Ovoz, vaqtlar, subtitr va qatlamlar o‘zgarmaydi. Belgini
          olib tashlasangiz eski rasmlar o‘rtasidan kesib ishlatiladi — tez, lekin
          chetdagi narsalar kadrdan chiqib ketishi mumkin.</small>`,
    });
    if (!answer) return;
    try {
      const clone = await api(`/api/jobs/${b.dataset.repurpose}/repurpose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_format: answer.video_format,
          regenerate_images: !!answer.regenerate_images,
        }),
      });
      await loadJobs();
      go('run');
      watch(clone.id, { reveal: true });
      toast('Nusxa tayyor — endi render qiling');
    } catch (e) {
      alert(e.message);
    }
  }));
}

// ══ suhbat ════════════════════════════════════════════════════════
// The one screen you talk to. It knows your channels because you showed it a
// screenshot once, it offers ideas you can tap, and it asks what it still needs
// before starting a video rather than guessing and charging you for the guess.

const CHAT = { platform: 'youtube', busy: false, messages: [], profiles: [] };

const PLAT_LABEL = { youtube: 'YouTube', instagram: 'Instagram', tiktok: 'TikTok', other: 'Kanal' };
const SHAPE_LABEL = { shorts: 'Shorts', long: 'Uzun' };

async function loadProfiles() {
  CHAT.profiles = await api('/api/profiles');
  drawChannels();
}

function drawChannels() {
  const list = CHAT.profiles;
  $('#chan-count').textContent = list.length ? `${list.length} ta` : '';
  $('#chan-row').innerHTML = list.length
    ? list.map((p) => `
        <figure class="chan-card" data-profile="${esc(p.id)}">
          <img src="${esc(p.url)}" alt="${esc(p.handle || p.platform)}" loading="lazy" />
          <figcaption>
            <b>${esc(p.handle || PLAT_LABEL[p.platform] || p.platform)}</b>
            <!-- What the channel is about, which is what the ideas are judged
                 against — the platform is a badge over the picture, because
                 "YouTube" says far less about a channel than its subject does. -->
            <span>${esc(p.niche || 'mavzusi yozilmagan')}</span>
          </figcaption>
          <u class="plat">${esc(PLAT_LABEL[p.platform] || p.platform)}</u>
          <i class="pen" data-edit-profile="${esc(p.id)}" role="button" aria-label="Tuzatish">✎</i>
          <i class="x" data-del-profile="${esc(p.id)}" role="button" aria-label="O‘chirish">×</i>
        </figure>`).join('')
    : '';
  $('#chan-note').classList.toggle('hidden', list.length > 0);

  $$('#chan-row [data-edit-profile]').forEach((b) => b.addEventListener('click', (e) => {
    e.stopPropagation();
    editProfile(b.dataset.editProfile);
  }));

  // Tapping a channel is the shortest way to say which one you mean.
  $$('#chan-row [data-profile]').forEach((card) => card.addEventListener('click', (e) => {
    if (e.target.closest('[data-del-profile], [data-edit-profile]')) return;
    const p = CHAT.profiles.find((x) => x.id === card.dataset.profile);
    if (!p) return;
    const name = p.handle ? `${PLAT_LABEL[p.platform] || p.platform} ${p.handle}` : PLAT_LABEL[p.platform];
    $('#chat-input').value = `${name} kanalim uchun g‘oyalar ber`;
    $('#chat-input').focus();
    growChatInput();
  }));

  $$('#chan-row [data-del-profile]').forEach((b) => b.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm('Bu skrinshotni o‘chirasizmi?')) return;
    try {
      await api(`/api/profiles/${b.dataset.delProfile}`, { method: 'DELETE' });
      await loadProfiles();
    } catch (err) { toast(err.message); }
  }));
}

/** Correct what was read off a screenshot. The specifics are what the assistant
    is held to, so a wrong subject matters more than a wrong sentence. */
async function editProfile(id) {
  const p = CHAT.profiles.find((x) => x.id === id);
  if (!p) return;
  const answer = await ask({
    title: 'Kanal haqida',
    ok: 'Saqlash',
    html: `
      <label class="f"><span>Nomi (@)</span>
        <input name="handle" value="${esc(p.handle || '')}" placeholder="@kanalim" /></label>
      <label class="f"><span>Mavzusi — aniq</span>
        <input name="niche" value="${esc(p.niche || '')}"
          placeholder="boshlang‘ich Python darslari" /></label>
      <label class="f"><span>Kim ko‘radi</span>
        <input name="audience" value="${esc(p.audience || '')}"
          placeholder="18-30 yosh, O‘zbekistondagi dasturchilar" /></label>
      <label class="f"><span>Postlar tili</span>
        <input name="language" value="${esc(p.language || '')}" placeholder="uz" maxlength="5" /></label>
      <label class="f"><span>Nima post qilasiz</span>
        <input name="pillars" value="${esc(p.pillars || '')}"
          placeholder="dars, kod tahlili, savol-javob" /></label>
      <label class="f"><span>Qanday olinadi</span>
        <input name="style" value="${esc(p.style || '')}"
          placeholder="ovoz ustidan, subtitr bilan, tez montaj" /></label>
      <small class="note">AI g‘oyalarni aynan shu ma’lumotlarga tayanib beradi —
        noto‘g‘ri bo‘lsa g‘oyalar ham umumiy chiqadi.</small>`,
  });
  if (!answer) return;
  try {
    await api(`/api/profiles/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        handle: answer.handle.trim(), niche: answer.niche.trim(),
        audience: answer.audience.trim(), language: answer.language.trim(),
        pillars: answer.pillars.trim(), style: answer.style.trim(),
      }),
    });
    await loadProfiles();
    toast('Saqlandi — endi g‘oyalar shunga qarab beriladi');
  } catch (e) { toast(e.message); }
}

$$('#profile-platform button').forEach((b) => b.addEventListener('click', () => {
  CHAT.platform = b.dataset.plat;
  $$('#profile-platform button').forEach((x) => x.setAttribute('aria-pressed', x === b));
}));

$('#profile-file').addEventListener('change', async (e) => {
  const file = e.target.files?.[0];
  e.target.value = '';
  // The shared file-input handler puts the chosen filename on the label, which
  // is right where the file is a setting to be confirmed. Here it is consumed
  // immediately and the channel appears in the strip, so the label goes back to
  // being an invitation rather than a stale receipt.
  const label = e.target.closest('.file');
  if (label?.dataset.label) {
    $('span', label).textContent = label.dataset.label;
    label.classList.remove('has');
  }
  if (!file) return;
  const note = $('#chan-note');
  note.classList.remove('hidden');
  note.textContent = 'Skrinshot o‘qilmoqda…';
  try {
    const body = new FormData();
    body.append('image', file);
    body.append('platform', CHAT.platform);
    const made = await api('/api/profiles', { method: 'POST', body });
    await loadProfiles();
    note.classList.toggle('hidden', CHAT.profiles.length > 0);
    // What it read back is worth showing: it is what every later answer is
    // based on, and a misreading is only fixable if it is visible.
    if (made.summary) {
      pushChat({ role: 'bot', text: `${PLAT_LABEL[made.platform] || made.platform}`
        + `${made.handle ? ` ${made.handle}` : ''} — o‘qib oldim.\n\n${made.summary}` });
    }
  } catch (err) {
    note.textContent = err.message;
    note.classList.remove('hidden');
  }
});

function drawChat() {
  const log = $('#chat-log');
  log.innerHTML = CHAT.messages.length
    ? CHAT.messages.map((m, i) => bubble(m, i)).join('')
      + (CHAT.busy ? '<div class="bub bot typing"><i></i><i></i><i></i></div>' : '')
    : `<div class="chat-empty">
         <h2>Nima suratga olamiz?</h2>
         <p>Kanalingiz skrinshotini yuklang, keyin g‘oya so‘rang. Yoqqanini bosasiz —
            qolganini o‘zi so‘rab, videoni boshlaydi.</p>
         <div class="chat-seeds">
           ${['YouTube Shorts uchun 5 ta g‘oya ber',
              'TikTok uchun trendga mos nima qilsam bo‘ladi?',
              'Instagram Reels uchun ta’limiy g‘oya kerak']
             .map((s) => `<button class="chip" data-seed="${esc(s)}">${esc(s)}</button>`).join('')}
         </div>
       </div>`;

  $$('#chat-log [data-seed]').forEach((b) =>
    b.addEventListener('click', () => sendChat(b.dataset.seed)));
  $$('#chat-log [data-ask]').forEach((b) =>
    b.addEventListener('click', () => sendChat(b.dataset.ask)));
  $$('#chat-log [data-idea]').forEach((b) => b.addEventListener('click', () => {
    const idea = JSON.parse(b.dataset.idea);
    sendChat(`«${idea.title}» ni tanladim. ${SHAPE_LABEL[idea.shape] || ''}, `
      + `${idea.seconds} soniya. Shuni qilamiz.`);
  }));
  $$('#chat-log [data-open-job]').forEach((b) => b.addEventListener('click', () => {
    go('run');
    watch(b.dataset.openJob, { reveal: true });
  }));

  // The page is the scroller now, not the log, so the newest message is brought
  // to where it can be read rather than the log's own scrollTop being set —
  // which, on a container that no longer scrolls, did nothing at all.
  const newest = log.lastElementChild;
  if (CHAT.messages.length && newest) {
    requestAnimationFrame(() =>
      newest.scrollIntoView({ behavior: 'smooth', block: 'end' }));
  }
}

function bubble(m, i) {
  if (m.role === 'user') return `<div class="bub me">${esc(m.text)}</div>`;
  const ideas = (m.ideas || []).length
    ? `<div class="ideas">${m.ideas.map((idea) => `
        <button class="idea" data-idea="${esc(JSON.stringify(idea))}">
          <b>${esc(idea.title)}</b>
          ${idea.hook ? `<span class="hook">${esc(idea.hook)}</span>` : ''}
          ${idea.why ? `<span class="why">${esc(idea.why)}</span>` : ''}
          ${idea.fit ? `<span class="fit">${esc(idea.fit)}</span>` : ''}
          <em>${esc(SHAPE_LABEL[idea.shape] || idea.shape)} · ${idea.seconds}s</em>
        </button>`).join('')}</div>`
    : '';
  // A question with options is a row of buttons, not a sentence to answer by
  // typing — which is the difference between one tap and a paragraph.
  const asks = (m.asks || []).length
    ? `<div class="asks">${m.asks.map((a) => `
        <div class="ask">
          <span>${esc(a.question)}</span>
          <div class="chips">${(a.options || []).map((o) =>
            `<button class="chip" data-ask="${esc(o)}">${esc(o)}</button>`).join('')}</div>
        </div>`).join('')}</div>`
    : '';
  const started = m.job_id
    ? `<div class="made-job">
         <span>Video boshlandi.</span>
         <button class="btn primary sm" data-open-job="${esc(m.job_id)}">Ochish</button>
       </div>`
    : '';
  return `<div class="bub bot" data-i="${i}">${esc(m.text)}${ideas}${asks}${started}</div>`;
}

function pushChat(m) {
  CHAT.messages.push(m);
  drawChat();
}

async function sendChat(text) {
  const said = (text ?? $('#chat-input').value).trim();
  if (!said || CHAT.busy) return;
  $('#chat-input').value = '';
  growChatInput();
  $('#chat-error').classList.add('hidden');
  CHAT.busy = true;
  pushChat({ role: 'user', text: said });
  $('#chat-send').disabled = true;

  try {
    const out = await api('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: said }),
    });
    CHAT.busy = false;
    pushChat({ role: 'bot', text: out.reply, ideas: out.ideas, asks: out.asks, job_id: out.job_id });
    if (out.job_id) { loadJobs(); toast('Video yaratish boshlandi'); }
  } catch (e) {
    CHAT.busy = false;
    drawChat();
    $('#chat-error').textContent = e.message;
    $('#chat-error').classList.remove('hidden');
  } finally {
    $('#chat-send').disabled = false;
  }
}

function growChatInput() {
  const box = $('#chat-input');
  box.style.height = 'auto';
  box.style.height = `${Math.min(140, box.scrollHeight)}px`;
}

$('#chat-new').addEventListener('click', async () => {
  if (!CHAT.messages.length) { toast('Suhbat allaqachon bo‘sh'); return; }
  if (!confirm('Suhbatni tozalaymizmi? Kanallar va videolar joyida qoladi.')) return;
  try {
    await api('/api/chat', { method: 'DELETE' });
    CHAT.messages = [];
    drawChat();
    $('#chat-input').focus();
    toast('Yangi suhbat');
  } catch (e) { toast(e.message); }
});

$('#chat-send').addEventListener('click', () => sendChat());
$('#chat-input').addEventListener('input', growChatInput);
$('#chat-input').addEventListener('keydown', (e) => {
  // Enter sends, shift-enter is a new line — what every chat does.
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
});

async function loadChat() {
  try {
    const out = await api('/api/chat');
    CHAT.messages = out.messages || [];
  } catch (e) { CHAT.messages = []; }
  drawChat();
}

// ══ Reja ══════════════════════════════════════════════════════════
// A video asked for in advance. The app builds it before the slot, waits to be
// told it is good, and YouTube holds it until the minute that was chosen.

const PLAN_STATUS = {
  idea: 'rejada', used: 'boshlangan',
  planned: 'vaqtga qo‘yilgan', building: 'tayyorlanmoqda',
  ready: 'tasdiqlashni kutmoqda',
  published: 'joylandi', failed: 'xato', cancelled: 'bekor',
};

// An idea does nothing on its own. Kept apart from the scheduled ones because
// the two answer different questions — "what shall I make next" and "what is
// about to go out" — and mixing them makes the first list unreadable.
const ON_SHELF = new Set(['idea', 'used']);

let PLANS = [];
let planPoll = null;

async function loadPlans() {
  try { PLANS = await api('/api/plans'); } catch (e) { PLANS = []; }
  const waiting = PLANS.filter((p) => p.status === 'ready').length;
  $('#plans-badge').textContent = waiting;
  $('#plans-badge').classList.toggle('hidden', !waiting);
  drawPlans();
  followPlans();
}

/** Keep this screen honest while something on it is moving.
 *
 * A plan builds itself in the background, so a card left on screen goes stale
 * within seconds of being drawn — and "tayyorlanmoqda" that never becomes
 * "tasdiqlashni kutmoqda" looks exactly like a plan that has stuck. Polled only
 * while this view is open and only while something is actually building. */
function followPlans() {
  const moving = PLANS.some((p) => p.status === 'building');
  const want = moving && state.view === 'plans';
  if (want && !planPoll) planPoll = setInterval(loadPlans, 5000);
  if (!want && planPoll) { clearInterval(planPoll); planPoll = null; }
}

function planWhen(iso) {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString('uz-UZ', { day: '2-digit', month: '2-digit',
                                      hour: '2-digit', minute: '2-digit' });
}

const IMAGE_PLAN_NOTE = {
  manual: 'rasmsiz — ovoz va subtitr',
  flow: 'Flow navbati',
  flowagent: 'Flow Agent',
};

/** The channel names already in use, so the next plan is picked not retyped. */
function planChannels() {
  return [...new Set(PLANS.map((p) => (p.channel || '').trim()).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b));
}

function planCard(p) {
  const shelf = ON_SHELF.has(p.status);
  const bits = [p.video_format];
  if (p.compose?.target_seconds) bits.push(durationLabel(p.compose.target_seconds));
  if (IMAGE_PLAN_NOTE[p.image_provider]) bits.push(IMAGE_PLAN_NOTE[p.image_provider]);
  if (!shelf) bits.push(p.approve ? 'tasdiqlash bilan' : 'o‘zi joylaydi');

  return `
    <article class="plan ${esc(p.status)}">
      <div class="plan-head">
        <span class="tag ${esc(p.status)}">${esc(PLAN_STATUS[p.status] || p.status)}</span>
        ${shelf ? '' : `<b>${esc(planWhen(p.publish_at))}</b>`}
        ${!shelf && p.batch
          ? `<em class="cheap" title="Rasmlar batch orqali — yarim narx">batch${
              p.batch_mode === 'on' ? ' ·' : ''}</em>`
          : (!shelf && p.batch_mode === 'off'
              ? '<em class="cheap off" title="Batch o‘chirilgan — to‘liq narx, tez">oddiy</em>' : '')}
        <i class="x" data-del-plan="${esc(p.id)}" role="button" aria-label="O‘chirish">×</i>
      </div>
      <h3>${esc(p.title || p.topic)}</h3>
      <small>${esc(bits.join(' · '))}</small>
      ${p.status === 'planned' ? `
        <label class="f tight"><span>Rasmlar</span>
          <select data-batch="${esc(p.id)}">
            <option value="auto"${p.batch_mode === 'auto' ? ' selected' : ''}>Avtomatik</option>
            <option value="on"${p.batch_mode === 'on' ? ' selected' : ''}>Batch — yarim narx</option>
            <option value="off"${p.batch_mode === 'off' ? ' selected' : ''}>Oddiy — tez</option>
          </select></label>` : ''}
      <p class="note">${esc(p.note)}</p>
      ${p.status === 'building' && p.job_progress
        ? `<div class="track live"><i style="width:${p.job_progress}%"></i></div>` : ''}
      <div class="plan-acts">
        ${shelf
          ? `<button class="btn primary" data-use-plan="${esc(p.id)}">Yasashni boshlash</button>` : ''}
        ${p.status === 'ready'
          ? `<button class="btn primary" data-approve="${esc(p.id)}">Tasdiqlash va joylash</button>` : ''}
        ${p.job_id
          ? `<button class="btn" data-open-plan="${esc(p.job_id)}">Videoni ko‘rish</button>` : ''}
        ${p.status === 'planned'
          ? `<button class="btn" data-start-plan="${esc(p.id)}">Hozir boshlash</button>
             <button class="btn ghost" data-move-plan="${esc(p.id)}">Vaqtini o‘zgartirish</button>` : ''}
        ${p.video_url
          ? `<a class="btn ok" href="${esc(p.video_url)}" target="_blank" rel="noopener">YouTube'da</a>` : ''}
      </div>
      ${p.error && p.status !== 'failed' ? `<p class="msg warn">${esc(p.error)}</p>` : ''}
    </article>`;
}

/** Take an idea off the shelf and hand it to the composer, already answered.
 *
 * Deliberately stops one press short of making the video. The whole point of
 * writing a plan down and coming back to it is looking at it again — so this
 * fills the form and steps out of the way, rather than starting a render on a
 * decision made a fortnight ago.
 */
async function usePlan(id, button) {
  if (button) button.disabled = true;
  try {
    const plan = await api(`/api/plans/${id}/activate`, { method: 'POST' });
    const c = plan.compose || {};

    // A plan describes a video to be invented, so the composer has to be on the
    // tab that invents one — dubbing somebody else's clip is a different form.
    $('#mode-tabs [data-mode="topic"]').click();
    $('#topic').value = c.topic || '';
    $('#topic').dispatchEvent(new Event('input'));
    if (c.video_format) setFormat(c.video_format);
    if (c.target_seconds) {
      duration.value = String(Math.max(Number(duration.min),
        Math.min(Number(duration.max), Number(c.target_seconds))));
    }
    if (c.language) $('#language').value = c.language;
    if (c.art_style) $('#art_style').value = c.art_style;
    if (c.tone) $('#tone').value = c.tone;
    $('#action').value = c.action || '';
    $('#animate_actors').checked = !!c.animate_actors;
    $('#music_id').value = c.music_id || '';
    // Only when the plan asked for one. An empty choice means "whatever the app
    // is set to", and forcing that to the first option would silently change it.
    if (c.image_provider) $('#image_provider').value = c.image_provider;
    $$('#hero-picker input').forEach((box) => {
      box.checked = (c.hero_ids || []).includes(box.value);
    });

    syncDuration();
    syncAnimate();
    drawSummary();
    go('create');
    await loadPlans();
    toast('Reja to‘ldirildi — sozlab «Video yaratish» ni bosing');
  } catch (e) {
    toast(e.message);
  } finally {
    if (button) button.disabled = false;
  }
}

function drawPlans() {
  const list = $('#plans-list');
  if (!list) return;

  // Offered to the channel box as you type, so the second plan for a channel
  // does not become a second channel because of a capital letter.
  const known = planChannels();
  const box = $('#plan-channels');
  if (box) box.innerHTML = known.map((c) => `<option value="${esc(c)}"></option>`).join('');

  if (!PLANS.length) {
    list.innerHTML = `<p class="empty">Hali reja yo‘q. Yuqorida «Yangi reja» ni
      to‘ldirsangiz, g‘oyangiz shu yerda turadi — xohlagan paytingizda bir
      bosishda yasashga o‘tasiz.</p>`;
    return;
  }

  // Grouped by channel, and inside each the shelf first: "what shall I make
  // next" is the question this screen is opened with, and a scheduled video
  // that is already building is not an answer to it.
  const groups = new Map();
  for (const p of PLANS) {
    const key = (p.channel || '').trim() || '—';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  const order = [...groups.keys()].sort((a, b) =>
    a === '—' ? 1 : b === '—' ? -1 : a.localeCompare(b));

  list.innerHTML = order.map((name) => {
    const rows = groups.get(name).slice().sort((a, b) =>
      Number(ON_SHELF.has(b.status)) - Number(ON_SHELF.has(a.status)));
    const waiting = rows.filter((p) => p.status === 'idea').length;
    return `<section class="plan-group">
      <h2>${esc(name === '—' ? 'Kanal ko‘rsatilmagan' : name)}
        <em>${rows.length} ta${waiting ? ` · ${waiting} tayyor emas` : ''}</em></h2>
      ${rows.map(planCard).join('')}
    </section>`;
  }).join('');

  $$('#plans-list [data-use-plan]').forEach((b) =>
    b.addEventListener('click', () => usePlan(b.dataset.usePlan, b)));

  $$('#plans-list [data-open-plan]').forEach((b) => b.addEventListener('click', () => {
    go('run');
    watch(b.dataset.openPlan, { reveal: true });
  }));

  $$('#plans-list [data-approve]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    b.textContent = 'Joylanmoqda…';
    try {
      await api(`/api/plans/${b.dataset.approve}/approve`, { method: 'POST' });
      await loadPlans();
      await loadJobs();
      toast('Joylandi — belgilangan vaqtda chiqadi');
    } catch (e) { b.disabled = false; b.textContent = 'Tasdiqlash va joylash'; alert(e.message); }
  }));

  $$('#plans-list [data-start-plan]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true;
    try {
      await api(`/api/plans/${b.dataset.startPlan}/start`, { method: 'POST' });
      await loadPlans();
      toast('Tayyorlash boshlandi');
    } catch (e) { b.disabled = false; toast(e.message); }
  }));

  $$('#plans-list [data-move-plan]').forEach((b) => b.addEventListener('click', async () => {
    const plan = PLANS.find((p) => p.id === b.dataset.movePlan);
    const answer = await ask({
      title: 'Vaqtini o‘zgartirish',
      ok: 'Saqlash',
      html: `<label class="f"><span>Qachon chiqsin</span>
        <input name="when" type="datetime-local" /></label>`,
    });
    if (!answer?.when) return;
    try {
      await api(`/api/plans/${plan.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ publish_at: new Date(answer.when).toISOString() }),
      });
      await loadPlans();
      toast('Vaqti o‘zgartirildi');
    } catch (e) { toast(e.message); }
  }));

  // Changed on the card rather than only at creation: "cheaper but slower" is a
  // decision worth revisiting when the slot moves.
  $$('#plans-list [data-batch]').forEach((sel) => sel.addEventListener('change', async () => {
    try {
      await api(`/api/plans/${sel.dataset.batch}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch: sel.value }),
      });
      await loadPlans();
      toast(sel.value === 'off' ? 'Oddiy yo‘l — tez, to‘liq narx'
        : sel.value === 'on' ? 'Batch — yarim narx, sekinroq' : 'Avtomatik');
    } catch (e) { toast(e.message); }
  }));

  $$('#plans-list [data-del-plan]').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('Rejani o‘chiramizmi?')) return;
    try {
      await api(`/api/plans/${b.dataset.delPlan}`, { method: 'DELETE' });
      await loadPlans();
    } catch (e) { toast(e.message); }
  }));
}

$('#plan-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const button = $('button', form);
  const timed = form.mode.value === 'timed';
  const when = form.publish_at.value;
  if (timed && !when) { toast('Chiqish vaqtini tanlang'); return; }
  button.disabled = true;
  $('#plan-error').classList.add('hidden');
  try {
    await api('/api/plans', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic: form.topic.value.trim(),
        channel: form.channel.value.trim(),
        image_provider: form.image_provider.value || '',
        // An absolute instant: a wall-clock time means nothing to a server in
        // another timezone, or to YouTube. Empty means the shelf.
        publish_at: timed ? new Date(when).toISOString() : '',
        video_format: form.video_format.value,
        target_seconds: Number(form.target_seconds.value) || 45,
        language: form.language.value,
        privacy: form.privacy.value,
        lead_minutes: Number(form.lead_minutes.value) || 240,
        approve: form.approve.checked,
        batch: form.batch.value,
      }),
    });
    // The channel is kept and the topic is cleared: a content plan is written
    // several rows at a time for one channel, so retyping its name for every
    // row is the tax this screen exists to remove.
    const channel = form.channel.value;
    form.reset();
    form.channel.value = channel;
    form.target_seconds.value = 45;
    syncPlanMode();
    form.topic.focus();
    await loadPlans();
    toast(timed ? 'Vaqtga qo‘yildi' : 'Rejaga qo‘shildi');
  } catch (err) {
    $('#plan-error').textContent = err.message;
    $('#plan-error').classList.remove('hidden');
  } finally {
    button.disabled = false;
  }
});

// Publishing settings only exist for a plan that publishes itself. Hidden
// rather than disabled: a field that cannot apply is not a field, it is a
// distraction on a screen already full of them.
function syncPlanMode() {
  const form = $('#plan-form');
  if (!form) return;
  const timed = form.mode.value === 'timed';
  $('#plan-timed').classList.toggle('hidden', !timed);
  form.publish_at.required = timed;
  $('button', form).textContent = timed ? 'Vaqtga qo‘yish' : 'Rejaga qo‘shish';
}

$('#plan-mode')?.addEventListener('change', syncPlanMode);
syncPlanMode();

// ══ YouTube ═══════════════════════════════════════════════════════
// The one thing this app does that other people can see, so it is never a side
// effect: a video goes up because it was published, not because it rendered.

let YT = { configured: false, connected: false };

async function loadYouTube() {
  try { YT = await api('/api/youtube'); } catch (e) { YT = { configured: false, connected: false }; }
  drawYouTube();
  drawReady();
}

function drawYouTube() {
  const box = $('#yt-box');
  if (!box) return;
  libCount('youtube', YT.connected ? (YT.channel_title || 'ulangan') : 'ulanmagan',
           !YT.connected);
  box.innerHTML = `
    <div class="row">
      <span>${YT.connected ? esc(YT.channel_title || 'Kanal') : 'Kanal ulanmagan'}</span>
      <span class="tag ${YT.connected ? 'done' : 'failed'}">${YT.connected ? 'ulangan' : 'yo‘q'}</span>
    </div>
    ${YT.note ? `<p class="note">${esc(YT.note)}</p>` : ''}
    ${YT.redirect_uri ? `
      <div class="row">
        <span style="font-size:.74rem;color:var(--muted)">Google'ga qo‘yiladigan manzil</span>
        <button class="copy" data-copy="${esc(YT.redirect_uri)}">Nusxalash</button>
      </div>
      <pre>${esc(YT.redirect_uri)}</pre>` : ''}
    <div class="model-acts">
      ${YT.connected
        ? '<button class="btn ghost" id="yt-off">Uzish</button>'
        : `<button class="btn primary" id="yt-on"${YT.configured ? '' : ' disabled'}>Kanalni ulash</button>`}
    </div>`;

  $$('#yt-box [data-copy]').forEach((b) =>
    b.addEventListener('click', () => copyText(b.dataset.copy, b)));
  $('#yt-on')?.addEventListener('click', connectYouTube);
  $('#yt-off')?.addEventListener('click', async () => {
    if (!confirm('YouTube ulanishini uzamizmi?')) return;
    await api('/api/youtube', { method: 'DELETE' });
    await loadYouTube();
    toast('Uzildi');
  });
}

async function connectYouTube() {
  try {
    const { url } = await api('/api/youtube/auth');
    // Another tab, not this one: coming back to a reloaded app mid-consent would
    // lose whatever was open here.
    const tab = open(url, 'sarideo-youtube', 'width=520,height=680');
    if (!tab) { location.href = url; return; }
    toast('Google oynasida ruxsat bering');
  } catch (e) { toast(e.message); }
}

// The consent page tells us when it is done, so the section refreshes itself.
addEventListener('message', (e) => {
  if (e.data?.sarideo === 'youtube') loadYouTube();
});

/** Publish a finished video, with the publishing pack already filled in. */
async function publishToYouTube(job) {
  if (!YT.connected) {
    go('library');
    $('#sec-youtube') && ($('#sec-youtube').open = true);
    toast('Avval YouTube kanalini ulang');
    return;
  }
  const meta = job.metadata?.youtube || {};
  const answer = await ask({
    title: 'YouTube\'ga joylash',
    ok: 'Joylash',
    html: `
      <label class="f"><span>Sarlavha</span>
        <input name="title" maxlength="100" value="${esc(meta.title || job.title || '')}" /></label>
      <label class="f"><span>Tavsif</span>
        <textarea name="description" rows="5" maxlength="5000">${esc(meta.description || '')}</textarea></label>
      <label class="f"><span>Teglar — vergul bilan</span>
        <input name="tags" value="${esc((meta.tags || []).join(', '))}" /></label>
      <label class="f"><span>Kim ko‘radi</span>
        <select name="privacy">
          <option value="private">Faqat men (private)</option>
          <option value="unlisted">Havola bilan (unlisted)</option>
          <option value="public">Hamma (public)</option>
        </select></label>
      <label class="f"><span>Qachon chiqsin — bo‘sh bo‘lsa darhol</span>
        <input name="when" type="datetime-local" /></label>
      <label class="sw"><input type="checkbox" name="with_thumbnail" checked /><i></i>
        <span>Tayyorlangan muqovani qo‘yish</span></label>
      <small class="note">Vaqt belgilasangiz YouTube videoni o‘sha paytda o‘zi
        chiqaradi — ilova o‘sha payt ishlab turishi shart emas.</small>`,
  });
  if (!answer) return;

  toast('YouTube\'ga yuklanmoqda…');
  try {
    const made = await api(`/api/jobs/${job.id}/publish`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: answer.title.trim(),
        description: answer.description,
        tags: answer.tags.split(',').map((t) => t.trim()).filter(Boolean),
        privacy: answer.privacy,
        // A local wall-clock time means nothing to YouTube; it is sent as the
        // instant it actually is.
        publish_at: answer.when ? new Date(answer.when).toISOString() : null,
        with_thumbnail: answer.with_thumbnail,
      }),
    });
    await loadJobs();
    drawReady();
    // Asked for public and given private is Google's rule for an unverified app,
    // and it is the kind of thing you find out weeks later if nobody says it.
    if (made.asked_privacy !== 'private' && made.privacy === 'private' && !made.publish_at) {
      toast('Joylandi, ammo private bo‘ldi — ilova Google tomonidan tasdiqlanmagan');
    } else {
      toast(made.publish_at ? 'Joylandi — belgilangan vaqtda chiqadi' : 'YouTube\'ga joylandi');
    }
  } catch (e) {
    alert(e.message);
  }
}

// ══ Shorts inside a long video ════════════════════════════════════
// A long video usually contains two or three moments that would stand alone,
// and finding them by scrubbing is the job nobody does. The app already knows
// where every sentence starts and how long it lasts, so a cut is chosen by
// scene rather than by dragging a handle — which is why every offer here comes
// with the real length attached instead of an estimate you discover afterwards.

const SHORT_MAX = 60;

/** Sum of the scene lengths in a range, in seconds. What the cut will run to. */
const spanSeconds = (scenes, from, to) => scenes
  .filter((s) => s.index >= from && s.index <= to)
  .reduce((total, s) => total + (s.duration || 0), 0);

async function cutShorts(job) {
  let scenes = [];
  try {
    scenes = (await api(`/api/jobs/${job.id}`)).scenes || [];
  } catch (e) {
    toast(e.message);
    return;
  }
  if (scenes.length < 2) { toast('Bu video bo‘linish uchun juda qisqa'); return; }

  const option = (s) => `<option value="${s.index}">${s.index + 1}. ${
    esc((s.narration || '').slice(0, 44))}${(s.narration || '').length > 44 ? '…' : ''}</option>`;

  const answer = await ask({
    title: 'Shortsga bo‘lish',
    ok: 'Kesib olish',
    html: `
      <div id="short-ai">
        <div class="short-acts">
          <button type="button" class="btn" id="short-ask">AI mos joylarni topsin</button>
          <button type="button" class="btn primary" id="short-all">Hammasini kesib ber</button>
        </div>
        <!-- One toggle for both ways in. Two of them, worded the same and set
             differently, is a question asked twice with two answers on screen. -->
        <label class="sw"><input type="checkbox" name="regenerate_images"
          id="short-all-redraw" checked /><i></i>
          <span>Rasmlarni vertikal kadr uchun qayta yaratish</span></label>
        <small class="note">«Hammasini» — videoda nechta mustaqil bo'lak bo'lsa,
          shuncha Short. Beshtami, o'ntami — o'zi hal qiladi. Har biri alohida
          loyiha bo'lib navbatga qo'yiladi.</small>
      </div>
      <div id="short-list"></div>
      <hr class="rule" />
      <p class="hint">Yoki o‘zingiz tanlang:</p>
      <label class="f"><span>Qaysi sahnadan</span>
        <select name="from_index">${scenes.map(option).join('')}</select></label>
      <label class="f"><span>Qaysi sahnagacha</span>
        <select name="to_index">${scenes.map(option).join('')}</select></label>
      <label class="f"><span>Nomi</span>
        <input name="title" maxlength="100" placeholder="Short nomi" /></label>
      <p class="short-len" id="short-len"></p>`,
    onOpen: () => {
      const from = $('#modal-body [name="from_index"]');
      const to = $('#modal-body [name="to_index"]');
      const out = $('#short-len');
      to.value = String(Math.min(scenes[scenes.length - 1].index, scenes[0].index + 2));

      const measure = () => {
        let a = Number(from.value);
        let b = Number(to.value);
        if (b < a) { b = a; to.value = String(a); }
        const secs = spanSeconds(scenes, a, b);
        // Said before you cut, not after: going over is the one mistake here
        // that costs a whole render.
        out.textContent = `Uzunligi: ${secs.toFixed(0)} soniya`
          + (secs > SHORT_MAX ? ` — Shorts uchun ${SHORT_MAX}s dan uzun` : '');
        out.classList.toggle('over', secs > SHORT_MAX);
        $('#modal-ok').disabled = secs <= 0;
      };
      from.addEventListener('change', measure);
      to.addEventListener('change', measure);
      measure();

      $('#short-all').addEventListener('click', async () => {
        const btn = $('#short-all');
        btn.disabled = true;
        btn.textContent = 'Kesilyapti…';
        try {
          const out = await api(`/api/jobs/${job.id}/shorts/all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              regenerate_images: $('#short-all-redraw').checked,
              video_format: '9:16',
            }),
          });
          // Closed with no value: the cutting is done, so the manual picker
          // below would only be a second, contradictory instruction.
          closeModal(null);
          await loadJobs();
          go('ready');
          toast(`${out.count} ta Short kesildi — navbatda render bo'lyapti`);
        } catch (e) {
          $('#short-list').innerHTML = `<p class="msg err">${esc(e.message)}</p>`;
          btn.disabled = false;
          btn.textContent = 'Hammasini kesib ber';
        }
      });

      $('#short-ask').addEventListener('click', async () => {
        const btn = $('#short-ask');
        btn.disabled = true;
        btn.textContent = 'Qidirilyapti…';
        try {
          const { shorts } = await api(`/api/jobs/${job.id}/shorts/suggest`, { method: 'POST' });
          $('#short-list').innerHTML = shorts.length ? shorts.map((s, i) => `
            <div class="short-pick" data-from="${s.from_index}" data-to="${s.to_index}">
              <b>${esc(s.title || `Short ${i + 1}`)}</b>
              <span class="short-meta">${s.seconds}s · ${s.scene_count} sahna ·
                ${s.from_index + 1}–${s.to_index + 1}</span>
              ${s.hook ? `<span class="short-hook">“${esc(s.hook)}”</span>` : ''}
              ${s.why ? `<span class="short-why">${esc(s.why)}</span>` : ''}
              <button type="button" class="chip" data-take="${i}">Shuni kesish</button>
            </div>`).join('')
            : '<p class="note">Mos keladigan tugallangan bo‘lak topilmadi.</p>';

          $$('#short-list [data-take]').forEach((take) => take.addEventListener('click', () => {
            const card = take.closest('.short-pick');
            closeModal({
              from_index: card.dataset.from,
              to_index: card.dataset.to,
              title: card.querySelector('b').textContent,
              regenerate_images: $('#short-all-redraw').checked,
            });
          }));
        } catch (e) {
          $('#short-list').innerHTML = `<p class="msg err">${esc(e.message)}</p>`;
        } finally {
          btn.disabled = false;
          btn.textContent = 'AI mos joylarni topsin';
        }
      });
    },
  });
  if (!answer) return;

  try {
    const made = await api(`/api/jobs/${job.id}/shorts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from_index: Number(answer.from_index),
        to_index: Number(answer.to_index),
        title: answer.title || '',
        regenerate_images: !!answer.regenerate_images,
        video_format: '9:16',
      }),
    });
    await loadJobs();
    go('run');
    watch(made.id, { reveal: true });
    toast('Short kesildi — render boshlandi');
  } catch (e) {
    alert(e.message);
  }
}

// ══ API kalitlari ═════════════════════════════════════════════════
// One key means one per-minute allowance, and a fifty-scene video spends most of
// its time waiting for that allowance to refill. Several keys per provider is
// what turns the wait into work, so this section is about supply: how many keys
// there are, which are ready this second, and which is benched and why.

let KEYS = { providers: [] };

const KEY_NAMES = {
  gemini: 'Gemini',
  anthropic: 'Claude (Anthropic)',
  openai: 'OpenAI',
  elevenlabs: 'ElevenLabs',
  fal: 'fal.ai',
};

async function loadKeys() {
  try { KEYS = await api('/api/keys'); } catch (e) { KEYS = { providers: [] }; }
  drawKeys();
}

// ── the Flow queue ────────────────────────────────────────────────────────────
// Prompts the app is not going to draw itself. The extension normally empties
// this without anyone looking at it; this panel exists so that it still works
// when the extension is not running, and so a render that is standing still can
// be seen to be waiting rather than stuck.
let FLOW = { tasks: [], waiting: 0 };

/** A textarea tall enough for what is in it. A prompt you cannot see all of is
 *  a prompt you cannot check. */
function grow(el) {
  el.style.height = 'auto';
  // A closed <details> has no layout, so `scrollHeight` reads 0 — and a height
  // measured then would pin the field shut for good, the moment the section is
  // finally opened. Left alone, it keeps its `rows` until it can be measured.
  if (!el.scrollHeight) return;
  el.style.height = `${Math.min(el.scrollHeight, 260)}px`;
}

// ── space, and getting it back ────────────────────────────────────
// A project's bytes live in four places — the folder on disk, the copy kept in
// the database, the file you handed over to be dubbed, and the bucket — and
// "why is this still full" has a different answer in each. So they are counted
// apart rather than added up into one number that explains nothing.

const size = (n) => {
  const b = Number(n) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`;
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`;
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

async function loadSpace() {
  try {
    state.space = await api('/api/storage');
  } catch { return; }
  drawSpace();
}

function drawSpace() {
  const s = state.space;
  const box = $('#space-box');
  if (!box || !s) return;
  const total = s.project_bytes + s.media_bytes + s.upload_bytes;
  libCount('space', total ? size(total) : '');

  box.innerHTML = `
    <div class="space-rows">
      <div><span>Loyihalar</span><b>${s.jobs} ta</b></div>
      <div><span>Rasm va videolar (disk)</span><b>${size(s.project_bytes)}</b></div>
      <div><span>Saqlangan nusxalar (baza)</span><b>${size(s.media_bytes)}</b></div>
      <div><span>Siz yuklagan fayllar</span><b>${size(s.upload_bytes)}</b></div>
      <div><span>Baza fayli</span><b>${size(s.db_bytes)}</b></div>
    </div>
    <p class="note">«Saqlangan nusxalar» — servis qayta ishga tushganda disk
      tozalanadi, shuning uchun rasmlar bazaga ham yoziladi. Diskda 0 bo‘lsa
      ham videolaringiz shu yerdan qayta tiklanadi.</p>
    <button class="btn danger" id="space-wipe">Barcha loyihalarni o‘chirish</button>
    <p class="note">Herolar, brend, musiqa va API kalitlari o‘chmaydi — faqat
      loyihalar va ularning fayllari.</p>`;

  $('#space-wipe').addEventListener('click', async () => {
    // Typed, not tapped. This is the only button in the app that cannot be
    // undone by anything, and a mis-tap on a phone is one pixel wide.
    const answer = await ask({
      title: `${s.jobs} ta loyiha o‘chiriladi`,
      ok: "O‘chirish",
      html: `<p class="note">Hamma videolar, rasmlar, ovozlar va matnlar
          butunlay o‘chadi. Buni ortga qaytarib bo‘lmaydi.</p>
        <label class="f"><span>Tasdiqlash uchun <b>hammasi</b> deb yozing</span>
          <input name="word" placeholder="hammasi" autocomplete="off" /></label>`,
    });
    if (!answer) return;
    if ((answer.word || '').trim().toLowerCase() !== 'hammasi') {
      toast('Tasdiqlanmadi — hech narsa o‘chirilmadi');
      return;
    }
    const btn = $('#space-wipe');
    btn.disabled = true;
    btn.textContent = 'O‘chirilmoqda…';
    try {
      const out = await api('/api/jobs?confirm=hammasi', { method: 'DELETE' });
      state.activeId = null;
      ED.job = null;
      $('#stage').classList.add('hidden');
      $('#editor').classList.add('hidden');
      $('#run-live').innerHTML = '';
      if (state.poll) { clearInterval(state.poll); state.poll = null; }
      await loadJobs();
      await loadSpace();
      drawDock();
      toast(`${out.deleted} ta loyiha o‘chirildi`);
    } catch (e) {
      toast(e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Barcha loyihalarni o‘chirish';
    }
  });
}

// ── the Flow Agent extension ──────────────────────────────────────
// Flow Agent's own extension has its server address compiled into two files
// and no field in its panel to change either, so pointing it at a backend of
// your own otherwise means editing a zip by hand from written instructions.
// Sarideo hands out the same folder with the edits already made.

async function loadAgent() {
  try {
    state.agent = await api('/api/flow-agent');
  } catch { state.agent = null; }
  drawAgent();
}

function drawAgent() {
  const a = state.agent;
  const box = $('#agent-box');
  if (!box) return;
  if (!a) { box.innerHTML = ''; libCount('agent', ''); return; }

  libCount('agent', a.host || 'sozlanmagan', !a.ready || a.local);
  box.innerHTML = `
    <div class="agent-where">
      <span>Manzil</span>
      <b>${esc(a.host || '— sozlanmagan —')}</b>
    </div>
    ${!a.ready ? `<p class="msg warn">${esc(a.why)}</p>` : ''}
    ${a.local ? `<p class="msg warn">Bu manzil faqat shu kompyuterda ishlaydi.
      Telefondan foydalanish uchun Flow Agent'ni Railway'da ishga tushiring va
      Sarideo'ning <code>FLOW_AGENT_URL</code> o'zgaruvchisiga o'sha manzilni
      yozing.</p>` : ''}
    ${a.ready ? `
      <a class="btn primary" href="/api/flow-agent/extension.zip" download>
        Kengaytmani yuklab olish</a>
      <ol class="agent-steps">
        <li>Faylni yechib oling (unzip).</li>
        <li>Chrome'da <code>chrome://extensions</code> ni oching,
          <b>Developer mode</b> ni yoqing.</li>
        <li><b>Load unpacked</b> — yechilgan papkani tanlang.</li>
        <li><code>labs.google/fx/tools/flow</code> ni oching va o'sha varaqni
          yopmang.</li>
        <li>Kengaytma panelida <b>Connected</b> yozuvi chiqishi kerak.</li>
        <li>Shu yerdan yuqoridagi «Rasmlarni kim yasaydi» ni
          <b>Flow Agent</b> qiling.</li>
      </ol>
      <p class="note">Kengaytma o'zi ulanadi — kompyuteringizda hech qanday port
        ochish shart emas. Rasmlarni Google'dan sizning brauzeringiz so'raydi,
        shuning uchun hisobingiz va IP manzilingiz o'zgarmaydi.</p>
      ${a.keyed ? `<p class="note warnish">Flow Agent kalit bilan himoyalangan,
        shuning uchun kalit shu faylning ichiga yoziladi —
        <b>faylni birovga bermang</b>.</p>` : ''}` : ''}`;
}

let flowPoll = null;

async function loadFlow() {
  try { FLOW = await api('/api/flow/tasks'); } catch (e) { FLOW = { tasks: [], waiting: 0 }; }
  drawFlow();
  // Polled only when there is a reason to: something is queued, or a video is
  // being made and might queue something. An idle library asks nothing.
  const want = (FLOW.waiting > 0) || !!state.activeId;
  if (want && !flowPoll) flowPoll = setInterval(loadFlow, 5000);
  if (!want && flowPoll) { clearInterval(flowPoll); flowPoll = null; }
}

function drawFlow() {
  const box = $('#flow-box');
  if (!box) return;
  const tasks = FLOW.tasks || [];
  libCount('flow', tasks.length ? `${tasks.length} ta kutilyapti`
    : FLOW.on ? 'yoqilgan' : 'o‘chirilgan', tasks.length > 0);

  // The switch lives here, next to the queue it fills, because this is where
  // somebody looking for "how do I turn Flow on" will look. It is a mode the app
  // is in, not a box to re-tick on every video, so it is stored.
  const sw = $('#flow-switch');
  if (sw) {
    sw.innerHTML = `
      <label class="sw"><input type="checkbox" id="flow-on"${FLOW.on ? ' checked' : ''} /><i></i>
        <span>Rasmlarni Flow'da yasash</span></label>
      <p class="note">${FLOW.on
        ? `Yoqilgan — har sahnaning prompti pastdagi navbatga tushadi, API'ga
           murojaat qilinmaydi. O‘chirsangiz <b>${esc(FLOW.off_provider || 'gemini')}</b>
           ga qaytadi.`
        : `O‘chirilgan — rasmlar <b>${esc(FLOW.off_provider || 'gemini')}</b> orqali,
           API hisobidan yasaladi. Yoqsangiz ular brauzeringizga o‘tadi.`}</p>`;
    $('#flow-on').addEventListener('change', async (e) => {
      const on = e.target.checked;
      e.target.disabled = true;
      try {
        FLOW = await api('/api/flow/mode', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ on }),
        });
        toast(on ? 'Flow yoqildi' : 'Flow o‘chirildi');
        drawFlow();
        loadHealth();
      } catch (err) {
        toast(err.message);
        e.target.checked = !on;
        e.target.disabled = false;
      }
    });
  }

  box.innerHTML = tasks.length ? tasks.map((t) => `
    <div class="flow-row" data-task="${esc(t.id)}">
      <div class="flow-what">
        <b>${esc(t.title || t.job_id)} · ${t.scene + 1}-sahna</b>
        <span class="flow-meta">${esc(t.aspect)}${
          t.status === 'taken' ? ` · ${esc(t.taken_by)} olib ketdi` : ' · navbatda'}</span>
        <!-- Editable in place. These prompts are written by a model, and the
             moment to fix a bad one is while it is still a prompt. -->
        <textarea class="flow-prompt" data-flow="text" rows="3"
          aria-label="${t.scene + 1}-sahna prompti">${esc(t.prompt)}</textarea>
      </div>
      <div class="flow-acts">
        <button class="chip" data-flow="save" hidden>Saqlash</button>
        <button class="chip" data-flow="copy">Promptni nusxalash</button>
        <label class="chip file"><input type="file" accept="image/*" data-flow="give" />
          <span>Rasmni yuklash</span></label>
        <button class="chip danger" data-flow="skip">Bekor qilish</button>
      </div>
    </div>`).join('')
    : '<p class="key-none">Hech narsa kutilmayapti.</p>';

  // Measured again when the section is opened, which is the first moment the
  // fields have a size at all.
  const section = $('#sec-flow');
  if (section && !section.dataset.grows) {
    section.dataset.grows = '1';
    section.addEventListener('toggle', () => {
      if (section.open) $$('#flow-box textarea[data-flow="text"]').forEach(grow);
    });
  }

  $$('#flow-box [data-flow]').forEach((el) => {
    const row = el.closest('.flow-row');
    const id = row.dataset.task;
    if (el.dataset.flow === 'give') {
      el.addEventListener('change', () => flowGive(id, el.files[0]));
    } else if (el.dataset.flow === 'text') {
      // The save button appears only once something has changed, so a row you
      // are only reading stays a row you are only reading.
      const was = el.value;
      const save = $('[data-flow="save"]', row);
      el.addEventListener('input', () => {
        save.hidden = el.value.trim() === was.trim() || el.value.trim().length < 2;
        grow(el);
      });
      grow(el);
    } else {
      el.addEventListener('click', () => flowAct(id, el.dataset.flow));
    }
  });
}

async function flowAct(id, act) {
  const task = (FLOW.tasks || []).find((t) => t.id === id);
  if (!task) return;
  const row = $(`.flow-row[data-task="${id}"]`);
  if (act === 'save') {
    const text = $('[data-flow="text"]', row).value.trim();
    try {
      await api(`/api/flow/tasks/${id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: text }),
      });
      toast('Prompt yangilandi');
    } catch (e) {
      toast(e.message);
    }
    await loadFlow();
    return;
  }
  if (act === 'copy') {
    // Whatever is in the box now, not what arrived — you may have just edited it.
    const live = $('[data-flow="text"]', row)?.value || task.prompt;
    try {
      await navigator.clipboard.writeText(live);
      toast('Prompt nusxalandi');
    } catch {
      // Clipboard access is refused on an insecure origin, which a self-hosted
      // app on a bare IP often is. Selecting the text is the honest fallback.
      toast('Nusxalab bo‘lmadi — promptni belgilab oling');
    }
    return;
  }
  if (act === 'skip') {
    if (!confirm('Bu sahna rasmsiz qoladi. Davom etamizmi?')) return;
    await api(`/api/flow/tasks/${id}/fail`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'Qo‘lda bekor qilindi' }),
    });
    toast('Bekor qilindi');
    await loadFlow();
  }
}

async function flowGive(id, file) {
  if (!file) return;
  const body = new FormData();
  body.append('image', file);
  try {
    await api(`/api/flow/tasks/${id}/image`, { method: 'POST', body });
    toast('Rasm yuborildi');
  } catch (e) {
    toast(e.message);
  }
  await loadFlow();
}

/** "2 daqiqa" rather than "132s" — a cooldown is read, not measured. */
function coolText(seconds) {
  if (seconds <= 0) return '';
  if (seconds < 90) return `${seconds} soniya`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} daqiqa`;
  return `${Math.round(seconds / 3600)} soat`;
}

function drawKeys() {
  const box = $('#keys-box');
  if (!box) return;

  const all = KEYS.providers || [];
  const stored = all.reduce((n, p) => n + (p.keys_list || []).length, 0);
  // Only a provider the app is set to call counts as missing. The badge is read
  // at a glance from the folded library, so it has to mean something.
  const inUse = new Set(Object.values(KEYS.in_use || {}));
  const short = all.filter((p) => p.keys === 0 && inUse.has(p.provider)).length;
  libCount('keys', short ? `${short} ta yetishmaydi` : stored ? `${stored} ta` : 'muhitdan',
           short > 0);

  const picker = $('#key-provider');
  if (picker && !picker.options.length) {
    picker.innerHTML = all
      .map((p) => `<option value="${p.provider}">${esc(KEY_NAMES[p.provider] || p.provider)}</option>`)
      .join('');
  }

  // Which providers this deployment actually calls. A missing ElevenLabs key is
  // nothing to worry about when the voice comes from Gemini, and colouring it red
  // anyway would teach you to ignore the colour.
  const needed = new Set(Object.values(KEYS.in_use || {}));

  box.innerHTML = all.map((prov) => {
    const rows = prov.keys_list || [];
    // A key that is cooling is still a key. Saying "3 ta, 2 tayyor" answers the
    // only question that matters mid-render — is anything free right now?
    const supply = prov.keys === 0
      ? (needed.has(prov.provider)
          ? '<span class="tag failed">kalit yo‘q — kerak</span>'
          : '<span class="tag">ishlatilmaydi</span>')
      : `<span class="tag ${prov.ready ? 'done' : 'warn'}">${prov.keys} ta, ${prov.ready} tayyor</span>`;
    return `
      <div class="key-prov">
        <div class="row">
          <span>${esc(KEY_NAMES[prov.provider] || prov.provider)}</span>
          ${supply}
        </div>
        ${prov.from_env ? `<p class="note">${esc(prov.env_var)} muhitdan olinadi — pastga qo‘shsangiz, o‘shalar ishlatiladi.</p>` : ''}
        ${rows.length ? rows.map((k) => `
          <div class="key-row${k.enabled ? '' : ' off'}" data-key="${k.id}">
            <div class="key-what">
              <b>${esc(k.label || k.mask || 'nomsiz')}</b>
              <!-- The ends and the length, always. When a provider says a key is
                   invalid the only question is whether this is the key on the
                   dashboard, and a name somebody typed cannot answer it. -->
              ${k.mask ? `<span class="key-id">${k.label ? `${esc(k.mask)} · ` : ''}${k.length} belgi</span>` : ''}
              <span class="key-stat">${k.uses} marta ishlatildi${k.fails ? ` · ${k.fails} xato` : ''}</span>
              ${k.cooldown_seconds > 0
                ? `<span class="key-cool">${coolText(k.cooldown_seconds)} dam oladi</span>` : ''}
              ${k.last_error ? `<span class="key-err">${esc(k.last_error)}</span>` : ''}
              ${k.last_error ? `<span class="key-tip">Yuqoridagi boshi-oxiri va uzunligi
                provayder saytidagi kalitga to‘g‘ri kelyaptimi? Kelmasa —
                «Qayta qo‘yish».</span>` : ''}
            </div>
            <div class="key-acts">
              <button class="chip" data-act="test">Tekshirish</button>
              <button class="chip" data-act="paste">Qayta qo‘yish</button>
              <button class="chip" data-act="toggle">${k.enabled ? 'O‘chirish' : 'Yoqish'}</button>
              ${k.cooldown_seconds > 0 ? '<button class="chip" data-act="wake">Darhol ishlat</button>' : ''}
              <button class="chip danger" data-act="del">Olib tashlash</button>
            </div>
          </div>`).join('') : '<p class="key-none">Hali kalit qo‘shilmagan</p>'}
      </div>`;
  }).join('');

  $$('#keys-box .key-row .chip').forEach((btn) => {
    btn.addEventListener('click', () => keyAction(
      btn.closest('.key-row').dataset.key, btn.dataset.act, btn));
  });
  if (short) {
    box.insertAdjacentHTML('afterbegin',
      `<p class="msg warn">${short} ta provayderda kalit yo‘q — shu bosqichlar ishlamaydi.</p>`);
  }
}

/** Every key call is JSON, and `api` sends exactly the headers it is given. */
const keyApi = (path, method, body) => api(path, {
  method,
  ...(body ? { headers: { 'Content-Type': 'application/json' },
               body: JSON.stringify(body) } : {}),
});

async function keyAction(id, act, btn) {
  const row = (KEYS.providers || [])
    .flatMap((p) => p.keys_list || []).find((k) => k.id === id);
  if (!row) return;
  const was = btn.textContent;
  btn.disabled = true;
  try {
    if (act === 'del') {
      if (!confirm('Kalit olib tashlanadi. Davom etamizmi?')) { btn.disabled = false; return; }
      await keyApi(`/api/keys/${id}`, 'DELETE');
      toast('Olib tashlandi');
    } else if (act === 'toggle') {
      await keyApi(`/api/keys/${id}`, 'PATCH', { enabled: !row.enabled });
      toast(row.enabled ? 'O‘chirildi' : 'Yoqildi');
    } else if (act === 'paste') {
      // Replacing beats deleting and re-adding: the key keeps its place, and a
      // fresh secret comes with a cleared cooldown, so it is usable at once.
      const next = prompt('Kalitni qaytadan qo‘ying:', '');
      if (!next || !next.trim()) { btn.disabled = false; return; }
      await keyApi(`/api/keys/${id}`, 'PATCH', { secret: next });
      btn.textContent = 'Tekshirilyapti…';
      const out = await keyApi(`/api/keys/${id}/test`, 'POST');
      toast(out.detail || (out.ok ? 'Ishlaydi' : 'Ishlamadi'));
    } else if (act === 'wake') {
      await keyApi(`/api/keys/${id}`, 'PATCH', { clear_cooldown: true });
      toast('Yana ishlatiladi');
    } else if (act === 'test') {
      btn.textContent = 'Tekshirilyapti…';
      const out = await keyApi(`/api/keys/${id}/test`, 'POST');
      toast(out.detail || (out.ok ? 'Ishlaydi' : 'Ishlamadi'));
    }
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = was;
    await loadKeys();
    await loadHealth();
  }
}

/** A pasted key, as the provider needs it — see `keys.clean` on the server.
 *
 * Copying a key on a phone is lossy in ways that say nothing about the key: the
 * dashboard wraps it so the paste carries newlines, a long-press takes the
 * quotes around it, the keyboard adds a trailing space.
 */
const cleanKey = (text) => String(text ?? '')
  .trim()
  // Opening and closing are not always the same character — a phone turns a pair
  // of straight quotes into “ … ” — so the pairs are matched, not just repeated.
  .replace(/^(["'`])([\s\S]*)\1$/, '$2')
  .replace(/^‘([\s\S]*)’$/, '$1')
  .replace(/^“([\s\S]*)”$/, '$1')
  .replace(/^«([\s\S]*)»$/, '$1')
  .replace(/\s+/g, '');

$('#key-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const body = {
    provider: form.provider.value,
    secret: cleanKey(form.secret.value),
    label: form.label.value.trim(),
  };
  // Only "you typed nothing" is checked here. There is no length or shape a key
  // has to have — Google alone issues both `AIza…` and `AQ.…` — so anything
  // stricter would just refuse real keys. The provider decides, when it is used.
  if (!body.secret) { toast('Kalit kiritilmadi'); return; }
  const btn = form.querySelector('button');
  btn.disabled = true;
  try {
    await keyApi('/api/keys', 'POST', body);
    // Cleared straight away: a key left sitting in a visible field is a key on
    // screen for anyone standing behind you.
    form.secret.value = '';
    form.label.value = '';
    toast('Kalit qo‘shildi');
    await loadKeys();
    await loadHealth();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
});

// ── boot ──────────────────────────────────────────────────────────
(async function boot() {
  try {
    await loadHealth();
    // Before the jobs land, so the strip is never briefly the wrong shape.
    const shut = projectsChoice();
    if (shut !== null) setProjectsShut(shut === '1', false);
    wireLibrarySections();
    wireFades();
    await Promise.all([loadHeroes(), loadMusic(), loadAssets(), loadJobs()]);
    await Promise.all([loadBrand(), loadModels(), loadProfiles(), loadChat(),
                       loadYouTube(), loadPlans(), loadKeys(), loadFlow(),
                       loadSpace(), loadAgent()]);
    applyBrandToComposer();
    // Only reattach to work that is actually moving. A draft waiting on review
    // is not urgent, and unfolding it on load would bury the composer.
    drawDock();
    drawSummary();
    // Rendering is server-side, so work carries on with the phone locked, the
    // tab closed, or the container replaced. What follows is about picking the
    // thread back up: the job you were last following wins, because "the one
    // I started" is what you came back for.
    const mine = state.jobs.find((j) => j.id === remembered());
    const busy = state.jobs.find((j) => BUSY.includes(j.status));

    if (mine && BUSY.includes(mine.status)) {
      go('run');
      watch(mine.id);
    } else if (mine && SETTLED.includes(mine.status)) {
      // It finished while you were away. Say so and show it, rather than
      // dropping you on the composer as though nothing had happened.
      remember(null);
      go('run');
      state.activeId = mine.id;
      state.reveal = true;
      $('#stage').classList.remove('hidden');
      tick();
      toast(mine.status === 'done' ? 'Videongiz tayyor bo‘libdi' : 'Ish tugagan');
    } else if (busy) {
      go('run');
      watch(busy.id);
    }
  } catch (e) {
    document.body.insertAdjacentHTML('afterbegin',
      `<p class="msg err" style="margin:16px">Ilova yuklanmadi: ${esc(e.message)}</p>`);
  }
})();
