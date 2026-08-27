"use strict";

mountThemeToggle($("theme-slot"));
document.addEventListener("themechange", () => { draw(); if (EVD.open) evdRender(); });

const SVGNS = "http://www.w3.org/2000/svg";
let LAST = null;                 // /api/lifetime/series payload
let computeJob = null, logOffset = 0;
let VIEW = null;                 // {tmin,tmax,ymin,ymax} zoom/pan, null = auto-fit
let DOMAIN = {};                 // full data extent (reset target)
let PLOT = null;                 // last-drawn transform + plot rect
let plotDragged = false;         // suppress point-click right after a pan-drag

const cssVar = (n, f) =>
  getComputedStyle(document.documentElement).getPropertyValue(n).trim() || f;
const fmt = (v, d = 2) => (v == null || isNaN(v)) ? "—" : (+v).toFixed(d);

function el(name, attrs, text) {
  const e = document.createElementNS(SVGNS, name);
  for (const k in (attrs || {})) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}
function niceMax(v) {
  if (!v || v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}
function seriesColor(which) {
  return which === "main" ? cssVar("--accent", "#4f9dff")
                          : cssVar("--warn", "#d29922");
}

/* ------------------------------------------------------------------ plot */
function collectSeries() {
  const out = [];
  if (LAST && LAST.main && (LAST.main.points || []).length)
    out.push({ id: "main", label: "current run", pts: LAST.main.points });
  if (LAST && LAST.overlay && (LAST.overlay.points || []).length)
    out.push({ id: "overlay",
               label: "overlay: " + (LAST.overlay.label || "folder"),
               pts: LAST.overlay.points });
  return out;
}

function draw() {
  const holder = $("plot");
  holder.innerHTML = "";
  const series = collectSeries();
  if (!series.length) {
    holder.innerHTML = '<div class="empty">No lifetime points yet. Press ' +
      '<b>Compute current run</b> once you have self-trigger data with ' +
      'anode-cathode crossers.</div>';
    $("legend").innerHTML = "";
    return;
  }
  const W = holder.clientWidth || 940, H = 380;
  const m = { l: 62, r: 18, t: 16, b: 52 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H });

  // common ranges over all series
  let tms = [], ymaxCand = 0;
  series.forEach((s) => s.pts.forEach((p) => {
    const t = new Date(p.time).getTime();
    tms.push(t);
    const up = p.tau_ms + (p.tau_err_pos_ms == null ? 0 : p.tau_err_pos_ms);
    if (isFinite(up)) ymaxCand = Math.max(ymaxCand, up);
    ymaxCand = Math.max(ymaxCand, p.tau_ms || 0);
  }));
  let tmin = Math.min(...tms), tmax = Math.max(...tms);
  if (tmin === tmax) { tmin -= 1000; tmax += 1000; }
  // full data domain (used for auto-fit and as reset target)
  DOMAIN.tmin = tmin; DOMAIN.tmax = tmax; DOMAIN.ymax = niceMax(ymaxCand);
  // apply zoom/pan view if set, else auto-fit
  if (VIEW) { tmin = VIEW.tmin; tmax = VIEW.tmax; }
  const ymin = VIEW ? VIEW.ymin : 0;
  const ymax = VIEW ? VIEW.ymax : DOMAIN.ymax;
  $("btn-reset-zoom").style.display = VIEW ? "" : "none";
  // stash the current transform + plot rect so wheel/drag handlers can map back
  PLOT = { m, iw, ih, W, H, tmin, tmax, ymin, ymax };

  const xOf = (t) => m.l + ((t - tmin) / (tmax - tmin)) * iw;
  const yOf = (v) => m.t + ih - ((Math.min(v, ymax) - ymin) / (ymax - ymin)) * ih;

  for (let g = 0; g <= 4; g++) {
    const val = ymin + (ymax - ymin) * g / 4, y = yOf(val);
    svg.appendChild(el("line", { x1: m.l, y1: y, x2: m.l + iw, y2: y,
      stroke: cssVar("--grid", "#2b3340") }));
    svg.appendChild(el("text", { x: m.l - 8, y: y + 4, fill: cssVar("--muted", "#889"),
      "font-size": 11, "text-anchor": "end" }, String(+val.toFixed(2))));
  }
  svg.appendChild(el("line", { x1: m.l, y1: m.t, x2: m.l, y2: m.t + ih,
    stroke: cssVar("--axis", "#3a4452") }));
  svg.appendChild(el("line", { x1: m.l, y1: m.t + ih, x2: m.l + iw, y2: m.t + ih,
    stroke: cssVar("--axis", "#3a4452") }));
  svg.appendChild(el("text", { x: 15, y: m.t + ih / 2, fill: cssVar("--muted", "#889"),
    "font-size": 11, "text-anchor": "middle",
    transform: `rotate(-90 15 ${m.t + ih / 2})` }, "τ  [ms]"));

  const nlab = 6;
  for (let i = 0; i < nlab; i++) {
    const t = tmin + (i / (nlab - 1)) * (tmax - tmin);
    const d = new Date(t);
    const lab = (d.getMonth() + 1) + "/" + d.getDate() + " " +
      String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    svg.appendChild(el("text", { x: xOf(t), y: m.t + ih + 18,
      fill: cssVar("--muted", "#889"), "font-size": 10, "text-anchor": "middle" }, lab));
  }

  series.forEach((s) => {
    const color = seriesColor(s.id);
    s.pts.forEach((p) => {
      if (p.tau_ms == null) return;
      const x = xOf(new Date(p.time).getTime());
      const y = yOf(p.tau_ms);

      // whole point excluded from the plot: faint grey ×, right-click restores
      if (p.point_excluded) {
        const g = cssVar("--muted", "#889");
        const mk = el("g", { cursor: "pointer", opacity: 0.5 });
        mk.appendChild(el("line", { x1: x - 5, y1: y - 5, x2: x + 5, y2: y + 5,
          stroke: g, "stroke-width": 1.6 }));
        mk.appendChild(el("line", { x1: x - 5, y1: y + 5, x2: x + 5, y2: y - 5,
          stroke: g, "stroke-width": 1.6 }));
        mk.appendChild(el("title", {}, "point " + p.point + " — excluded\n" +
          "right-click to restore"));
        mk.addEventListener("contextmenu", (ev) => {
          ev.preventDefault();
          setPointExcluded(s.id, p.point - 1, false);
        });
        svg.appendChild(mk);
        return;
      }
      const pos = p.tau_err_pos_ms;
      const up = pos == null ? m.t : yOf(p.tau_ms + pos);
      const dn = yOf(Math.max(0, p.tau_ms - (p.tau_err_neg_ms || 0)));
      svg.appendChild(el("line", { x1: x, y1: up, x2: x, y2: dn,
        stroke: cssVar("--muted", "#889"), "stroke-width": 1 }));
      svg.appendChild(el("line", { x1: x - 3, y1: dn, x2: x + 3, y2: dn,
        stroke: cssVar("--muted", "#889") }));
      if (pos != null)
        svg.appendChild(el("line", { x1: x - 3, y1: up, x2: x + 3, y2: up,
          stroke: cssVar("--muted", "#889") }));

      // ghost of the original tau when exclusions changed it
      if (p.tau_ms_orig != null && Math.abs(p.tau_ms_orig - p.tau_ms) > 1e-9) {
        svg.appendChild(el("circle", { cx: x, cy: yOf(p.tau_ms_orig), r: 3.5,
          fill: "none", stroke: color, "stroke-width": 1, "stroke-dasharray": "2 2",
          opacity: 0.55 }));
      }
      const c = el("circle", { cx: x, cy: y, r: 4.5, fill: color,
        stroke: cssVar("--text", "#fff"), "stroke-width": 0.6, cursor: "pointer" });
      if (p.n_excluded) {
        svg.appendChild(el("circle", { cx: x, cy: y, r: 7.5, fill: "none",
          stroke: color, "stroke-width": 1.4 }));
      }
      const posTxt = pos == null ? "∞" : fmt(pos);
      let tip = "point " + p.point + "  " + p.time +
        "\nτ = " + fmt(p.tau_ms, 3) + " +" + posTxt + " / -" + fmt(p.tau_err_neg_ms, 3) + " ms" +
        "\n" + p.n_tracks + " AC tracks";
      if (p.n_excluded) tip += "\n" + p.n_excluded + " excluded (original τ = " +
        fmt(p.tau_ms_orig, 3) + " ms)";
      tip += "\nclick for 3D event display · right-click to exclude point";
      c.appendChild(el("title", {}, tip));
      c.addEventListener("click", () => {
        if (plotDragged) { plotDragged = false; return; }  // was a pan
        openDisplay(s.id, p.point - 1);
      });
      c.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        setPointExcluded(s.id, p.point - 1, true);
      });
      svg.appendChild(c);
    });
  });
  holder.appendChild(svg);

  const nDropped = series.reduce((a, s) =>
    a + s.pts.filter((p) => p.point_excluded).length, 0);
  $("legend").innerHTML = series.map((s) => {
    const active = s.pts.filter((p) => !p.point_excluded).length;
    return '<span><span class="sw" style="background:' + seriesColor(s.id) +
      '"></span>' + s.label + " (" + active + " pts)</span>";
  }).join("") +
    '<span><span class="sw" style="background:none;border:1.4px solid ' +
    seriesColor("main") + '"></span>ring = point has excluded tracks</span>' +
    (nDropped ? '<span><span class="sw" style="background:none">✕</span>' +
      nDropped + " excluded point" + (nDropped > 1 ? "s" : "") +
      " (right-click to restore)</span>" : "");
}

/* ---- zoom / pan / reset on the τ-vs-time plot --------------------------- */
// pixel -> data coordinates using the last-drawn transform
function pxToData(px, py) {
  if (!PLOT) return null;
  const holder = $("plot");
  const rect = holder.getBoundingClientRect();
  const sx = PLOT.W / rect.width, sy = PLOT.H / rect.height;
  const vx = (px - rect.left) * sx, vy = (py - rect.top) * sy;
  const t = PLOT.tmin + (vx - PLOT.m.l) / PLOT.iw * (PLOT.tmax - PLOT.tmin);
  const v = PLOT.ymin + (PLOT.m.t + PLOT.ih - vy) / PLOT.ih * (PLOT.ymax - PLOT.ymin);
  return { t, v };
}

$("plot").addEventListener("wheel", (e) => {
  if (!PLOT) return;
  e.preventDefault();
  const at = pxToData(e.clientX, e.clientY);
  if (!at) return;
  const f = e.deltaY > 0 ? 1.15 : 1 / 1.15;       // zoom out / in
  VIEW = {
    tmin: PLOT.tmin, tmax: PLOT.tmax,              // x (time) unchanged
    ymin: at.v - (at.v - PLOT.ymin) * f, ymax: at.v + (PLOT.ymax - at.v) * f,
  };
  if (VIEW.ymin < 0) VIEW.ymin = 0;
  draw();
}, { passive: false });

(() => {
  let panning = false, last = null, moved = 0;
  $("plot").addEventListener("mousedown", (e) => {
    if (!PLOT) return;
    panning = true; moved = 0; last = { x: e.clientX, y: e.clientY };
    plotDragged = false;
  });
  window.addEventListener("mousemove", (e) => {
    if (!panning || !PLOT) return;
    moved += Math.abs(e.clientX - last.x) + Math.abs(e.clientY - last.y);
    if (moved > 4) plotDragged = true;
    // start from current view (or the auto-fit domain) then shift by the drag
    const base = VIEW || { tmin: PLOT.tmin, tmax: PLOT.tmax,
                           ymin: PLOT.ymin, ymax: PLOT.ymax };
    const holder = $("plot"), rect = holder.getBoundingClientRect();
    const dt = -(e.clientX - last.x) / rect.width * (base.tmax - base.tmin) *
      (PLOT.W / PLOT.iw);
    const dv = (e.clientY - last.y) / rect.height * (base.ymax - base.ymin) *
      (PLOT.H / PLOT.ih);
    VIEW = { tmin: base.tmin + dt, tmax: base.tmax + dt,
             ymin: Math.max(0, base.ymin + dv), ymax: base.ymax + dv };
    last = { x: e.clientX, y: e.clientY };
    draw();
  });
  window.addEventListener("mouseup", () => { panning = false; });
})();

$("btn-reset-zoom").onclick = () => { VIEW = null; draw(); };

// exclude / restore a whole point from the plot (point is 0-based)
async function setPointExcluded(series, point, excluded) {
  try {
    await postJSON("/api/lifetime/excludepoint", { series, point, excluded });
  } catch (e) { return; }
  await loadSeries();       // redraw with the point dropped / restored
}

// how a series was binned, for plot titles ("group_by" is absent on older runs)
function binningLabel(meta) {
  if (!meta) return "?";
  return meta.group_by === "file" ? "one point per run"
                                  : (meta.bin_size || "?") + " AC tracks/point";
}

function kpis() {
  const box = $("kpis");
  const main = (LAST && LAST.main) || {};
  const pts = main.points || [];
  const meta = main.meta || {};
  if (!pts.length) { box.innerHTML = ""; return; }
  const taus = pts.map((p) => p.tau_ms).filter((v) => v != null);
  const latest = taus[taus.length - 1];
  const mean = taus.reduce((a, b) => a + b, 0) / (taus.length || 1);
  const nExcl = pts.reduce((a, p) => a + (p.n_excluded || 0), 0);
  const kpi = (v, l) => '<div class="kpi"><div class="v">' + v +
    '</div><div class="l">' + l + "</div></div>";
  box.innerHTML =
    kpi(fmt(latest) + " ms", "Latest τ") +
    kpi(fmt(mean) + " ms", "Mean τ") +
    kpi(pts.length, "Points") +
    kpi(meta.n_tracks != null ? meta.n_tracks : "—", "AC tracks") +
    kpi(nExcl, "Excluded tracks");
  $("plot-title").textContent =
    "Lifetime τ vs time — " + binningLabel(meta) + ", E = " +
    (meta.efield || "?") + " V/cm";
}

/* ------------------------------------------------- series load + compute */
let seriesLoaded = false;
let seriesFetching = false;

async function loadSeries() {
  if (seriesFetching) return;         // never overlap polls
  seriesFetching = true;
  let data;
  try { data = await api("/api/lifetime/series"); } catch (e) {
    if (!seriesLoaded) $("plot").innerHTML = loadingHTML("Waiting for the server…");
    return;
  } finally { seriesFetching = false; }
  seriesLoaded = true;
  LAST = data;
  $("sub").innerHTML = data.descriptor
    ? "Descriptor: <b>" + data.descriptor + "</b>"
    : '<span style="color:var(--err)">No descriptor set — open the flow page first.</span>';
  if (!computeJob) {
    const have = (data.main && (data.main.points || []).length);
    setStatus(data.running ? "running" : (have ? "done" : "idle"));
  }
  kpis();
  draw();

  // Progressive compute: the script rewrites the series JSON after EVERY file,
  // so the plot fills in file-by-file. Surface which file it is on and, until
  // the first tau point exists, replace the static "no points" state with a
  // clear "processing file i/N" placeholder so it never looks frozen. Works for
  // both the current-run compute and the overlay "group of files" compute.
  const prog = activeProgress(data);
  const anyPoints =
    (((data.main && data.main.points) || []).length) ||
    (((data.overlay && data.overlay.points) || []).length);

  let title = "Lifetime τ vs time";
  const mm = (data.main && data.main.meta) || {};
  if (mm.bin_size) title += " — " + binningLabel(mm) +
    ", E = " + mm.efield + " V/cm";

  if (prog && data.running) {
    title += '  <span class="muted">· ' + SPINNER_SVG + " processing file " +
      (prog.n_files_done || 0) + " / " + (prog.n_files_total || "?") +
      (prog.current_file ? " (" + prog.current_file + ")" : "") + "</span>";
    if (!anyPoints) {
      $("plot").innerHTML = loadingHTML("Processing file " +
        (prog.n_files_done || 0) + " / " + (prog.n_files_total || "?") +
        " — τ points appear as anode-cathode crossers accumulate");
      $("legend").innerHTML = "";
    }
  } else if (prog && !data.running) {
    title += '  <span class="muted">· partial — last compute did not finish</span>';
  }
  $("plot-title").innerHTML = title;
}

// The partial series meta being actively written. If both main and overlay
// carry a partial (e.g. a stale one lingers), pick the most recently generated
// so the "processing file i/N" reflects the compute that's actually running.
function activeProgress(data) {
  let best = null;
  for (const s of ["main", "overlay"]) {
    const m = (data[s] && data[s].meta) || {};
    if (m.partial && (!best || (m.generated || "") > (best.generated || ""))) {
      best = m;
    }
  }
  return best;
}

function setStatus(s) {
  const chip = $("comp-status");
  chip.className = "chip " + (s === "idle" ? "" : s);
  chip.textContent = s;
  $("btn-compute").disabled = (s === "running");
  $("btn-overlay").disabled = (s === "running");
}

async function pollJob() {
  if (!computeJob) return;
  let snap;
  try { snap = await api("/api/job/" + computeJob + "?offset=" + logOffset); }
  catch (e) { return; }
  const log = $("complog");
  log.style.display = "";
  if (snap.lines && snap.lines.length) {
    snap.lines.forEach((l) => { log.textContent += l + "\n"; });
    logOffset = snap.total;
    log.scrollTop = log.scrollHeight;
  }
  setStatus(snap.status);
  // While the compute runs, refresh the plot every poll (800 ms) so each
  // processed file shows up promptly; refresh once more when it finishes.
  loadSeries();
  if (snap.status !== "running") computeJob = null;
}

function computeParams(extra) {
  return Object.assign({
    source: $("source").value,
    group_by: $("group_by").value,
    bin_size: parseInt($("bin_size").value, 10) || 100,
    efield: parseFloat($("efield").value) || 500,
    file_seconds: parseFloat($("file_seconds").value) || 0,
  }, extra || {});
}

// tracks-per-point is meaningless when every file is its own point: in that
// mode each point fits *every* AC track in its file (minus excluded ones), so
// the box is blocked out and says so rather than showing a number that looks
// like a cap.
function syncBinning() {
  const perFile = $("group_by").value === "file";
  const box = $("bin_size"), label = $("bin_size-label");
  box.disabled = perFile;
  label.style.opacity = perFile ? 0.45 : "";
  label.textContent = perFile ? "AC tracks / point (not used)"
                              : "AC tracks / point";
  box.title = perFile
    ? "Not used in one-point-per-file mode — every point fits all the AC " +
      "tracks in its file, minus any you've excluded."
    : "";
  $("bin_size-note").style.display = perFile ? "" : "none";
}
$("group_by").onchange = syncBinning;
syncBinning();

async function startCompute(body) {
  try {
    const r = await postJSON("/api/lifetime/compute", body);
    computeJob = r.job_id; logOffset = 0;
    $("complog").textContent = "";
    setStatus("running");
    toast("Lifetime computation started");
    loadSeries();               // immediate feedback before the first poll
  } catch (e) { toast(e.message); }
}

$("btn-compute").onclick = () => startCompute(computeParams({ series: "main" }));
$("btn-overlay").onclick = () => {
  const folder = $("ov-folder").value.trim();
  if (!folder) { toast("Enter the overlay folder path"); return; }
  try { localStorage.setItem("larpix-ov-folder", folder); } catch (e) {}
  startCompute(computeParams({ series: "overlay", folder }));
};
$("btn-overlay-clear").onclick = async () => {
  try {
    await postJSON("/api/lifetime/overlay/clear", {});
    toast("Overlay cleared");
    loadSeries();
  } catch (e) { toast(e.message); }
};
try {
  const f = localStorage.getItem("larpix-ov-folder");
  if (f) $("ov-folder").value = f;
} catch (e) {}

/* ------------------------------------------------------ 3D event display */
const EVD = {
  open: false, series: "main", point: 0,
  tracks: [], info: null, refit: null, nExcluded: 0,
  yaw: 0.7, pitch: 0.45, zoom: 1.0,
  center: [0, 0, 0], scale: 1.0, hover: null,
  tab: "evd", efield: null, efSource: "", plotsStale: true,
};

function trackColor(i, excluded) {
  if (excluded) return cssVar("--muted", "#8b97a8");
  return "hsl(" + ((i * 47) % 360) + " 75% 55%)";
}

async function openDisplay(series, point) {
  // Show the modal immediately with a loading state -- building the 3D view
  // (parsing hits for every track) can take a moment for a busy point.
  $("evd-back").style.display = "";
  $("evd-title").textContent = "Point " + (point + 1) +
    (series === "overlay" ? "  (overlay)" : "");
  $("evd-sub").textContent = "Loading tracks…";
  $("evd-tau").textContent = "";
  $("evd-ntracks").textContent = "…";
  $("evd-nexcl").textContent = "";
  $("evd-list").innerHTML = loadingHTML("Loading tracks…");
  const ctx0 = $("evd-canvas").getContext("2d");
  ctx0.clearRect(0, 0, $("evd-canvas").width, $("evd-canvas").height);

  let data;
  try {
    data = await api("/api/lifetime/tracks?series=" + series + "&point=" + point);
  } catch (e) {
    toast(e.message);
    $("evd-sub").textContent = "Failed to load: " + e.message;
    $("evd-list").innerHTML = "";
    return;
  }
  EVD.open = true;
  EVD.series = series;
  EVD.point = point;
  EVD.tracks = data.tracks || [];
  EVD.info = data.point_info;
  EVD.refit = data.refit;
  EVD.nExcluded = data.n_excluded || 0;
  EVD.hover = null;
  EVD.yaw = 0.7; EVD.pitch = 0.45; EVD.zoom = 1.0;

  // bounding box -> center + scale
  let mn = [1e9, 1e9, 1e9], mx = [-1e9, -1e9, -1e9];
  EVD.tracks.forEach((t) => {
    for (let i = 0; i < t.x.length; i++) {
      const p = [t.x[i], t.y[i], t.z[i]];
      for (let k = 0; k < 3; k++) {
        if (p[k] < mn[k]) mn[k] = p[k];
        if (p[k] > mx[k]) mx[k] = p[k];
      }
    }
  });
  if (mn[0] > mx[0]) { mn = [0, 0, 0]; mx = [1, 1, 1]; }
  EVD.center = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
  const span = Math.max(mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2], 1);
  EVD.scale = 480 / span;
  EVD.bbox = [mn, mx];
  EVD.efield = data.efield_used;
  EVD.efSource = data.efield_source || "";
  EVD.plotsStale = true;
  switchTab("evd");

  evdHeader();
  evdList();
  evdRender();
  loadPointMeta();
}

/* ---- modal tabs + per-point fit/2D plots + E-field controls ------------- */
function switchTab(tab) {
  EVD.tab = tab;
  document.querySelectorAll(".evd-tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".evd-pane").forEach((p) =>
    p.style.display = p.dataset.pane === tab ? "" : "none");
  if (tab === "fit" || tab === "dqdx") loadPointPlots();
}
document.querySelectorAll(".evd-tab").forEach((b) =>
  b.onclick = () => switchTab(b.dataset.tab));

async function loadPointPlots(force) {
  if (!EVD.plotsStale && !force) return;
  const fitW = $("evd-fit-wrap"), dqW = $("evd-dqdx-wrap");
  fitW.innerHTML = loadingHTML("Generating fit plot…");
  dqW.innerHTML = loadingHTML("Generating dQ/dx-2D plot…");
  try {
    const r = await api("/api/lifetime/pointplots?series=" + EVD.series +
                        "&point=" + EVD.point);
    const bust = "&t=" + Date.now();
    fitW.innerHTML = '<img src="/api/plot?path=' + encodeURIComponent(r.fit_png) + bust + '">';
    dqW.innerHTML = '<img src="/api/plot?path=' + encodeURIComponent(r.dqdx2d_png) + bust + '">';
    EVD.plotsStale = false;
  } catch (e) {
    fitW.innerHTML = '<div class="hint">' + e.message + "</div>";
    dqW.innerHTML = '<div class="hint">' + e.message + "</div>";
  }
}

async function loadPointMeta() {
  try {
    const m = await api("/api/lifetime/pointmeta?series=" + EVD.series +
                        "&point=" + EVD.point);
    $("evd-ef").value = m.efield_effective;
    $("evd-hv").value = "";
    let src = "using E = " + fmt(m.efield_effective, 1) + " V/cm (" + m.efield_source + ")";
    if (m.db_hv) src += " · DB HV " + m.db_hv + " → " + fmt(m.db_efield, 1) + " V/cm";
    src += " · HV×" + m.hv_to_efield_factor;
    $("evd-ef-src").textContent = src;
  } catch (e) { /* ignore */ }
}

async function applyEfield(body) {
  try {
    const res = await postJSON("/api/lifetime/efield",
      Object.assign({ series: EVD.series, point: EVD.point }, body));
    EVD.efield = res.efield_used;
    EVD.efSource = res.efield_source;
    EVD.refit = res.refit;
    EVD.plotsStale = true;
    evdHeader();
    loadPointMeta();
    if (EVD.tab !== "evd") loadPointPlots(true);
    loadSeries();          // adjust the τ plot underneath
    toast("E-field set to " + fmt(res.efield_used, 1) + " V/cm");
  } catch (e) { toast(e.message); }
}
$("evd-ef-apply").onclick = () => {
  const hv = $("evd-hv").value.trim();
  const ef = $("evd-ef").value.trim();
  applyEfield(hv ? { hv } : { efield: ef });
};
$("evd-ef-reset").onclick = () => applyEfield({});   // empty -> clear override

function evdHeader() {
  const p = EVD.info || {};
  $("evd-title").textContent = "Point " + (EVD.point + 1) +
    (EVD.series === "overlay" ? "  (overlay)" : "");
  $("evd-sub").textContent = (p.time || "") + "  ·  " +
    EVD.tracks.length + " anode-cathode tracks" +
    (EVD.efield != null ? "  ·  E = " + fmt(EVD.efield, 1) + " V/cm" : "");
  const orig = p.tau_ms_orig != null ? p.tau_ms_orig : p.tau_ms;
  const adjusted = EVD.refit ? EVD.refit.tau_ms : null;
  let tau = "τ = " + fmt(orig, 3) + " ms";
  if (adjusted != null) {
    tau = "original τ = " + fmt(orig, 3) + " ms   →   adjusted τ = " +
      fmt(adjusted, 3) + " ms";
    if (EVD.nExcluded > 0) tau += "  (" + EVD.nExcluded + " excluded)";
  } else if (EVD.nExcluded > 0) {
    tau = "original τ = " + fmt(orig, 3) + " ms → fit failed (" +
      EVD.nExcluded + " excluded)";
  }
  $("evd-tau").textContent = tau;
  $("evd-ntracks").textContent = EVD.tracks.length;
  $("evd-nexcl").textContent = EVD.nExcluded ? EVD.nExcluded + " excluded" : "";
}

function evdList() {
  const list = $("evd-list");
  list.innerHTML = "";
  EVD.tracks.forEach((t, i) => {
    const row = document.createElement("div");
    row.className = "evd-row" + (t.excluded ? " excluded" : "");
    row.innerHTML =
      '<span class="sw" style="background:' + trackColor(i, t.excluded) + '"></span>' +
      '<span class="mono">#' + t.seq + "</span>" +
      '<span class="muted">' + t.n_hits + " hits</span>" +
      '<span class="muted">' + fmt(t.span_us, 0) + " µs</span>";
    const btn = document.createElement("button");
    btn.className = t.excluded ? "" : "danger";
    btn.textContent = t.excluded ? "Restore" : "Exclude";
    btn.onclick = () => toggleTrack(t, btn);
    row.appendChild(btn);
    row.addEventListener("mouseenter", () => { EVD.hover = i; evdRender(); });
    row.addEventListener("mouseleave", () => { EVD.hover = null; evdRender(); });
    list.appendChild(row);
  });
}

async function toggleTrack(t, btn) {
  btn.disabled = true;
  try {
    const res = await postJSON("/api/lifetime/exclude", {
      series: EVD.series, point: EVD.point, seq: t.seq, excluded: !t.excluded });
    t.excluded = !t.excluded;
    EVD.nExcluded = res.n_excluded;
    EVD.refit = res.refit;
    EVD.plotsStale = true;
    if (res.fit_failed) toast("Too few tracks left — fit failed for this point");
    evdHeader();
    evdList();
    evdRender();
    if (EVD.tab !== "evd") loadPointPlots(true);
    loadSeries();          // refresh the τ plot underneath
  } catch (e) { toast(e.message); }
  btn.disabled = false;
}

function project(x, y, z) {
  // center, rotate (yaw about vertical, pitch about horizontal), orthographic
  const cx = x - EVD.center[0], cy = y - EVD.center[1], cz = z - EVD.center[2];
  const cyaw = Math.cos(EVD.yaw), syaw = Math.sin(EVD.yaw);
  const cp = Math.cos(EVD.pitch), sp = Math.sin(EVD.pitch);
  const x1 = cyaw * cx + syaw * cz;
  const z1 = -syaw * cx + cyaw * cz;
  const y1 = cp * cy - sp * z1;
  const s = EVD.scale * EVD.zoom;
  const canvas = $("evd-canvas");
  return [canvas.width / 2 + x1 * s, canvas.height / 2 - y1 * s];
}

function evdRender() {
  const canvas = $("evd-canvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = cssVar("--bg", "#0e1116");
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // axes from the bbox min corner
  if (EVD.bbox) {
    const [mn, mx] = EVD.bbox;
    const axes = [
      { to: [mx[0], mn[1], mn[2]], label: "x" },
      { to: [mn[0], mx[1], mn[2]], label: "y" },
      { to: [mn[0], mn[1], mx[2]], label: "z (drift)" },
    ];
    ctx.strokeStyle = cssVar("--axis", "#3a4452");
    ctx.fillStyle = cssVar("--muted", "#8b97a8");
    ctx.font = "11px sans-serif";
    ctx.lineWidth = 1;
    axes.forEach((a) => {
      const p0 = project(mn[0], mn[1], mn[2]);
      const p1 = project(a.to[0], a.to[1], a.to[2]);
      ctx.beginPath(); ctx.moveTo(p0[0], p0[1]); ctx.lineTo(p1[0], p1[1]); ctx.stroke();
      ctx.fillText(a.label, p1[0] + 4, p1[1] + 3);
    });
  }

  // tracks: hovered one drawn last & bigger
  const order = EVD.tracks.map((_, i) => i);
  if (EVD.hover != null) {
    order.splice(order.indexOf(EVD.hover), 1);
    order.push(EVD.hover);
  }
  order.forEach((i) => {
    const t = EVD.tracks[i];
    const hovered = i === EVD.hover;
    const size = hovered ? 3.2 : 1.8;
    ctx.fillStyle = trackColor(i, t.excluded);
    ctx.globalAlpha = t.excluded ? 0.28 : (hovered ? 1.0 : 0.85);
    for (let k = 0; k < t.x.length; k++) {
      const [px, py] = project(t.x[k], t.y[k], t.z[k]);
      ctx.fillRect(px - size / 2, py - size / 2, size, size);
    }
    if (hovered) {
      ctx.globalAlpha = 1.0;
      ctx.strokeStyle = cssVar("--text", "#fff");
      ctx.lineWidth = 1;
      const [sx, sy] = project(t.x[0], t.y[0], t.z[0]);
      ctx.strokeRect(sx - 5, sy - 5, 10, 10);
    }
  });
  ctx.globalAlpha = 1.0;
}

// mouse: drag rotate, wheel zoom
(() => {
  const canvas = $("evd-canvas");
  let dragging = false, lx = 0, ly = 0;
  canvas.addEventListener("mousedown", (e) => {
    dragging = true; lx = e.clientX; ly = e.clientY; e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    EVD.yaw += (e.clientX - lx) * 0.008;
    EVD.pitch += (e.clientY - ly) * 0.008;
    EVD.pitch = Math.max(-1.5, Math.min(1.5, EVD.pitch));
    lx = e.clientX; ly = e.clientY;
    evdRender();
  });
  window.addEventListener("mouseup", () => { dragging = false; });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    EVD.zoom *= (e.deltaY > 0 ? 0.9 : 1.1);
    EVD.zoom = Math.max(0.2, Math.min(8, EVD.zoom));
    evdRender();
  }, { passive: false });
})();

function closeDisplay() {
  EVD.open = false;
  $("evd-back").style.display = "none";
}
$("evd-close").onclick = closeDisplay;
$("evd-back").addEventListener("click", (e) => {
  if (e.target === $("evd-back")) closeDisplay();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && EVD.open) closeDisplay();
});

/* ---------------------------------------------------------------- timers */
$("plot").innerHTML = loadingHTML("Loading lifetime series…");
$("sub").innerHTML = '<span class="muted">' + SPINNER_SVG + " Loading…</span>";
loadSeries();
setInterval(loadSeries, 3000);   // background refresh (also catches externally-
                                 // started computes); pollJob drives the fast
                                 // per-file refresh while a compute is running
setInterval(pollJob, 800);
window.addEventListener("resize", () => draw());
