/* AI Video Studio — frontend.
   Write a topic or a script, watch it build, edit the scenes, publish. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  health: null,
  heroes: [],
  music: [],
  assets: [],
  jobs: [],
  motions: [],
  transitions: [],
  format: '16:9',
  mode: 'topic',
  view: 'create',
  activeId: null,
  poll: null,
  drawn: null,      // stamp of the editor currently on screen
  reveal: false,
};

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

const VOICE_HINTS = {
  gemini: "Nom yozing. Erkak: Puck, Fenrir, Orus, Charon, Iapetus. "
    + "Ayol: Kore, Aoede, Leda, Autonoe. Tembr har birida boshqacha — sinab ko'ring.",
  elevenlabs: "ElevenLabs Voice Library'dan Voice ID ni nusxalang.",
  openai: 'Nom yozing: ash, echo, onyx, verse, alloy, nova, shimmer, sage, coral, ballad, fable.',
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

// ── navigation ────────────────────────────────────────────────────
function go(view) {
  state.view = view;
  $$('.view').forEach((v) => v.classList.toggle('on', v.id === `view-${view}`));
  $$('#tabbar button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.go === view));
  scrollTo({ top: 0, behavior: 'instant' in document.documentElement.style ? 'instant' : 'auto' });
  if (view === 'jobs' || view === 'ready') loadJobs();
}
$$('[data-go]').forEach((b) => b.addEventListener('click', () => go(b.dataset.go)));

addEventListener('scroll', () => $('.bar').classList.toggle('stuck', scrollY > 8), { passive: true });

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
  syncVoiceHint();

  $('#subtitle_style').innerHTML = (h.caption_templates || []).map((t) =>
    `<option value="${esc(t.id)}"${t.id === 'bold' ? ' selected' : ''}>${esc(t.label)}</option>`).join('');

  const checks = [
    ['ffmpeg', h.ffmpeg],
    [`skript — ${h.llm_provider}`, h.llm],
    ['transkripsiya', h.transcription],
    ...Object.entries(h.image_providers).map(([n, v]) => [`rasm — ${n}`, v]),
    ...Object.entries(h.tts_providers).map(([n, v]) => [`ovoz — ${n}`, v]),
    [`saqlash — ${h.storage}`, true],
  ];
  $('#health-list').innerHTML = checks.map(([label, ok]) =>
    `<div class="row"><span>${esc(label)}</span><span class="tag ${ok ? 'done' : 'failed'}">${ok ? 'bor' : 'yo‘q'}</span></div>`
  ).join('');

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
    input.addEventListener('change', () => chip.classList.toggle('on', input.checked));
  });

  $('#hero-list').innerHTML = state.heroes.length
    ? state.heroes.map((h) => `
        <div class="lib-card">
          <img src="${esc(h.url)}" alt="${esc(h.name)}" loading="lazy" />
          <button class="x" data-del-hero="${esc(h.id)}" aria-label="O‘chirish">×</button>
          <b>${esc(h.name)}</b>
        </div>`).join('')
    : '<p class="empty">Hali hero yo‘q.</p>';

  $$('[data-del-hero]').forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('Bu heroni o‘chirasizmi?')) return;
    await api(`/api/heroes/${b.dataset.delHero}`, { method: 'DELETE' });
    loadHeroes();
  }));
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
}

$('#asset-form').addEventListener('submit', (e) => {
  e.preventDefault();
  submitLibraryForm(e.target, '/api/assets', loadAssets);
});

// ── music ─────────────────────────────────────────────────────────
async function loadMusic() {
  state.music = await api('/api/music');
  $('#music_id').innerHTML = '<option value="">— yo‘q —</option>' +
    state.music.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');
  $('#music-list').innerHTML = state.music.length
    ? state.music.map((m) => `<div class="row"><span>${esc(m.name)}</span>
        <button class="x" data-del-music="${esc(m.id)}" aria-label="O‘chirish">×</button></div>`).join('')
    : '<p class="empty">Hali musiqa yo‘q.</p>';
  $$('[data-del-music]').forEach((b) => b.addEventListener('click', async () => {
    await api(`/api/music/${b.dataset.delMusic}`, { method: 'DELETE' });
    loadMusic();
  }));
  syncMusicStart();
}

// The trim offset is meaningless with no track chosen, so it only appears once
// there is something to trim.
function syncMusicStart() {
  $('#music-start-field').classList.toggle('hidden', !$('#music_id').value);
}
$('#music_id').addEventListener('change', syncMusicStart);

// file inputs show the chosen name
$$('.file input').forEach((i) => i.addEventListener('change', () => {
  const label = i.closest('.file');
  const name = i.files[0]?.name;
  label.classList.toggle('has', !!name);
  if (name) $('span', label).textContent = name;
}));

async function submitLibraryForm(form, url, reload) {
  const button = $('button', form);
  button.disabled = true;
  try {
    await api(url, { method: 'POST', body: new FormData(form) });
    form.reset();
    $$('.file', form).forEach((l) => {
      l.classList.remove('has');
      $('span', l).textContent = l.querySelector('input').accept.startsWith('image')
        ? 'Rasm tanlash' : 'Audio tanlash';
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
}));

// Typing a style by hand means none of the presets describes it any more.
$('#art_style').addEventListener('input', () => {
  const value = $('#art_style').value.trim();
  $$('#style-presets button').forEach((b) =>
    b.setAttribute('aria-pressed', STYLE_PRESETS[Number(b.dataset.p)][1] === value));
});

function syncVoiceHint() {
  $('#voice-hint').textContent = VOICE_HINTS[$('#tts_provider').value] || '';
}
$('#tts_provider').addEventListener('change', syncVoiceHint);

// ── topic / script mode ───────────────────────────────────────────
$$('#mode-tabs button').forEach((b) => b.addEventListener('click', () => {
  state.mode = b.dataset.mode;
  $$('#mode-tabs button').forEach((x) => x.setAttribute('aria-pressed', x === b));
  const script = state.mode === 'script';
  $('#script-box').classList.toggle('hidden', !script);
  // With a script supplied, length comes from the words, not from a slider.
  $('#duration-row').classList.toggle('hidden', script || $('#use_upload').checked);
  $('#topic').placeholder = script
    ? 'Video nima haqida — bir qatorda (ixtiyoriy kontekst)'
    : "Ipak yo'li bo'ylab sayohat qilgan uch savdogarning haqiqiy tarixi…";
  $('#topic').rows = script ? 1 : 2;
}));

// ── composer ──────────────────────────────────────────────────────
const duration = $('#duration');
const syncDuration = () => { $('#duration-label').textContent = durationLabel(Number(duration.value)); };
duration.addEventListener('input', syncDuration);
syncDuration();

$('#topic').addEventListener('input', (e) => {
  e.target.style.height = 'auto';
  e.target.style.height = `${Math.min(e.target.scrollHeight, 220)}px`;
});

$('#use_upload').addEventListener('change', (e) => {
  const up = e.target.checked;
  $('#audio-field').classList.toggle('hidden', !up);
  $('#duration-row').classList.toggle('hidden', up || state.mode === 'script');
  $('#tts-field').classList.toggle('hidden', up);
  $('#voice-field').classList.toggle('hidden', up);
});

$('#submit-btn').addEventListener('click', async () => {
  const btn = $('#submit-btn');
  const err = $('#create-error');
  err.classList.add('hidden');

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
function watch(jobId, { reveal = false } = {}) {
  state.activeId = jobId;
  state.drawn = null;
  state.reveal = reveal;
  $('#stage').classList.remove('hidden');
  if (state.poll) clearInterval(state.poll);
  tick();
  state.poll = setInterval(tick, 2500);
}

async function tick() {
  if (!state.activeId) return;
  try {
    const job = await api(`/api/jobs/${state.activeId}`);
    drawStage(job);
    syncEditor(job);
    if (SETTLED.includes(job.status)) {
      clearInterval(state.poll);
      state.poll = null;
      loadJobs();
    }
  } catch (e) {
    clearInterval(state.poll);
    state.poll = null;
    $('#stage').innerHTML = `<p class="msg err">${esc(e.message)}</p>`;
  }
}

function drawStage(job) {
  const p = [];
  const busy = BUSY.includes(job.status);

  // The raw step name is only worth showing while something is moving —
  // once settled it just repeats the status word next to it.
  const meta = [STATUS[job.status] || job.status];
  if (busy && job.step && job.step !== job.status) meta.push(job.step);
  if (job.duration) meta.push(clock(job.duration));
  if (job.scene_count) meta.push(`${job.scene_count} sahna`);

  p.push(`<div class="stage-head">
      <h2>${esc(job.title || job.topic || 'Video')}</h2>
      <span class="stage-pct">${job.progress}%</span>
    </div>
    <div class="track ${esc(job.status)}"><i style="width:${job.progress}%"></i></div>
    <p class="step">${esc(meta.join(' · '))}</p>`);

  if (job.status === 'failed' && job.error) p.push(`<p class="msg err">${esc(job.error)}</p>`);
  (job.warnings || []).forEach((w) => p.push(`<p class="msg warn">${esc(w)}</p>`));

  if (job.status === 'done' && job.video_url) {
    p.push(`<video controls playsinline preload="metadata" src="${esc(job.video_url)}"></video>
      <div class="acts">
        <a class="btn primary" href="${esc(job.download_url || job.video_url)}" download>Videoni yuklab olish</a>
        ${job.subtitle_url ? `<a class="btn" href="${esc(job.subtitle_url)}" download>Subtitr .srt</a>` : ''}
        <button class="btn ghost" data-go="ready">Matnlarni ko‘rish</button>
      </div>`);
  }

  if (job.logs?.length) {
    p.push(`<details class="fold"${busy ? ' open' : ''}><summary>Jurnal</summary>
      <div class="fold-body"><div class="logs">${esc(job.logs.join('\n'))}</div></div></details>`);
  }

  $('#stage').innerHTML = p.join('');
  $$('#stage [data-go]').forEach((b) => b.addEventListener('click', () => go(b.dataset.go)));

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
  dirty: new Set(), styleDirty: false, timer: null, saving: 0, busy: false,
};

const LAYER_ANIMS = {
  none: 'yo‘q', fade: 'Yumshoq', pop: 'Sakrash', rise: 'Pastdan',
  slide_left: 'Chapdan', slide_right: "O‘ngdan", float: 'Suzish', drift: 'Siljish',
};

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
          overlays: s.overlays || [],
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
  const on = (job.status === 'review' || job.status === 'done') && job.scenes?.length;
  $('#editor').classList.toggle('hidden', !on);
  if (!on) { state.drawn = null; ED.job = null; return; }

  $('#editor-title').textContent = `Studio · ${job.scenes.length} sahna`;
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
  if (!sameJob) { ED.i = 0; ED.sel = null; ED.tab = 'scene'; }
  ED.i = Math.max(0, Math.min(ED.i, ED.scenes.length - 1));
  if (!selected()) ED.sel = null;
  drawAll();
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
    return `<button class="frame${i === ED.i ? ' on' : ''}${stale ? ' stale' : ''}" data-scene="${i}">
      ${s.image_url ? `<img src="${esc(s.image_url)}" alt="" loading="lazy" />` : '<i class="blank"></i>'}
      <b>${i + 1}</b>
      ${count ? `<em>${count}</em>` : ''}
    </button>`;
  }).join('');

  $$('#filmstrip [data-scene]').forEach((b) => b.addEventListener('click', () => {
    ED.i = Number(b.dataset.scene);
    ED.sel = null;
    drawAll();
  }));
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

function drawLayers() {
  const host = $('#ov-layer');
  const s = scene();
  if (!s) { host.innerHTML = ''; return; }
  const h = $('#canvas').clientHeight || 1;
  const w = $('#canvas').clientWidth || 1;

  host.innerHTML = (s.overlays || []).map((l) => {
    const pos = `left:${(l.x * 100).toFixed(2)}%;top:${(l.y * 100).toFixed(2)}%;` +
      `transform:translate(-50%,-50%) rotate(${l.rotate || 0}deg);opacity:${l.opacity ?? 1};`;
    const on = l.id === ED.sel ? ' on' : '';
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
function drawCaptionSample() {
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

  const words = String(s.narration || 'Namuna matn').split(/\s+/);
  let sample = '';
  for (const word of words) {
    if ((sample + ' ' + word).trim().length > (f.caption?.max_chars || 42)) break;
    sample = (sample + ' ' + word).trim();
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
  span.textContent = sample || 'Namuna matn';
}

new ResizeObserver(() => { if (ED.job) { drawLayers(); drawCaptionSample(); } })
  .observe($('#canvas'));

// ── panel ─────────────────────────────────────────────────────────
$$('#panel-tabs button').forEach((b) => b.addEventListener('click', () => {
  ED.tab = b.dataset.tab;
  drawPanel();
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

function slider(key, label, min, max, step, value, suffix = '') {
  return `<label class="f rng"><span>${esc(label)}<b>${Number(value).toFixed(2).replace(/\.?0+$/, '')}${suffix}</b></span>
    <input type="range" data-k="${key}" min="${min}" max="${max}" step="${step}" value="${value}" /></label>`;
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
    <label class="f"><span>Rasm prompti</span>
      <textarea data-k="image_prompt" rows="3">${esc(s.image_prompt)}</textarea></label>

    <div class="f2">
      <label class="f"><span>Kamera harakati</span>
        <select data-k="motion">${motions.map((m) =>
          `<option value="${esc(m)}"${m === s.motion ? ' selected' : ''}>${esc(MOTIONS[m] || m)}</option>`).join('')}</select></label>
      <label class="f"><span>O‘tish effekti</span>
        <select data-k="transition"><option value="">avtomatik</option>
          ${transitions.map((t) =>
            `<option value="${esc(t)}"${t === s.transition ? ' selected' : ''}>${esc(TRANSITIONS[t] || t)}</option>`).join('')}
        </select></label>
    </div>
    ${slider('motion_strength', 'Harakat kuchi', 0.3, 1.8, 0.05, s.motion_strength ?? 1, '×')}

    <label class="f"><span>Ekran yozuvi — sahna boshida chiqadi</span>
      <input data-k="on_screen_text" value="${esc(s.on_screen_text || '')}" placeholder="ixtiyoriy" /></label>

    ${heroes.length ? `<div class="f"><span>Bu sahnadagi herolar</span>
      <div class="cast-strip small" id="scene-heroes">${heroes.map((h) => `
        <label class="hero-chip${(s.hero_ids || []).includes(h.id) ? ' on' : ''}" title="${esc(h.name)}">
          <input type="checkbox" value="${esc(h.id)}"${(s.hero_ids || []).includes(h.id) ? ' checked' : ''} />
          <img src="${esc(h.url)}" alt="${esc(h.name)}" /></label>`).join('')}</div></div>` : ''}

    <div class="sc-acts">
      <button class="btn" data-a="image">Rasmni qayta yaratish</button>
      ${ED.job.uses_uploaded_audio ? '' : '<button class="btn" data-a="voice">Ovozni qayta yozish</button>'}
      <label class="btn" data-a="upload">O‘z rasmim<input type="file" accept="image/*" hidden /></label>
    </div>`;

  wire(host, s, (key) => {
    if (key === 'motion_strength') drawScenePanel();
    touch();
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
  $('[data-a="voice"]', host)?.addEventListener('click', () => regen({ image: false, voice: true }));
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
          `<option value="${a}"${a === l.anim ? ' selected' : ''}>${esc(LAYER_ANIMS[a] || a)}</option>`).join('')}</select></label>

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
}

$('.canvas-tools [data-add="text"]').addEventListener('click', () => addLayer(TEXT_DEFAULTS));

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

  $('#jobs-list').innerHTML = state.jobs.length
    ? state.jobs.map((j) => `
        <div class="row tap" data-job="${esc(j.id)}">
          <div>
            <span>${esc(j.title || j.topic || j.id)}</span>
            <small>${esc(j.video_format)} · ${esc(j.language)}${j.duration ? ` · ${clock(j.duration)}` : ''}</small>
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <span class="tag ${esc(j.status)}">${esc(STATUS[j.status] || j.status)}</span>
            <button class="x" data-del-job="${esc(j.id)}" aria-label="O‘chirish">×</button>
          </div>
        </div>`).join('')
    : '<p class="empty">Hali loyiha yo‘q.</p>';

  $$('[data-job]').forEach((row) => row.addEventListener('click', (e) => {
    if (e.target.closest('[data-del-job]')) return;
    go('create');
    watch(row.dataset.job, { reveal: true });
  }));

  $$('[data-del-job]').forEach((b) => b.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm('Loyihani o‘chirasizmi?')) return;
    await api(`/api/jobs/${b.dataset.delJob}`, { method: 'DELETE' });
    if (state.activeId === b.dataset.delJob) {
      state.activeId = null;
      $('#stage').classList.add('hidden');
      $('#editor').classList.add('hidden');
    }
    loadJobs();
  }));

  drawReady(done);
}

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
        </div>
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
}

// ── boot ──────────────────────────────────────────────────────────
(async function boot() {
  try {
    await loadHealth();
    await Promise.all([loadHeroes(), loadMusic(), loadAssets(), loadJobs()]);
    // Only reattach to work that is actually moving. A draft waiting on review
    // is not urgent, and unfolding it on load would bury the composer.
    const resume = state.jobs.find((j) => BUSY.includes(j.status));
    if (resume) watch(resume.id);
  } catch (e) {
    document.body.insertAdjacentHTML('afterbegin',
      `<p class="msg err" style="margin:16px">Ilova yuklanmadi: ${esc(e.message)}</p>`);
  }
})();
