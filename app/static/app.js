/* AI Video Studio — frontend.
   Write a topic or a script, watch it build, edit the scenes, publish. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  health: null,
  heroes: [],
  music: [],
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

// ── scene editor ──────────────────────────────────────────────────
function syncEditor(job) {
  const on = (job.status === 'review' || job.status === 'done') && job.scenes?.length;
  $('#editor').classList.toggle('hidden', !on);
  if (!on) { state.drawn = null; return; }

  $('#editor-title').textContent = `Sahnalar · ${job.scenes.length}`;
  $('#editor-note').textContent = job.status === 'review'
    ? 'Kartaga bosib tahrirlang, keyin render qiling.'
    : 'Video tayyor. O‘zgartirsangiz qayta render qiling.';
  $('#render-btn').textContent = job.status === 'review' ? 'Render qilish' : 'Qayta render';

  // Never redraw over someone who is typing.
  const stamp = `${job.id}:${job.updated_at}`;
  if (state.drawn === stamp) return;
  if (state.drawn?.startsWith(`${job.id}:`) && document.activeElement?.closest('.sc')) return;
  state.drawn = stamp;
  drawEditor(job);
}

function drawEditor(job) {
  const motions = state.motions.length ? state.motions : Object.keys(MOTIONS);
  const transitions = state.transitions.length ? state.transitions : Object.keys(TRANSITIONS);
  const open = new Set($$('#editor-scenes .sc.open').map((el) => el.dataset.i));

  $('#editor-scenes').innerHTML = job.scenes.map((s) => {
    const stale = s.needs_image || s.needs_voice;
    return `
    <article class="sc${stale ? ' stale' : ''}${open.has(String(s.index)) ? ' open' : ''}" data-i="${s.index}">
      <figure class="sc-top">
        ${s.image_url ? `<img src="${esc(s.image_url)}" alt="" loading="lazy" />` : ''}
        ${stale ? `<span class="sc-flag">${s.needs_image ? 'rasm' : 'ovoz'} eskirgan</span>` : ''}
        <figcaption>
          <span class="sc-n">${s.index + 1}</span>
          <span class="sc-t">${clock(s.start)} · ${s.duration.toFixed(1)}s</span>
        </figcaption>
      </figure>

      <p class="sc-line">${esc(s.narration)}</p>

      <div class="sc-edit">
        <label class="f"><span>Matn — ovoz va subtitr</span>
          <textarea data-f="narration" rows="3">${esc(s.narration)}</textarea></label>
        <label class="f"><span>Rasm prompti</span>
          <textarea data-f="image_prompt" rows="3">${esc(s.image_prompt)}</textarea></label>
        <div class="f2">
          <label class="f"><span>Kamera harakati</span>
            <select data-f="motion">${motions.map((m) =>
              `<option value="${esc(m)}"${m === s.motion ? ' selected' : ''}>${esc(MOTIONS[m] || m)}</option>`).join('')}</select></label>
          <label class="f"><span>O‘tish effekti</span>
            <select data-f="transition">
              <option value="">avtomatik</option>
              ${transitions.map((t) =>
                `<option value="${esc(t)}"${t === s.transition ? ' selected' : ''}>${esc(TRANSITIONS[t] || t)}</option>`).join('')}
            </select></label>
        </div>
        <label class="f"><span>Ekran yozuvi</span>
          <input data-f="on_screen_text" value="${esc(s.on_screen_text)}" placeholder="ixtiyoriy" /></label>
        <div class="sc-acts">
          <button class="btn primary" data-a="save">Saqlash</button>
          <button class="btn" data-a="image">Rasmni qayta</button>
          ${job.uses_uploaded_audio ? '' : '<button class="btn" data-a="voice">Ovozni qayta</button>'}
          <label class="btn" data-a="upload">O‘z rasmim
            <input type="file" accept="image/*" hidden /></label>
          <button class="btn ghost" data-a="close">Yopish</button>
        </div>
        <p class="msg err hidden" data-err></p>
      </div>
    </article>`;
  }).join('');

  $$('#editor-scenes .sc').forEach((card) => {
    const index = Number(card.dataset.i);
    const err = $('[data-err]', card);
    const values = () => Object.fromEntries($$('[data-f]', card).map((el) => [el.dataset.f, el.value]));

    $('.sc-top', card).addEventListener('click', () => card.classList.toggle('open'));
    $('[data-a="close"]', card).addEventListener('click', () => card.classList.remove('open'));

    const guard = async (fn) => {
      const controls = $$('.btn', card);
      controls.forEach((b) => (b.disabled = true));
      err.classList.add('hidden');
      try { await fn(); } catch (e) {
        err.textContent = e.message;
        err.classList.remove('hidden');
      } finally { controls.forEach((b) => (b.disabled = false)); }
    };

    const save = () => api(`/api/jobs/${job.id}/scenes/${index}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(values()),
    });

    $('[data-a="save"]', card).addEventListener('click', () => guard(async () => {
      await save();
      state.drawn = null;
      tick();
      toast('Saqlandi');
    }));

    const regen = (body) => guard(async () => {
      await save();
      await api(`/api/jobs/${job.id}/scenes/${index}/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      state.drawn = null;
      watch(job.id);
    });

    $('[data-a="image"]', card).addEventListener('click', () => regen({ image: true, voice: false }));
    $('[data-a="voice"]', card)?.addEventListener('click', () => regen({ image: false, voice: true }));

    $('[data-a="upload"] input', card).addEventListener('change', (e) => {
      const file = e.target.files[0];
      if (!file) return;
      guard(async () => {
        const body = new FormData();
        body.append('image', file);
        await api(`/api/jobs/${job.id}/scenes/${index}/image`, { method: 'POST', body });
        e.target.value = '';
        state.drawn = null;
        await tick();
        toast('Rasm almashtirildi');
      });
    });
  });
}

$('#render-btn').addEventListener('click', async () => {
  if (!state.activeId) return;
  const btn = $('#render-btn');
  btn.disabled = true;
  try {
    await api(`/api/jobs/${state.activeId}/render`, { method: 'POST' });
    state.drawn = null;
    watch(state.activeId, { reveal: true });
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
  }
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
    await Promise.all([loadHeroes(), loadMusic(), loadJobs()]);
    // Only reattach to work that is actually moving. A draft waiting on review
    // is not urgent, and unfolding it on load would bury the composer.
    const resume = state.jobs.find((j) => BUSY.includes(j.status));
    if (resume) watch(resume.id);
  } catch (e) {
    document.body.insertAdjacentHTML('afterbegin',
      `<p class="msg err" style="margin:16px">Ilova yuklanmadi: ${esc(e.message)}</p>`);
  }
})();
