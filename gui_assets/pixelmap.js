"use strict";

mountThemeToggle($("theme-slot"));

const SVGNS = "http://www.w3.org/2000/svg";
const MAP_PX = 640;              // rendered map size in px
let GEO = null;                  // geometry payload
let RECTS = {};                  // "chip-ch" -> rect element
let CENTERS = {};                // "chip-ch" -> {cx, cy} in viewBox units
let VIEW = { w: 0, h: 0 };       // viewBox size
let SVG_EL = null;
let DENSITY = { counts: {}, total: 0, source: "none", file: null };
let selected = new Set();        // selected pixel keys
let queuedKeys = new Set();
let hotKeys = new Set();         // pixels tagged "hot" for the displayed run
let hotRun = null;               // {in_db, run_id, label, file} for the displayed file
let hotFile = null;              // basename the current hot set belongs to

/* ---- color ramp: 0 -> panel gray, low blue -> yellow -> red high ---------- */
function rampColor(t) {
  const hue = 220 - 220 * t;
  return "hsl(" + hue + " 85% 52%)";
}
function isDark() {
  return document.documentElement.getAttribute("data-theme") === "dark";
}
function zeroColor() { return isDark() ? "#232a36" : "#e3e8ef"; }

/* ---- build the SVG map ---------------------------------------------------- */
function buildMap() {
  const holder = $("map");
  holder.innerHTML = "";
  const pts = GEO.pixels;
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const ux = [...new Set(xs)].sort((a, b) => a - b);
  let pitch = Infinity;
  for (let i = 1; i < ux.length; i++) pitch = Math.min(pitch, ux[i] - ux[i - 1]);
  if (!isFinite(pitch) || pitch <= 0) pitch = 4.434;

  const spanX = maxX - minX + pitch, spanY = maxY - minY + pitch;
  const scale = MAP_PX / Math.max(spanX, spanY);
  const cell = pitch * scale;
  VIEW = { w: spanX * scale, h: spanY * scale };

  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${VIEW.w} ${VIEW.h}`);
  svg.setAttribute("width", "100%");
  svg.setAttribute("class", "pixsvg");
  SVG_EL = svg;

  pts.forEach((p) => {
    const r = document.createElementNS(SVGNS, "rect");
    const rx = (p.x - minX) * scale + (cell * 0.06);
    const ry = (maxY - p.y) * scale + (cell * 0.06);   // physics y up -> svg y down
    r.setAttribute("x", rx);
    r.setAttribute("y", ry);
    r.setAttribute("width", cell * 0.88);
    r.setAttribute("height", cell * 0.88);
    r.setAttribute("rx", cell * 0.12);
    r.setAttribute("fill", zeroColor());
    r.setAttribute("class", "pix");
    r.dataset.k = p.k;
    const title = document.createElementNS(SVGNS, "title");
    r.appendChild(title);
    r.addEventListener("click", (ev) => onPixelClick(ev, p.k));
    svg.appendChild(r);
    RECTS[p.k] = r;
    CENTERS[p.k] = { cx: rx + cell * 0.44, cy: ry + cell * 0.44 };
  });
  CELL = cell;
  HOT_LAYER = document.createElementNS(SVGNS, "g");
  HOT_LAYER.setAttribute("class", "hot-layer");
  HOT_LAYER.setAttribute("pointer-events", "none");
  svg.appendChild(HOT_LAYER);   // drawn on top of the pixels
  holder.appendChild(svg);
  attachDragSelect(svg);
}

let CELL = 8, HOT_LAYER = null;

function renderHot() {
  if (!HOT_LAYER) return;
  HOT_LAYER.innerHTML = "";
  const r = CELL * 0.22;
  hotKeys.forEach((k) => {
    const c = CENTERS[k];
    if (!c) return;
    const dot = document.createElementNS(SVGNS, "circle");
    dot.setAttribute("cx", c.cx);
    dot.setAttribute("cy", c.cy);
    dot.setAttribute("r", r);
    dot.setAttribute("class", "hot-dot");
    HOT_LAYER.appendChild(dot);
  });
  $("hot-count").textContent = hotKeys.size;
}

/* ---- selection ------------------------------------------------------------- */
let didDrag = false;

function onPixelClick(ev, k) {
  if (didDrag) { didDrag = false; return; }   // suppress click right after a drag
  if (ev.shiftKey) {
    if (selected.has(k)) selected.delete(k); else selected.add(k);
  } else {
    selected = new Set([k]);
  }
  updatePanel();
  recolor();
}

function clientToView(svg, cx, cy) {
  const b = svg.getBoundingClientRect();
  return { x: ((cx - b.left) / b.width) * VIEW.w,
           y: ((cy - b.top) / b.height) * VIEW.h };
}

function attachDragSelect(svg) {
  let start = null, band = null, additive = false;

  svg.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0) return;
    start = clientToView(svg, ev.clientX, ev.clientY);
    additive = ev.shiftKey;
    didDrag = false;
    ev.preventDefault();
  });

  window.addEventListener("mousemove", (ev) => {
    if (!start) return;
    const cur = clientToView(svg, ev.clientX, ev.clientY);
    const dx = Math.abs(cur.x - start.x), dy = Math.abs(cur.y - start.y);
    if (!band && (dx > 4 || dy > 4)) {
      band = document.createElementNS(SVGNS, "rect");
      band.setAttribute("class", "band");
      svg.appendChild(band);
      didDrag = true;
    }
    if (band) {
      band.setAttribute("x", Math.min(start.x, cur.x));
      band.setAttribute("y", Math.min(start.y, cur.y));
      band.setAttribute("width", dx);
      band.setAttribute("height", dy);
      ev.preventDefault();
    }
  });

  window.addEventListener("mouseup", (ev) => {
    if (!start) return;
    if (band) {
      const cur = clientToView(svg, ev.clientX, ev.clientY);
      const x1 = Math.min(start.x, cur.x), x2 = Math.max(start.x, cur.x);
      const y1 = Math.min(start.y, cur.y), y2 = Math.max(start.y, cur.y);
      const hit = [];
      for (const k in CENTERS) {
        const c = CENTERS[k];
        if (c.cx >= x1 && c.cx <= x2 && c.cy >= y1 && c.cy <= y2) hit.push(k);
      }
      if (!additive) selected = new Set();
      hit.forEach((k) => selected.add(k));
      band.remove();
      updatePanel();
      recolor();
    }
    start = null; band = null;
  });
}

$("btn-clear").onclick = () => {
  selected = new Set();
  updatePanel();
  recolor();
};

/* ---- coloring --------------------------------------------------------------
 * The map draws one scalar per pixel. Which scalar depends on the mode: hit
 * counts (0..max, optionally log) for the density and time-resolved views, or
 * the fitted trigger threshold in ke- (a narrow band, so vmin/vmax matter and
 * log makes no sense). Everything below the ramp is shared; `currentMetric()`
 * is the only place the two differ.
 */
function currentMetric() {
  if (THRESH.mode && THRESH.data && THRESH.data.values) {
    const vals = THRESH.data.values;
    return {
      values: vals, vmin: THRESH.vmin, vmax: THRESH.vmax, log: false,
      unit: "ke-", label: "trigger threshold [ke-]",
      fmt: (v) => v.toFixed(2) + " ke-",
    };
  }
  const counts = DENSITY.counts || {};
  let max = 0;
  for (const k in counts) if (counts[k] > max) max = counts[k];
  return {
    values: counts, vmin: 0, vmax: max, log: $("log-scale").checked,
    unit: "hits", label: "hits per pixel",
    fmt: (v) => v.toLocaleString() + " hits",
  };
}

function recolor() {
  if (!GEO) return;
  const M = currentMetric();
  const span = M.vmax - M.vmin;
  const denom = M.log ? Math.log10(M.vmax + 1) : span;
  GEO.pixels.forEach((p) => {
    const r = RECTS[p.k];
    const v = M.values[p.k];
    const has = v != null && !(M.vmin === 0 && v <= 0);
    if (!has || denom <= 0) {
      r.setAttribute("fill", zeroColor());
    } else {
      // clamp so values outside a pinned vmin/vmax saturate rather than vanish
      const t = M.log ? Math.log10(v + 1) / denom : (v - M.vmin) / denom;
      r.setAttribute("fill", rampColor(Math.max(0, Math.min(1, t))));
    }
    r.querySelector("title").textContent =
      p.k + "  (" + p.x.toFixed(1) + ", " + p.y.toFixed(1) + ") mm  " +
      (has ? M.fmt(v) : "no " + M.unit);
    r.classList.toggle("selected", selected.has(p.k));
    r.classList.toggle("queued", queuedKeys.has(p.k));
  });
  $("scale-min").textContent = M.vmin ? M.fmt(M.vmin) : "0";
  $("scale-max").textContent = M.vmax ? M.fmt(M.vmax) : "-";
  const g = $("scale-gradient");
  const stops = [];
  for (let i = 0; i <= 10; i++) stops.push(rampColor(i / 10));
  g.style.background = "linear-gradient(90deg," + stops.join(",") + ")";
  renderHot();
  renderHist();
}

/* ---- hit-count histogram (x = hits/pixel, y = number of pixels) ----------- */
function cssv(n, f) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim() || f;
}

function mk(name, attrs, text) {
  const e = document.createElementNS(SVGNS, name);
  for (const k in (attrs || {})) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}

function renderHist() {
  const holder = $("hist");
  if (!holder) return;
  const M = currentMetric();
  const isThresh = THRESH.mode && THRESH.data && THRESH.data.values;
  const vals = Object.values(M.values).filter((v) => v != null);
  const totalPix = GEO ? GEO.pixels.length : 4900;
  const zero = Math.max(0, totalPix - vals.length);
  $("hist-title").innerHTML = isThresh
    ? "<b>Threshold distribution</b> &mdash; number of channels per threshold bin"
    : "<b>Hit-count distribution</b> &mdash; number of pixels per hits-per-pixel bin";
  $("hist-note").textContent = vals.length
    ? totalPix.toLocaleString() + " pixels total — " + zero.toLocaleString() +
      (isThresh ? " without a fitted threshold (not binned)"
                : " with 0 hits (not binned)")
    : "";
  holder.innerHTML = "";
  if (!vals.length) return;

  const useLog = !$("hist-log") || $("hist-log").checked;
  // counts bin from 0 in integer steps; a float metric bins across its own range
  const max = Math.max(...vals);
  const lo = isThresh ? Math.min(...vals) : 0;
  const step = isThresh ? Math.max((max - lo) / 36, 1e-9)
                        : Math.max(1, Math.ceil(max / 36));   // ~36 bins
  const bins = new Array(Math.floor((max - lo) / step) + 1).fill(0);
  vals.forEach((v) => bins[Math.floor((v - lo) / step)]++);
  const binLo = (i) => lo + i * step;
  const fmtEdge = (v) => (isThresh ? v.toFixed(2) : Math.round(v).toLocaleString());

  const W = holder.clientWidth || 620, H = 170;
  const m = { l: 46, r: 8, t: 8, b: 30 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const yv = (n) => (useLog ? Math.log10(1 + n) : n);
  const ymax = Math.max(...bins.map(yv)) || 1;
  const yOf = (n) => m.t + ih - (yv(n) / ymax) * ih;

  const svg = mk("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%" });

  // y gridlines: powers of ten in log mode, quarters otherwise
  const ticks = [];
  if (useLog) {
    for (let p = 1; p <= Math.pow(10, Math.ceil(ymax)); p *= 10) {
      if (yv(p) <= ymax * 1.001) ticks.push(p);
    }
  } else {
    for (let q = 1; q <= 4; q++) ticks.push(Math.round((ymax / 4) * q));
  }
  ticks.forEach((t) => {
    const y = yOf(t);
    svg.appendChild(mk("line", { x1: m.l, y1: y, x2: m.l + iw, y2: y,
      stroke: cssv("--grid", "#2b3340") }));
    svg.appendChild(mk("text", { x: m.l - 6, y: y + 3, fill: cssv("--muted", "#889"),
      "font-size": 10, "text-anchor": "end" }, t.toLocaleString()));
  });
  svg.appendChild(mk("line", { x1: m.l, y1: m.t, x2: m.l, y2: m.t + ih,
    stroke: cssv("--axis", "#3a4452") }));
  svg.appendChild(mk("line", { x1: m.l, y1: m.t + ih, x2: m.l + iw, y2: m.t + ih,
    stroke: cssv("--axis", "#3a4452") }));

  const bw = iw / bins.length;
  const cspan = M.vmax - M.vmin;
  bins.forEach((n, i) => {
    if (!n) return;
    const b0 = binLo(i), b1 = isThresh ? binLo(i + 1)
                                       : Math.min(binLo(i + 1) - 1, max);
    const mid = (b0 + b1) / 2;
    // bar colored with the same ramp as the map, so shapes cross-reference
    const bar = mk("rect", {
      x: m.l + i * bw + 0.5, y: yOf(n),
      width: Math.max(1, bw - 1), height: m.t + ih - yOf(n),
      fill: rampColor(cspan > 0
        ? Math.max(0, Math.min(1, (mid - M.vmin) / cspan)) : 0), rx: 1,
    });
    bar.appendChild(mk("title", {},
      (b0 === b1 ? fmtEdge(b0) : fmtEdge(b0) + "–" + fmtEdge(b1)) + " " +
      M.unit + ": " + n.toLocaleString() +
      (isThresh ? " channel" : " pixel") + (n === 1 ? "" : "s")));
    svg.appendChild(bar);
  });

  // x tick labels: every ~6 bins, labelled with the bin's lower edge
  const strideX = Math.max(1, Math.round(bins.length / 6));
  for (let i = 0; i < bins.length; i += strideX) {
    svg.appendChild(mk("text", { x: m.l + i * bw, y: m.t + ih + 14,
      fill: cssv("--muted", "#889"), "font-size": 10, "text-anchor": "middle" },
      fmtEdge(binLo(i))));
  }
  svg.appendChild(mk("text", { x: m.l + iw / 2, y: H - 2,
    fill: cssv("--muted", "#889"), "font-size": 10, "text-anchor": "middle" },
    M.label));
  svg.appendChild(mk("text", { x: 12, y: m.t + ih / 2, fill: cssv("--muted", "#889"),
    "font-size": 10, "text-anchor": "middle",
    transform: `rotate(-90 12 ${m.t + ih / 2})` },
    isThresh ? "channels" : "pixels"));
  holder.appendChild(svg);
}

/* ---- hot pixels ------------------------------------------------------------ */
// The file being tagged = the single displayed run file (not a folder view).
function displayedFile() {
  if (!DENSITY) return null;
  if (DENSITY.source === "live" || DENSITY.source === "file") return DENSITY.file;
  if (DENSITY.source === "path" && DENSITY.mode === "file") return DENSITY.file;
  return null;   // folder / none -> hot tagging not applicable
}

async function syncHotForFile() {
  const file = displayedFile();
  if (file === hotFile) return;         // same file -> keep current hot set
  hotFile = file;
  hotKeys = new Set();
  hotRun = null;
  const actions = $("hot-actions"), note = $("hot-note"), addBtn = $("btn-add-db");
  if (!file) {
    $("hot-target").textContent =
      "Load a single run file (not a folder) to tag hot pixels.";
    actions.style.display = "none"; note.style.display = "none";
    addBtn.style.display = "none";
    renderHot();
    return;
  }
  try {
    const r = await api("/api/hotpixels?file=" + encodeURIComponent(file));
    hotRun = r;
    if (r.in_db) {
      (r.hot || []).forEach((k) => hotKeys.add(k));
      $("hot-target").innerHTML = "Tagging for <b>" + file + "</b> (run #" + r.run_id + ")";
      actions.style.display = ""; note.style.display = "none"; addBtn.style.display = "none";
    } else {
      $("hot-target").innerHTML = "<b>" + file + "</b>";
      actions.style.display = "none";
      note.style.display = "";
      note.textContent = "This file isn't in the run database yet, so hot pixels " +
        "can't be saved for it.";
      addBtn.style.display = (DENSITY.mode === "file" || DENSITY.source === "file")
        ? "" : "none";
    }
  } catch (e) { /* no descriptor etc. */ }
  renderHot();
}

async function saveHot() {
  if (!hotRun || !hotRun.in_db) return;
  try {
    await postJSON("/api/hotpixels", { run_id: hotRun.run_id, hot: [...hotKeys] });
  } catch (e) { toast(e.message); }
}

$("btn-hot").onclick = () => {
  if (!hotRun || !hotRun.in_db) { toast("This file isn't in the run database"); return; }
  if (!selected.size) { toast("Select pixel(s) first"); return; }
  selected.forEach((k) => hotKeys.add(k));
  renderHot(); saveHot();
};
$("btn-unhot").onclick = () => {
  if (!hotRun || !hotRun.in_db) return;
  selected.forEach((k) => hotKeys.delete(k));
  renderHot(); saveHot();
};
$("btn-clear-hot").onclick = () => {
  if (!hotRun || !hotRun.in_db) return;
  if (!hotKeys.size) return;
  if (!confirm("Clear all " + hotKeys.size + " hot pixels for this file?")) return;
  hotKeys = new Set();
  renderHot(); saveHot();
};
$("btn-add-db").onclick = async () => {
  if (!DENSITY || !DENSITY.path) { toast("No file path to add"); return; }
  try {
    const r = await postJSON("/api/runs/add", { path: DENSITY.path, kind: "auto" });
    toast("Added " + r.added + " / skipped " + r.skipped);
    hotFile = null;             // force a re-resolve now that it's in the DB
    await syncHotForFile();
  } catch (e) { toast(e.message); }
};

/* ---- selection panel ------------------------------------------------------- */
function updateThreshRow(thr) {
  $("pd-thresh-row").style.display = THRESH.mode ? "" : "none";
  if (!THRESH.mode) { $("spectrum-wrap").style.display = "none"; return; }
  const keys = [...selected];
  if (!thr) { $("pd-thresh").textContent = "-"; return; }
  if (keys.length === 1) {
    const v = thr.values[keys[0]];
    $("pd-thresh").textContent = v != null ? v.toFixed(2) + " ke-"
      : (thr.hits[keys[0]] ? "no rising edge found" : "no hits");
    loadSpectrum(keys[0]);
    return;
  }
  $("spectrum-wrap").style.display = "none";
  const vals = keys.map((k) => thr.values[k]).filter((v) => v != null);
  if (!vals.length) { $("pd-thresh").textContent = "— (none fitted)"; return; }
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  $("pd-thresh").textContent = "mean " + mean.toFixed(2) + " ke-  (" +
    Math.min(...vals).toFixed(2) + " .. " + Math.max(...vals).toFixed(2) +
    " over " + vals.length + "/" + keys.length + ")";
}

function updatePanel() {
  const n = selected.size;
  $("sel-count").textContent = n ? (n === 1 ? "1 pixel selected"
    : n + " pixels selected") : "";
  if (!n) {
    $("pix-info").style.display = "";
    $("pix-detail").style.display = "none";
    return;
  }
  $("pix-info").style.display = "none";
  $("pix-detail").style.display = "";
  // in threshold mode the hit counts come from the same clustered files the
  // thresholds were fitted to, so the two rows always describe one dataset
  const thr = (THRESH.mode && THRESH.data) ? THRESH.data : null;
  const counts = thr ? (thr.hits || {}) : (DENSITY.counts || {});
  const tot = thr ? (thr.n_hits || 0) : (DENSITY.total || 0);
  updateThreshRow(thr);

  if (n === 1) {
    const k = [...selected][0];
    const p = GEO.pixels.find((q) => q.k === k);
    const hits = counts[k] || 0;
    $("pd-id").textContent = k;
    $("pd-x").textContent = p.x.toFixed(2) + " mm";
    $("pd-y").textContent = p.y.toFixed(2) + " mm";
    $("pd-hits").textContent = hits.toLocaleString();
    let dens = tot ? ((100 * hits) / tot).toFixed(3) + "% of hits" : "-";
    if (DENSITY.duration_s) dens += "  (" + (hits / DENSITY.duration_s).toFixed(2) + " Hz)";
    $("pd-dens").textContent = dens;
    loadPixelConfig();
  } else {
    const pts = GEO.pixels.filter((q) => selected.has(q.k));
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    let hits = 0;
    selected.forEach((k) => { hits += counts[k] || 0; });
    $("pd-id").textContent = n + " pixels";
    $("pd-x").textContent = Math.min(...xs).toFixed(1) + " .. " + Math.max(...xs).toFixed(1) + " mm";
    $("pd-y").textContent = Math.min(...ys).toFixed(1) + " .. " + Math.max(...ys).toFixed(1) + " mm";
    $("pd-hits").textContent = hits.toLocaleString();
    let dens = tot ? ((100 * hits) / tot).toFixed(3) + "% of hits" : "-";
    if (DENSITY.duration_s) dens += "  (" + (hits / DENSITY.duration_s).toFixed(2) + " Hz)";
    $("pd-dens").textContent = dens;
    $("pd-trim").textContent = "— (multiple)";
    $("pd-csa").textContent = "— (multiple)";
  }
}

async function loadPixelConfig() {
  $("pd-trim").textContent = "-";
  $("pd-csa").textContent = "-";
  const folder = $("sel-asic-folder").value;
  if (!folder || selected.size !== 1) return;
  const [chip, ch] = [...selected][0].split("-").map(Number);
  try {
    const cfg = await api("/api/pixel/config?folder=" + encodeURIComponent(folder) +
                          "&chip=" + chip + "&ch=" + ch);
    $("pd-trim").textContent = cfg.pixel_trim_dac;
    $("pd-csa").textContent = cfg.csa_enable ? "1 (enabled)" : "0 (disabled)";
  } catch (e) { /* no folder / config yet */ }
}

/* ---- queue ------------------------------------------------------------------ */
async function refreshQueue() {
  let q;
  try { q = await api("/api/pixel/queue"); } catch (e) { return; }
  const list = $("queue-list");
  list.innerHTML = "";
  queuedKeys = new Set();
  const items = q.items || [];
  $("q-count").textContent = items.length;
  items.forEach((it) => {
    queuedKeys.add(it.chip + "-" + it.ch);
    const row = document.createElement("div");
    row.className = "queue-item";
    const what = it.action === "disable" ? "disable"
      : "trim " + (it.shift > 0 ? "+" : "") + it.shift;
    row.innerHTML = "<span>" + it.chip + "-" + it.ch + " &middot; " + what + "</span>";
    const x = document.createElement("button");
    x.className = "secondary qx";
    x.textContent = "✕";
    x.onclick = async () => {
      await postJSON("/api/pixel/queue/remove", { index: it.index });
      refreshQueue();
    };
    row.appendChild(x);
    list.appendChild(row);
  });
  if (!items.length) {
    list.innerHTML = '<div class="hint">No pending changes.</div>';
  }
  recolor();
}

async function queueChange(action, shift) {
  if (!selected.size) { toast("Select pixel(s) first — click or drag on the map"); return; }
  const items = [...selected].map((k) => {
    const [chip, ch] = k.split("-").map(Number);
    return { chip, ch, action, shift };
  });
  try {
    const r = await postJSON("/api/pixel/queue/batch", { items });
    let msg = "Queued " + r.added + " change(s) (" + r.will_apply + ")";
    if (r.errors && r.errors.length) msg += " — " + r.errors.length + " skipped";
    toast(msg);
    refreshQueue();
  } catch (e) { toast(e.message); }
}

$("btn-down").onclick = () => queueChange("trim", -1);
$("btn-up").onclick = () => queueChange("trim", +1);
$("btn-custom").onclick = () => {
  const s = parseInt($("custom-shift").value, 10);
  if (isNaN(s) || s === 0) { toast("Enter a non-zero shift"); return; }
  queueChange("trim", s);
};
$("btn-disable").onclick = () => queueChange("disable", 0);

$("btn-apply").onclick = async () => {
  const folder = $("sel-asic-folder").value;
  if (!folder) { toast("Choose an ASIC config folder"); return; }
  await withBusy($("btn-apply"), "Applying…", async () => {
    try {
      const r = await postJSON("/api/pixel/queue/apply", { folder });
      $("apply-log").textContent = (r.log || []).join("\n");
      toast("Applied " + r.applied + " change(s)");
      await refreshQueue();
      await loadPixelConfig();
    } catch (e) { toast(e.message); }
  });
};

$("sel-asic-folder").onchange = loadPixelConfig;
$("log-scale").onchange = recolor;
$("hist-log").onchange = renderHist;
document.addEventListener("themechange", recolor);

/* ---- polling ----------------------------------------------------------------- */
let LOADED = null;   // {path, kind} when viewing a chosen file/folder; null = current run

function densityUrl() {
  if (!LOADED) return "/api/pixelmap/density";
  return "/api/pixelmap/density?path=" + encodeURIComponent(LOADED.path) +
    "&kind=" + encodeURIComponent(LOADED.kind || "auto");
}

/* ---- time-resolved mode: scrub the map through the run(s) ---------------- */
const TIME = { mode: false, data: null, bin: 0, playing: null };

function timeUrl() {
  const b = "&bins=40";
  if (LOADED) return "/api/pixelmap/timedensity?path=" +
    encodeURIComponent(LOADED.path) + "&kind=" +
    encodeURIComponent(LOADED.kind || "auto") + b;
  return "/api/pixelmap/timedensity?" + b.slice(1);
}

async function pollTimeDensity() {
  if (!TIME.mode) return;
  let d;
  try { d = await api(timeUrl()); } catch (e) { $("time-warn").textContent = e.message; return; }
  if (d.error) { $("time-warn").textContent = d.error; return; }
  if (d.in_progress) {
    $("time-label").innerHTML = SPINNER_SVG + " building… " +
      (d.n_done || 0) + "/" + d.n_total + " files";
    setTimeout(pollTimeDensity, 1000);
    return;
  }
  TIME.data = d;
  $("time-warn").textContent = d.time_reliable === false
    ? "⚠ timestamps too disordered to reconstruct real time — showing acquisition progress"
    : (d.warning || "");
  const sl = $("time-slider");
  sl.max = d.bins - 1;
  if (TIME.bin > d.bins - 1) TIME.bin = d.bins - 1;
  sl.value = TIME.bin;
  drawTimeStrip();
  applyTimeBin();
}

function drawTimeStrip() {
  const holder = $("time-strip");
  const d = TIME.data;
  if (!d) { holder.innerHTML = ""; return; }
  const tot = d.per_bin_total || [];
  const W = holder.clientWidth || 600, H = 34, n = tot.length;
  const max = Math.max(1, ...tot);
  const bw = W / n;
  let bars = "";
  for (let i = 0; i < n; i++) {
    const h = (tot[i] / max) * (H - 4);
    const on = i === TIME.bin;
    bars += '<rect x="' + (i * bw) + '" y="' + (H - h) + '" width="' +
      Math.max(1, bw - 1) + '" height="' + h + '" fill="' +
      (on ? cssv("--accent", "#4f9dff") : cssv("--border", "#2b3340")) + '"/>';
  }
  holder.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' +
    H + '">' + bars + "</svg>";
}

function applyTimeBin() {
  const d = TIME.data;
  if (!d) return;
  const b = TIME.bin;
  const cumulative = $("time-cumulative").checked;
  const width = Math.max(0, parseInt($("time-width").value, 10) || 0);
  const lo = cumulative ? 0 : Math.max(0, b - width);
  const hi = cumulative ? b : Math.min(d.bins - 1, b + width);
  const counts = {};
  let total = 0;
  for (const k in d.pixels) {
    const arr = d.pixels[k];
    let s = 0;
    for (let i = lo; i <= hi; i++) s += arr[i] || 0;
    if (s > 0) { counts[k] = s; total += s; }
  }
  DENSITY = { source: "timed", counts, total, file: (LOADED ? LOADED.path : "current run") };
  const secs = (d.bin_seconds && d.bin_seconds[b] != null) ? d.bin_seconds[b] : null;
  const tstr = secs != null
    ? "t = " + secs.toFixed(1) + " s / " + (d.total_seconds || 0).toFixed(0) + " s"
    : "bin " + (b + 1) + " / " + d.bins;
  $("time-label").textContent = tstr + "  ·  " +
    (cumulative ? "cumulative" : "window ±" + width) + "  ·  " +
    total.toLocaleString() + " hits";
  $("src-badge").textContent = "TIME"; $("src-badge").className = "livebadge loaded";
  $("map-file").textContent = "time-resolved" + (LOADED ? " · " + LOADED.path : "");
  recolor();
  drawTimeStrip();
}

$("time-mode").onchange = () => {
  TIME.mode = $("time-mode").checked;
  $("time-controls").style.display = TIME.mode ? "" : "none";
  if (TIME.mode && $("thresh-mode").checked) {    // one mode at a time
    $("thresh-mode").checked = false;
    setThreshMode(false);
  }
  if (TIME.mode) {
    $("time-label").innerHTML = SPINNER_SVG + " building time bins…";
    pollTimeDensity();
  } else {
    if (TIME.playing) { clearInterval(TIME.playing); TIME.playing = null; $("time-play").textContent = "▶"; }
    TIME.data = null;
    refreshDensity();                   // back to total-density view
  }
};
$("time-slider").oninput = () => { TIME.bin = parseInt($("time-slider").value, 10) || 0; applyTimeBin(); };
$("time-cumulative").onchange = () => {
  $("time-width-wrap").style.display = $("time-cumulative").checked ? "none" : "";
  applyTimeBin();
};
$("time-width").oninput = applyTimeBin;
$("time-play").onclick = () => {
  if (TIME.playing) {
    clearInterval(TIME.playing); TIME.playing = null; $("time-play").textContent = "▶";
    return;
  }
  $("time-play").textContent = "⏸";
  TIME.playing = setInterval(() => {
    if (!TIME.data) return;
    TIME.bin = (TIME.bin + 1) % TIME.data.bins;
    $("time-slider").value = TIME.bin;
    applyTimeBin();
  }, 500);
};

/* ---- trigger-threshold mode ----------------------------------------------
 * Per-channel charge spectra from clustered files -> the 50% rising-edge of
 * each spectrum, i.e. the channel's effective trigger threshold. Same shape as
 * time-resolved mode: one background build on the server, polled until done,
 * then the map is driven from the returned values instead of hit counts.
 */
const THRESH = { mode: false, data: null, vmin: 0, vmax: 1, pinned: false };

function threshParams() {
  return {
    min_hits: $("th-min-hits").value || 50,
    max_hits: $("th-max-hits").value || 350,
    bins: $("th-bins").value || 50,
    q_to_ke: $("th-q-to-ke").value || 0.221,
  };
}

function threshUrl(base) {
  const p = threshParams();
  const parts = Object.keys(p).map((k) => k + "=" + encodeURIComponent(p[k]));
  if (LOADED) {
    parts.push("path=" + encodeURIComponent(LOADED.path));
    parts.push("kind=" + encodeURIComponent(
      LOADED.kind && LOADED.kind !== "auto" ? LOADED.kind : "clustered"));
  }
  return base + "?" + parts.join("&");
}

function applyThreshScale() {
  const d = THRESH.data;
  if (!d || !d.values) return;
  const vmin = parseFloat($("th-vmin").value), vmax = parseFloat($("th-vmax").value);
  if (THRESH.pinned && isFinite(vmin) && isFinite(vmax) && vmax > vmin) {
    THRESH.vmin = vmin; THRESH.vmax = vmax;
  } else {
    THRESH.vmin = d.vmin != null ? d.vmin : 0;
    THRESH.vmax = d.vmax != null ? d.vmax : 1;
    $("th-vmin").value = THRESH.vmin;
    $("th-vmax").value = THRESH.vmax;
  }
  recolor();
}

function showThreshError(msg) {
  // api() rejects on any payload carrying an "error", so this is where every
  // server-side failure lands -- clear the progress spinner with it, or the map
  // keeps claiming it is still reading files.
  THRESH.data = null;
  $("th-status").textContent = "";
  $("th-warn").textContent = msg;
  $("map-file").textContent = msg;
  $("map-stats").textContent = "-";
  recolor();
}

async function pollThresholds() {
  if (!THRESH.mode) return;
  let d;
  try { d = await api(threshUrl("/api/pixelmap/thresholds")); }
  catch (e) { showThreshError(e.message); return; }
  $("th-warn").textContent = d.warning || "";
  if (d.in_progress) {
    $("th-status").innerHTML = SPINNER_SVG + " reading " +
      (d.current_file || "…") + " — " + (d.n_done || 0) + "/" + d.n_total + " files";
    setTimeout(pollThresholds, 1000);
    return;
  }
  THRESH.data = d;
  const s = d.stats;
  $("th-status").textContent = s
    ? d.n_channels.toLocaleString() + " channels · median " + s.median.toFixed(2) +
      " ke- · mean " + s.mean.toFixed(2) + " ± " + s.std.toFixed(2) +
      " · range " + s.min.toFixed(2) + "–" + s.max.toFixed(2) +
      (d.n_missing ? "  ·  " + d.n_missing + " without a rising edge" : "")
    : "no channels passed the cut";
  $("src-badge").textContent = "THRESH";
  $("src-badge").className = "livebadge loaded";
  $("map-file").textContent = "trigger thresholds · " +
    (LOADED ? LOADED.path : "current run (clustered_data)") +
    "  ·  clusters with " + d.params.min_hits + "–" + d.params.max_hits + " hits";
  $("map-stats").textContent = s
    ? d.n_hits.toLocaleString() + " hits on " + s.n.toLocaleString() +
      " channels with a threshold"
    : "no hits passed the cluster-size cut — widen the range";
  applyThreshScale();
  if (selected.size) updatePanel();
}

function setThreshMode(on) {
  THRESH.mode = on;
  $("thresh-controls").style.display = on ? "" : "none";
  $("log-scale").disabled = on;
  $("pd-thresh-row").style.display = on ? "" : "none";
  $("spectrum-wrap").style.display = "none";
  if (on) {
    if ($("time-mode").checked) {                 // one mode at a time
      $("time-mode").checked = false;
      $("time-mode").onchange();
    }
    $("th-status").innerHTML = SPINNER_SVG + " building charge spectra…";
    pollThresholds();
  } else {
    THRESH.data = null;
    refreshDensity();                             // back to hit density
  }
}

$("thresh-mode").onchange = () => setThreshMode($("thresh-mode").checked);
$("th-apply").onclick = () => {
  THRESH.data = null;
  $("th-status").innerHTML = SPINNER_SVG + " building charge spectra…";
  pollThresholds();
};
$("th-auto").onclick = () => { THRESH.pinned = false; applyThreshScale(); };
["th-vmin", "th-vmax"].forEach((id) => {
  $(id).oninput = () => { THRESH.pinned = true; applyThreshScale(); };
});

/* ---- per-channel charge spectrum (side panel) ---------------------------- */
async function loadSpectrum(k) {
  const wrap = $("spectrum-wrap");
  if (!THRESH.mode || !k) { wrap.style.display = "none"; return; }
  wrap.style.display = "";
  const [chip, ch] = k.split("-");
  $("spectrum-note").textContent = "loading…";
  let d;
  try {
    d = await api(threshUrl("/api/pixelmap/threshold/pixel") +
                  "&chip=" + chip + "&ch=" + ch);
  } catch (e) {
    $("pix-spectrum").innerHTML = "";
    $("spectrum-note").textContent = e.message;
    return;
  }
  if (d.in_progress) {
    $("pix-spectrum").innerHTML = "";
    $("spectrum-note").textContent = "still building…";
    return;
  }
  renderSpectrum(d);
}

function renderSpectrum(d) {
  const holder = $("pix-spectrum");
  holder.innerHTML = "";
  if (!d.counts || !d.counts.length || !d.n_hits) {
    $("spectrum-note").textContent = "no hits passed the cluster-size cut";
    return;
  }
  $("spectrum-note").textContent = d.n_hits.toLocaleString() + " hits" +
    (d.threshold != null ? "  ·  threshold " + d.threshold.toFixed(2) + " ke-"
                         : "  ·  no rising edge found");
  const W = holder.clientWidth || 260, H = 120;
  const m = { l: 30, r: 6, t: 6, b: 22 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const edges = d.edges;
  const x0 = edges[0], x1 = edges[edges.length - 1];
  const xOf = (v) => m.l + ((v - x0) / (x1 - x0)) * iw;
  const ymax = Math.max(...d.counts) || 1;
  const yOf = (n) => m.t + ih - (n / ymax) * ih;

  const svg = mk("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%" });
  svg.appendChild(mk("line", { x1: m.l, y1: m.t + ih, x2: m.l + iw, y2: m.t + ih,
    stroke: cssv("--axis", "#3a4452") }));
  d.counts.forEach((n, i) => {
    if (!n) return;
    const xa = xOf(edges[i]), xb = xOf(edges[i + 1]);
    svg.appendChild(mk("rect", { x: xa, y: yOf(n), width: Math.max(1, xb - xa - 0.5),
      height: m.t + ih - yOf(n), fill: cssv("--accent", "#4f9dff"), rx: 1 }));
  });
  // half-max level and the interpolated 50% crossing the threshold comes from
  svg.appendChild(mk("line", { x1: m.l, y1: yOf(d.half_max), x2: m.l + iw,
    y2: yOf(d.half_max), stroke: cssv("--muted", "#889"),
    "stroke-dasharray": "3 3" }));
  if (d.threshold != null) {
    svg.appendChild(mk("line", { x1: xOf(d.threshold), y1: m.t,
      x2: xOf(d.threshold), y2: m.t + ih, stroke: cssv("--warn", "#e0a800"),
      "stroke-width": 1.5, "stroke-dasharray": "4 2" }));
  }
  // anchor the end labels inward so neither is clipped by the panel edge
  [[x0, "start"], [(x0 + x1) / 2, "middle"], [x1, "end"]].forEach(([v, anchor]) => {
    svg.appendChild(mk("text", { x: xOf(v), y: H - 8, fill: cssv("--muted", "#889"),
      "font-size": 9, "text-anchor": anchor }, v.toFixed(1)));
  });
  svg.appendChild(mk("text", { x: m.l - 4, y: m.t + 8, fill: cssv("--muted", "#889"),
    "font-size": 9, "text-anchor": "end" }, ymax));
  holder.appendChild(svg);
}

let densityFetching = false;
let densityQueued = false;

async function refreshDensity() {
  if (TIME.mode) return;                // slider drives the map in time mode
  if (THRESH.mode) return;              // threshold values drive it in that mode
  // Never drop a refresh: stepping quickly through database runs used to leave
  // the map (and the hot-pixel panel) showing the previous file, because the
  // newer request was discarded while the older one was still in flight.
  if (densityFetching) { densityQueued = true; return; }
  densityFetching = true;
  try { DENSITY = await api(densityUrl()); } catch (e) { return; }
  finally {
    densityFetching = false;
    if (densityQueued) { densityQueued = false; setTimeout(refreshDensity, 0); }
  }
  const badge = $("src-badge");
  const link = DENSITY.rundb_link;
  const linkNote = link
    ? (link.added || link.skipped)
      ? "  · DB: +" + link.added + " linked" +
        (link.skipped ? ", " + link.skipped + " already there" : "")
      : (link.errors && link.errors.length ? "  · DB: not linked (" + link.errors[0] + ")" : "")
    : "";
  if (DENSITY.source === "path" && DENSITY.in_progress) {
    // folder is being aggregated file-by-file: draw what we have so far
    badge.textContent = "LOADING " + (DENSITY.n_done || 0) + "/" + DENSITY.n_total;
    badge.className = "livebadge loading";
    $("map-file").innerHTML = SPINNER_SVG + " processing " +
      (DENSITY.current_file || "…") + "  — " + (DENSITY.n_done || 0) + " of " +
      DENSITY.n_total + " files done" +
      (DENSITY.warning ? "  ⚠ " + DENSITY.warning : "");
    setTimeout(refreshDensity, 1000);   // follow per-file progress closely
  } else if (DENSITY.source === "path") {
    const many = DENSITY.mode === "folder";
    badge.textContent = many ? "FOLDER" : "FILE";
    badge.className = "livebadge loaded";
    // a single file stepped to from a folder says so, so "1 of 12" is visible
    const inFolder = !many && FOLDER.dir && FOLDER.idx >= 0
      ? "  ·  file " + (FOLDER.idx + 1) + " of " + FOLDER.files.length +
        " in " + basename(FOLDER.dir)
      : "";
    $("map-file").textContent = (many
      ? "folder: " + DENSITY.file + "  (" + DENSITY.n_files + " file" +
        (DENSITY.n_files === 1 ? "" : "s") + ")"
      : DENSITY.file) + "  [" + DENSITY.kind + "]" + inFolder +
      (DENSITY.warning ? "  ⚠ " + DENSITY.warning : "") + (many ? linkNote : "");
  } else if (DENSITY.source === "live") {
    badge.textContent = "LIVE";
    badge.className = "livebadge live";
    $("map-file").textContent = DENSITY.file + "  (parsing " +
      (DENSITY.parsed_msgs || 0).toLocaleString() + " / " +
      (DENSITY.total_msgs || 0).toLocaleString() + " msgs)";
  } else if (DENSITY.source === "file") {
    badge.textContent = "last file";
    badge.className = "livebadge";
    $("map-file").textContent = DENSITY.file || "-";
  } else {
    badge.textContent = "no data";
    badge.className = "livebadge";
    $("map-file").textContent = (DENSITY.error ||
      (LOADED ? "Nothing readable at that path." : "No raw or converted files yet.")) +
      linkNote;
  }
  const px = Object.keys(DENSITY.counts || {}).length;
  $("map-stats").textContent =
    (DENSITY.total || 0).toLocaleString() + " hits on " + px + " pixels";
  await syncHotForFile();
  recolor();
  if (selected.size) updatePanel();
}

async function loadPath(p, kind, busyBtn) {
  LOADED = { path: p, kind: kind || "auto" };
  $("btn-live").style.display = "";
  const badge = $("src-badge");
  badge.textContent = "LOADING";
  badge.className = "livebadge loading";
  $("map-file").innerHTML = SPINNER_SVG + " Reading " + p + " …";
  if (THRESH.mode) { THRESH.data = null; pollThresholds(); return; }
  if (TIME.mode) { TIME.data = null; TIME.bin = 0; pollTimeDensity(); return; }
  await withBusy(busyBtn || $("btn-load"), "Loading…", refreshDensity);
}

async function doLoad() {
  const p = $("load-path").value.trim();
  if (!p) { toast("Enter a file or folder path"); return; }
  try { localStorage.setItem("larpix-pixmap-path", p); } catch (e) {}
  DB.idx = -1;                       // a typed path is not a database position
  syncDbPos();
  clearFolder();
  await loadFolderList(p);           // a directory also gains a per-file stepper
  await loadPath(p, $("load-kind").value);
}
function backToLive() {
  LOADED = null;
  DB.idx = -1; syncDbPos();
  clearFolder();
  $("btn-live").style.display = "none";
  if (THRESH.mode) { THRESH.data = null; pollThresholds(); return; }
  if (TIME.mode) { TIME.data = null; TIME.bin = 0; pollTimeDensity(); return; }
  refreshDensity();
}
$("btn-load").onclick = doLoad;
$("btn-live").onclick = backToLive;
$("load-path").addEventListener("keydown", (e) => { if (e.key === "Enter") doLoad(); });
try {
  const saved = localStorage.getItem("larpix-pixmap-path");
  if (saved) $("load-path").value = saved;
} catch (e) {}

/* ---- step through the files inside a loaded folder -----------------------
 * Loading a directory draws the whole-folder aggregate; this walks the same
 * .h5 files one at a time without retyping paths. idx -1 is the aggregate,
 * 0..n-1 are the individual files (server order, so the numbering matches).
 * The path box keeps showing the *folder*, so Load always re-establishes the
 * folder rather than pinning whichever file happens to be on screen.
 */
const FOLDER = { dir: null, files: [], idx: -1 };

function clearFolder() {
  FOLDER.dir = null; FOLDER.files = []; FOLDER.idx = -1;
  syncFolderPos();
}

function syncFolderPos() {
  const n = FOLDER.files.length;
  $("folder-row").style.display = FOLDER.dir && n ? "" : "none";
  if (!FOLDER.dir || !n) return;
  $("folder-file").value = String(FOLDER.idx);
  $("folder-pos").textContent = FOLDER.idx < 0
    ? "all " + n + " file" + (n === 1 ? "" : "s")
    : (FOLDER.idx + 1) + " / " + n;
  $("folder-prev").disabled = FOLDER.idx <= -1;
  $("folder-next").disabled = FOLDER.idx >= n - 1;
}

// Populate the stepper for a path. A file (or a bad path) just hides the row.
async function loadFolderList(p) {
  let d;
  try { d = await api("/api/pixelmap/folder?path=" + encodeURIComponent(p)); }
  catch (e) { clearFolder(); return; }
  if (!d.is_dir || !(d.files || []).length) { clearFolder(); return; }
  FOLDER.dir = d.path; FOLDER.files = d.files; FOLDER.idx = -1;
  const sel = $("folder-file");
  sel.innerHTML = "";
  const agg = document.createElement("option");
  agg.value = "-1";
  agg.textContent = "▦ whole folder — all " + FOLDER.files.length + " files combined";
  sel.appendChild(agg);
  FOLDER.files.forEach((f, i) => {
    const o = document.createElement("option");
    o.value = String(i);
    o.textContent = (i + 1) + ".  " + f.name +
      (f.size_mb ? "  ·  " + f.size_mb + " MB" : "");
    sel.appendChild(o);
  });
  syncFolderPos();
}

async function loadFolderIndex(i, btn) {
  if (!FOLDER.dir || i < -1 || i >= FOLDER.files.length) return;
  FOLDER.idx = i;
  syncFolderPos();
  // -1 goes back to the folder aggregate; otherwise load just that one file
  await loadPath(i < 0 ? FOLDER.dir : FOLDER.files[i].path,
                 $("load-kind").value, btn);
}

function stepFolder(dir, btn) {
  if (!FOLDER.dir) return;
  const i = FOLDER.idx + dir;
  if (i < -1 || i >= FOLDER.files.length) return;
  loadFolderIndex(i, btn);
}

$("folder-file").onchange = () => {
  const i = parseInt($("folder-file").value, 10);
  if (!isNaN(i)) loadFolderIndex(i);
};
$("folder-prev").onclick = () => stepFolder(-1, $("folder-prev"));
$("folder-next").onclick = () => stepFolder(1, $("folder-next"));

/* ---- step through the run database --------------------------------------
 * The database stores basenames, so the server resolves each row to a real
 * path under the run tree; this just walks that ordered list (oldest first).
 */
const DB = { files: [], idx: -1 };

async function loadDbList(keepSelection) {
  const want = keepSelection && DB.idx >= 0 ? DB.files[DB.idx].path : null;
  let d;
  try {
    d = await api("/api/rundb/files?kind=" +
                  encodeURIComponent($("load-kind").value));
  } catch (e) { DB.files = []; DB.idx = -1; syncDbPos(); return; }
  DB.files = d.files || [];
  const sel = $("db-run");
  sel.innerHTML = "";
  DB.files.forEach((f, i) => {
    const o = document.createElement("option");
    o.value = String(i);
    o.textContent = (f.ts || "?") + "  ·  " + f.name +
      (f.hv ? "  ·  HV " + f.hv : "") +
      (f.n_hot ? "  ·  " + f.n_hot + " hot" : "") +
      (f.exists ? "" : "   (file missing)");
    o.disabled = !f.exists;
    sel.appendChild(o);
  });
  DB.idx = want ? DB.files.findIndex((f) => f.path === want) : -1;
  if (DB.idx >= 0) sel.value = String(DB.idx);
  syncDbPos(d);
}

// is there a loadable (on-disk) run further along in this direction?
function hasMoreDb(dir) {
  let i = DB.idx < 0 ? (dir > 0 ? -1 : DB.files.length) : DB.idx;
  for (i += dir; i >= 0 && i < DB.files.length; i += dir)
    if (DB.files[i].exists) return true;
  return false;
}

function syncDbPos(meta) {
  const n = DB.files.length;
  const missing = meta && meta.n_missing;
  $("db-pos").textContent = !n
    ? "no runs in database"
    : (DB.idx >= 0 ? (DB.idx + 1) + " / " + n : "— / " + n) +
      (missing ? "  (" + missing + " missing on disk)" : "");
  $("db-prev").disabled = !n || !hasMoreDb(-1);
  $("db-next").disabled = !n || !hasMoreDb(1);
}

async function loadDbIndex(i, btn) {
  if (i < 0 || i >= DB.files.length) return;
  const f = DB.files[i];
  if (!f.exists) { toast("That run's file is not on disk: " + f.name); return; }
  DB.idx = i;
  $("db-run").value = String(i);
  $("load-path").value = f.path;      // keep the path box in sync
  clearFolder();                      // a DB run is its own navigation context
  syncDbPos();
  await loadPath(f.path, $("load-kind").value, btn);
}

// Step over runs whose file isn't on disk: a gap in the data must not dead-end
// the walk through the database.
function stepDb(dir, btn) {
  if (!DB.files.length) return;
  let i = DB.idx < 0 ? (dir > 0 ? -1 : DB.files.length) : DB.idx;
  for (i += dir; i >= 0 && i < DB.files.length; i += dir) {
    if (DB.files[i].exists) return loadDbIndex(i, btn);
  }
  toast(dir > 0 ? "No further run with a file on disk"
                : "No earlier run with a file on disk");
}

$("db-run").onchange = () => loadDbIndex(parseInt($("db-run").value, 10) || 0);
$("db-prev").onclick = () => stepDb(-1, $("db-prev"));
$("db-next").onclick = () => stepDb(1, $("db-next"));
// changing how files are read changes which DB column each row resolves to
$("load-kind").addEventListener("change", () => loadDbList(true));
loadDbList();

async function refreshState() {
  let st;
  try { st = await api("/api/state"); } catch (e) { return; }
  $("desc-status").innerHTML = st.active
    ? "Descriptor: <b>" + st.active + "</b>"
    : "<span style='color:var(--err)'>No descriptor set — open the flow page first.</span>";
  fillSelect($("sel-asic-folder"), (st.files && st.files.asic_folders) || []);
}

$("map").innerHTML = loadingHTML("Loading pixel geometry…");
(async () => {
  try {
    GEO = await api("/api/pixelmap/geometry");
  } catch (e) {
    $("map").innerHTML = '<div class="hint">Geometry yaml not found: ' + e.message + "</div>";
    return;
  }
  buildMap();
  $("map-file").innerHTML = SPINNER_SVG + " Loading hit density…";
  await refreshState();
  await refreshDensity();
  await refreshQueue();
  setInterval(refreshDensity, 3000);
  setInterval(refreshState, 10000);
  setInterval(refreshQueue, 5000);
})();
