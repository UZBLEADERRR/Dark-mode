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
  review: "ko'rib chiqish", done: 'tayyor', failed: 'xato',
};

const BUSY = ['queued', 'running', 'rendering'];
const SETTLED = ['done', 'failed', 'review'];

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

// ── navigation ────────────────────────────────────────────────────
function go(view) {
  state.view = view;
  $$('.view').forEach((v) => v.classList.toggle('on', v.id === `view-${view}`));
  $$('#topnav button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.go === view));
  closeSheet();
  drawDock();
  scrollTo({ top: 0, behavior: 'instant' in document.documentElement.style ? 'instant' : 'auto' });
  if (view === 'edit' || view === 'ready') loadJobs();
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

function drawDock() {
  let items = DOCK[state.view] || [];
  if (state.view === 'create' && state.mode === 'dub') {
    items = items.filter((item) => DUB_TOOLS.has(item.id));
  }
  const dock = $('#dock');
  dock.classList.toggle('hidden', !items.length);
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
  const chips = (dub ? [
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

  const fill = (sel, map, preferred) => {
    const pick = map[preferred] ? preferred : Object.keys(map).find((k) => map[k]);
    $(sel).innerHTML = Object.entries(map).map(([n, ok]) =>
      `<option value="${n}"${ok ? '' : ' disabled'}${n === pick ? ' selected' : ''}>${n}${ok ? '' : ' — kalit yo‘q'}</option>`
    ).join('');
  };
  fill('#image_provider', h.image_providers, h.defaults.image_provider);
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

  $('#dub_source').innerHTML = '<option value="">avtomatik aniqlansin</option>' +
    h.languages.map((l) => `<option value="${esc(l.id)}">${esc(l.label)}</option>`).join('');
  $('#mode-tabs [data-mode="dub"]').classList.toggle('hidden', !h.can_dub);

  $('#subtitle_style').innerHTML = (h.caption_templates || []).map((t) =>
    `<option value="${esc(t.id)}"${t.id === 'bold' ? ' selected' : ''}>${esc(t.label)}</option>`).join('');

  const checks = [
    ['ffmpeg', h.ffmpeg],
    [`skript — ${h.llm_provider}`, h.llm],
    ['transkripsiya', h.transcription],
    ...Object.entries(h.image_providers).map(([n, v]) => [`rasm — ${n}`, v]),
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

  const core = h.ffmpeg && h.llm && Object.values(h.image_providers).some(Boolean);
  const voice = Object.values(h.tts_providers).some(Boolean);
  const pill = $('#health-pill');
  pill.className = `health-pill ${core && voice ? 'ok' : core ? 'part' : 'bad'}`;
  pill.textContent = core && voice ? 'tayyor' : core ? 'ovoz yo‘q' : 'sozlash kerak';
  pill.title = checks.map(([l, ok]) => `${l}: ${ok ? 'bor' : "yo'q"}`).join('\n');
}

// ── heroes ────────────────────────────────────────────────────────
async function loadHeroes() {
  state.heroes = await api('/api/heroes');

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

  $('#models-box').innerHTML = `
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
        body: JSON.stringify({ models: readModelRows(), voices: state.models.voices || {} }),
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
    ${b.logo_asset_id ? `<div class="f2">
      ${brandSlider('logo_size', 'Logotip kattaligi', 0.03, 0.35, 0.01, b.logo_size)}
      ${brandSlider('logo_opacity', 'Shaffofligi', 0.1, 1, 0.05, b.logo_opacity)}
    </div>
    <div class="f2">
      ${brandSlider('logo_x', 'Gorizontal', 0, 1, 0.01, b.logo_x)}
      ${brandSlider('logo_y', 'Vertikal', 0, 1, 0.01, b.logo_y)}
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
  }));
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
  $('#script-box').classList.toggle('hidden', !script);
  $('#dub-box').classList.toggle('hidden', !dub);
  // Dubbing has no picture to stage and no script to direct.
  $('#action-box').classList.toggle('hidden', dub);
  $('#animate-row').classList.toggle('hidden', dub);
  $('#animate-note').classList.toggle('hidden', dub);
  // Dubbing replaces the voice on a picture that already exists, so there is no
  // topic to write and nothing for the AI to imagine.
  $('.composer .prompt').classList.toggle('hidden', dub);
  $('#topic').closest('.prompt').classList.toggle('hidden', dub);
  // With a script supplied, length comes from the words, not from a slider.
  $('#duration-row').classList.toggle('hidden', script || dub || $('#use_upload').checked);
  $('#topic').placeholder = script
    ? 'Video nima haqida — bir qatorda (ixtiyoriy kontekst)'
    : "Ipak yo'li bo'ylab sayohat qilgan uch savdogarning haqiqiy tarixi…";
  $('#topic').rows = script ? 1 : 2;
  $('#submit-btn').textContent = dub ? 'Dublyaj qilish' : 'Video yaratish';
  $('.composer h1').textContent = dub
    ? 'Qaysi videoni tarjima qilamiz?' : 'Nima haqida video?';
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
  go('edit');
  watch(job.id, { reveal: true });
}

$('#submit-btn').addEventListener('click', async () => {
  const btn = $('#submit-btn');
  const err = $('#create-error');
  err.classList.add('hidden');

  if (state.mode === 'dub') {
    btn.disabled = true;
    btn.textContent = 'Yuborilmoqda…';
    try {
      await submitDub();
    } catch (e) {
      err.textContent = e.message;
      err.classList.remove('hidden');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Dublyaj qilish';
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
    go('edit');
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
  if (job.scene_count) meta.push(`${job.scene_count} sahna`);

  // How long since the job last said anything. A provider that accepts a
  // request and never answers used to look exactly like a frozen app, so the
  // wait is now on screen — with a way out of it once it stops being normal.
  const stamp = `${job.id}:${job.updated_at}`;
  if (!busy) { state.mark = null; state.markAt = 0; }
  else if (state.mark !== stamp) { state.mark = stamp; state.markAt = Date.now(); }
  const idle = busy && state.markAt ? Math.floor((Date.now() - state.markAt) / 1000) : 0;

  p.push(`<div class="stage-head">
      <h2>${esc(job.title || job.topic || 'Video')}</h2>
      <span class="stage-pct">${job.progress}%</span>
    </div>
    <div class="track ${esc(job.status)}${busy ? ' live' : ''}"><i style="width:${job.progress}%"></i></div>
    <p class="step${busy ? ' busy' : ''}">${esc(meta.join(' · '))}</p>`);

  if (busy && idle >= IDLE_WARN) {
    p.push(`<div class="stall${idle >= IDLE_STOP ? ' long' : ''}">
      <span>${idle >= IDLE_STOP
        ? `Provayder ${idle} soniyadan beri javob bermayapti.`
        : `Kutilmoqda… ${idle} s`}</span>
      ${idle >= IDLE_STOP
        ? `<button class="btn ghost sm" data-stop="${esc(job.id)}">To‘xtatish</button>` : ''}
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
    p.push(`<video controls playsinline preload="metadata" src="${esc(job.video_url)}"></video>
      <div class="acts">
        <a class="btn primary" href="${esc(job.download_url || job.video_url)}" download>Videoni yuklab olish</a>
        ${job.subtitle_url ? `<a class="btn" href="${esc(job.subtitle_url)}" download>Subtitr .srt</a>` : ''}
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

  if (job.logs?.length) {
    p.push(`<details class="fold"${busy ? ' open' : ''}><summary>Jurnal</summary>
      <div class="fold-body"><div class="logs">${esc(job.logs.join('\n'))}</div></div></details>`);
  }

  $('#stage').innerHTML = p.join('');
  $$('#stage [data-go]').forEach((b) => b.addEventListener('click', () => go(b.dataset.go)));
  $$('#stage [data-stop]').forEach((b) => b.addEventListener('click', () => stopJob(b.dataset.stop, b)));
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
function syncEditor(job) {
  const on = job.kind !== 'dub'
    && (job.status === 'review' || job.status === 'done') && job.scenes?.length;
  $('#editor').classList.toggle('hidden', !on);
  if (!on) {
    state.drawn = null;
    if (ED.job) { ED.job = null; drawDock(); }
    return;
  }

  $('#editor-title').textContent = `Sarideo · ${job.scenes.length} sahna`;
  $('#editor-note').textContent = job.status === 'review'
    ? 'Rasm ustidan sudrab joylashtiring. O‘zgarishlar o‘zi saqlanadi.'
    : 'Video tayyor. O‘zgartirsangiz qayta render qiling.';
  $('#render-btn').textContent = job.status === 'review' ? 'Render qilish' : 'Qayta render';

  const stamp = `${job.id}:${job.updated_at}`;
  if (state.drawn === stamp) return;
  // Never redraw over work that has not reached the server yet, or over
  // someone who is mid-sentence in one of the fields.
  if (ED.dirty.size || ED.styleDirty || ED.saving) return;
  if (state.drawn?.startsWith(`${job.id}:`) && document.activeElement?.closest('.panel')) return;
  state.drawn = stamp;
  buildStudio(job);
}

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

// ── scene preview ─────────────────────────────────────────────────
// Playing the scene's own voice-over and driving the canvas from its clock is
// the only way to check that a layer lands on the word it is meant to land on.
const PREVIEW = { audio: null, raf: 0, index: -1 };

function stopPreview() {
  PREVIEW.audio?.pause();
  cancelAnimationFrame(PREVIEW.raf);
  PREVIEW.raf = 0;
  PREVIEW.index = -1;
  $('#play-icon').innerHTML = '<path d="M8 5.5l11 6.5-11 6.5z"/>';
  $('#play-btn span').textContent = 'Eshitish';
  $('#scrub-fill').style.width = '0%';
  if (ED.job) { drawLayers(); drawCaptionSample(); }
}

function togglePreview() {
  const s = scene();
  if (!s?.audio_url) { toast('Bu sahnada hali ovoz yo‘q'); return; }
  if (PREVIEW.raf && PREVIEW.index === ED.i) { stopPreview(); return; }

  stopPreview();
  PREVIEW.audio = PREVIEW.audio || new Audio();
  PREVIEW.audio.src = s.audio_url;
  PREVIEW.audio.currentTime = 0;
  PREVIEW.index = ED.i;
  $('#play-icon').innerHTML = '<path d="M8 5h3v14H8zM13 5h3v14h-3z"/>';
  $('#play-btn span').textContent = 'To‘xtatish';

  PREVIEW.audio.play().then(() => {
    const step = () => {
      if (PREVIEW.index !== ED.i) { stopPreview(); return; }
      const t = PREVIEW.audio.currentTime;
      const span = Math.max(0.1, s.duration || PREVIEW.audio.duration || 1);
      $('#scrub-fill').style.width = `${Math.min(100, (t / span) * 100)}%`;
      drawLayers(t);
      drawCaptionSample(t);
      if (PREVIEW.audio.ended) { stopPreview(); return; }
      PREVIEW.raf = requestAnimationFrame(step);
    };
    PREVIEW.raf = requestAnimationFrame(step);
  }).catch(() => {
    stopPreview();
    toast('Brauzer ovozni to‘sdi — kadrga bosing');
  });
}

$('#play-btn').addEventListener('click', togglePreview);

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
  const px = (f.caption?.font_size || 96) * (st.size ?? 1) * scale;
  const budget = f.caption || { max_chars: 42, max_words: 7 };

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

  $('[data-a="image"]', host).addEventListener('click', () => regen({ image: true, voice: false }));

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
        <label class="sw"><input type="checkbox" name="all_scenes" /><i></i>
          <span>Barcha sahnalarni qayta yozish</span></label>
        <small class="note">Ovoz butun videoga tegishli — o‘zgartirsangiz qolgan
          sahnalar ham render paytida qayta yoziladi.</small>`,
      onOpen: () => fillRevoice(current, ED.job.voice_id || ''),
    });
    if (!answer) return;
    regen({
      image: false,
      voice: true,
      tts_provider: answer.tts_provider || null,
      voice_id: answer.voice_id || null,
      all_scenes: !!answer.all_scenes,
    });
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
        <button class="btn ghost" data-a="dup">Nusxalash</button>
        <button class="btn ghost" data-a="drop">O‘chirish</button>
      </div>
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

// ── jobs + ready gallery ──────────────────────────────────────────
async function loadJobs() {
  state.jobs = await api('/api/jobs');

  const live = state.jobs.filter((j) => BUSY.includes(j.status) || j.status === 'review').length;
  $('#jobs-badge').textContent = live;
  $('#jobs-badge').classList.toggle('hidden', !live);

  const done = state.jobs.filter((j) => j.status === 'done');
  $('#ready-badge').textContent = done.length;
  $('#ready-badge').classList.toggle('hidden', !done.length);

  // A strip of projects, newest first — pick one and the studio below is it.
  $('#jobs-list').innerHTML = state.jobs.length
    ? state.jobs.map((j) => `
        <button class="proj${j.id === state.activeId ? ' on' : ''}" data-job="${esc(j.id)}">
          <span class="tag ${esc(j.status)}">${esc(STATUS[j.status] || j.status)}</span>
          <b>${esc(j.title || j.topic || j.id)}</b>
          <small>${esc(j.video_format)}${j.duration ? ` · ${clock(j.duration)}` : ''}</small>
          <i class="x" data-del-job="${esc(j.id)}" role="button" aria-label="O‘chirish">×</i>
        </button>`).join('')
    : '<p class="empty">Hali loyiha yo‘q. «Yaratish» bo‘limidan boshlang.</p>';

  $$('[data-job]').forEach((row) => row.addEventListener('click', (e) => {
    if (e.target.closest('[data-del-job]')) return;
    go('edit');
    watch(row.dataset.job, { reveal: true });
  }));

  $$('[data-del-job]').forEach((b) => b.addEventListener('click', async (e) => {
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

  autoProjects();
  syncProjectsHead();
  drawReady(done);
}

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

function drawReady(done) {
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
          <small>${esc(j.video_format)} · ${clock(j.duration)} · ${j.scene_count || 0} sahna</small>
        </div>
        <div class="acts" style="margin-top:0">
          <a class="btn primary" href="${esc(j.download_url || j.video_url || '')}" download>Videoni yuklab olish</a>
          ${j.subtitle_url ? `<a class="btn" href="${esc(j.subtitle_url)}" download>.srt</a>` : ''}
          <button class="btn" data-music="${esc(j.id)}" data-track="${esc(j.music_id || '')}"
            data-at="${esc(String(j.music_start || 0))}">${j.music_id ? 'Musiqani almashtirish' : 'Musiqa qo‘shish'}</button>
          <button class="btn" data-thumbs="${esc(j.id)}">Muqova yaratish</button>
          <button class="btn" data-repurpose="${esc(j.id)}" data-fmt="${esc(j.video_format)}">Boshqa formatga</button>
          ${j.kind === 'dub' ? '' :
            `<button class="btn" data-translate="${esc(j.id)}" data-lang="${esc(j.language)}">Boshqa tilga</button>`}
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
      go('edit');
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
      go('edit');
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
      go('edit');
      watch(clone.id, { reveal: true });
      toast('Tarjima tayyor — endi render qiling');
    } catch (e) {
      alert(e.message);
    } finally {
      b.disabled = false;
      b.textContent = 'Boshqa tilga';
    }
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
      go('edit');
      watch(clone.id, { reveal: true });
      toast('Nusxa tayyor — endi render qiling');
    } catch (e) {
      alert(e.message);
    }
  }));
}

// ── boot ──────────────────────────────────────────────────────────
(async function boot() {
  try {
    await loadHealth();
    // Before the jobs land, so the strip is never briefly the wrong shape.
    const shut = projectsChoice();
    if (shut !== null) setProjectsShut(shut === '1', false);
    await Promise.all([loadHeroes(), loadMusic(), loadAssets(), loadJobs()]);
    await Promise.all([loadBrand(), loadModels()]);
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
      go('edit');
      watch(mine.id);
    } else if (mine && SETTLED.includes(mine.status)) {
      // It finished while you were away. Say so and show it, rather than
      // dropping you on the composer as though nothing had happened.
      remember(null);
      go('edit');
      state.activeId = mine.id;
      state.reveal = true;
      $('#stage').classList.remove('hidden');
      tick();
      toast(mine.status === 'done' ? 'Videongiz tayyor bo‘libdi' : 'Ish tugagan');
    } else if (busy) {
      go('edit');
      watch(busy.id);
    }
  } catch (e) {
    document.body.insertAdjacentHTML('afterbegin',
      `<p class="msg err" style="margin:16px">Ilova yuklanmadi: ${esc(e.message)}</p>`);
  }
})();
