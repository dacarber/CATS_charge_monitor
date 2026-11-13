"use strict";
// Live monitor -- one page of compact, read-only panels for watching a run:
// self-trigger rate (5 s bins), conversion + clustering data rate, pixel map,
// newest event, electron lifetime and the cluster charge spectrum. Lighter
// than the dedicated pages on purpose; it is for spotting trouble, not analysis.
//
// The whole file is one IIFE: the other pages' scripts declare the same
// top-level names (SVGNS, VIEW, cssVar, project, ...), so nothing may leak out.
(function () {

mountThemeToggle($("theme-slot"));

/* ---- small helpers ------------------------------------------------------- */
const SVGNS = "http://www.w3.org/2000/svg";
const css = (name, fb) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fb;

function svgEl(tag, attrs, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in (attrs || {})) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function fmtNum(v, digits) {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  if (digits == null) digits = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits });
}
function niceStep(range, target) {
  const raw = range / Math.max(1, target);
  if (!(raw > 0)) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / pow;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
}
function emptyMsg(holder, msg) {
  holder.innerHTML = '<div class="empty">' + esc(msg) + "</div>";
}
const parseT = (iso) => (iso ? new Date(iso).getTime() : NaN);   // naive ISO = local
const pad2 = (n) => String(n).padStart(2, "0");
function clock(ms, withSec) {
  const d = new Date(ms);
  return pad2(d.getHours()) + ":" + pad2(d.getMinutes()) +
         (withSec ? ":" + pad2(d.getSeconds()) : "");
}
function median(xs) {
  if (!xs.length) return null;
  const s = xs.slice().sort((a, b) => a - b), h = s.length >> 1;
  return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
}

/* ---- time-series chart (numeric time axis, gaps, error bars) ------------- */
const DAY = 86400e3;
const TIME_STEPS = [5e3, 15e3, 30e3, 60e3, 120e3, 300e3, 600e3, 900e3, 1800e3,
                    3600e3, 7200e3, 10800e3, 21600e3, 43200e3, DAY, 2 * DAY,
                    7 * DAY, 14 * DAY, 30 * DAY, 91 * DAY, 182 * DAY, 365 * DAY];

function timeTickLabel(t, step, span) {
  const d = new Date(t);
  if (step >= 30 * DAY) return d.getFullYear() + "-" + pad2(d.getMonth() + 1);
  if (step >= DAY) return (d.getMonth() + 1) + "/" + d.getDate();
  const lab = clock(t, step < 60e3);
  return span > DAY ? (d.getMonth() + 1) + "/" + d.getDate() + " " + lab : lab;
}

// series: [{name, color, kind: "line"|"dots"|"segments", gapMs, markers, points}]
//   line/dots points: {t (ms), y, lo?, hi?, tip?}   segments: {t0, t1, y, tip?}
// opts: {yLabel, height, yCap}  -> false if there was nothing to draw
function timeChart(holder, series, opts) {
  opts = opts || {};
  const W = Math.max(280, holder.clientWidth || 600), H = opts.height || 230;
  const m = { l: 62, r: 14, t: 10, b: 30 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  let tMin = Infinity, tMax = -Infinity, yMax = 0;
  series.forEach((s) => s.points.forEach((p) => {
    (s.kind === "segments" ? [p.t0, p.t1] : [p.t]).forEach((t) => {
      if (isFinite(t)) { tMin = Math.min(tMin, t); tMax = Math.max(tMax, t); }
    });
    [p.y, p.hi].forEach((v) => { if (v != null && isFinite(v)) yMax = Math.max(yMax, v); });
  }));
  if (!isFinite(tMin)) return false;
  if (opts.yCap) yMax = Math.min(yMax, opts.yCap);
  if (tMax - tMin < 60e3) { const c = (tMin + tMax) / 2; tMin = c - 30e3; tMax = c + 30e3; }
  const padT = (tMax - tMin) * 0.02;
  tMin -= padT; tMax += padT;
  const yStep = niceStep(yMax > 0 ? yMax * 1.08 : 1, 4);
  const yHi = Math.max(yStep, Math.ceil((yMax > 0 ? yMax * 1.08 : 1) / yStep) * yStep);
  const X = (t) => m.l + (t - tMin) / (tMax - tMin) * iw;
  const Y = (v) => Math.max(m.t, m.t + ih - (v / yHi) * ih);   // clamp to top
  const grid = css("--grid", "#2b3340"), axis = css("--axis", "#3a4452"),
        muted = css("--muted", "#8b97a8");
  const root = svgEl("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H });

  for (let v = 0; v <= yHi + yStep * 1e-6; v += yStep) {
    root.appendChild(svgEl("line", { x1: m.l, x2: m.l + iw, y1: Y(v), y2: Y(v), stroke: grid }));
    root.appendChild(svgEl("text", { x: m.l - 6, y: Y(v) + 4, fill: muted,
      "font-size": 11, "text-anchor": "end" }, fmtNum(v)));
  }
  const span = tMax - tMin;
  const maxTicks = Math.max(2, iw / 95);
  let step = TIME_STEPS.find((s) => span / s <= maxTicks) || TIME_STEPS[TIME_STEPS.length - 1];
  while (span / step > maxTicks) step *= 2;          // spans beyond a few years
  for (let t = Math.ceil(tMin / step) * step; t <= tMax; t += step) {
    root.appendChild(svgEl("line", { x1: X(t), x2: X(t), y1: m.t, y2: m.t + ih, stroke: grid }));
    root.appendChild(svgEl("text", { x: X(t), y: H - 10, fill: muted,
      "font-size": 11, "text-anchor": "middle" }, timeTickLabel(t, step, span)));
  }
  root.appendChild(svgEl("line", { x1: m.l, x2: m.l, y1: m.t, y2: m.t + ih, stroke: axis }));
  root.appendChild(svgEl("line", { x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih, stroke: axis }));
  if (opts.yLabel) {
    root.appendChild(svgEl("text", { x: 13, y: m.t + ih / 2, fill: muted, "font-size": 11,
      "text-anchor": "middle", transform: "rotate(-90 13 " + (m.t + ih / 2) + ")" }, opts.yLabel));
  }

  series.forEach((s) => {
    if (s.kind === "segments") {
      s.points.forEach((p) => {
        const ln = svgEl("line", { x1: X(p.t0), x2: X(p.t1), y1: Y(p.y), y2: Y(p.y),
          stroke: s.color, "stroke-width": 2, "stroke-dasharray": "6 3", opacity: 0.9 });
        if (p.tip) ln.appendChild(svgEl("title", {}, p.tip));
        root.appendChild(ln);
      });
      return;
    }
    // error bars first so markers sit on top
    s.points.forEach((p) => {
      if (p.lo == null && p.hi == null) return;
      const lo = p.lo != null ? p.lo : p.y, hi = p.hi != null ? p.hi : yHi * 2;
      root.appendChild(svgEl("line", { x1: X(p.t), x2: X(p.t), y1: Y(Math.max(0, lo)),
        y2: Y(hi), stroke: s.color, "stroke-width": 1.2, opacity: 0.7 }));
    });
    if (s.kind === "line") {
      let d = "", prev = null;
      s.points.forEach((p) => {
        if (p.y == null || !isFinite(p.y)) { prev = null; return; }
        const gap = prev && s.gapMs && (p.t - prev.t) > s.gapMs;
        d += (prev && !gap ? " L" : " M") + X(p.t).toFixed(1) + " " + Y(p.y).toFixed(1);
        prev = p;
      });
      root.appendChild(svgEl("path", { d: d.trim(), fill: "none", stroke: s.color,
        "stroke-width": 1.6, "stroke-linejoin": "round" }));
    }
    if (s.kind === "dots" || s.markers) {
      s.points.forEach((p) => {
        if (p.y == null || !isFinite(p.y)) return;
        const c = svgEl("circle", { cx: X(p.t), cy: Y(p.y), r: 3.2, fill: s.color });
        if (p.tip) c.appendChild(svgEl("title", {}, p.tip));
        root.appendChild(c);
      });
    }
  });
  holder.innerHTML = "";
  holder.appendChild(root);
  return true;
}

/* ---- step histogram ------------------------------------------------------- */
// series: [{name, color, counts, fill}]  -> false if every bin is empty
function histChart(holder, edges, series, opts) {
  opts = opts || {};
  const W = Math.max(280, holder.clientWidth || 600), H = opts.height || 230;
  const m = { l: 62, r: 14, t: 10, b: 34 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const n = edges.length - 1, x0 = edges[0], x1 = edges[n];
  let yMax = 0;
  series.forEach((s) => (s.counts || []).forEach((c) => { yMax = Math.max(yMax, c); }));
  if (!(yMax > 0)) return false;
  const grid = css("--grid", "#2b3340"), axis = css("--axis", "#3a4452"),
        muted = css("--muted", "#8b97a8");
  const X = (v) => m.l + (v - x0) / (x1 - x0) * iw;
  let Y, ticks = [];
  if (opts.logY) {
    const lo = -0.3, top = Math.ceil(Math.log10(yMax * 1.3));
    Y = (v) => (v > 0 ? m.t + ih - (Math.log10(v) - lo) / (top - lo) * ih : m.t + ih);
    for (let e = 0; e <= top; e++) ticks.push([Math.pow(10, e), e <= 3 ? String(Math.pow(10, e)) : "1e" + e]);
  } else {
    const st = niceStep(yMax * 1.08, 4), hi = Math.ceil(yMax * 1.08 / st) * st;
    Y = (v) => m.t + ih - (v / hi) * ih;
    for (let v = 0; v <= hi + st * 1e-6; v += st) ticks.push([v, fmtNum(v)]);
  }
  const root = svgEl("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", height: H });
  ticks.forEach(([v, lab]) => {
    root.appendChild(svgEl("line", { x1: m.l, x2: m.l + iw, y1: Y(v), y2: Y(v), stroke: grid }));
    root.appendChild(svgEl("text", { x: m.l - 6, y: Y(v) + 4, fill: muted, "font-size": 11,
      "text-anchor": "end" }, lab));
  });
  const xs = niceStep(x1 - x0, Math.max(2, iw / 70));
  for (let v = Math.ceil(x0 / xs) * xs; v <= x1 + xs * 1e-6; v += xs) {
    root.appendChild(svgEl("text", { x: X(v), y: m.t + ih + 15, fill: muted, "font-size": 11,
      "text-anchor": "middle" }, fmtNum(v)));
  }
  root.appendChild(svgEl("line", { x1: m.l, x2: m.l, y1: m.t, y2: m.t + ih, stroke: axis }));
  root.appendChild(svgEl("line", { x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih, stroke: axis }));
  if (opts.xLabel) root.appendChild(svgEl("text", { x: m.l + iw / 2, y: H - 3, fill: muted,
    "font-size": 11, "text-anchor": "middle" }, opts.xLabel));
  if (opts.yLabel) root.appendChild(svgEl("text", { x: 13, y: m.t + ih / 2, fill: muted,
    "font-size": 11, "text-anchor": "middle",
    transform: "rotate(-90 13 " + (m.t + ih / 2) + ")" }, opts.yLabel));
  series.forEach((s) => {
    if (!s.counts) return;
    const base = m.t + ih;
    let d = "M" + X(edges[0]).toFixed(1) + " " + base;
    s.counts.forEach((c, i) => {
      const y = Y(c).toFixed(1);
      d += " L" + X(edges[i]).toFixed(1) + " " + y + " L" + X(edges[i + 1]).toFixed(1) + " " + y;
    });
    d += " L" + X(edges[n]).toFixed(1) + " " + base;
    root.appendChild(svgEl("path", { d: d, stroke: s.color, "stroke-width": 1.5,
      fill: s.fill ? s.color : "none", "fill-opacity": s.fill ? 0.15 : 0 }));
  });
  holder.innerHTML = "";
  holder.appendChild(root);
  return true;
}

/* ---- state ---------------------------------------------------------------- */
const S = {
  summary: null, lifetime: null, spec: null, density: null, event: null,
  geo: null, pix: null,                 // pixel-map geometry + built SVG
  lastConvRecords: null, lastAutoLt: 0, booted: false, bannerKey: null,
};
const ACCENT = () => css("--accent", "#4f9dff");
const ACCENT2 = () => css("--accent-2", "#2ea043");
const WARN = () => css("--warn", "#d29922");
const LT_AUTO_MIN_MS = 5 * 60e3;        // auto lifetime: at most every 5 min

/* ---- rate health (drives the colour of the live-rate KPI tile) ------------ */
// latest: the newest closed 5 s bin's rate in Hz.
// recent: rates (Hz) of the full 5 s bins in the current time window, oldest
//         first (partial end-of-file bins are already left out). May be short
//         right after a run starts.
// Return "ok", "warn", "bad", or "" for no colour.
//
// Baseline = median of the last ~10 min of bins (a lone spike can't drag it).
// A change is flagged only if it is BOTH large (ratio) and statistically real
// (Poisson z-score of the 5 s counts), so a quiet tile's natural bin-to-bin
// wobble isn't painted amber. Zero hits is "bad" unless the baseline is so low
// that an empty 5 s bin is expected now and then.
function rateHealth(latest, recent) {
  if (latest == null || !isFinite(latest)) return "";
  const base = recent.length >= 6 ? median(recent.slice(-120)) : null;  // >= 30 s
  if (latest === 0) return base != null && base * 5 < 5 ? "warn" : "bad";
  if (base == null) return "";               // too little baseline to judge yet
  if (!(base > 0)) return "warn";            // back from a silent stretch
  const ratio = latest / base;
  const z = Math.abs(latest - base) * 5 / Math.sqrt(base * 5);
  if ((ratio < 0.25 || ratio > 5) && z > 5) return "bad";
  if ((ratio < 0.6 || ratio > 2) && z > 3) return "warn";
  return "ok";
}

/* ---- KPIs + banner ------------------------------------------------------- */
function kpi(label, value, sub, cls) {
  return '<div class="kpi ' + (cls || "") + '"><div class="v">' + value +
    '</div><div class="l">' + esc(label) + '</div>' +
    (sub ? '<div class="s" title="' + esc(sub) + '">' + esc(sub) + "</div>" : "") + "</div>";
}

function renderKpis() {
  const d = S.summary || {}, f = d.files || {}, r = d.running || {};
  const hist = d.history_5s || [];
  const full = hist.filter((b) => !b.partial);
  const last = full.length ? full[full.length - 1] : null;
  let html = kpi("Self-trigger", r.self_trigger ? "Running" : "Idle",
    d.live && d.live.file ? d.live.file : "", r.self_trigger ? "ok" : "");
  html += kpi("Live rate (5 s bins)", last ? fmtNum(last.rate) + " Hz" : "—",
    last ? "bin at " + clock(parseT(last.t), true) + (r.self_trigger ? "" : " (last run)")
         : "no 5 s data yet",
    last ? rateHealth(last.rate, full.map((b) => b.rate)) : "");
  html += kpi("Raw files", f.raw != null ? f.raw : "—", f.newest_raw || "");
  html += kpi("Waiting to convert", f.backlog_convert != null ? f.backlog_convert : "—",
    r.convert_watch ? "convert watch running" : "convert watch off",
    f.backlog_convert >= 3 ? "warn" : "");
  html += kpi("Waiting to cluster", f.backlog_cluster != null ? f.backlog_cluster : "—",
    r.clustering_watch ? "cluster watch running" : "cluster watch off",
    f.backlog_cluster >= 3 ? "warn" : "");
  const lt = ltPoints();
  const lastLt = lt.length ? lt[lt.length - 1] : null;
  html += kpi("Latest τ", lastLt ? fmtNum(lastLt.y) + " ms" : "—",
    lastLt ? clock(lastLt.t) + " · " + lastLt.n + " tracks" : (r.lifetime ? "computing…" : ""));
  const sp = S.spec && !S.spec.error ? S.spec : null;
  html += kpi("Clusters", sp ? fmtNum(sp.n_clusters, 0) : "—",
    sp ? sp.n_files + " clustered file(s)" : "");
  $("kpis").innerHTML = html;
}

function renderBanner() {
  const d = S.summary || {}, f = d.files || {}, r = d.running || {};
  const needed = r.self_trigger || f.backlog_convert || f.backlog_cluster;
  const missing = [];
  if (!r.convert_watch) missing.push(["convert", "Start convert watch"]);
  if (!r.clustering_watch) missing.push(["clustering", "Start cluster watch"]);
  const box = $("pipe-banner");
  const show = !!(d.descriptor && needed && missing.length);
  // Rebuild only when it changes: re-rendering every poll would swap the
  // buttons out from under a click that lands mid-refresh.
  const key = show ? missing.map((m) => m[0]).join(",") : "";
  if (key === S.bannerKey) return;
  S.bannerKey = key;
  if (!show) { box.style.display = "none"; return; }
  box.style.display = "";
  box.innerHTML = "<span>" + esc(missing.length === 2
      ? "Convert and cluster watch jobs are not running — the conversion, clustering, " +
        "spectrum and lifetime panels won't get new files."
      : missing[0][0] === "convert"
        ? "Convert watch job is not running — new raw files won't be converted."
        : "Cluster watch job is not running — new converted files won't be clustered.") +
    "</span>" +
    missing.map(([a, lab]) => '<button class="secondary" data-start="' + a + '">' +
      lab + "</button>").join("");
  box.querySelectorAll("button[data-start]").forEach((b) => {
    b.onclick = () => withBusy(b, "Starting…", async () => {
      try {
        await postJSON("/api/run/" + b.dataset.start, { mode: "all" });
        toast("Started " + b.dataset.start + " watch");
        refreshSummary();
      } catch (e) { toast(e.message); }
    });
  });
}

/* ---- panels: rates ------------------------------------------------------- */
function drawRate() {
  const d = S.summary || {}, holder = $("rate-plot");
  const hist = d.history_5s || [];
  const accent = ACCENT(), accent2 = ACCENT2();
  let legend = "";
  if (hist.length) {
    const pts = hist.map((b) => ({ t: parseT(b.t), y: b.rate,
      tip: clock(parseT(b.t), true) + "  " + fmtNum(b.rate) + " Hz  (" + b.hits +
           " hits" + (b.partial ? ", partial bin" : "") + ")\n" + b.file }));
    // per-file average over that file's bins, drawn across its time span
    const byFile = new Map();
    hist.forEach((b) => {
      const g = byFile.get(b.file) || { t0: parseT(b.t), t1: parseT(b.t), hits: 0, secs: 0 };
      g.t1 = parseT(b.t) + 5e3;
      g.hits += b.hits;
      g.secs += b.partial && b.rate > 0 ? b.hits / b.rate : 5;
      byFile.set(b.file, g);
    });
    const segs = [];
    byFile.forEach((g, file) => segs.push({ t0: g.t0, t1: g.t1, y: g.secs ? g.hits / g.secs : 0,
      tip: file + "\naverage " + fmtNum(g.secs ? g.hits / g.secs : 0) + " Hz" }));
    timeChart(holder, [
      { name: "5 s bins", color: accent, kind: "line", gapMs: 12.5e3, points: pts,
        markers: pts.length <= 60 },
      { name: "file average", color: accent2, kind: "segments", points: segs },
    ], { yLabel: "hits / s" });
    legend = '<span><i style="background:' + accent + '"></i>hits/s per 5 s bin</span>' +
             '<span><i style="background:' + accent2 + '"></i>per-file average</span>';
  } else if ((d.self_trigger_files || []).length) {
    const pts = d.self_trigger_files.map((r) => ({ t: parseT(r.t), y: r.rate,
      tip: r.file + "\n" + fmtNum(r.rate) + " messages/s" }));
    timeChart(holder, [{ name: "per file", color: WARN(), kind: "dots", points: pts }],
              { yLabel: "messages / s" });
    legend = '<span><i style="background:' + WARN() + '"></i>messages/s per file ' +
             "(no 5 s data in this window yet)</span>";
  } else {
    emptyMsg(holder, d.descriptor ? "No self-trigger data yet — start a run (step 9)."
                                  : "No descriptor set — set one on the flow page.");
  }
  $("rate-legend").innerHTML = legend;
  const cur = d.live && d.live.running && d.live.current;
  $("rate-sub").textContent = "hits / s in 5 s bins" +
    (cur ? " · current bin: " + cur.hits + " hits so far" : "");
}

function drawStage(rows, holder, unit, color) {
  rows = rows || [];
  const pts = rows.filter((r) => r.rate != null).map((r) => ({
    t: parseT(r.t), y: r.rate,
    tip: r.file + "\n" + fmtNum(r.rate) + " " + unit + "/s  (" + fmtNum(r.count, 0) + " " +
         unit + " / " + fmtNum(r.seconds, 0) + " s from " + r.seconds_source + ")" }));
  if (!timeChart(holder, [{ name: unit, color: color, kind: "line", markers: true, points: pts }],
                 { yLabel: unit + " / s" })) {
    emptyMsg(holder, rows.length ? "Files found but no run time to divide by yet."
                                 : "No files at this stage yet.");
  }
}

/* ---- pixel map ------------------------------------------------------------ */
const zeroColor = () => (document.documentElement.getAttribute("data-theme") === "dark"
  ? "#232a36" : "#e3e8ef");
const rampColor = (t) => "hsl(" + (220 - 220 * t) + " 85% 52%)";   // as pixelmap.js

function buildPixmap(holder) {
  const pts = S.geo.pixels;
  const xs = [...new Set(pts.map((p) => p.x))].sort((a, b) => a - b);
  let pitch = Infinity;
  for (let i = 1; i < xs.length; i++) pitch = Math.min(pitch, xs[i] - xs[i - 1]);
  if (!isFinite(pitch) || pitch <= 0) pitch = 4.434;
  const minX = Math.min(...pts.map((p) => p.x)) - pitch / 2,
        maxX = Math.max(...pts.map((p) => p.x)) + pitch / 2,
        minY = Math.min(...pts.map((p) => p.y)) - pitch / 2,
        maxY = Math.max(...pts.map((p) => p.y)) + pitch / 2;
  const root = svgEl("svg", { viewBox: [minX, -maxY, maxX - minX, maxY - minY].join(" "),
    width: "100%", height: 330 });
  const rects = new Map(), s = pitch * 0.92;
  pts.forEach((p) => {
    const r = svgEl("rect", { x: p.x - s / 2, y: -p.y - s / 2, width: s, height: s,
      "data-k": p.k });
    rects.set(p.k, r);
    root.appendChild(r);
  });
  root.addEventListener("mouseover", (ev) => {
    const k = ev.target.getAttribute && ev.target.getAttribute("data-k");
    if (!k) return;
    const c = (S.density && S.density.counts && S.density.counts[k]) || 0;
    $("pix-sub").textContent = "chip-ch " + k + ": " + fmtNum(c, 0) + " hits";
  });
  root.addEventListener("mouseleave", () => drawPixmapSub());
  holder.innerHTML = "";
  holder.appendChild(root);
  S.pix = { rects };
}

function drawPixmapSub() {
  const d = S.density;
  $("pix-sub").textContent = !d ? "" : d.source === "live"
    ? "live · " + (d.file || "") + " · " + fmtNum(d.parsed_msgs, 0) + "/" + fmtNum(d.total_msgs, 0) + " msgs parsed"
    : d.source === "file" ? "newest converted · " + d.file : "no data yet";
}

function drawPixmap() {
  const holder = $("pix-plot");
  if (!S.geo) { emptyMsg(holder, "Loading pixel geometry…"); return; }
  if (!S.pix || !holder.contains(S.pix.rects.values().next().value)) buildPixmap(holder);
  const counts = (S.density && S.density.counts) || {};
  let max = 0;
  for (const k in counts) if (counts[k] > max) max = counts[k];
  const lmax = Math.log1p(Math.max(1, max)), zero = zeroColor();
  S.pix.rects.forEach((r, k) => {
    const v = counts[k] || 0;
    r.setAttribute("fill", v > 0 ? rampColor(Math.log1p(v) / lmax) : zero);
  });
  const stops = [];
  for (let i = 0; i <= 10; i++) stops.push(rampColor(i / 10));
  $("pix-bar").style.background = "linear-gradient(90deg," + stops.join(",") + ")";
  $("pix-max").textContent = fmtNum(max, 0);
  drawPixmapSub();
}

/* ---- event display (two canvas projections) ------------------------------- */
const RAMP = [[40, 60, 160], [30, 150, 200], [60, 190, 110], [230, 200, 60], [220, 70, 50]];
function chargeColor(f) {                                     // as eventdisplay.js
  f = Math.max(0, Math.min(1, f));
  const x = f * (RAMP.length - 1), i = Math.min(RAMP.length - 2, Math.floor(x));
  const t = x - i, a = RAMP[i], b = RAMP[i + 1];
  return "rgb(" + Math.round(a[0] + (b[0] - a[0]) * t) + "," +
    Math.round(a[1] + (b[1] - a[1]) * t) + "," + Math.round(a[2] + (b[2] - a[2]) * t) + ")";
}

function drawEvent() {
  const cv = $("evd-canvas"), ctx = cv.getContext("2d"), ev = S.event;
  // draw at the on-screen size (x devicePixelRatio) so text stays legible
  // instead of a fixed 900 px canvas shrunk by CSS
  const W = Math.max(320, cv.clientWidth || 900), H = Math.round(W * 0.47);
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
    cv.width = Math.round(W * dpr);
    cv.height = Math.round(H * dpr);
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = css("--bg", "#0e1116");
  ctx.fillRect(0, 0, W, H);
  $("evd-bar").style.background = "linear-gradient(90deg," +
    RAMP.map((c) => "rgb(" + c.join(",") + ")").join(",") + ")";
  if (!ev || ev.error || !ev.x) {
    ctx.fillStyle = css("--muted", "#8b97a8");
    ctx.font = "15px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(ev && ev.error ? ev.error : "Waiting for an event…", W / 2, H / 2);
    return;
  }
  const det = ev.detector, padX = 34, padY = 26, gap = 44;
  const side = Math.min((W - 2 * padX - gap) / 2, H - 2 * padY);
  const qs = ev.q.slice().sort((a, b) => a - b);
  const qlo = qs[Math.floor(qs.length * 0.05)] || 0;
  const qhi = qs[Math.floor(qs.length * 0.95)] || qlo + 1;
  const order = ev.q.map((_, i) => i).sort((a, b) => ev.q[a] - ev.q[b]);  // high q on top
  const panels = [
    { title: "anode view (x–y)", ux: det.x, uy: det.y, gx: ev.x, gy: ev.y, xl: "x [mm]" },
    { title: "side view (drift z–y)", ux: det.z, uy: det.y, gx: ev.z, gy: ev.y, xl: "z drift [mm]" },
  ];
  const muted = css("--muted", "#8b97a8"), axis = css("--axis", "#3a4452");
  panels.forEach((P, pi) => {
    const ox = padX + pi * (side + gap), oy = padY;
    const sx = side / ((P.ux[1] - P.ux[0]) || 1), sy = side / ((P.uy[1] - P.uy[0]) || 1);
    ctx.strokeStyle = axis;
    ctx.lineWidth = 1;
    ctx.strokeRect(ox, oy, side, side);
    ctx.fillStyle = muted;
    ctx.font = "12px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(P.title, ox, oy - 10);
    ctx.textAlign = "center";
    ctx.fillText(P.xl, ox + side / 2, oy + side + 18);
    ctx.save();
    ctx.translate(ox - 14, oy + side / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("y [mm]", 0, 0);
    ctx.restore();
    const sz = Math.max(2.5, 4.4 * Math.min(sx, sy));
    order.forEach((i) => {
      const gx = P.gx[i], gy = P.gy[i];
      if (gx == null || gy == null) return;
      ctx.fillStyle = chargeColor((ev.q[i] - qlo) / ((qhi - qlo) || 1));
      ctx.fillRect(ox + (gx - P.ux[0]) * sx - sz / 2, oy + side - (gy - P.uy[0]) * sy - sz / 2,
                   sz, sz);
    });
  });
}

/* ---- lifetime -------------------------------------------------------------- */
function ltPoints() {
  const pts = (S.lifetime && S.lifetime.main && S.lifetime.main.points) || [];
  return pts.filter((p) => !p.point_excluded && p.tau_ms != null && isFinite(p.tau_ms))
    .map((p) => ({ t: parseT(p.time), y: p.tau_ms, n: p.n_tracks,
      lo: p.tau_err_neg_ms != null ? p.tau_ms - p.tau_err_neg_ms : null,
      hi: p.tau_err_pos_ms != null ? p.tau_ms + p.tau_err_pos_ms : null,
      tip: clock(parseT(p.time)) + "  τ = " + fmtNum(p.tau_ms) + " ms" +
           (p.tau_err_pos_ms != null ? " +" + fmtNum(p.tau_err_pos_ms) : " +∞") +
           (p.tau_err_neg_ms != null ? " −" + fmtNum(p.tau_err_neg_ms) : "") +
           "  (" + p.n_tracks + " tracks)" }))
    .filter((p) => isFinite(p.t));
}

function drawLifetime() {
  const d = S.lifetime, holder = $("lt-plot");
  const running = !!(d && d.running);
  $("lt-update").disabled = running;
  const pts = ltPoints();
  const meta = d && d.main && d.main.meta;
  const failed = !running && S.summary && S.summary.last_status &&
                 S.summary.last_status.lifetime === "error";
  $("lt-sub").textContent = running
    ? "computing…" + (meta && meta.n_files_total ? " file " + (meta.n_files_done || 0) +
        "/" + meta.n_files_total : "")
    : [failed ? "last update failed" : "",
       meta && meta.generated ? "computed " + meta.generated.replace("T", " ") : ""]
        .filter(Boolean).join(" · ");
  if (!pts.length) {
    emptyMsg(holder, running ? "Computing lifetime — points appear as tracks accumulate."
      : failed ? "The last lifetime update failed — often too few anode–cathode tracks " +
                 "so far. The Lifetime page has its log."
      : "No lifetime points yet — press Update now (needs converted files).");
    return;
  }
  const taus = pts.map((p) => p.y);
  timeChart(holder, [{ name: "τ", color: ACCENT(), kind: "dots", points: pts }],
            { yLabel: "τ [ms]", yCap: Math.max(...taus) * 2 });
}

function lifetimeBody() {
  const meta = (S.lifetime && S.lifetime.main && S.lifetime.main.meta) || {};
  return { series: "main", source: "converted",
           efield: meta.efield || 500, bin_size: meta.bin_size || 100,
           group_by: meta.group_by === "file" ? "file" : "tracks" };
}

async function startLifetime(auto) {
  try {
    await postJSON("/api/lifetime/compute", lifetimeBody());
    S.lastAutoLt = Date.now();
    toast(auto ? "New converted file — lifetime update started" : "Lifetime update started");
    refreshLifetime();
  } catch (e) { if (!auto) toast(e.message); }
}

function maybeAutoLifetime() {
  const d = S.summary;
  if (!d || !d.files) return;
  const n = d.files.convert_records;
  const grew = S.lastConvRecords != null && n > S.lastConvRecords;
  S.lastConvRecords = n;
  if (!grew || !$("lt-auto").checked || d.running.lifetime) return;
  if (Date.now() - S.lastAutoLt < LT_AUTO_MIN_MS) return;
  startLifetime(true);
}

/* ---- spectrum ------------------------------------------------------------- */
function drawSpectrum() {
  const d = S.spec, holder = $("spec-plot");
  let legend = "";
  if (!d || d.error) {
    emptyMsg(holder, d && d.error ? d.error : "Loading…");
  } else if (!d.n_clusters) {
    emptyMsg(holder, "No clustered files yet — start the cluster watch (step 13).");
  } else {
    const series = [{ name: "all", color: ACCENT(), counts: d.all, fill: true }];
    const tot = d.all.reduce((a, b) => a + b, 0);
    const latTot = d.latest ? d.latest.reduce((a, b) => a + b, 0) : 0;
    if (d.latest && d.n_files > 1 && latTot > 0) {
      const k = tot / latTot;           // shape comparison: scale to the total
      series.push({ name: "newest", color: WARN(), counts: d.latest.map((c) => c * k) });
    }
    histChart(holder, d.edges, series, { logY: $("spec-log").checked,
      xLabel: "cluster charge [ke⁻]", yLabel: "clusters / bin" });
    legend = '<span><i style="background:' + ACCENT() + '"></i>all ' + d.n_files +
      " file(s)</span>" + (series.length > 1 ? '<span><i style="background:' + WARN() +
      '"></i>newest file (scaled to total)</span>' : "") +
      (d.underflow ? "<span>" + fmtNum(d.underflow, 0) + " below " + fmtNum(d.edges[0]) +
       " ke⁻</span>" : "") +
      (d.overflow ? "<span>" + fmtNum(d.overflow, 0) + " above " + fmtNum(d.edges[d.edges.length - 1]) +
       " ke⁻</span>" : "");
  }
  $("spec-legend").innerHTML = legend;
  $("spec-sub").textContent = d && !d.error && d.latest_file ? "newest: " + d.latest_file : "";
}

/* ---- refreshers ------------------------------------------------------------ */
async function refreshSummary() {
  const win = $("rate-window").value;
  try { S.summary = await api("/api/monitor/summary?minutes=" + win); }
  catch (e) { $("desc-status").textContent = "Waiting for the server… (" + e.message + ")"; return; }
  const d = S.summary;
  $("desc-status").innerHTML = d.descriptor
    ? "Descriptor: <b>" + esc(d.descriptor) + "</b>"
    : "<span style='color:var(--err)'>No descriptor set — set one on the flow page.</span>";
  const badge = $("run-badge");
  badge.className = "livebadge" + (d.running.self_trigger ? " live" : "");
  badge.textContent = d.running.self_trigger ? "run in progress" : "no run";
  renderKpis();
  renderBanner();
  drawRate();
  drawStage(d.convert, $("conv-plot"), "packets", ACCENT());
  drawStage(d.clustering, $("clu-plot"), "clusters", ACCENT2());
  maybeAutoLifetime();
  if (!S.booted) {                // first good summary: load the slow panels now
    S.booted = true;              // rather than waiting out their intervals
    tickPix(); tickEvd(); tickLt(); tickSpec();
  }
}

async function refreshPixmap() {
  if (!S.summary || !S.summary.descriptor) return;
  if (!S.geo) {
    try { S.geo = await api("/api/pixelmap/geometry"); }
    catch (e) { emptyMsg($("pix-plot"), "Pixel geometry unavailable: " + e.message); return; }
  }
  try { S.density = await api("/api/pixelmap/density"); }
  catch (e) { S.density = null; }
  drawPixmap();
}

async function refreshEvent() {
  const d = S.summary;
  if (!d || !d.descriptor) return;
  const minHits = Math.max(1, parseInt($("evd-minhits").value, 10) || 20);
  let qs = "min_hits=" + minHits;
  let label;
  if (d.running.self_trigger) {
    qs += "&src=live";
    label = "live";
  } else {
    const src = await api("/api/evd/sources");
    const newest = src.files && src.files.converted && src.files.converted.newest;
    if (!newest) { S.event = { error: "No converted files yet" }; drawEvent(); return; }
    qs += "&src=file&kind=converted&path=" + encodeURIComponent(newest);
    label = "newest converted · " + basename(newest);
  }
  try {
    const idx = await api("/api/evd/events?" + qs);
    if (!idx.n_events) {
      S.event = { error: "No event with ≥ " + minHits + " hits yet" };
      $("evd-sub").textContent = label;
    } else {
      S.event = await api("/api/evd/event?" + qs + "&index=" + (idx.n_events - 1));
      $("evd-sub").textContent = label + " · event " + idx.n_events + "/" + idx.n_events +
        " · " + S.event.n_hits + " hits · " + fmtNum(S.event.span_us, 0) + " µs";
    }
  } catch (e) {
    S.event = { error: e.message };
  }
  drawEvent();
}

async function refreshLifetime() {
  try { S.lifetime = await api("/api/lifetime/series"); } catch (e) { return; }
  drawLifetime();
  renderKpis();
}

async function refreshSpectrum() {
  if (!S.summary || !S.summary.descriptor) return;
  const min = parseFloat($("spec-min").value) || 0;
  const max = parseFloat($("spec-max").value) || 100;
  const minhit = Math.max(1, parseInt($("spec-minhit").value, 10) || 1);
  try {
    S.spec = await api("/api/monitor/spectrum?bins=100&min_ke=" + min + "&max_ke=" + max +
                       "&min_nhit=" + minhit);
  } catch (e) { S.spec = { error: e.message }; }
  drawSpectrum();
  renderKpis();
}

// Run fn now and every ms; skip a tick while the previous one is in flight
// or the tab is hidden (no point polling a page nobody is looking at).
function every(fn, ms) {
  let busy = false;
  const tick = async () => {
    if (busy || document.hidden) return;
    busy = true;
    try { await fn(); } catch (e) { console.warn(e); } finally { busy = false; }
  };
  setInterval(tick, ms);
  return tick;
}

/* ---- wiring ---------------------------------------------------------------- */
function redrawAll() {
  if (S.summary) {
    drawRate();
    drawStage(S.summary.convert, $("conv-plot"), "packets", ACCENT());
    drawStage(S.summary.clustering, $("clu-plot"), "clusters", ACCENT2());
  }
  drawPixmap();
  drawEvent();
  drawLifetime();
  drawSpectrum();
}

try { $("lt-auto").checked = localStorage.getItem("larpix-mon-lt-auto") === "1"; } catch (e) {}
$("lt-auto").onchange = () => {
  try { localStorage.setItem("larpix-mon-lt-auto", $("lt-auto").checked ? "1" : "0"); } catch (e) {}
};
$("lt-update").onclick = () => startLifetime(false);
$("spec-log").onchange = drawSpectrum;

const tickSummary = every(refreshSummary, 2500);
const tickPix = every(refreshPixmap, 5000);
const tickEvd = every(refreshEvent, 10000);
const tickLt = every(refreshLifetime, 15000);
const tickSpec = every(refreshSpectrum, 30000);
$("spec-max").onchange = tickSpec;
$("spec-min").onchange = tickSpec;
$("spec-minhit").onchange = tickSpec;
$("evd-minhits").onchange = tickEvd;
$("rate-window").onchange = tickSummary;

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(redrawAll, 150);
});
document.addEventListener("themechange", redrawAll);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { tickSummary(); tickPix(); }
});

emptyMsg($("rate-plot"), "Loading…");
emptyMsg($("conv-plot"), "Loading…");
emptyMsg($("clu-plot"), "Loading…");
emptyMsg($("lt-plot"), "Loading…");
drawEvent();
drawSpectrum();
// summary first: the other panels need to know the descriptor / run state,
// and refreshSummary starts them once it has it
tickSummary();

})();
