/* AI Video Studio — frontend.
   Submit a topic, watch it build, edit the scenes, download the MP4. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  health: null,
  heroes: [],
  music: [],
  jobs: [],
  motions: [],
  format: '16:9',
  activeId: null,
  poll: null,
  drawn: null,      // stamp of the editor currently on screen
};

const MOTIONS = {
  zoom_in: 'Zoom in',
  zoom_out: 'Zoom out',
  pan_left: 'Chapga',
  pan_right: "O'ngga",
  pan_up: 'Yuqoriga',
  pan_down: 'Pastga',
  zoom_in_pan_right: "Zoom in + o'ngga",
  zoom_out_pan_left: 'Zoom out + chapga',
};

const STATUS = {
  queued: 'navbatda', running: 'ishlayapti', rendering: 'render',
  review: "ko'rib chiqish", done: 'tayyor', failed: 'xato',
};

const BUSY = ['queued', 'running', 'rendering'];
const SETTLED = ['done', 'failed', 'review'];

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

// ── sheets ────────────────────────────────────────────────────────
function openSheet(name) {
  $('#scrim').classList.add('on');
  $(`#sheet-${name}`).classList.add('on');
  $(`#sheet-${name}`).setAttribute('aria-hidden', 'false');
  if (name === 'jobs') loadJobs();
}
function closeSheets() {
  $('#scrim').classList.remove('on');
  $$('.sheet').forEach((s) => { s.classList.remove('on'); s.setAttribute('aria-hidden', 'true'); });
}
$$('[data-open]').forEach((b) => b.addEventListener('click', () => openSheet(b.dataset.open)));
$$('[data-close]').forEach((b) => b.addEventListener('click', closeSheets));
$('#scrim').addEventListener('click', closeSheets);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheets(); });

addEventListener('scroll', () => $('.bar').classList.toggle('stuck', scrollY > 8), { passive: true });

// ── health ────────────────────────────────────────────────────────
async function loadHealth() {
  const h = await api('/api/health');
  state.health = h;
  state.motions = h.motions || [];

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
  $('#health-dot').className = `health-dot ${core && voice ? 'ok' : core ? 'part' : 'bad'}`;
  $('#health-dot').title = core && voice ? 'Hammasi tayyor'
    : core ? 'Ovoz provayderi sozlanmagan' : 'Sozlash kerak';
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
}

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
  $('#duration-row').classList.toggle('hidden', up);
  $('#tts-field').classList.toggle('hidden', up);
});

$('#submit-btn').addEventListener('click', async () => {
  const btn = $('#submit-btn');
  const err = $('#create-error');
  err.classList.add('hidden');

  const topic = $('#topic').value.trim();
  if (topic.length < 2) {
    err.textContent = 'Avval mavzuni yozing.';
    err.classList.remove('hidden');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Yuborilmoqda…';
  const heroIds = $$('#hero-picker input:checked').map((i) => i.value);
  const uploading = $('#use_upload').checked;
  const autoRender = !$('#review_first').checked;

  const common = {
    topic,
    video_format: state.format,
    language: $('#language').value,
    art_style: $('#art_style').value,
    tone: $('#tone').value,
    subtitle_style: $('#subtitle_style').value,
    burn_subtitles: $('#burn_subtitles').checked,
    auto_render: autoRender,
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
          target_seconds: Number(duration.value),
          hero_ids: heroIds,
          image_provider: $('#image_provider').value || null,
          tts_provider: $('#tts_provider').value || null,
          music_id: $('#music_id').value || null,
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
  // Scrolling here would aim at an empty card — the first tick has not drawn
  // anything yet. Defer it to drawStage, once there is something to land on.
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
      </div>`);
  }

  const m = job.metadata;
  if (m?.title) {
    p.push(`<details class="fold"><summary>YouTube uchun matnlar</summary><div class="fold-body kv">
      <h4>Sarlavha</h4><pre>${esc(m.title)}</pre>
      ${m.description ? `<h4>Tavsif</h4><pre>${esc(m.description)}</pre>` : ''}
      ${m.tags?.length ? `<h4>Teglar</h4><div class="tags">${m.tags.map((t) => `<span>${esc(t)}</span>`).join('')}</div>` : ''}
      ${m.thumbnail_prompt ? `<h4>Thumbnail prompt</h4><pre>${esc(m.thumbnail_prompt)}</pre>` : ''}
    </div></details>`);
  }

  if (job.logs?.length) {
    p.push(`<details class="fold"${busy ? ' open' : ''}><summary>Jurnal</summary>
      <div class="fold-body"><div class="logs">${esc(job.logs.join('\n'))}</div></div></details>`);
  }

  $('#stage').innerHTML = p.join('');

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
          <label class="f"><span>Kamera</span>
            <select data-f="motion">${motions.map((m) =>
              `<option value="${esc(m)}"${m === s.motion ? ' selected' : ''}>${esc(MOTIONS[m] || m)}</option>`).join('')}</select></label>
          <label class="f"><span>Ekran yozuvi</span>
            <input data-f="on_screen_text" value="${esc(s.on_screen_text)}" placeholder="ixtiyoriy" /></label>
        </div>
        <div class="sc-acts">
          <button class="btn primary" data-a="save">Saqlash</button>
          <button class="btn" data-a="image">Rasmni qayta</button>
          ${job.uses_uploaded_audio ? '' : '<button class="btn" data-a="voice">Ovozni qayta</button>'}
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
      const btns = $$('.btn', card);
      btns.forEach((b) => (b.disabled = true));
      err.classList.add('hidden');
      try { await fn(); } catch (e) {
        err.textContent = e.message;
        err.classList.remove('hidden');
      } finally { btns.forEach((b) => (b.disabled = false)); }
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

// ── jobs sheet ────────────────────────────────────────────────────
async function loadJobs() {
  state.jobs = await api('/api/jobs');

  const live = state.jobs.filter((j) => BUSY.includes(j.status) || j.status === 'review').length;
  $('#jobs-badge').textContent = live;
  $('#jobs-badge').classList.toggle('hidden', !live);

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
    closeSheets();
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
}

// ── boot ──────────────────────────────────────────────────────────
(async function boot() {
  try {
    await loadHealth();
    await Promise.all([loadHeroes(), loadMusic(), loadJobs()]);
    // Only reattach to work that is actually moving. A draft waiting on review
    // is not urgent, and unfolding it on load would bury the composer under a
    // progress card and a scene grid before the user has asked for anything.
    const resume = state.jobs.find((j) => BUSY.includes(j.status));
    if (resume) watch(resume.id);
  } catch (e) {
    document.body.insertAdjacentHTML('afterbegin',
      `<p class="msg err" style="margin:16px">Ilova yuklanmadi: ${esc(e.message)}</p>`);
  }
})();
