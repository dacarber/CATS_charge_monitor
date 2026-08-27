"use strict";
// Tiny self-contained SVG charts (no external libraries / CDN).

const SVGNS = "http://www.w3.org/2000/svg";

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function el(name, attrs, text) {
  const e = document.createElementNS(SVGNS, name);
  for (const k in (attrs || {})) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}

function niceMax(v) {
  if (!v || v <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / pow;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * pow;
}

function clear(container) { container.innerHTML = ""; }

// series: [{name, color, points:[{label, y}]}]  (x = index)
function drawLine(container, series, opts) {
  opts = opts || {};
  clear(container);
  const W = container.clientWidth || 600, H = 260;
  const m = { l: 56, r: 16, t: 16, b: 46 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H });

  let maxY = 0, n = 0;
  series.forEach((s) => {
    n = Math.max(n, s.points.length);
    s.points.forEach((p) => { if (p.y != null && p.y > maxY) maxY = p.y; });
  });
  maxY = niceMax(maxY);
  const xOf = (i) => m.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const yOf = (v) => m.t + ih - (v / maxY) * ih;

  // gridlines + y labels
  for (let g = 0; g <= 4; g++) {
    const val = (maxY / 4) * g;
    const y = yOf(val);
    svg.appendChild(el("line", { x1: m.l, y1: y, x2: m.l + iw, y2: y,
      stroke: cssVar("--grid", "#2b3340"), "stroke-width": 1 }));
    svg.appendChild(el("text", { x: m.l - 8, y: y + 4, fill: cssVar("--muted", "#8b97a8"),
      "font-size": 11, "text-anchor": "end" }, String(+val.toFixed(2))));
  }
  // axes
  svg.appendChild(el("line", { x1: m.l, y1: m.t, x2: m.l, y2: m.t + ih,
    stroke: cssVar("--axis", "#3a4452") }));
  svg.appendChild(el("line", { x1: m.l, y1: m.t + ih, x2: m.l + iw, y2: m.t + ih,
    stroke: cssVar("--axis", "#3a4452") }));
  if (opts.yLabel) {
    svg.appendChild(el("text", {
      x: 14, y: m.t + ih / 2, fill: cssVar("--muted", "#8b97a8"), "font-size": 11,
      "text-anchor": "middle",
      transform: `rotate(-90 14 ${m.t + ih / 2})`,
    }, opts.yLabel));
  }

  // x labels (sparse)
  const ref = series[0] ? series[0].points : [];
  const stride = Math.ceil((ref.length || 1) / 6);
  ref.forEach((p, i) => {
    if (i % stride !== 0 && i !== ref.length - 1) return;
    svg.appendChild(el("text", { x: xOf(i), y: m.t + ih + 16, fill: cssVar("--muted", "#8b97a8"),
      "font-size": 10, "text-anchor": "middle" }, p.label || String(i)));
  });

  series.forEach((s) => {
    let d = "";
    s.points.forEach((p, i) => {
      if (p.y == null) return;
      d += (d ? " L" : "M") + xOf(i) + " " + yOf(p.y);
    });
    if (d) svg.appendChild(el("path", { d, fill: "none", stroke: s.color,
      "stroke-width": 2 }));
    s.points.forEach((p, i) => {
      if (p.y == null) return;
      const c = el("circle", { cx: xOf(i), cy: yOf(p.y), r: 3, fill: s.color });
      c.appendChild(el("title", {}, (p.label || "") + " : " + p.y));
      svg.appendChild(c);
    });
  });

  // legend
  if (series.length > 1) {
    let lx = m.l;
    series.forEach((s) => {
      svg.appendChild(el("rect", { x: lx, y: 2, width: 10, height: 10, fill: s.color }));
      const t = el("text", { x: lx + 14, y: 11, fill: cssVar("--text", "#d6deeb"), "font-size": 11 }, s.name);
      svg.appendChild(t);
      lx += 14 + s.name.length * 7 + 16;
    });
  }
  container.appendChild(svg);
}

// items: [{label, value}]
function drawBars(container, items, opts) {
  opts = opts || {};
  clear(container);
  const W = container.clientWidth || 600, H = 260;
  const m = { l: 56, r: 16, t: 16, b: 46 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H });

  let maxY = niceMax(Math.max(0, ...items.map((d) => d.value || 0)));
  const n = items.length || 1;
  const bw = Math.max(2, (iw / n) * 0.7);
  const yOf = (v) => m.t + ih - (v / maxY) * ih;

  for (let g = 0; g <= 4; g++) {
    const val = (maxY / 4) * g;
    const y = yOf(val);
    svg.appendChild(el("line", { x1: m.l, y1: y, x2: m.l + iw, y2: y,
      stroke: cssVar("--grid", "#2b3340") }));
    svg.appendChild(el("text", { x: m.l - 8, y: y + 4, fill: cssVar("--muted", "#8b97a8"),
      "font-size": 11, "text-anchor": "end" }, String(+val.toFixed(0))));
  }
  svg.appendChild(el("line", { x1: m.l, y1: m.t + ih, x2: m.l + iw, y2: m.t + ih,
    stroke: cssVar("--axis", "#3a4452") }));
  if (opts.yLabel) {
    svg.appendChild(el("text", {
      x: 14, y: m.t + ih / 2, fill: cssVar("--muted", "#8b97a8"), "font-size": 11,
      "text-anchor": "middle", transform: `rotate(-90 14 ${m.t + ih / 2})`,
    }, opts.yLabel));
  }

  const stride = Math.ceil(n / 8);
  items.forEach((d, i) => {
    const x = m.l + (i + 0.5) * (iw / n) - bw / 2;
    const y = yOf(d.value || 0);
    const r = el("rect", { x, y, width: bw, height: (m.t + ih) - y,
      fill: opts.color || "#4f9dff", rx: 2 });
    r.appendChild(el("title", {}, (d.label || "") + " : " + (d.value || 0)));
    svg.appendChild(r);
    if (i % stride === 0) {
      svg.appendChild(el("text", { x: m.l + (i + 0.5) * (iw / n),
        y: m.t + ih + 16, fill: cssVar("--muted", "#8b97a8"), "font-size": 10,
        "text-anchor": "middle" }, String(i + 1)));
    }
  });
  container.appendChild(svg);
}

window.Charts = { drawLine, drawBars };
