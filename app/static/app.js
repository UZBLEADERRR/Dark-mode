/* AI Video Studio — frontend.
   Single page: submit a job, then poll it until the MP4 is ready. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  health: null,
  heroes: [],
  music: [],
  jobs: [],
  motions: [],
  activeId: null,
  poll: null,
  editorFor: null,   // job id whose scenes are currently drawn in the editor
};

const MOTION_LABELS = {
  zoom_in: 'Zoom in (yaqinlashish)',
  zoom_out: 'Zoom out (uzoqlashish)',
  pan_left: 'Chapga surilish',
  pan_right: 'O‘ngga surilish',
  pan_up: 'Yuqoriga surilish',
  pan_down: 'Pastga surilish',
  zoom_in_pan_right: 'Zoom in + o‘ngga',
  zoom_out_pan_left: 'Zoom out + chapga',
};

// ── api ───────────────────────────────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(body?.error || body?.detail || `${res.status} ${res.statusText}`);
  return body;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const clock = (seconds) => {
  const s = Math.max(0, Math.round(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

// ── tabs ──────────────────────────────────────────────────────────
$$('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    $$('.tab').forEach((t) => t.classList.toggle('is-active', t === tab));
    $$('.panel').forEach((p) => p.classList.toggle('is-active', p.id === `tab-${tab.dataset.tab}`));
    if (tab.dataset.tab === 'jobs') loadJobs();
    if (tab.dataset.tab === 'library') { loadHeroes(); loadMusic(); }
  });
});

// ── health ────────────────────────────────────────────────────────
async function loadHealth() {
  const health = await api('/api/health');
  state.health = health;

  state.motions = health.motions || [];

  const pills = [
    ['ffmpeg', health.ffmpeg],
    [`skript: ${health.llm_provider}`, health.llm],
    ['transkripsiya', health.transcription],
  ];
  for (const [name, ready] of Object.entries(health.image_providers)) pills.push([`rasm: ${name}`, ready]);
  for (const [name, ready] of Object.entries(health.tts_providers)) pills.push([`ovoz: ${name}`, ready]);

  $('#health').innerHTML = pills
    .map(([label, ok]) => `<span class="pill ${ok ? 'on' : 'off'}">${esc(label)}</span>`)
    .join('');

  $('#format-select').innerHTML = health.formats
    .map((f) => `<option value="${esc(f.id)}">${esc(f.label)}</option>`).join('');

  $('#language-select').innerHTML = health.languages
    .map((l) => `<option value="${esc(l.id)}"${l.id === 'en' ? ' selected' : ''}>${esc(l.label)}</option>`)
    .join('');

  const option = (name, ready, fallbackReady) =>
    `<option value="${name}"${!ready ? ' disabled' : ''}${ready && fallbackReady === name ? ' selected' : ''}>` +
    `${name}${ready ? '' : ' — kalit yo‘q'}</option>`;

  const firstReady = (map, preferred) =>
    (map[preferred] ? preferred : Object.keys(map).find((k) => map[k])) || preferred;

  const imgPreferred = firstReady(health.image_providers, health.defaults.image_provider);
  $('#image-provider').innerHTML = Object.entries(health.image_providers)
    .map(([name, ready]) => option(name, ready, imgPreferred)).join('');

  const ttsPreferred = firstReady(health.tts_providers, health.defaults.tts_provider);
  $('#tts-provider').innerHTML = Object.entries(health.tts_providers)
    .map(([name, ready]) => option(name, ready, ttsPreferred)).join('');
}

// ── heroes ────────────────────────────────────────────────────────
async function loadHeroes() {
  state.heroes = await api('/api/heroes');
  renderHeroPicker();
  renderHeroLibrary();
}

function renderHeroPicker() {
  const box = $('#hero-picker');
  if (!state.heroes.length) {
    box.innerHTML = '<p class="muted">Hali hero yuklanmagan — «Herolar va musiqa» bo‘limidan qo‘shing.</p>';
    return;
  }
  box.innerHTML = state.heroes.map((h) => `
    <label class="hero-chip" data-id="${esc(h.id)}">
      <input type="checkbox" value="${esc(h.id)}" />
      <img src="${esc(h.url)}" alt="${esc(h.name)}" loading="lazy" />
      <span>${esc(h.name)}</span>
    </label>`).join('');

  $$('#hero-picker .hero-chip').forEach((chip) => {
    const input = $('input', chip);
    input.addEventListener('change', () => chip.classList.toggle('checked', input.checked));
  });
}

function renderHeroLibrary() {
  const box = $('#hero-list');
  if (!state.heroes.length) { box.innerHTML = '<p class="muted">Bo‘sh.</p>'; return; }
  box.innerHTML = state.heroes.map((h) => `
    <div class="hero-card">
      <img src="${esc(h.url)}" alt="${esc(h.name)}" loading="lazy" />
      <div class="body">
        <b>${esc(h.name)}</b>
        <p>${esc(h.description || '—')}</p>
        <button class="ghost danger" data-del-hero="${esc(h.id)}">O‘chirish</button>
      </div>
    </div>`).join('');

  $$('[data-del-hero]').forEach((btn) => btn.addEventListener('click', async () => {
    if (!confirm('Bu heroni o‘chirasizmi?')) return;
    await api(`/api/heroes/${btn.dataset.delHero}`, { method: 'DELETE' });
    loadHeroes();
  }));
}

$('#hero-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const button = $('button', form);
  button.disabled = true;
  try {
    await api('/api/heroes', { method: 'POST', body: new FormData(form) });
    form.reset();
    loadHeroes();
  } catch (err) {
    alert(err.message);
  } finally {
    button.disabled = false;
  }
});

// ── music ─────────────────────────────────────────────────────────
async function loadMusic() {
  state.music = await api('/api/music');
  $('#music-select').innerHTML = '<option value="">— yo‘q —</option>' +
    state.music.map((m) => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');
  $('#music-list').innerHTML = state.music.length
    ? state.music.map((m) => `
        <div class="job">
          <div><b>${esc(m.name)}</b></div>
          <button class="ghost danger" data-del-music="${esc(m.id)}">O‘chirish</button>
        </div>`).join('')
    : '<p class="muted">Bo‘sh.</p>';

  $$('[data-del-music]').forEach((btn) => btn.addEventListener('click', async () => {
    await api(`/api/music/${btn.dataset.delMusic}`, { method: 'DELETE' });
    loadMusic();
  }));
}

$('#music-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const button = $('button', form);
  button.disabled = true;
  try {
    await api('/api/music', { method: 'POST', body: new FormData(form) });
    form.reset();
    loadMusic();
  } catch (err) {
    alert(err.message);
  } finally {
    button.disabled = false;
  }
});

// ── create form ───────────────────────────────────────────────────
const duration = $('#duration');
const setDurationLabel = () => {
  const seconds = Number(duration.value);
  $('#duration-label').textContent =
    seconds < 60 ? `${seconds} soniya` : `${(seconds / 60).toFixed(seconds % 60 ? 1 : 0)} daqiqa`;
};
duration.addEventListener('input', setDurationLabel);
setDurationLabel();

$('#use-upload').addEventListener('change', (event) => {
  const uploading = event.target.checked;
  $('#audio-field').classList.toggle('hidden', !uploading);
  $('#duration-field').classList.toggle('hidden', uploading);
  $('#tts-provider').closest('.field').classList.toggle('hidden', uploading);
});

$('#create-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const button = $('#submit-btn');
  const errorBox = $('#create-error');
  errorBox.classList.add('hidden');
  button.disabled = true;
  button.textContent = 'Yuborilmoqda…';

  const heroIds = $$('#hero-picker input:checked').map((i) => i.value);
  const data = new FormData(form);
  const uploading = $('#use-upload').checked;
  const autoRender = !$('#review-first').checked;

  try {
    let job;
    if (uploading) {
      const file = $('#audio-file').files[0];
      if (!file) throw new Error('Audio fayl tanlanmagan.');
      const body = new FormData();
      body.append('topic', data.get('topic'));
      body.append('video_format', data.get('video_format'));
      body.append('language', data.get('language'));
      body.append('art_style', data.get('art_style'));
      body.append('tone', data.get('tone'));
      body.append('hero_ids', heroIds.join(','));
      body.append('subtitle_style', data.get('subtitle_style'));
      body.append('burn_subtitles', form.burn_subtitles.checked ? 'true' : 'false');
      body.append('auto_render', autoRender ? 'true' : 'false');
      if (data.get('image_provider')) body.append('image_provider', data.get('image_provider'));
      if (data.get('music_id')) body.append('music_id', data.get('music_id'));
      body.append('audio', file);
      job = await api('/api/jobs/with-audio', { method: 'POST', body });
    } else {
      job = await api('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          topic: data.get('topic'),
          video_format: data.get('video_format'),
          language: data.get('language'),
          target_seconds: Number(data.get('target_seconds')),
          art_style: data.get('art_style'),
          tone: data.get('tone'),
          hero_ids: heroIds,
          image_provider: data.get('image_provider') || null,
          tts_provider: data.get('tts_provider') || null,
          music_id: data.get('music_id') || null,
          subtitle_style: data.get('subtitle_style'),
          burn_subtitles: form.burn_subtitles.checked,
          auto_render: autoRender,
        }),
      });
    }
    watch(job.id);
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.classList.remove('hidden');
  } finally {
    button.disabled = false;
    button.textContent = 'Video yaratish';
  }
});

// ── active job ────────────────────────────────────────────────────
function watch(jobId) {
  state.activeId = jobId;
  $('#active-empty').classList.add('hidden');
  $('#active').classList.remove('hidden');
  if (state.poll) clearInterval(state.poll);
  tick();
  state.poll = setInterval(tick, 2500);
}

const SETTLED = ['done', 'failed', 'review'];

async function tick() {
  if (!state.activeId) return;
  try {
    const job = await api(`/api/jobs/${state.activeId}`);
    renderActive(job);
    syncEditor(job);
    if (SETTLED.includes(job.status)) {
      clearInterval(state.poll);
      state.poll = null;
      loadJobs();
    }
  } catch (err) {
    clearInterval(state.poll);
    state.poll = null;
    $('#active').innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

// ── scene editor ──────────────────────────────────────────────────
function syncEditor(job) {
  const editable = (job.status === 'review' || job.status === 'done') && job.scenes?.length;
  $('#editor').classList.toggle('hidden', !editable);
  if (!editable) { state.editorFor = null; return; }

  $('#editor-note').textContent = job.status === 'review'
    ? 'Matn, prompt yoki kamera harakatini o‘zgartiring. Tayyor bo‘lsa render qiling.'
    : 'Video tayyor. O‘zgartirish kiritsangiz qayta render qilishingiz mumkin.';
  $('#render-btn').textContent = job.status === 'review'
    ? '🎬 Videoni render qilish' : '🎬 Qayta render qilish';

  // Only redraw when the job changed — otherwise polling would wipe out
  // whatever the user is in the middle of typing.
  const stamp = `${job.id}:${job.updated_at}`;
  if (state.editorFor === stamp) return;
  if (state.editorFor?.startsWith(`${job.id}:`) && document.activeElement?.closest('.ed-scene')) return;
  state.editorFor = stamp;
  drawEditor(job);
}

function drawEditor(job) {
  const motions = state.motions.length ? state.motions : Object.keys(MOTION_LABELS);

  $('#editor-scenes').innerHTML = job.scenes.map((s) => `
    <div class="ed-scene${s.needs_image || s.needs_voice ? ' dirty' : ''}" data-index="${s.index}">
      ${s.image_url ? `<img src="${esc(s.image_url)}" alt="" loading="lazy" />` : ''}
      <div class="ed-body">
        <div class="ed-head">
          <b>Sahna ${s.index + 1}</b>
          <small>${clock(s.start)} · ${s.duration.toFixed(1)}s
            ${s.needs_image ? '<span class="flag">rasm eskirgan</span>' : ''}
            ${s.needs_voice ? '<span class="flag">ovoz eskirgan</span>' : ''}</small>
        </div>

        <div>
          <label>Matn (ovoz va subtitr)</label>
          <textarea data-f="narration" rows="3">${esc(s.narration)}</textarea>
        </div>

        <div>
          <label>Rasm prompti</label>
          <textarea data-f="image_prompt" rows="3">${esc(s.image_prompt)}</textarea>
        </div>

        <div class="row">
          <div>
            <label>Kamera harakati</label>
            <select data-f="motion">${motions.map((m) =>
              `<option value="${esc(m)}"${m === s.motion ? ' selected' : ''}>${esc(MOTION_LABELS[m] || m)}</option>`).join('')}</select>
          </div>
          <div>
            <label>Ekran yozuvi</label>
            <input data-f="on_screen_text" value="${esc(s.on_screen_text)}" placeholder="ixtiyoriy" />
          </div>
        </div>

        <div class="ed-actions">
          <button class="ghost" data-act="save">💾 Saqlash</button>
          <button class="ghost" data-act="image">🖼 Rasmni qayta</button>
          ${job.uses_uploaded_audio ? '' : '<button class="ghost" data-act="voice">🎙 Ovozni qayta</button>'}
        </div>
        <p class="error hidden" data-err></p>
      </div>
    </div>`).join('');

  $$('#editor-scenes .ed-scene').forEach((card) => {
    const index = Number(card.dataset.index);
    const errBox = $('[data-err]', card);
    const fields = () => Object.fromEntries(
      $$('[data-f]', card).map((el) => [el.dataset.f, el.value]));

    const run = async (label, fn) => {
      const buttons = $$('button', card);
      buttons.forEach((b) => (b.disabled = true));
      errBox.classList.add('hidden');
      try {
        await fn();
      } catch (err) {
        errBox.textContent = err.message;
        errBox.classList.remove('hidden');
      } finally {
        buttons.forEach((b) => (b.disabled = false));
      }
    };

    $('[data-act="save"]', card).addEventListener('click', () => run('save', async () => {
      await api(`/api/jobs/${job.id}/scenes/${index}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields()),
      });
      state.editorFor = null;
      tick();
    }));

    const regen = (body) => run('regen', async () => {
      await api(`/api/jobs/${job.id}/scenes/${index}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields()),
      });
      await api(`/api/jobs/${job.id}/scenes/${index}/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      state.editorFor = null;
      watch(job.id);
    });

    $('[data-act="image"]', card).addEventListener('click', () => regen({ image: true, voice: false }));
    const voiceBtn = $('[data-act="voice"]', card);
    if (voiceBtn) voiceBtn.addEventListener('click', () => regen({ image: false, voice: true }));
  });
}

$('#render-btn').addEventListener('click', async () => {
  if (!state.activeId) return;
  const btn = $('#render-btn');
  btn.disabled = true;
  try {
    await api(`/api/jobs/${state.activeId}/render`, { method: 'POST' });
    state.editorFor = null;
    watch(state.activeId);
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
});

function renderActive(job) {
  const parts = [];

  parts.push(`
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
      <b>${esc(job.title || job.topic)}</b>
      <span class="status ${esc(job.status)}">${esc(job.status)}</span>
    </div>
    <div class="bar"><i style="width:${job.progress}%"></i></div>
    <small class="muted">${job.progress}% — ${esc(job.step || '')}${
      job.duration ? ` · ${clock(job.duration)}` : ''}${
      job.scene_count ? ` · ${job.scene_count} sahna` : ''}</small>`);

  if (job.status === 'failed' && job.error) {
    parts.push(`<p class="error">${esc(job.error)}</p>`);
  }

  (job.warnings || []).forEach((w) => parts.push(`<p class="warn">${esc(w)}</p>`));

  if (job.status === 'done' && job.video_url) {
    parts.push(`<video controls preload="metadata" src="${esc(job.video_url)}"></video>`);
    parts.push(`
      <div class="actions">
        <a href="${esc(job.download_url || job.video_url)}" download>
          <button class="ghost">⬇ Videoni yuklab olish</button></a>
        ${job.subtitle_url ? `<a href="${esc(job.subtitle_url)}" download>
          <button class="ghost">⬇ Subtitr (.srt)</button></a>` : ''}
      </div>`);
  }

  const meta = job.metadata;
  if (meta && meta.title) {
    parts.push(`
      <div class="meta-block">
        <h4>YouTube sarlavhasi</h4>
        <p>${esc(meta.title)}</p>
        ${meta.description ? `<h4>Tavsif</h4><pre>${esc(meta.description)}</pre>` : ''}
        ${meta.tags?.length ? `<h4>Teglar</h4><div class="tags">${
          meta.tags.map((t) => `<span>${esc(t)}</span>`).join('')}</div>` : ''}
        ${meta.thumbnail_prompt ? `<h4>Thumbnail prompt</h4><pre>${esc(meta.thumbnail_prompt)}</pre>` : ''}
      </div>`);
  }

  if (job.status === 'review') {
    parts.push('<p class="warn">Qoralama tayyor. Pastdagi muharrirda sahnalarni ' +
      'ko‘rib chiqing, kerak bo‘lsa tahrirlang, keyin render qiling.</p>');
  }

  if (job.logs?.length) {
    const busy = job.status === 'running' || job.status === 'rendering';
    parts.push(`<details${busy ? ' open' : ''}>
      <summary>Jurnal</summary>
      <div class="logs">${esc(job.logs.join('\n'))}</div></details>`);
  }

  $('#active').innerHTML = parts.join('');
}

// ── jobs list ─────────────────────────────────────────────────────
async function loadJobs() {
  state.jobs = await api('/api/jobs');
  const box = $('#jobs-list');
  if (!state.jobs.length) { box.innerHTML = '<p class="muted">Hali loyiha yo‘q.</p>'; return; }

  box.innerHTML = state.jobs.map((job) => `
    <div class="job" data-job="${esc(job.id)}">
      <div>
        <b>${esc(job.title || job.topic || job.id)}</b>
        <small>${esc(job.video_format)} · ${esc(job.language)} · ${job.progress}%${
          job.duration ? ` · ${clock(job.duration)}` : ''}</small>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <span class="status ${esc(job.status)}">${esc(job.status)}</span>
        <button class="ghost danger" data-del-job="${esc(job.id)}">×</button>
      </div>
    </div>`).join('');

  $$('[data-job]').forEach((row) => row.addEventListener('click', (event) => {
    if (event.target.closest('[data-del-job]')) return;
    $$('.tab').forEach((t) => t.classList.toggle('is-active', t.dataset.tab === 'create'));
    $$('.panel').forEach((p) => p.classList.toggle('is-active', p.id === 'tab-create'));
    watch(row.dataset.job);
  }));

  $$('[data-del-job]').forEach((btn) => btn.addEventListener('click', async (event) => {
    event.stopPropagation();
    if (!confirm('Loyihani o‘chirasizmi?')) return;
    await api(`/api/jobs/${btn.dataset.delJob}`, { method: 'DELETE' });
    if (state.activeId === btn.dataset.delJob) {
      state.activeId = null;
      $('#active').classList.add('hidden');
      $('#active-empty').classList.remove('hidden');
    }
    loadJobs();
  }));
}

// ── boot ──────────────────────────────────────────────────────────
(async function boot() {
  try {
    await loadHealth();
    await Promise.all([loadHeroes(), loadMusic(), loadJobs()]);
    const resume = state.jobs.find((j) =>
      ['running', 'queued', 'rendering', 'review'].includes(j.status));
    if (resume) watch(resume.id);
  } catch (err) {
    document.body.insertAdjacentHTML('afterbegin',
      `<p class="error" style="margin:16px">Ilova yuklanmadi: ${esc(err.message)}</p>`);
  }
})();
