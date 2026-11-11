import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/OrbitControls.js';
import { STLLoader } from '/static/vendor/STLLoader.js';

const $ = (id) => document.getElementById(id);
const SVGNS = 'http://www.w3.org/2000/svg';
const GRID = 42;
const GAP = 0.25, MIN_WALL = 1.2, LIP_T = 2.6; // mm; keep in sync with gridfinity.py
const lipOn = () => $('lip').checked;
const edge = () => GAP + (lipOn() ? LIP_T : MIN_WALL); // how far pockets must stay from the grid edge

function svgEl(name, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

const state = {
  photo: null,          // {id, width, height}
  corners: null,        // paper corners in photo px
  manualCorners: false,
  sheet: null,          // [w, h] mm of the flattened sheet
  traces: [],           // raw outlines on the sheet (for the flattened view)
  items: [],            // tools + finger holes placed in the bin
  sel: null,
  reference: 'paper',
  method: 'classic',
  photoView: 'original',
  nextId: 1,
  dirty: true,
  stl: null,            // {blob, name, info} for the current layout
  rev: 0,
};

/* ---------- helpers ---------- */

function setStatus(msg, cls = '') {
  const s = $('status');
  s.textContent = msg || '';
  s.className = cls;
}

async function postJson(path, body) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(detail);
  }
  return res;
}

const num = (id) => parseFloat($(id).value);
const clearance = () => Math.max(0, num('clearance') || 0);
const defaultDepth = () => Math.max(1, num('depth') || 10);
const gridSize = () => [Math.max(1, parseInt($('nx').value) || 1), Math.max(1, parseInt($('ny').value) || 1)];

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

function allBounds() {
  if (!state.items.length) return null;
  const b = state.items.map(itemBounds);
  return [Math.min(...b.map((v) => v[0])), Math.min(...b.map((v) => v[1])), Math.max(...b.map((v) => v[2])), Math.max(...b.map((v) => v[3]))];
}

function markDirty() {
  state.dirty = true;
  state.stl = null;
  state.rev++;
  $('downloadBtn').disabled = !state.items.length;
  if (currentTab() === 'preview') schedulePreview();
}

/* ---------- segmented buttons + tabs ---------- */

function bindSeg(id, onChange) {
  $(id).addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    [...$(id).children].forEach((c) => c.classList.toggle('on', c === b));
    onChange(b.dataset.v);
  });
}

const currentTab = () => document.querySelector('#tabs button.on').dataset.tab;

function showTab(name) {
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === 'pane-' + name));
  if (name === 'preview') { resizeThree(); if (state.dirty) updatePreview(); }
}
$('tabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) showTab(b.dataset.tab); });

/* ---------- upload + trace ---------- */

async function upload(file) {
  if (!file) return;
  setStatus('Uploading…', 'busy');
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    state.photo = await res.json();
    state.corners = null;
    state.manualCorners = false;
    state.traces = [];
    state.sheet = null;
    state.photoView = 'original';
    $('dropText').textContent = file.name;
    $('traceBtn').disabled = false;
    $('photoEmpty').hidden = true;
    showTab('photo');
    renderPhoto();
    await trace();
  } catch (err) {
    setStatus(err.message, 'err');
  }
}

async function trace() {
  if (!state.photo) return;
  setStatus(state.method === 'ai' ? 'Tracing with AI (first run downloads the model)…' : 'Tracing…', 'busy');
  $('traceBtn').disabled = true;
  try {
    const body = {
      id: state.photo.id,
      reference: state.reference,
      paper: $('paper').value,
      custom_size: $('paper').value === 'custom' ? [num('customW'), num('customH')] : null,
      corners: state.reference === 'paper' && state.manualCorners ? state.corners : null,
      method: state.method,
      sensitivity: num('sensitivity'),
      min_area: num('minArea') || 0,
    };
    const data = await (await postJson('/api/trace', body)).json();
    state.corners = data.corners;
    state.sheet = data.sheet_mm;
    state.traces = data.tools;
    state.rectUrl = `/api/rectified/${state.photo.id}.jpg?v=${Date.now()}`;
    state.items = data.tools.map((t) => {
      const xs = t.points.map((p) => p[0]), ys = t.points.map((p) => p[1]);
      const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
      return { id: state.nextId++, kind: 'tool', pts: t.points.map(([x, y]) => [x - cx, y - cy]), x: cx, y: cy, rot: 0, depth: null, src: { cx, cy }, size: [t.width, t.height] };
    });
    state.sel = null;
    fit();
    if (data.tools.length) {
      state.photoView = 'flat';
      setStatus(data.warning || `Found ${data.tools.length} tool${data.tools.length === 1 ? '' : 's'}. Check the outlines, then open Layout.`, data.warning ? 'warn' : '');
    } else {
      state.photoView = 'original';
      setStatus(data.warning || 'No tools found.', 'warn');
    }
    renderPhoto();
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

function renderPhoto() {
  photoSvg.replaceChildren();
  if (!viewToggle) {
    viewToggle = document.createElement('div');
    viewToggle.className = 'seg';
    viewToggle.style.cssText = 'position:absolute;top:10px;left:12px;width:220px;z-index:2';
    viewToggle.innerHTML = '<button data-v="original">Original</button><button data-v="flat">Flattened + traces</button>';
    viewToggle.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b && !b.disabled) { state.photoView = b.dataset.v; renderPhoto(); } });
    $('pane-photo').appendChild(viewToggle);
  }
  viewToggle.hidden = !state.photo;
  if (!state.photo) return;
  const flatOk = !!state.sheet;
  if (!flatOk) state.photoView = 'original';
  [...viewToggle.children].forEach((b) => { b.classList.toggle('on', b.dataset.v === state.photoView); b.disabled = b.dataset.v === 'flat' && !flatOk; });

  if (state.photoView === 'flat') {
    const [w, h] = state.sheet;
    photoSvg.setAttribute('viewBox', `${-w * 0.04} ${-h * 0.08} ${w * 1.08} ${h * 1.12}`);
    svgEl('image', { href: state.rectUrl, x: 0, y: 0, width: w, height: h, preserveAspectRatio: 'none' }, photoSvg);
    for (const t of state.traces) svgEl('polygon', { class: 'trace', points: t.points.map((p) => p.join(',')).join(' ') }, photoSvg);
    return;
  }
  const { width: w, height: h, id } = state.photo;
  photoSvg.setAttribute('viewBox', `${-w * 0.04} ${-h * 0.08} ${w * 1.08} ${h * 1.12}`);
  svgEl('image', { href: `/api/photo/${id}.jpg`, x: 0, y: 0, width: w, height: h }, photoSvg);
  if (!state.corners) return;
  const quad = svgEl('polygon', { class: 'quad', points: state.corners.map((p) => p.join(',')).join(' ') }, photoSvg);
  if (state.reference !== 'paper') return;
  const r = Math.max(w, h) / 70;
  state.corners.forEach((c, i) => {
    const hnd = svgEl('circle', { class: 'handle', cx: c[0], cy: c[1], r }, photoSvg);
    hnd.addEventListener('pointerdown', (e) => {
      hnd.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const p = toSvg(photoSvg, ev);
        state.corners[i] = [Math.round(p.x), Math.round(p.y)];
        hnd.setAttribute('cx', p.x); hnd.setAttribute('cy', p.y);
        quad.setAttribute('points', state.corners.map((q) => q.join(',')).join(' '));
      };
      const up = () => {
        hnd.removeEventListener('pointermove', move);
        state.manualCorners = true;
        setStatus('Corners adjusted — press Trace tools to re-run.', 'warn');
      };
      hnd.addEventListener('pointermove', move);
      hnd.addEventListener('pointerup', up, { once: true });
    });
  });
}

function toSvg(svg, e) {
  const pt = svg.createSVGPoint();
  pt.x = e.clientX; pt.y = e.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

/* ---------- layout view ---------- */

const layoutSvg = $('layoutSvg');

function fit() {
  const b = allBounds();
  if (b) {
    const c = clearance();
    const nx = Math.min(12, Math.max(1, Math.ceil((b[2] - b[0] + 2 * c + 2 * edge()) / GRID - 1e-9)));
    const ny = Math.min(12, Math.max(1, Math.ceil((b[3] - b[1] + 2 * c + 2 * edge()) / GRID - 1e-9)));
    $('nx').value = nx; $('ny').value = ny;
    const dx = (nx * GRID) / 2 - (b[0] + b[2]) / 2, dy = (ny * GRID) / 2 - (b[1] + b[3]) / 2;
    for (const it of state.items) { it.x += dx; it.y += dy; }
  }
  state.lastGrid = gridSize();
  renderLayout();
  markDirty();
}

function renderLayout() {
  const [nx, ny] = gridSize();
  const W = nx * GRID, H = ny * GRID, pad = 10, c = clearance();
  layoutSvg.setAttribute('viewBox', `${-pad} ${-pad} ${W + 2 * pad} ${H + 2 * pad + 4}`);
  layoutSvg.replaceChildren();
  svgEl('rect', { class: 'bin', x: 0.25, y: 0.25, width: W - 0.5, height: H - 0.5, rx: 3.75 }, layoutSvg);
  if (lipOn()) {
    const e = edge();
    svgEl('rect', { class: 'lip', x: e / 2 + GAP / 2, y: e / 2 + GAP / 2, width: W - e - GAP, height: H - e - GAP, rx: 3.75 - LIP_T / 2, 'stroke-width': LIP_T }, layoutSvg);
  }
  for (let i = 1; i < nx; i++) svgEl('line', { class: 'gridline', x1: i * GRID, y1: 0.25, x2: i * GRID, y2: H - 0.25 }, layoutSvg);
  for (let j = 1; j < ny; j++) svgEl('line', { class: 'gridline', x1: 0.25, y1: j * GRID, x2: W - 0.25, y2: j * GRID }, layoutSvg);
  svgEl('text', { class: 'dim', x: W / 2, y: H + 7 }, layoutSvg).textContent = `${nx} × ${ny} units · ${W - 0.5} × ${H - 0.5} mm`;
  const defs = svgEl('defs', {}, layoutSvg);
  // Holes first so tools draw on top of them.
  const ordered = [...state.items].sort((a, b) => (a.kind === 'hole' ? 0 : 1) - (b.kind === 'hole' ? 0 : 1));
  for (const it of ordered) {
    const g = svgEl('g', { class: `item ${it.kind}${it.id === state.sel ? ' sel' : ''}`, 'data-id': it.id, transform: `translate(${it.x} ${it.y}) rotate(${it.rot || 0})` }, layoutSvg);
    if (it.kind === 'hole') {
      svgEl('circle', { class: 'halo', r: it.d / 2 }, g);
      svgEl('circle', { class: 'edge', r: it.d / 2 }, g);
      continue;
    }
    const pts = it.pts.map((p) => p.join(',')).join(' ');
    svgEl('polygon', { class: 'halo', points: pts, 'stroke-width': 2 * c }, g);
    if (state.sheet && it.src) {
      svgEl('polygon', { points: pts }, svgEl('clipPath', { id: `clip${it.id}` }, defs));
      svgEl('image', { href: state.rectUrl, x: -it.src.cx, y: -it.src.cy, width: state.sheet[0], height: state.sheet[1], preserveAspectRatio: 'none', 'clip-path': `url(#clip${it.id})`, opacity: 0.8 }, g);
    }
    svgEl('polygon', { class: 'edge', points: pts }, g);
  }
  syncSelPanel();
}

let drag = null;
layoutSvg.addEventListener('pointerdown', (e) => {
  const g = e.target.closest('[data-id]');
  const id = g ? +g.dataset.id : null;
  if (id !== state.sel) { state.sel = id; renderLayout(); }
  if (id == null) return;
  const it = state.items.find((i) => i.id === id);
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

const selected = () => state.items.find((i) => i.id === state.sel) || null;

function syncSelPanel() {
  const it = selected();
  $('selPanel').hidden = !it;
  if (!it) return;
  const isHole = it.kind === 'hole';
  $('selName').textContent = isHole ? 'finger hole' : `tool · ${it.size[0]} × ${it.size[1]} mm`;
  $('selRotRow').hidden = isHole;
  $('selDiaRow').hidden = !isHole;
  $('selDepth').value = it.depth ?? '';
  $('selDepth').placeholder = `${defaultDepth()} (default)`;
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
  if (state.sel == null) return;
  state.items = state.items.filter((i) => i.id !== state.sel);
  state.sel = null;
  renderLayout(); markDirty();
}

$('selDepth').addEventListener('input', () => { const it = selected(); if (it) { const v = num('selDepth'); it.depth = v > 0 ? v : null; markDirty(); } });
$('selRot').addEventListener('input', () => { const it = selected(); if (it) { it.rot = num('selRot') || 0; renderLayoutKeepFocus('selRot'); markDirty(); } });
$('selDia').addEventListener('input', () => { const it = selected(); if (it && num('selDia') > 0) { it.d = num('selDia'); renderLayoutKeepFocus('selDia'); markDirty(); } });
$('rotL').addEventListener('click', () => rotateSel(-90));
$('rotR').addEventListener('click', () => rotateSel(90));
$('delBtn').addEventListener('click', deleteSel);
$('dupBtn').addEventListener('click', () => {
  const it = selected();
  if (!it) return;
  const copy = { ...structuredClone(it), id: state.nextId++, x: it.x + 6, y: it.y + 6 };
  state.items.push(copy); state.sel = copy.id;
  renderLayout(); markDirty();
});

function renderLayoutKeepFocus(id) {
  const v = $(id).value;
  renderLayout();
  $(id).value = v;
}

$('addHole').addEventListener('click', () => {
  const [nx, ny] = gridSize();
  const it = { id: state.nextId++, kind: 'hole', x: (nx * GRID) / 2, y: (ny * GRID) / 2, d: 22, rot: 0, depth: null };
  state.items.push(it); state.sel = it.id;
  showTab('layout'); renderLayout(); markDirty();
});

document.addEventListener('keydown', (e) => {
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
    const [nx, ny] = gridSize();
    const [px, py] = state.lastGrid || [nx, ny];
    const dx = ((nx - px) * GRID) / 2, dy = ((ny - py) * GRID) / 2;
    for (const it of state.items) { it.x += dx; it.y += dy; }
    state.lastGrid = [nx, ny];
    renderLayout(); markDirty();
  });
}
for (const id of ['depth', 'clearance']) $(id).addEventListener('input', () => { renderLayout(); markDirty(); });
for (const id of ['units', 'magnets']) $(id).addEventListener('change', markDirty);
$('lip').addEventListener('change', () => { $('lipHint').hidden = !lipOn(); renderLayout(); markDirty(); });
$('fitBtn').addEventListener('click', fit);
for (let u = 2; u <= 12; u++) $('units').add(new Option(`${u}u · ${u * 7} mm`, u));

/* ---------- build / preview / download ---------- */

function binRequest() {
  const [nx, ny] = gridSize();
  const d = defaultDepth();
  return {
    nx, ny,
    pockets: state.items.filter((i) => i.kind === 'tool').map((i) => ({ points: worldPts(i).map(([x, y]) => [+x.toFixed(3), +y.toFixed(3)]), depth: i.depth ?? d })),
    circles: state.items.filter((i) => i.kind === 'hole').map((i) => ({ x: i.x, y: i.y, d: i.d, depth: i.depth ?? d })),
    clearance: clearance(),
    height_units: parseInt($('units').value) || null,
    magnets: $('magnets').checked,
    lip: lipOn(),
  };
}

async function buildStl() {
  if (state.stl && !state.dirty) return state.stl;
  const rev = state.rev;
  const res = await postJson('/api/bin', binRequest());
  const info = JSON.parse(res.headers.get('X-Bin-Info'));
  const name = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '')?.[1] || 'bin.stl';
  const stl = { blob: await res.blob(), name, info };
  if (rev === state.rev) { state.stl = stl; state.dirty = false; }
  const [x, y, z] = info.size_mm;
  const notes = [];
  if (info.lip) notes.push('Includes stacking lip.');
  if (info.clipped) notes.push(`⚠ Some pockets reach past the bin ${info.lip ? 'lip' : 'wall'} and were trimmed — move them in or enlarge the grid.`);
  if (info.raised_height) notes.push(`Height raised to ${info.height_units}u to keep a solid floor under the deepest pocket.`);
  $('binInfo').textContent = `${info.nx}×${info.ny}×${info.height_units}u · ${x} × ${y} × ${z} mm · ${info.volume_cm3} cm³ solid. ${notes.join(' ')}`;
  return stl;
}

let previewTimer = null;
function schedulePreview() { clearTimeout(previewTimer); previewTimer = setTimeout(updatePreview, 350); }

async function updatePreview() {
  $('previewEmpty').hidden = state.items.length > 0;
  if (!state.items.length) { if (three) { clearMesh(); renderThree(); } return; }
  try {
    const stl = await buildStl();
    showMesh(await stl.blob.arrayBuffer());
  } catch (err) { setStatus(err.message, 'err'); }
}

$('downloadBtn').addEventListener('click', async () => {
  try {
    setStatus('Building STL…', 'busy');
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
  const key = `${size.x}x${size.y}`;
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
window.addEventListener('drop', (e) => { e.preventDefault(); if (e.target.closest?.('#drop') == null && e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });

$('traceBtn').addEventListener('click', trace);
$('sensitivity').addEventListener('input', () => { $('sensOut').textContent = $('sensitivity').value; });
$('paper').addEventListener('change', () => {
  $('customRow').hidden = $('paper').value !== 'custom';
  $('markerLink').href = `/markers?paper=${$('paper').value === 'custom' ? 'letter' : $('paper').value}`;
});
bindSeg('reference', (v) => {
  state.reference = v;
  $('markerHint').hidden = v !== 'markers';
  if (v === 'markers' && $('paper').value === 'custom') { $('paper').value = 'letter'; $('customRow').hidden = true; }
  $('paper').querySelector('[value=custom]').disabled = v === 'markers';
  renderPhoto();
});
bindSeg('method', (v) => { state.method = v; refreshConfig(); });

async function refreshConfig() {
  try {
    const cfg = await (await fetch('/api/config')).json();
    const hint = $('aiHint');
    hint.hidden = state.method !== 'ai' || (cfg.ai_installed && cfg.ai_model_downloaded);
    hint.textContent = !cfg.ai_installed
      ? 'AI mode needs ONNX Runtime: run “uv sync --extra ai” and restart.'
      : 'The first AI trace downloads a ~170 MB model into the models/ folder. After that it runs offline.';
  } catch {}
}

renderLayout();
refreshConfig();
