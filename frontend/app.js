'use strict';

const API_BASE = 'http://127.0.0.1:8000';

// n_fft slider maps index 0–7 to powers of 2
const FFT_SIZES = [512, 1024, 2048, 4096, 8192, 16384, 32768, 65536];

// ── state ─────────────────────────────────────────────────────────────────────

const state = {
  files: [],        // [{file: File, info: object|null}]
  mode: 'single',
  resultBlob: null,
  resultUrl: null,
  processing: false,
};

// ── DOM refs ──────────────────────────────────────────────────────────────────

const els = {
  dropZone:        document.getElementById('drop-zone'),
  fileInput:       document.getElementById('file-input'),
  fileList:        document.getElementById('file-list'),
  processBtn:      document.getElementById('process-btn'),
  statusBar:       document.getElementById('status-bar'),
  outputSection:   document.getElementById('output-section'),
  waveformCanvas:  document.getElementById('waveform-canvas'),
  audioPlayer:     document.getElementById('audio-player'),
  downloadBtn:     document.getElementById('download-btn'),
  contrastEnable:  document.getElementById('contrast-enable'),
  contrastParams:  document.getElementById('contrast-params'),
  // sliders + displays
  nFft:            document.getElementById('n-fft'),
  nFftDisplay:     document.getElementById('n-fft-display'),
  hopPct:          document.getElementById('hop-pct'),
  hopPctDisplay:   document.getElementById('hop-pct-display'),
  outputDur:       document.getElementById('output-dur'),
  outputDurDisplay:document.getElementById('output-dur-display'),
  glIters:         document.getElementById('gl-iters'),
  glItersDisplay:  document.getElementById('gl-iters-display'),
  contrastThresh:  document.getElementById('contrast-thresh'),
  contrastThreshDisplay: document.getElementById('contrast-thresh-display'),
  boostPower:      document.getElementById('boost-power'),
  boostPowerDisplay:     document.getElementById('boost-power-display'),
  suppressPower:   document.getElementById('suppress-power'),
  suppressPowerDisplay:  document.getElementById('suppress-power-display'),
};

// ── helpers ───────────────────────────────────────────────────────────────────

function setStatus(msg, type = '') {
  els.statusBar.textContent = msg;
  els.statusBar.className = type; // '' | 'error' | 'success'
}

function getParams() {
  return {
    mode:               state.mode,
    n_fft:              FFT_SIZES[parseInt(els.nFft.value)],
    hop_pct:            parseFloat(els.hopPct.value),
    output_duration:    parseFloat(els.outputDur.value),
    sample_rate:        22050,
    contrast_enable:    els.contrastEnable.checked,
    contrast_threshold: parseInt(els.contrastThresh.value) / 100,
    boost_power:        parseFloat(els.boostPower.value),
    suppress_power:     parseFloat(els.suppressPower.value),
    griffinlim_iters:   parseInt(els.glIters.value),
  };
}

function buildFormData() {
  const fd = new FormData();
  for (const { file } of state.files) fd.append('files', file);
  fd.append('params', JSON.stringify(getParams()));
  return fd;
}

function updateProcessButton() {
  const n = state.files.length;
  const ok = state.mode === 'single' ? n === 1 : n >= 2;
  els.processBtn.disabled = !ok || state.processing;
  els.processBtn.classList.toggle('processing', state.processing);
}

// ── file info ─────────────────────────────────────────────────────────────────

async function fetchFileInfo(file) {
  try {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch(`${API_BASE}/api/info`, { method: 'POST', body: fd });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

async function addFiles(fileList) {
  let added = 0;
  for (const file of fileList) {
    if (state.files.some(f => f.file.name === file.name)) continue;
    state.files.push({ file, info: null });
    added++;
  }
  if (!added) return;

  renderFileList();
  updateProcessButton();

  // fetch metadata for newly added files (those with info === null)
  const pending = state.files.filter(e => e.info === null);
  await Promise.all(pending.map(async (entry) => {
    entry.info = await fetchFileInfo(entry.file);
    renderFileList();
  }));

  updateProcessButton();
}

// ── render ────────────────────────────────────────────────────────────────────

function renderFileList() {
  els.fileList.innerHTML = '';
  for (let i = 0; i < state.files.length; i++) {
    const { file, info } = state.files[i];
    const li = document.createElement('li');
    li.className = 'file-item';

    const metaText = info
      ? `${info.duration}s · ${info.sample_rate}Hz · ${info.channels}ch`
      : 'reading...';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'file-name';
    nameSpan.textContent = file.name;
    nameSpan.title = file.name;

    const metaSpan = document.createElement('span');
    metaSpan.className = `file-meta${info ? '' : ' loading'}`;
    metaSpan.textContent = metaText;

    const removeBtn = document.createElement('button');
    removeBtn.className = 'remove-btn';
    removeBtn.dataset.idx = i;
    removeBtn.setAttribute('aria-label', `remove ${file.name}`);
    removeBtn.textContent = 'x';

    li.append(nameSpan, metaSpan, removeBtn);
    els.fileList.appendChild(li);
  }
}

// ── waveform ──────────────────────────────────────────────────────────────────

async function drawWaveform(blob) {
  const arrayBuf = await blob.arrayBuffer();
  let audioCtx;
  try {
    audioCtx = new AudioContext();
    const decoded = await audioCtx.decodeAudioData(arrayBuf);
    await audioCtx.close();

    const data = decoded.getChannelData(0);
    const canvas = els.waveformCanvas;
    // match display width
    canvas.width = canvas.clientWidth * (window.devicePixelRatio || 1);
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const step = Math.ceil(data.length / W);
    const mid = H / 2;

    ctx.strokeStyle = '#7dd3fc';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x < W; x++) {
      let min = 1, max = -1;
      for (let s = 0; s < step; s++) {
        const sample = data[x * step + s] ?? 0;
        if (sample < min) min = sample;
        if (sample > max) max = sample;
      }
      ctx.moveTo(x, mid + min * mid);
      ctx.lineTo(x, mid + max * mid);
    }
    ctx.stroke();

    // center line
    ctx.strokeStyle = '#2a2a2a';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, mid);
    ctx.lineTo(W, mid);
    ctx.stroke();
  } catch (e) {
    console.warn('waveform draw failed:', e);
  }
}

// ── process ───────────────────────────────────────────────────────────────────

async function process() {
  state.processing = true;
  updateProcessButton();
  setStatus('processing...');
  els.outputSection.hidden = true;

  try {
    const res = await fetch(`${API_BASE}/api/process`, {
      method: 'POST',
      body: buildFormData(),
    });

    if (!res.ok) {
      let detail = 'server error';
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(detail);
    }

    const blob = await res.blob();

    // revoke previous object url
    if (state.resultUrl) URL.revokeObjectURL(state.resultUrl);
    state.resultBlob = blob;
    state.resultUrl = URL.createObjectURL(blob);

    els.audioPlayer.src = state.resultUrl;
    await drawWaveform(blob);

    els.outputSection.hidden = false;
    setStatus('done', 'success');

    // autoplay — called inside click handler so browser policy is satisfied
    try { await els.audioPlayer.play(); } catch {}
  } catch (e) {
    setStatus(e.message, 'error');
  } finally {
    state.processing = false;
    updateProcessButton();
  }
}

// ── download ──────────────────────────────────────────────────────────────────

function download() {
  if (!state.resultBlob) return;
  const a = document.createElement('a');
  a.href = state.resultUrl;
  a.download = 'resonant-average.wav';
  a.click();
}

// ── slider wiring ─────────────────────────────────────────────────────────────

const sliderBindings = [
  [els.nFft,         els.nFftDisplay,         v => String(FFT_SIZES[parseInt(v)])],
  [els.hopPct,       els.hopPctDisplay,        v => `${v}%`],
  [els.outputDur,    els.outputDurDisplay,     v => `${v}s`],
  [els.glIters,      els.glItersDisplay,       v => v],
  [els.contrastThresh, els.contrastThreshDisplay, v => `${v}%`],
  [els.boostPower,   els.boostPowerDisplay,    v => parseFloat(v).toFixed(1)],
  [els.suppressPower,els.suppressPowerDisplay, v => parseFloat(v).toFixed(1)],
];

for (const [slider, display, fmt] of sliderBindings) {
  slider.addEventListener('input', () => { display.textContent = fmt(slider.value); });
}

// ── event wiring ──────────────────────────────────────────────────────────────

// drop zone — click + keyboard
els.dropZone.addEventListener('click', () => els.fileInput.click());
els.dropZone.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); els.fileInput.click(); }
});

// drag-drop
els.dropZone.addEventListener('dragover', e => {
  e.preventDefault();
  els.dropZone.classList.add('drag-over');
});
els.dropZone.addEventListener('dragleave', e => {
  if (!els.dropZone.contains(e.relatedTarget)) {
    els.dropZone.classList.remove('drag-over');
  }
});
els.dropZone.addEventListener('drop', e => {
  e.preventDefault();
  els.dropZone.classList.remove('drag-over');
  if (e.dataTransfer?.files.length) addFiles(e.dataTransfer.files);
});

// browse dialog
els.fileInput.addEventListener('change', () => {
  if (els.fileInput.files.length) addFiles(els.fileInput.files);
  els.fileInput.value = ''; // reset so same file can be re-added after remove
});

// file list remove — event delegation
els.fileList.addEventListener('click', e => {
  const btn = e.target.closest('.remove-btn');
  if (!btn) return;
  state.files.splice(parseInt(btn.dataset.idx), 1);
  renderFileList();
  updateProcessButton();
});

// mode buttons
document.querySelectorAll('.mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    state.mode = btn.dataset.mode;
    document.querySelectorAll('.mode-btn').forEach(b => {
      b.classList.toggle('active', b === btn);
    });
    updateProcessButton();
  });
});

// contrast toggle
els.contrastEnable.addEventListener('change', () => {
  els.contrastParams.classList.toggle('disabled', !els.contrastEnable.checked);
});

// process + download
els.processBtn.addEventListener('click', process);
els.downloadBtn.addEventListener('click', download);
