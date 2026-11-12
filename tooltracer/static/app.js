import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/OrbitControls.js';
import { STLLoader } from '/static/vendor/STLLoader.js';

const $ = (id) => document.getElementById(id);
const SVGNS = 'http://www.w3.org/2000/svg';
const GRID = 42;
const GAP = 0.25, MIN_WALL = 1.2, LIP_T = 2.6; // mm; keep in sync with gridfinity.py
const NEEDS_LIBRARY = ['capture', 'library', 'build'];
const DEFAULT_SETTINGS = { nx: 2, ny: 2, units: 0, depth: 10, clearance: 0.5, magnets: false, lip: false, uniform: true };

function svgEl(name, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function htmlEl(tag, props = {}, parent) {
  const e = Object.assign(document.createElement(tag), props);
  if (parent) parent.appendChild(e);
  return e;
}

function newWorkspace(name = 'New bin') {
  return { items: [], sel: null, settings: { ...DEFAULT_SETTINGS }, lastGrid: [2, 2], dirty: true, stl: null, rev: 0, bin: { id: null, name } };
}

const state = {
  mode: 'quick',
  cfg: null,
  // The current photo is shared by Quick bin and Capture tools.
  ph: { photo: null, corners: null, manualCorners: false, sheet: null, traces: [], rectUrl: null, view: 'original', capture: [], hot: null },
  reference: 'paper',
  method: 'classic',
  ws: { quick: newWorkspace(), build: newWorkspace('Bin 1') },
  tools: [],   // library tools
  bins: [],    // saved bins
  lib: { search: '', tag: null, sel: null },
  nextId: 1,
};
const wsOf = (mode) => state.ws[mode === 'build' ? 'build' : 'quick'];
const W = () => wsOf(state.mode);

/* ---------- helpers ---------- */

function setStatus(msg, cls = '') {
  const s = $('status');
  s.textContent = msg || '';
  s.className = cls;
}

async function api(method, path, body) {
  const opts = { method };
  if (body !== undefined) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(detail);
  }
  return res;
}
const getJson = async (path) => (await api('GET', path)).json();
const sendJson = async (method, path, body) => (await api(method, path, body)).json();

const num = (id) => parseFloat($(id).value);
const clearance = () => Math.max(0, num('clearance') || 0);
const defaultDepth = () => Math.max(1, num('depth') || 10);
const gridSize = () => [Math.max(1, parseInt($('nx').value) || 1), Math.max(1, parseInt($('ny').value) || 1)];
const lipOn = () => $('lip').checked;
const edge = () => GAP + (lipOn() ? LIP_T : MIN_WALL); // how far pockets must stay from the grid edge
const uniformDepth = () => state.mode === 'build' && $('uniform').checked;
const parseTags = (s) => [...new Set(String(s || '').split(',').map((t) => t.trim().toLowerCase()).filter(Boolean))];
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** Depth actually cut for an item. Quick bin: own override or the default. Build bin: one depth
 *  for everything unless "Same depth for all" is off, then override > tool thickness > default. */
function effDepth(it) {
  const d = defaultDepth();
  if (uniformDepth()) return d;
  return it.depth ?? it.thickness ?? d;
}

function readSettings() {
  const [nx, ny] = gridSize();
  return { nx, ny, units: parseInt($('units').value) || 0, depth: defaultDepth(), clearance: clearance(), magnets: $('magnets').checked, lip: lipOn(), uniform: $('uniform').checked };
}
function writeSettings(s) {
  s = { ...DEFAULT_SETTINGS, ...s };
  $('nx').value = s.nx; $('ny').value = s.ny; $('units').value = s.units; $('depth').value = s.depth;
  $('clearance').value = s.clearance; $('magnets').checked = s.magnets; $('lip').checked = s.lip; $('uniform').checked = s.uniform;
  syncSettingHints();
}
function syncSettingHints() {
  $('lipHint').hidden = !lipOn();
  const separate = state.mode === 'build' && !$('uniform').checked;
  $('depthHint').hidden = !separate;
  $('depthLabel').textContent = separate ? 'Default depth' : 'Pocket depth';
}

function worldPts(item) {
  const a = (item.rot * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a);
  return item.pts.map(([x, y]) => [item.x + x * c - y * s, item.y + x * s + y * c]);
}
function itemBounds(item) {
  if (item.kind === 'hole') return [item.x - item.d / 2, item.y - item.d / 2, item.x + item.d / 2, item.y + item.d / 2];
  const p = worldPts(item);
  const xs = p.map((q) => q[0]), ys = p.map((q) => q[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}
function allBounds(items) {
  if (!items.length) return null;
  const b = items.map(itemBounds);
  return [Math.min(...b.map((v) => v[0])), Math.min(...b.map((v) => v[1])), Math.max(...b.map((v) => v[2])), Math.max(...b.map((v) => v[3]))];
}

function markDirty() {
  const ws = W();
  ws.dirty = true; ws.stl = null; ws.rev++;
  syncExport();
  if (currentTab() === 'preview') schedulePreview();
  if (state.mode === 'build') saveBinSoon();
}
function syncExport() {
  $('downloadBtn').disabled = !W().items.length;
  if (W().dirty) $('binInfo').textContent = '';
}

/** Two-click confirmation for destructive buttons (native dialogs are unreliable in embedded webviews). */
function armed(btn, label, action) {
  const original = btn.textContent;
  btn.addEventListener('click', () => {
    if (btn.classList.contains('armed')) { disarm(); action(); return; }
    btn.classList.add('armed'); btn.textContent = label;
    btn._t = setTimeout(disarm, 3500);
  });
  function disarm() { clearTimeout(btn._t); btn.classList.remove('armed'); btn.textContent = original; }
}

/* ---------- modes + tabs ---------- */

const currentTab = () => document.querySelector('#tabs button.on:not(.mode-off)')?.dataset.tab || null;

function showTab(name) {
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === 'pane-' + name));
  if (name === 'preview') { resizeThree(); updatePreview(); }
}
$('tabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) showTab(b.dataset.tab); });

async function setMode(mode, { force = false } = {}) {
  if (NEEDS_LIBRARY.includes(mode) && !state.cfg?.library) { openLibModal(mode); return; }
  if (mode === state.mode && !force) return;
  if (state.mode === 'build') await flushBinSave();
  wsOf(state.mode).settings = readSettings();
  state.mode = mode;
  try { localStorage.setItem('tooltracer.mode', mode); } catch {}

  document.querySelectorAll('#modes button').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  document.querySelectorAll('[data-modes]').forEach((e) => e.classList.toggle('mode-off', !e.dataset.modes.split(' ').includes(mode)));
  $('binStepNo').textContent = mode === 'build' ? '3' : '2';
  $('exportStepNo').textContent = mode === 'build' ? '4' : '3';
  writeSettings(W().settings);
  setStatus('');

  if (mode === 'library') {
    showTab(null);
    $('pane-library').classList.add('on');
    await loadLibrary();
    renderLibrary();
    return;
  }
  const allowed = [...document.querySelectorAll('#tabs button:not(.mode-off)')].map((b) => b.dataset.tab);
  const prev = document.querySelector('#tabs button.on')?.dataset.tab;
  if (mode === 'build') {
    await loadLibrary();
    await enterBuild();
  }
  showTab(mode === 'capture' ? 'photo' : allowed.includes(prev) ? prev : allowed[0]);
  renderPhoto();
  renderCaptureList();
  renderLayout();
  syncExport();
}
$('modes').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) setMode(b.dataset.mode); });

/* ---------- library folder ---------- */

let pendingMode = null;

async function refreshConfig() {
  try { state.cfg = await getJson('/api/config'); } catch { return; }
  const lib = state.cfg.library;
  $('appVersion').textContent = state.cfg.version.includes('dev') ? 'dev' : `v${state.cfg.version}`;
  $('libBtn').textContent = lib ? `Library: ${lib.name} · ${plural(lib.tools, 'tool')}` : 'Choose library folder…';
  $('libBtn').classList.toggle('unset', !lib);
  $('libBtn').title = lib ? lib.path : 'Choose where your tool library is kept';
  const hint = $('aiHint');
  hint.hidden = state.method !== 'ai' || (state.cfg.ai_installed && state.cfg.ai_model_downloaded);
  hint.textContent = !state.cfg.ai_installed
    ? 'AI mode needs ONNX Runtime: run “uv sync --extra ai” and restart.'
    : 'The first AI trace downloads a ~170 MB model into the models/ folder. After that it runs offline.';
}

function openLibModal(thenMode = null) {
  pendingMode = thenMode;
  const lib = state.cfg?.library;
  $('libPath').value = lib?.path || state.cfg?.default_library_path || '';
  $('libBrowse').hidden = !state.cfg?.native;
  $('libReveal').hidden = !lib;
  $('libModalInfo').textContent = lib
    ? `Current library: ${plural(lib.tools, 'tool')}, ${plural(lib.bins, 'saved bin')}. Choosing another folder switches libraries; nothing is moved or deleted.`
    : 'The folder is created if it does not exist. You can change it later from the top bar.';
  $('libModalErr').textContent = '';
  $('libModal').hidden = false;
  $('libPath').focus();
}
function closeLibModal() { $('libModal').hidden = true; pendingMode = null; }

$('libBtn').addEventListener('click', () => openLibModal());
$('libCancel').addEventListener('click', closeLibModal);
$('libBrowse').addEventListener('click', async () => {
  try {
    const { path } = await sendJson('POST', '/api/library/browse');
    if (path) $('libPath').value = path;
  } catch (err) { $('libModalErr').textContent = err.message; }
});
$('libReveal').addEventListener('click', () => api('POST', '/api/library/reveal').catch((err) => { $('libModalErr').textContent = err.message; }));
$('libUse').addEventListener('click', async () => {
  try {
    await flushBinSave();
    await sendJson('POST', '/api/library', { path: $('libPath').value });
    await refreshConfig();
    // A different folder is a different library: forget what was loaded from the old one.
    state.tools = []; state.bins = []; state.lib.sel = null; libraryLoaded = false;
    state.ws.build = newWorkspace('Bin 1');
    const go = pendingMode || state.mode;
    closeLibModal();
    await setMode(go, { force: true });
    setStatus(`Library folder: ${state.cfg.library.path}`);
  } catch (err) { $('libModalErr').textContent = err.message; }
});
$('libPath').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('libUse').click(); });

let libraryLoaded = false;
async function loadLibrary() {
  try {
    [state.tools, state.bins] = await Promise.all([getJson('/api/tools'), getJson('/api/bins')]);
    libraryLoaded = true;
    refreshConfig();
  } catch (err) { setStatus(err.message, 'err'); }
}

/* ---------- upload + trace ---------- */

async function upload(file) {
  if (!file) return;
  setStatus('Uploading…', 'busy');
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    Object.assign(state.ph, { photo: await res.json(), corners: null, manualCorners: false, traces: [], sheet: null, view: 'original', capture: [], hot: null });
    $('dropText').textContent = file.name;
    $('traceBtn').disabled = false;
    $('photoEmpty').hidden = true;
    showTab('photo');
    renderPhoto();
    renderCaptureList();
    await trace();
  } catch (err) {
    setStatus(err.message, 'err');
  }
}

async function trace() {
  const ph = state.ph;
  if (!ph.photo) return;
  setStatus(state.method === 'ai' ? 'Tracing with AI (first run downloads the model)…' : 'Tracing…', 'busy');
  $('traceBtn').disabled = true;
  try {
    const body = {
      id: ph.photo.id,
      reference: state.reference,
      paper: $('paper').value,
      custom_size: $('paper').value === 'custom' ? [num('customW'), num('customH')] : null,
      corners: state.reference === 'paper' && ph.manualCorners ? ph.corners : null,
      method: state.method,
      sensitivity: num('sensitivity'),
      min_area: num('minArea') || 0,
    };
    const data = await sendJson('POST', '/api/trace', body);
    ph.corners = data.corners;
    ph.sheet = data.sheet_mm;
    ph.traces = data.tools;
    ph.rectUrl = `/api/rectified/${ph.photo.id}.jpg?v=${Date.now()}`;
    ph.capture = data.tools.map(() => ({ include: true, name: '', tags: '', thickness: '', saved: false }));
    ph.hot = null;

    const quick = state.ws.quick;
    quick.items = data.tools.map((t) => {
      const xs = t.points.map((p) => p[0]), ys = t.points.map((p) => p[1]);
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
      return {
        id: state.nextId++, kind: 'tool', pts: t.points.map(([x, y]) => [x - cx, y - cy]), x: cx, y: cy, rot: 0, depth: null,
        img: { href: ph.rectUrl, x: -cx, y: -cy, w: ph.sheet[0], h: ph.sheet[1] }, size: [t.width, t.height], name: 'tool',
      };
    });
    quick.sel = null;
    fit();
    const n = data.tools.length;
    if (n) {
      ph.view = 'flat';
      const next = state.mode === 'capture' ? 'Name them on the left, then save.' : 'Check the outlines, then open Layout.';
      setStatus(data.warning || `Found ${plural(n, 'tool')}. ${next}`, data.warning ? 'warn' : '');
    } else {
      ph.view = 'original';
      setStatus(data.warning || 'No tools found.', 'warn');
    }
    renderPhoto();
    renderCaptureList();
    refreshConfig();
  } catch (err) {
    setStatus(err.message, 'err');
  } finally {
    $('traceBtn').disabled = false;
  }
}

/* ---------- photo view ---------- */

const photoSvg = $('photoSvg');
let viewToggle;

function toSvg(svg, e) {
  const pt = svg.createSVGPoint();
  pt.x = e.clientX; pt.y = e.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

function renderPhoto() {
  const ph = state.ph;
  photoSvg.replaceChildren();
  if (!viewToggle) {
    viewToggle = htmlEl('div', { className: 'seg', innerHTML: '<button data-v="original">Original</button><button data-v="flat">Flattened + traces</button>' }, $('pane-photo'));
    viewToggle.style.cssText = 'position:absolute;top:10px;left:12px;width:240px;z-index:2';
    viewToggle.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b && !b.disabled) { ph.view = b.dataset.v; renderPhoto(); } });
  }
  viewToggle.hidden = !ph.photo;
  $('photoEmpty').hidden = !!ph.photo;
  if (!ph.photo) return;
  const flatOk = !!ph.sheet;
  if (!flatOk) ph.view = 'original';
  [...viewToggle.children].forEach((b) => { b.classList.toggle('on', b.dataset.v === ph.view); b.disabled = b.dataset.v === 'flat' && !flatOk; });

  if (ph.view === 'flat') {
    const [w, h] = ph.sheet;
    const capturing = state.mode === 'capture';
    photoSvg.setAttribute('viewBox', `${-w * 0.04} ${-h * 0.08} ${w * 1.08} ${h * 1.12}`);
    svgEl('image', { href: ph.rectUrl, x: 0, y: 0, width: w, height: h, preserveAspectRatio: 'none' }, photoSvg);
    ph.traces.forEach((t, i) => {
      const c = ph.capture[i];
      let cls = 'trace';
      if (capturing) cls += ' pickable' + (c.saved || !c.include ? ' off' : '') + (ph.hot === i ? ' hot' : '');
      const poly = svgEl('polygon', { class: cls, points: t.points.map((p) => p.join(',')).join(' '), 'data-i': i }, photoSvg);
      if (!capturing) return;
      poly.addEventListener('click', () => document.querySelector(`.cap[data-i="${i}"] input[type=text]`)?.focus());
      const xs = t.points.map((p) => p[0]), ys = t.points.map((p) => p[1]);
      svgEl('text', { class: 'traceNo', x: (Math.min(...xs) + Math.max(...xs)) / 2, y: (Math.min(...ys) + Math.max(...ys)) / 2, 'font-size': Math.max(w, h) / 40 }, photoSvg).textContent = i + 1;
    });
    return;
  }
  const { width: w, height: h, id } = ph.photo;
  photoSvg.setAttribute('viewBox', `${-w * 0.04} ${-h * 0.08} ${w * 1.08} ${h * 1.12}`);
  svgEl('image', { href: `/api/photo/${id}.jpg`, x: 0, y: 0, width: w, height: h }, photoSvg);
  if (!ph.corners) return;
  const quad = svgEl('polygon', { class: 'quad', points: ph.corners.map((p) => p.join(',')).join(' ') }, photoSvg);
  if (state.reference !== 'paper') return;
  const r = Math.max(w, h) / 70;
  ph.corners.forEach((c, i) => {
    const hnd = svgEl('circle', { class: 'handle', cx: c[0], cy: c[1], r }, photoSvg);
    hnd.addEventListener('pointerdown', (e) => {
      hnd.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const p = toSvg(photoSvg, ev);
        ph.corners[i] = [Math.round(p.x), Math.round(p.y)];
        hnd.setAttribute('cx', p.x); hnd.setAttribute('cy', p.y);
        quad.setAttribute('points', ph.corners.map((q) => q.join(',')).join(' '));
      };
      const up = () => {
        hnd.removeEventListener('pointermove', move);
        ph.manualCorners = true;
        setStatus('Corners adjusted — press Trace tools to re-run.', 'warn');
      };
      hnd.addEventListener('pointermove', move);
      hnd.addEventListener('pointerup', up, { once: true });
    });
  });
}

/* ---------- capture: name + save traced tools ---------- */

function setHot(i) {
  state.ph.hot = i;
  document.querySelectorAll('.cap').forEach((c) => c.classList.toggle('hot', +c.dataset.i === i));
  photoSvg.querySelectorAll('.trace').forEach((p) => p.classList.toggle('hot', +p.dataset.i === i));
}

function syncSaveToolsBtn() {
  const n = state.ph.capture.filter((c) => c.include && !c.saved).length;
  $('saveToolsBtn').disabled = !n;
  $('saveToolsBtn').textContent = n ? `Save ${plural(n, 'tool')} to library` : 'Save to library';
}

function renderCaptureList() {
  const list = $('captureList'), ph = state.ph;
  list.replaceChildren();
  $('captureEmpty').hidden = ph.traces.length > 0;
  ph.traces.forEach((t, i) => {
    const c = ph.capture[i];
    const card = htmlEl('div', { className: 'cap' + (c.saved ? ' saved' : '') }, list);
    card.dataset.i = i;
    const no = htmlEl('div', { className: 'no', textContent: i + 1 }, card);
    const cb = htmlEl('input', { type: 'checkbox', checked: c.include, disabled: c.saved, title: 'Include this outline' }, no);
    const name = htmlEl('input', { type: 'text', value: c.name, placeholder: `Tool ${i + 1} name`, maxLength: 80, disabled: c.saved }, card);
    const line2 = htmlEl('div', { className: 'line2' }, card);
    const tags = htmlEl('input', { type: 'text', value: c.tags, placeholder: 'tags, comma separated', disabled: c.saved }, line2);
    const thick = htmlEl('input', { type: 'number', value: c.thickness, placeholder: 'thick', min: 0, max: 200, step: 0.5, title: 'Tool thickness = suggested pocket depth (mm)', disabled: c.saved }, line2);
    htmlEl('span', { textContent: 'mm' }, line2);
    htmlEl('div', { className: 'meta', textContent: c.saved ? 'Saved to library ✓' : `${t.width} × ${t.height} mm` }, card);
    cb.addEventListener('change', () => { c.include = cb.checked; syncSaveToolsBtn(); renderPhoto(); });
    name.addEventListener('input', () => { c.name = name.value; });
    tags.addEventListener('input', () => { c.tags = tags.value; });
    thick.addEventListener('input', () => { c.thickness = thick.value; });
    card.addEventListener('focusin', () => setHot(i));
    card.addEventListener('mouseenter', () => setHot(i));
  });
  syncSaveToolsBtn();
}

$('saveToolsBtn').addEventListener('click', async () => {
  const ph = state.ph;
  const picks = ph.capture.map((c, i) => ({ c, i })).filter(({ c }) => c.include && !c.saved);
  if (!picks.length) return;
  setStatus('Saving…', 'busy');
  try {
    const saved = await sendJson('POST', '/api/tools', {
      session_id: ph.photo.id,
      tools: picks.map(({ c, i }) => ({ points: ph.traces[i].points, name: c.name.trim() || `Tool ${i + 1}`, tags: parseTags(c.tags), thickness: parseFloat(c.thickness) || null })),
    });
    picks.forEach(({ c }) => { c.saved = true; });
    state.tools.push(...saved);
    renderCaptureList(); renderPhoto();
    await refreshConfig();
    setStatus(`Saved ${plural(saved.length, 'tool')} to your library. Open Build bin to use ${saved.length === 1 ? 'it' : 'them'}.`);
  } catch (err) { setStatus(err.message, 'err'); }
});

/* ---------- library ---------- */

function toolThumb(tool) {
  const p = tool.photo;
  const svg = svgEl('svg', { viewBox: `${p.x} ${p.y} ${p.w} ${p.h}`, preserveAspectRatio: 'xMidYMid meet' });
  svgEl('image', { href: `/api/tools/${tool.id}/photo.jpg`, x: p.x, y: p.y, width: p.w, height: p.h, preserveAspectRatio: 'none' }, svg);
  svgEl('polygon', { class: 'thumb-edge', points: tool.points.map((q) => q.join(',')).join(' ') }, svg);
  return svg;
}
const toolMeta = (t) => `${t.width} × ${t.height} mm${t.thickness ? ` · ${t.thickness} mm thick` : ''}`;
const matches = (t, q) => !q || t.name.toLowerCase().includes(q) || t.tags.some((g) => g.includes(q));

function renderLibrary() {
  const { search, tag } = state.lib;
  const q = search.trim().toLowerCase();
  const allTags = [...new Set(state.tools.flatMap((t) => t.tags))].sort();
  if (tag && !allTags.includes(tag)) state.lib.tag = null;
  const chips = $('tagChips');
  chips.replaceChildren();
  for (const g of allTags) {
    const b = htmlEl('button', { className: 'chip' + (g === state.lib.tag ? ' on' : ''), textContent: g }, chips);
    b.addEventListener('click', () => { state.lib.tag = state.lib.tag === g ? null : g; renderLibrary(); });
  }
  const shown = state.tools.filter((t) => matches(t, q) && (!state.lib.tag || t.tags.includes(state.lib.tag)));
  const grid = $('libGrid');
  grid.replaceChildren();
  for (const t of shown) {
    const card = htmlEl('button', { className: 'card' + (t.id === state.lib.sel ? ' sel' : '') }, grid);
    card.appendChild(toolThumb(t));
    htmlEl('div', { className: 'name', textContent: t.name }, card);
    htmlEl('small', { textContent: toolMeta(t) }, card);
    if (t.tags.length) { const c = htmlEl('div', { className: 'chips' }, card); t.tags.forEach((g) => htmlEl('span', { className: 'chip', textContent: g }, c)); }
    card.addEventListener('click', () => { state.lib.sel = t.id; renderLibrary(); });
    card.addEventListener('dblclick', () => addToolToBin(t));
  }
  $('libCount').textContent = state.tools.length ? `${shown.length} of ${plural(state.tools.length, 'tool')}` : '';
  $('libEmpty').hidden = shown.length > 0;
  $('libEmpty').textContent = state.tools.length ? 'No tools match that search.' : 'Your library is empty. Use “Capture tools” to add tools from a photo.';
  renderToolEdit();
}

function renderToolEdit() {
  const t = state.tools.find((x) => x.id === state.lib.sel);
  $('toolEdit').hidden = !t;
  if (!t) return;
  $('teName').value = t.name;
  $('teTags').value = t.tags.join(', ');
  $('teThick').value = t.thickness ?? '';
  const uses = state.bins.filter((b) => b.items.some((i) => i.tool_id === t.id)).length;
  $('teInfo').textContent = `${t.width} × ${t.height} mm · added ${t.created.slice(0, 10)}${uses ? ` · used in ${plural(uses, 'saved bin')}` : ''}`;
}

async function patchTool(patch) {
  const id = state.lib.sel;
  if (!id) return;
  try {
    const updated = await sendJson('PATCH', `/api/tools/${id}`, patch);
    state.tools = state.tools.map((t) => (t.id === id ? updated : t));
    renderLibrary();
  } catch (err) { setStatus(err.message, 'err'); }
}
$('teName').addEventListener('change', () => patchTool({ name: $('teName').value }));
$('teTags').addEventListener('change', () => patchTool({ tags: parseTags($('teTags').value) }));
$('teThick').addEventListener('change', () => patchTool({ thickness: num('teThick') || null }));
$('teAdd').addEventListener('click', () => { const t = state.tools.find((x) => x.id === state.lib.sel); if (t) addToolToBin(t); });
armed($('teDelete'), 'Delete forever?', async () => {
  const id = state.lib.sel;
  try {
    await api('DELETE', `/api/tools/${id}`);
    state.tools = state.tools.filter((t) => t.id !== id);
    state.lib.sel = null;
    renderLibrary();
    refreshConfig();
    setStatus('Tool deleted. Saved bins that used it will open without it.');
  } catch (err) { setStatus(err.message, 'err'); }
});
$('libSearch').addEventListener('input', () => { state.lib.search = $('libSearch').value; renderLibrary(); });

/* ---------- build bin: saved bins + tool picker ---------- */

function toolItem(tool, placed = {}) {
  return {
    id: state.nextId++, kind: 'tool', toolId: tool.id, name: tool.name, thickness: tool.thickness, pts: tool.points,
    img: { href: `/api/tools/${tool.id}/photo.jpg`, ...tool.photo }, size: [tool.width, tool.height],
    x: placed.x ?? 0, y: placed.y ?? 0, rot: placed.rot ?? 0, depth: placed.depth ?? null,
  };
}

function loadBinRecord(rec) {
  const ws = newWorkspace(rec.name);
  ws.bin.id = rec.id;
  ws.settings = { ...DEFAULT_SETTINGS, ...rec.settings };
  ws.lastGrid = [ws.settings.nx, ws.settings.ny];
  let missing = 0;
  for (const it of rec.items) {
    if (it.kind === 'hole') { ws.items.push({ id: state.nextId++, kind: 'hole', x: it.x, y: it.y, d: it.d, rot: 0, depth: it.depth ?? null }); continue; }
    const tool = state.tools.find((t) => t.id === it.tool_id);
    if (tool) ws.items.push(toolItem(tool, it)); else missing++;
  }
  state.ws.build = ws;
  writeSettings(ws.settings);
  return missing;
}

/** Refresh the build workspace from the library (tools may have been edited or deleted). */
async function enterBuild() {
  const ws = state.ws.build;
  if (!ws.bin.id && !ws.items.length && state.bins.length) {
    const missing = loadBinRecord(state.bins[0]);
    if (missing) setStatus(`${plural(missing, 'tool')} in this bin no longer exist in the library and were left out.`, 'warn');
  } else {
    ws.items = ws.items.filter((it) => {
      if (it.kind !== 'tool') return true;
      const tool = state.tools.find((t) => t.id === it.toolId);
      if (!tool) return false;
      Object.assign(it, { name: tool.name, thickness: tool.thickness, pts: tool.points, size: [tool.width, tool.height], img: { href: `/api/tools/${tool.id}/photo.jpg`, ...tool.photo } });
      return true;
    });
    ws.dirty = true; ws.stl = null;
  }
  renderBinPanel();
  renderPicker();
}

function renderBinPanel() {
  const ws = state.ws.build, sel = $('binSelect');
  sel.replaceChildren();
  if (!ws.bin.id) sel.add(new Option(`${ws.bin.name} (not saved yet)`, ''));
  for (const b of state.bins) sel.add(new Option(`${b.name} · ${b.settings.nx ?? '?'}×${b.settings.ny ?? '?'} · ${plural(b.items.filter((i) => i.kind !== 'hole').length, 'tool')}`, b.id));
  sel.value = ws.bin.id || '';
  if (document.activeElement !== $('binName')) $('binName').value = ws.bin.name;
  $('binDelete').disabled = !ws.bin.id;
  $('binDup').disabled = !ws.items.length;
}

function renderPicker() {
  const q = $('pickSearch').value.trim().toLowerCase();
  const list = $('pickList');
  list.replaceChildren();
  const shown = state.tools.filter((t) => matches(t, q));
  for (const t of shown) {
    const b = htmlEl('button', { className: 'pick', title: 'Add to this bin' }, list);
    b.appendChild(toolThumb(t));
    const txt = htmlEl('span', {}, b);
    htmlEl('span', { textContent: t.name }, txt);
    htmlEl('small', { textContent: toolMeta(t) }, txt);
    htmlEl('span', { className: 'plus', textContent: '+' }, b);
    b.addEventListener('click', () => addToolToBin(t));
  }
  if (!shown.length) htmlEl('p', { className: 'hint', textContent: state.tools.length ? 'No tools match.' : 'Your library is empty — use “Capture tools” first.' }, list);
}
$('pickSearch').addEventListener('input', renderPicker);

/** Add a library tool to the bin being built: beside what is already there, then re-fit the grid. */
async function addToolToBin(tool) {
  if (state.mode !== 'build') await setMode('build');
  const ws = state.ws.build;
  const it = toolItem(tool);
  const b = allBounds(ws.items);
  if (b) {
    const gap = 2 * clearance() + 3;
    const bw = b[2] - b[0], bh = b[3] - b[1];
    if (bw + tool.width <= bh + tool.height) { it.x = b[2] + gap + tool.width / 2; it.y = b[1] + tool.height / 2; }
    else { it.x = b[0] + tool.width / 2; it.y = b[3] + gap + tool.height / 2; }
  }
  ws.items.push(it);
  ws.sel = it.id;
  showTab('layout');
  fit();
  renderBinPanel();
  setStatus(`Added ${tool.name}.`);
}

let saveTimer = null, saveChain = Promise.resolve();
function saveBinSoon() {
  clearTimeout(saveTimer);
  $('binSaved').textContent = 'Saving…';
  saveTimer = setTimeout(() => { saveTimer = null; queueBinSave(); }, 600);
}
function flushBinSave() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; queueBinSave(); }
  return saveChain;
}
function queueBinSave() {
  const ws = state.ws.build;
  if (!ws.bin.id && !ws.items.length) { $('binSaved').textContent = ''; return; } // nothing worth a file yet
  const payload = {
    name: ws.bin.name,
    settings: state.mode === 'build' ? readSettings() : ws.settings,
    items: ws.items.map((it) => it.kind === 'hole'
      ? { kind: 'hole', x: it.x, y: it.y, d: it.d, depth: it.depth }
      : { kind: 'tool', tool_id: it.toolId, x: it.x, y: it.y, rot: it.rot, depth: it.depth }),
  };
  saveChain = saveChain.then(async () => {
    try {
      const rec = ws.bin.id ? await sendJson('PUT', `/api/bins/${ws.bin.id}`, payload) : await sendJson('POST', '/api/bins', payload);
      ws.bin.id = rec.id;
      state.bins = [rec, ...state.bins.filter((b) => b.id !== rec.id)];
      if (state.ws.build === ws) { renderBinPanel(); $('binSaved').textContent = 'All changes saved to your library.'; }
    } catch (err) { $('binSaved').textContent = ''; setStatus(`Could not save the bin: ${err.message}`, 'err'); }
  });
}

function showBuild(msg) {
  renderBinPanel(); renderLayout(); syncExport();
  $('binSaved').textContent = '';
  if (currentTab() === 'preview') updatePreview();
  if (msg) setStatus(msg.text, msg.cls);
}
function nextBinName() {
  const names = new Set(state.bins.map((b) => b.name));
  let n = state.bins.length + 1;
  while (names.has(`Bin ${n}`)) n++;
  return `Bin ${n}`;
}

$('binSelect').addEventListener('change', async () => {
  await flushBinSave();
  const rec = state.bins.find((b) => b.id === $('binSelect').value);
  if (!rec) return;
  const missing = loadBinRecord(rec);
  showBuild(missing ? { text: `${plural(missing, 'tool')} in this bin no longer exist in the library and were left out.`, cls: 'warn' } : { text: `Opened ${rec.name}.` });
});
$('binName').addEventListener('input', () => { state.ws.build.bin.name = $('binName').value.trim() || 'Untitled bin'; saveBinSoon(); });
$('binNew').addEventListener('click', async () => {
  await flushBinSave();
  state.ws.build = newWorkspace(nextBinName());
  writeSettings(state.ws.build.settings);
  showBuild({ text: 'New bin. Add tools from your library.' });
});
$('binDup').addEventListener('click', async () => {
  await flushBinSave();
  const ws = state.ws.build;
  ws.bin = { id: null, name: `${ws.bin.name} copy` };
  queueBinSave();
  await saveChain;
  showBuild({ text: `Duplicated as ${ws.bin.name}.` });
  $('binSaved').textContent = 'All changes saved to your library.';
});
armed($('binDelete'), 'Delete bin?', async () => {
  const ws = state.ws.build, id = ws.bin.id;
  clearTimeout(saveTimer); saveTimer = null;
  await saveChain;
  try {
    if (id) await api('DELETE', `/api/bins/${id}`);
    state.bins = state.bins.filter((b) => b.id !== id);
    if (state.bins.length) loadBinRecord(state.bins[0]);
    else { state.ws.build = newWorkspace('Bin 1'); writeSettings(state.ws.build.settings); }
    showBuild({ text: `Deleted ${ws.bin.name}.` });
  } catch (err) { setStatus(err.message, 'err'); }
});

/* ---------- layout view ---------- */

const layoutSvg = $('layoutSvg');

function fit() {
  const ws = W(), b = allBounds(ws.items);
  if (b) {
    const c = clearance();
    const nx = Math.min(12, Math.max(1, Math.ceil((b[2] - b[0] + 2 * c + 2 * edge()) / GRID - 1e-9)));
    const ny = Math.min(12, Math.max(1, Math.ceil((b[3] - b[1] + 2 * c + 2 * edge()) / GRID - 1e-9)));
    $('nx').value = nx; $('ny').value = ny;
    const dx = (nx * GRID) / 2 - (b[0] + b[2]) / 2, dy = (ny * GRID) / 2 - (b[1] + b[3]) / 2;
    for (const it of ws.items) { it.x += dx; it.y += dy; }
  }
  ws.lastGrid = gridSize();
  renderLayout();
  markDirty();
}

function renderLayout() {
  const ws = W();
  const [nx, ny] = gridSize();
  const Wd = nx * GRID, H = ny * GRID, pad = 10, c = clearance();
  layoutSvg.setAttribute('viewBox', `${-pad} ${-pad} ${Wd + 2 * pad} ${H + 2 * pad + 4}`);
  layoutSvg.replaceChildren();
  svgEl('rect', { class: 'bin', x: 0.25, y: 0.25, width: Wd - 0.5, height: H - 0.5, rx: 3.75 }, layoutSvg);
  if (lipOn()) {
    const e = edge();
    svgEl('rect', { class: 'lip', x: e / 2 + GAP / 2, y: e / 2 + GAP / 2, width: Wd - e - GAP, height: H - e - GAP, rx: 3.75 - LIP_T / 2, 'stroke-width': LIP_T }, layoutSvg);
  }
  for (let i = 1; i < nx; i++) svgEl('line', { class: 'gridline', x1: i * GRID, y1: 0.25, x2: i * GRID, y2: H - 0.25 }, layoutSvg);
  for (let j = 1; j < ny; j++) svgEl('line', { class: 'gridline', x1: 0.25, y1: j * GRID, x2: Wd - 0.25, y2: j * GRID }, layoutSvg);
  svgEl('text', { class: 'dim', x: Wd / 2, y: H + 7 }, layoutSvg).textContent = `${nx} × ${ny} units · ${Wd - 0.5} × ${H - 0.5} mm`;
  const defs = svgEl('defs', {}, layoutSvg);
  // Holes first so tools draw on top of them.
  const ordered = [...ws.items].sort((a, b) => (a.kind === 'hole' ? 0 : 1) - (b.kind === 'hole' ? 0 : 1));
  for (const it of ordered) {
    const g = svgEl('g', { class: `item ${it.kind}${it.id === ws.sel ? ' sel' : ''}`, 'data-id': it.id, transform: `translate(${it.x} ${it.y}) rotate(${it.rot || 0})` }, layoutSvg);
    if (it.kind === 'hole') {
      svgEl('circle', { class: 'halo', r: it.d / 2 }, g);
      svgEl('circle', { class: 'edge', r: it.d / 2 }, g);
      continue;
    }
    const pts = it.pts.map((p) => p.join(',')).join(' ');
    svgEl('polygon', { class: 'halo', points: pts, 'stroke-width': 2 * c }, g);
    if (it.img) {
      svgEl('polygon', { points: pts }, svgEl('clipPath', { id: `clip${it.id}` }, defs));
      svgEl('image', { href: it.img.href, x: it.img.x, y: it.img.y, width: it.img.w, height: it.img.h, preserveAspectRatio: 'none', 'clip-path': `url(#clip${it.id})`, opacity: 0.8 }, g);
    }
    svgEl('polygon', { class: 'edge', points: pts }, g);
  }
  const empty = $('layoutEmpty');
  empty.hidden = ws.items.length > 0;
  if (!ws.items.length) {
    empty.textContent = state.mode === 'build'
      ? (state.tools.length ? 'Add tools from your library using the list on the left.' : 'Your library is empty. Use “Capture tools” to add some, then come back.')
      : 'Trace a photo to place tools here.';
  }
  syncSelPanel();
}

let drag = null;
layoutSvg.addEventListener('pointerdown', (e) => {
  const ws = W();
  const g = e.target.closest('[data-id]');
  const id = g ? +g.dataset.id : null;
  if (id !== ws.sel) { ws.sel = id; renderLayout(); }
  if (id == null) return;
  const it = ws.items.find((i) => i.id === id);
  const p = toSvg(layoutSvg, e);
  drag = { it, ox: it.x - p.x, oy: it.y - p.y, moved: false };
  layoutSvg.setPointerCapture(e.pointerId);
});
layoutSvg.addEventListener('pointermove', (e) => {
  if (!drag) return;
  const p = toSvg(layoutSvg, e);
  drag.it.x = Math.round((p.x + drag.ox) * 10) / 10;
  drag.it.y = Math.round((p.y + drag.oy) * 10) / 10;
  drag.moved = true;
  layoutSvg.querySelector(`[data-id="${drag.it.id}"]`)?.setAttribute('transform', `translate(${drag.it.x} ${drag.it.y}) rotate(${drag.it.rot || 0})`);
});
const endDrag = () => { if (drag?.moved) markDirty(); drag = null; };
layoutSvg.addEventListener('pointerup', endDrag);
layoutSvg.addEventListener('pointercancel', endDrag);

const selected = () => W().items.find((i) => i.id === W().sel) || null;

function syncSelPanel() {
  const it = selected();
  $('selPanel').hidden = !it;
  if (!it) return;
  const isHole = it.kind === 'hole';
  $('selName').textContent = isHole ? 'finger hole' : `${it.name} · ${it.size[0]} × ${it.size[1]} mm`;
  $('selRotRow').hidden = isHole;
  $('selDiaRow').hidden = !isHole;
  const same = uniformDepth();
  $('selDepth').disabled = same;
  $('selDepth').value = same ? '' : it.depth ?? '';
  $('selDepth').placeholder = same ? `${defaultDepth()} (same for all)`
    : it.thickness ? `${it.thickness} (tool thickness)` : `${defaultDepth()} (default)`;
  $('selRot').value = Math.round(it.rot || 0);
  if (isHole) $('selDia').value = it.d;
}

function rotateSel(delta) {
  const it = selected();
  if (!it || it.kind !== 'tool') return;
  it.rot = (((it.rot || 0) + delta) % 360 + 360) % 360;
  renderLayout(); markDirty();
}
function deleteSel() {
  const ws = W();
  if (ws.sel == null) return;
  ws.items = ws.items.filter((i) => i.id !== ws.sel);
  ws.sel = null;
  renderLayout(); markDirty();
  if (state.mode === 'build') renderBinPanel();
}
function renderLayoutKeepValue(id) {
  const v = $(id).value;
  renderLayout();
  $(id).value = v;
}

$('selDepth').addEventListener('input', () => { const it = selected(); if (it) { const v = num('selDepth'); it.depth = v > 0 ? v : null; markDirty(); } });
$('selRot').addEventListener('input', () => { const it = selected(); if (it) { it.rot = num('selRot') || 0; renderLayoutKeepValue('selRot'); markDirty(); } });
$('selDia').addEventListener('input', () => { const it = selected(); if (it && num('selDia') > 0) { it.d = num('selDia'); renderLayoutKeepValue('selDia'); markDirty(); } });
$('rotL').addEventListener('click', () => rotateSel(-90));
$('rotR').addEventListener('click', () => rotateSel(90));
$('delBtn').addEventListener('click', deleteSel);
$('dupBtn').addEventListener('click', () => {
  const it = selected(), ws = W();
  if (!it) return;
  const copy = { ...structuredClone(it), id: state.nextId++, x: it.x + 6, y: it.y + 6 };
  ws.items.push(copy); ws.sel = copy.id;
  renderLayout(); markDirty();
});
$('addHole').addEventListener('click', () => {
  const [nx, ny] = gridSize(), ws = W();
  const it = { id: state.nextId++, kind: 'hole', x: (nx * GRID) / 2, y: (ny * GRID) / 2, d: 22, rot: 0, depth: null };
  ws.items.push(it); ws.sel = it.id;
  showTab('layout'); renderLayout(); markDirty();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('libModal').hidden) { closeLibModal(); return; }
  if (currentTab() !== 'layout' || /INPUT|SELECT|TEXTAREA/.test(document.activeElement?.tagName || '')) return;
  const it = selected();
  if (!it) return;
  const step = e.shiftKey ? 5 : 1;
  const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
  if (e.key === 'Delete' || e.key === 'Backspace') deleteSel();
  else if (e.key === 'r' || e.key === 'R') rotateSel(e.shiftKey ? -90 : 90);
  else if (moves[e.key]) { it.x += moves[e.key][0]; it.y += moves[e.key][1]; renderLayout(); markDirty(); }
  else return;
  e.preventDefault();
});

/* ---------- bin settings ---------- */

for (const id of ['nx', 'ny']) {
  $(id).addEventListener('change', () => {
    const ws = W();
    const [nx, ny] = gridSize();
    const [px, py] = ws.lastGrid || [nx, ny];
    const dx = ((nx - px) * GRID) / 2, dy = ((ny - py) * GRID) / 2;
    for (const it of ws.items) { it.x += dx; it.y += dy; }
    ws.lastGrid = [nx, ny];
    renderLayout(); markDirty();
  });
}
for (const id of ['depth', 'clearance']) $(id).addEventListener('input', () => { renderLayout(); markDirty(); });
for (const id of ['units', 'magnets']) $(id).addEventListener('change', markDirty);
for (const id of ['lip', 'uniform']) $(id).addEventListener('change', () => { syncSettingHints(); renderLayout(); markDirty(); });
$('fitBtn').addEventListener('click', fit);
for (let u = 2; u <= 12; u++) $('units').add(new Option(`${u}u · ${u * 7} mm`, u));

/* ---------- build / preview / save STL ---------- */

function binRequest() {
  const ws = W();
  const [nx, ny] = gridSize();
  return {
    nx, ny,
    pockets: ws.items.filter((i) => i.kind === 'tool').map((i) => ({ points: worldPts(i).map(([x, y]) => [+x.toFixed(3), +y.toFixed(3)]), depth: effDepth(i) })),
    circles: ws.items.filter((i) => i.kind === 'hole').map((i) => ({ x: i.x, y: i.y, d: i.d, depth: uniformDepth() ? defaultDepth() : i.depth ?? defaultDepth() })),
    clearance: clearance(),
    height_units: parseInt($('units').value) || null,
    magnets: $('magnets').checked,
    lip: lipOn(),
    name: state.mode === 'build' ? ws.bin.name : '',
  };
}

function showBinInfo(info) {
  const [x, y, z] = info.size_mm;
  const notes = [];
  if (info.lip) notes.push('Includes stacking lip.');
  if (info.clipped) notes.push(`⚠ Some pockets reach past the bin ${info.lip ? 'lip' : 'wall'} and were trimmed — move them in or enlarge the grid.`);
  if (info.raised_height) notes.push(`Height raised to ${info.height_units}u to keep a solid floor under the deepest pocket.`);
  $('binInfo').textContent = `${info.nx}×${info.ny}×${info.height_units}u · ${x} × ${y} × ${z} mm · ${info.volume_cm3} cm³ solid. ${notes.join(' ')}`;
}

async function buildStl() {
  const ws = W();
  if (ws.stl && !ws.dirty) return ws.stl;
  const rev = ws.rev;
  const res = await api('POST', '/api/bin', binRequest());
  const info = JSON.parse(res.headers.get('X-Bin-Info'));
  const name = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '')?.[1] || 'bin.stl';
  const stl = { blob: await res.blob(), name, info };
  if (rev === ws.rev && ws === W()) { ws.stl = stl; ws.dirty = false; }
  showBinInfo(info);
  return stl;
}

let previewTimer = null;
function schedulePreview() { clearTimeout(previewTimer); previewTimer = setTimeout(updatePreview, 350); }

async function updatePreview() {
  const ws = W();
  $('previewEmpty').hidden = ws.items.length > 0;
  if (!ws.items.length) { if (three) { clearMesh(); renderThree(); } return; }
  try {
    const stl = await buildStl();
    showMesh(await stl.blob.arrayBuffer());
  } catch (err) { setStatus(err.message, 'err'); }
}

$('downloadBtn').addEventListener('click', async () => {
  try {
    setStatus('Building STL…', 'busy');
    if (state.cfg?.native) {
      // Embedded webviews don't do browser downloads; the app shows a native Save dialog instead.
      const { path, info } = await sendJson('POST', '/api/bin/save', binRequest());
      showBinInfo(info);
      setStatus(path ? `Saved ${path}` : 'Save cancelled.');
      return;
    }
    const stl = await buildStl();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(stl.blob);
    a.download = stl.name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    setStatus(`Saved ${stl.name}`);
  } catch (err) { setStatus(err.message, 'err'); }
});

/* ---------- three.js preview ---------- */

let three = null;

function initThree() {
  const host = $('three');
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setClearColor(0x15171a);
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 1, 5000);
  camera.up.set(0, 0, 1);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x30343a, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.6);
  key.position.set(-1, -1.4, 2.2);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.5);
  fill.position.set(1.5, 1, 0.8);
  scene.add(fill);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.addEventListener('change', renderThree);
  three = { renderer, scene, camera, controls, mesh: null, framed: false };
  new ResizeObserver(resizeThree).observe(host);
  resizeThree();
}

function resizeThree() {
  if (!three) return;
  const host = $('three');
  const w = host.clientWidth, h = host.clientHeight;
  if (!w || !h) return;
  three.renderer.setSize(w, h);
  three.camera.aspect = w / h;
  three.camera.updateProjectionMatrix();
  renderThree();
}
function renderThree() { if (three) three.renderer.render(three.scene, three.camera); }
function clearMesh() {
  if (three?.mesh) { three.scene.remove(three.mesh); three.mesh.geometry.dispose(); three.mesh = null; }
}

function showMesh(buffer) {
  if (!three) initThree();
  clearMesh();
  const geo = new STLLoader().parse(buffer);
  geo.computeBoundingBox();
  const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x4f9fe0, roughness: 0.75, metalness: 0.05, flatShading: true }));
  three.scene.add(mesh);
  three.mesh = mesh;
  const bb = geo.boundingBox, c = bb.getCenter(new THREE.Vector3()), size = bb.getSize(new THREE.Vector3());
  const key = `${state.mode}:${size.x}x${size.y}`;
  if (three.framed !== key) {
    // Back off further in a narrow pane so the whole bin stays in frame.
    const r = Math.max(size.x, size.y, size.z) * Math.max(1, 1.2 / three.camera.aspect);
    three.controls.target.copy(c);
    three.camera.position.set(c.x - r * 0.4, c.y - r * 1.7, c.z + r * 1.9);
    three.framed = key;
  }
  three.controls.update();
  renderThree();
}

/* ---------- wiring ---------- */

$('file').addEventListener('change', (e) => upload(e.target.files[0]));
const drop = $('drop');
for (const ev of ['dragenter', 'dragover']) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); });
for (const ev of ['dragleave', 'drop']) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); });
drop.addEventListener('drop', (e) => upload(e.dataTransfer.files[0]));
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault();
  const canTrace = state.mode === 'quick' || state.mode === 'capture';
  if (canTrace && e.target.closest?.('#drop') == null && e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]);
});

$('traceBtn').addEventListener('click', trace);
$('sensitivity').addEventListener('input', () => { $('sensOut').textContent = $('sensitivity').value; });
$('paper').addEventListener('change', () => {
  $('customRow').hidden = $('paper').value !== 'custom';
  $('markerLink').href = `/markers?paper=${$('paper').value === 'custom' ? 'letter' : $('paper').value}`;
});
function bindSeg(id, onChange) {
  $(id).addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    [...$(id).children].forEach((c) => c.classList.toggle('on', c === b));
    onChange(b.dataset.v);
  });
}
bindSeg('reference', (v) => {
  state.reference = v;
  $('markerHint').hidden = v !== 'markers';
  if (v === 'markers' && $('paper').value === 'custom') { $('paper').value = 'letter'; $('customRow').hidden = true; }
  $('paper').querySelector('[value=custom]').disabled = v === 'markers';
  renderPhoto();
});
bindSeg('method', (v) => { state.method = v; refreshConfig(); });

(async function start() {
  await refreshConfig();
  let mode = 'quick';
  try { mode = localStorage.getItem('tooltracer.mode') || 'quick'; } catch {}
  if (!['quick', 'capture', 'library', 'build'].includes(mode) || (NEEDS_LIBRARY.includes(mode) && !state.cfg?.library)) mode = 'quick';
  await setMode(mode, { force: true });
})();
