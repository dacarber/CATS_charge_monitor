/* Event display -- hits inside one drift-time window, in 3D, colored by charge.
 *
 * The 3D projection is the same orthographic yaw/pitch renderer the lifetime
 * point modal uses (lifetime.js project/evdRender), reused here for a whole
 * event instead of a group of tracks; hits are colored by collected charge
 * rather than by track index.
 */
initTheme();
mountThemeToggle($("theme-slot"));

const VIEW = {
  src: "live", path: "", kind: "auto",
  mode: "window", minHits: 20,
  efield: null, hv: "",
  events: [], index: 0, pinned: false,
  hits: null, meta: {},
  yaw: 0.7, pitch: 0.45, zoom: 1.0, center: [0, 0, 0], scale: 1,
  timer: null, loading: false,
};

/* ---- charge color ramp (blue -> cyan -> green -> yellow -> red) ---------- */
const RAMP = [[40, 60, 160], [30, 150, 200], [60, 190, 110],
              [230, 200, 60], [220, 70, 50]];

function chargeColor(f) {
  f = Math.max(0, Math.min(1, f));
  const x = f * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const t = x - i, a = RAMP[i], b = RAMP[i + 1];
  return "rgb(" + Math.round(a[0] + (b[0] - a[0]) * t) + "," +
                  Math.round(a[1] + (b[1] - a[1]) * t) + "," +
                  Math.round(a[2] + (b[2] - a[2]) * t) + ")";
}

function paintGradient() {
  const stops = RAMP.map((c, i) =>
    "rgb(" + c.join(",") + ") " + (i / (RAMP.length - 1) * 100).toFixed(0) + "%");
  $("q-gradient").style.background = "linear-gradient(90deg," + stops.join(",") + ")";
}

/* ---- 3D projection ------------------------------------------------------- */
function project(x, y, z) {
  const cx = x - VIEW.center[0], cy = y - VIEW.center[1], cz = z - VIEW.center[2];
  const cyaw = Math.cos(VIEW.yaw), syaw = Math.sin(VIEW.yaw);
  const cp = Math.cos(VIEW.pitch), sp = Math.sin(VIEW.pitch);
  const x1 = cyaw * cx + syaw * cz;
  const z1 = -syaw * cx + cyaw * cz;
  const y1 = cp * cy - sp * z1;
  const s = VIEW.scale * VIEW.zoom;
  const canvas = $("evd-canvas");
  return [canvas.width / 2 + x1 * s, canvas.height / 2 - y1 * s];
}

function fitView(h) {
  const n = h.x.length;
  let mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < n; i++) {
    const p = [h.x[i], h.y[i], h.z[i]];
    for (let k = 0; k < 3; k++) {
      if (p[k] < mn[k]) mn[k] = p[k];
      if (p[k] > mx[k]) mx[k] = p[k];
    }
  }
  if (n) VIEW.bbox = [mn, mx];   // hit extent only -- drives the axis ticks

  // Camera fit uses hits + the detector volume together, so the outline
  // stays fully in view even for a handful of hits in a big empty volume.
  let fmn = mn.slice(), fmx = mx.slice();
  if (VIEW.detector) {
    const d = VIEW.detector;
    fmn = [Math.min(fmn[0], d.x[0]), Math.min(fmn[1], d.y[0]), Math.min(fmn[2], d.z[0])];
    fmx = [Math.max(fmx[0], d.x[1]), Math.max(fmx[1], d.y[1]), Math.max(fmx[2], d.z[1])];
  }
  if (!isFinite(fmn[0])) return;   // no hits and no detector bounds yet
  VIEW.center = [(fmn[0] + fmx[0]) / 2, (fmn[1] + fmx[1]) / 2, (fmn[2] + fmx[2]) / 2];
  const ext = Math.max(fmx[0] - fmn[0], fmx[1] - fmn[1], fmx[2] - fmn[2], 1);
  const canvas = $("evd-canvas");
  VIEW.scale = 0.62 * Math.min(canvas.width, canvas.height) / ext;
}

/* ---- detector outline: wireframe box from anode plane to full drift depth */
function drawDetectorOutline(ctx, det) {
  const [x0, x1] = det.x, [y0, y1] = det.y, [z0, z1] = det.z;
  const corners = [
    [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],   // anode face
    [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],   // cathode face
  ].map((c) => project(c[0], c[1], c[2]));
  const edges = [
    [0, 1], [1, 2], [2, 3], [3, 0],   // anode face
    [4, 5], [5, 6], [6, 7], [7, 4],   // cathode face
    [0, 4], [1, 5], [2, 6], [3, 7],   // anode -> cathode verticals
  ];
  ctx.save();
  ctx.strokeStyle = cssVar("--axis", "#3a4452");
  ctx.setLineDash([4, 3]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  edges.forEach(([a, b]) => {
    ctx.moveTo(corners[a][0], corners[a][1]);
    ctx.lineTo(corners[b][0], corners[b][1]);
  });
  ctx.stroke();
  ctx.restore();
}

function render() {
  const canvas = $("evd-canvas");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = cssVar("--bg", "#0e1116");
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (VIEW.detector) drawDetectorOutline(ctx, VIEW.detector);
  const h = VIEW.hits;
  if (!h || !h.x.length) {
    ctx.fillStyle = cssVar("--muted", "#8b97a8");
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No event loaded", canvas.width / 2, canvas.height / 2);
    ctx.textAlign = "start";
    return;
  }

  // axes from the bbox min corner
  if (VIEW.bbox) {
    const [mn, mx] = VIEW.bbox;
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

  // hits, painted far-to-near so nearer hits land on top
  const n = h.x.length;
  const idx = Array.from({ length: n }, (_, i) => i);
  const depth = idx.map((i) => {
    const cx = h.x[i] - VIEW.center[0], cz = h.z[i] - VIEW.center[2];
    return -Math.sin(VIEW.yaw) * cx + Math.cos(VIEW.yaw) * cz;
  });
  idx.sort((a, b) => depth[a] - depth[b]);
  const qmin = VIEW.qmin, qspan = (VIEW.qmax - VIEW.qmin) || 1;
  for (const i of idx) {
    const [px, py] = project(h.x[i], h.y[i], h.z[i]);
    ctx.fillStyle = chargeColor((h.q[i] - qmin) / qspan);
    ctx.fillRect(px - 1.4, py - 1.4, 2.8, 2.8);
  }
}

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name);
  return (v || "").trim() || fallback;
}

/* ---- data loading -------------------------------------------------------- */
function qs(extra) {
  const p = new URLSearchParams({
    src: VIEW.src, kind: VIEW.kind, mode: VIEW.mode,
    min_hits: String(VIEW.minHits),
  });
  if (VIEW.src !== "live") p.set("path", VIEW.path);
  if (VIEW.efield != null) p.set("efield", String(VIEW.efield));
  else if (VIEW.hv) p.set("hv", VIEW.hv);
  Object.entries(extra || {}).forEach(([k, v]) => p.set(k, String(v)));
  return p.toString();
}

async function loadEvents(keepIndex) {
  // Never drop a request: a click that lands while an auto-refresh is in flight
  // is queued and re-run, so the UI can't silently ignore the user.
  if (VIEW.loading) { VIEW.queued = { keepIndex }; return; }
  VIEW.loading = true;
  try {
    const d = await api("/api/evd/events?" + qs());
    VIEW.events = d.events || [];
    VIEW.meta = d;
    applyMeta(d);
    const last = Math.max(0, VIEW.events.length - 1);
    $("evd-slider").max = String(last);
    if (!keepIndex && !VIEW.pinned) VIEW.index = last;   // follow the newest
    VIEW.index = Math.min(VIEW.index, last);
    $("evd-slider").value = String(VIEW.index);
    VIEW.lastRow = null;        // the event set changed -> full rebuild
    renderList();
    await loadEvent();
    $("evd-refreshed").textContent = "updated " +
      new Date().toLocaleTimeString();
  } catch (e) {
    $("evd-eventinfo").textContent = e.message;
    $("src-badge").textContent = "error";
  } finally {
    VIEW.loading = false;
    const q = VIEW.queued;
    if (q) { VIEW.queued = null; loadEvents(q.keepIndex); }
  }
}

async function loadEvent() {
  if (!VIEW.events.length) {
    VIEW.hits = null; render();
    $("evd-eventinfo").textContent = "No event passed the minimum-hits cut.";
    return;
  }
  try {
    const d = await api("/api/evd/event?" + qs({ index: VIEW.index }));
    VIEW.hits = { x: d.x, y: d.y, z: d.z, q: d.q };
    VIEW.detector = d.detector;
    VIEW.qmin = Math.min.apply(null, d.q);
    VIEW.qmax = Math.max.apply(null, d.q);
    $("q-min").textContent = VIEW.qmin.toFixed(0);
    $("q-max").textContent = VIEW.qmax.toFixed(0);
    if (!VIEW.userMoved) fitView(VIEW.hits);
    render();
    showInfo(d);
  } catch (e) { $("evd-eventinfo").textContent = e.message; }
}

function applyMeta(d) {
  $("evd-window").textContent = "drift window " + d.window_us + " µs" +
    (d.hv ? "  (HV " + d.hv + " → " + d.efield + " V/cm, " + d.hv_source + ")"
          : "  (E = " + d.efield + " V/cm, " + d.hv_source + ")");
  if (VIEW.efield == null && !VIEW.hv) $("evd-efield").placeholder = d.efield;
  $("ev-count").textContent = d.n_events;
  $("ei-window").textContent = d.window_us + " µs";
  $("ei-efield").textContent = d.efield + " V/cm";
  // the file's own clusters are only meaningful if it actually has them
  const cl = $("evd-mode").querySelector('option[value="cluster"]');
  cl.disabled = !d.has_clusters;
  cl.textContent = d.has_clusters ? "file's own clusters"
                                  : "file's own clusters (none in file)";
  if (VIEW.src === "live") {
    $("src-badge").textContent = "LIVE";
    $("ei-src").textContent = (d.file || "live") +
      (d.total_msgs ? " · " + d.parsed_msgs + "/" + d.total_msgs + " msgs" : "");
  } else {
    $("src-badge").textContent = d.kind || "file";
    $("ei-src").textContent = basename(VIEW.path) + " (" + d.kind + ")";
  }
}

function showInfo(d) {
  $("ei-index").textContent = (d.index + 1) + " / " + d.n_events;
  $("ei-hits").textContent = d.n_hits +
    (d.n_drawn < d.n_hits ? " (" + d.n_drawn + " drawn)" : "");
  $("ei-t").textContent = d.t_start_s + " s";
  $("ei-span").textContent = d.span_us + " µs";
  $("ei-zsrc").textContent = d.z_source === "file"
    ? "file (reconstructed)" : "drift time × v_drift";
  const ev = VIEW.events[d.index];
  $("ei-q").textContent = ev ? ev.q_total.toLocaleString() + " ADC" : "-";
  $("evd-eventinfo").textContent =
    "Event " + (d.index + 1) + " of " + d.n_events + " · " + d.n_hits +
    " hits · " + d.span_us + " µs span · starts at t = " + d.t_start_s + " s";
}

// Full rebuild -- only when the event set itself changes. Rebuilding a list of
// hundreds of rows on every step made playback frontend-bound (the fetch is
// ~25 ms), so stepping uses highlightRow() instead.
function renderList() {
  const box = $("ev-list");
  box.innerHTML = "";
  VIEW.events.forEach((e, i) => {
    const row = document.createElement("div");
    row.className = "evd-row" + (i === VIEW.index ? " sel" : "");
    row.innerHTML = '<span class="mono">#' + (i + 1) + "</span>" +
      '<span class="hint">' + e.n_hits + " hits</span>" +
      '<span class="hint" style="margin-left:auto">' + e.t_start_s + " s</span>";
    row.onclick = () => { VIEW.index = i; VIEW.pinned = true;
      $("evd-pin").checked = true; $("evd-slider").value = String(i);
      highlightRow(); loadEvent(); };
    box.appendChild(row);
  });
  highlightRow();
}

// Cheap selection move: toggle the class and scroll, no DOM rebuild.
function highlightRow() {
  const rows = $("ev-list").children;
  if (VIEW.lastRow != null && rows[VIEW.lastRow])
    rows[VIEW.lastRow].classList.remove("sel");
  const sel = rows[VIEW.index];
  if (sel) { sel.classList.add("sel"); sel.scrollIntoView({ block: "nearest" }); }
  VIEW.lastRow = VIEW.index;
}

/* ---- controls ------------------------------------------------------------ */
$("evd-src").onchange = () => {
  VIEW.src = $("evd-src").value;
  const isFile = VIEW.src === "file";
  $("evd-path").style.display = isFile ? "" : "none";
  $("evd-kind").style.display = isFile ? "" : "none";
  if (!isFile) { VIEW.path = ""; loadEvents(); }
};
// an explicit E-field wins; otherwise HV is converted server-side
function readField() {
  const ef = $("evd-efield").value.trim(), hv = $("evd-hv").value.trim();
  VIEW.efield = ef ? parseFloat(ef) : null;
  VIEW.hv = ef ? "" : hv;
}
$("evd-load").onclick = () => {
  if (VIEW.src === "file") {
    const p = $("evd-path").value.trim();
    if (!p) { toast("Enter a file path"); return; }
    VIEW.path = p; VIEW.kind = $("evd-kind").value;
    try { localStorage.setItem("larpix-evd-path", p); } catch (e) {}
  }
  readField();            // pick up an HV/E-field typed before pressing Load
  stopPlay();             // the event set is about to change under playback
  VIEW.userMoved = false; VIEW.pinned = false; $("evd-pin").checked = false;
  loadEvents();
};
$("evd-apply-field").onclick = () => { readField(); loadEvents(true); };
$("evd-mode").onchange = () => { VIEW.mode = $("evd-mode").value; loadEvents(); };
$("evd-minhits").onchange = () => {
  VIEW.minHits = Math.max(1, parseInt($("evd-minhits").value, 10) || 20);
  loadEvents();
};
$("evd-refresh").onclick = () => loadEvents(true);
$("evd-pin").onchange = () => { VIEW.pinned = $("evd-pin").checked; };
$("evd-prev").onclick = () => step(-1);
$("evd-next").onclick = () => step(1);
$("evd-slider").oninput = () => {
  VIEW.index = parseInt($("evd-slider").value, 10) || 0;
  VIEW.pinned = true; $("evd-pin").checked = true;
  highlightRow(); loadEvent();
};
function step(d) {
  if (!VIEW.events.length) return;
  VIEW.index = Math.max(0, Math.min(VIEW.events.length - 1, VIEW.index + d));
  VIEW.pinned = true; $("evd-pin").checked = true;
  $("evd-slider").value = String(VIEW.index);
  highlightRow(); loadEvent();
}

/* ---- playback: step through the events automatically --------------------- */
function stopPlay() {
  if (VIEW.playTimer) { clearTimeout(VIEW.playTimer); VIEW.playTimer = null; }
  VIEW.playing = false;
  $("evd-play").innerHTML = "&#9654;";
  $("evd-play").title = "play through the events automatically";
}

function startPlay() {
  if (!VIEW.events.length) { toast("No events to play"); return; }
  VIEW.playing = true;
  $("evd-play").innerHTML = "&#9208;";
  $("evd-play").title = "pause";
  // playback drives the index, so pin it: otherwise an auto-refresh tick would
  // snap back to the newest event mid-playthrough
  VIEW.pinned = true; $("evd-pin").checked = true;
  tickPlay();
}

// Self-scheduling rather than setInterval: each step fetches its event, so a
// fixed interval could stack requests when a fetch is slower than the delay.
async function tickPlay() {
  if (!VIEW.playing) return;
  const n = VIEW.events.length;
  if (n) {
    VIEW.index = (VIEW.index + 1) % n;        // wrap around at the end
    $("evd-slider").value = String(VIEW.index);
    highlightRow();
    await loadEvent();
  }
  if (!VIEW.playing) return;                  // paused while we were fetching
  const secs = Math.max(0.1, parseFloat($("evd-speed").value) || 0.6);
  VIEW.playTimer = setTimeout(tickPlay, secs * 1000);
}

$("evd-play").onclick = () => (VIEW.playing ? stopPlay() : startPlay());

/* ---- auto-refresh -------------------------------------------------------- */
function rearmTimer() {
  if (VIEW.timer) clearInterval(VIEW.timer);
  VIEW.timer = null;
  if (!$("evd-auto").checked) return;
  const secs = Math.max(2, parseInt($("evd-interval").value, 10) || 30);
  VIEW.timer = setInterval(() => loadEvents(true), secs * 1000);
}
$("evd-auto").onchange = rearmTimer;
$("evd-interval").onchange = rearmTimer;

/* ---- mouse: drag rotate, wheel zoom, double-click reset ------------------ */
(() => {
  const canvas = $("evd-canvas");
  let dragging = false, lx = 0, ly = 0;
  canvas.addEventListener("mousedown", (e) => {
    dragging = true; lx = e.clientX; ly = e.clientY;
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    VIEW.yaw += (e.clientX - lx) * 0.01;
    VIEW.pitch += (e.clientY - ly) * 0.01;
    VIEW.pitch = Math.max(-1.4, Math.min(1.4, VIEW.pitch));
    lx = e.clientX; ly = e.clientY;
    VIEW.userMoved = true;
    render();
  });
  window.addEventListener("mouseup", () => { dragging = false; });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    VIEW.zoom *= (e.deltaY > 0 ? 0.9 : 1.1);
    VIEW.zoom = Math.max(0.2, Math.min(8, VIEW.zoom));
    VIEW.userMoved = true;
    render();
  }, { passive: false });
  canvas.addEventListener("dblclick", () => {
    VIEW.yaw = 0.7; VIEW.pitch = 0.45; VIEW.zoom = 1.0;
    VIEW.userMoved = false;
    if (VIEW.hits) fitView(VIEW.hits);
    render();
  });
})();

/* ---- boot ---------------------------------------------------------------- */
async function boot() {
  paintGradient();
  render();
  try {
    const s = await api("/api/evd/sources");
    const bits = Object.entries(s.files || {})
      .filter(([, v]) => v.newest)
      .map(([k, v]) => k + ": " + v.n + " file(s)");
    $("evd-newest").textContent = bits.join(" · ");
    if (!s.live) {
      // no run writing right now -- open the newest processed file instead
      const pick = (s.files || {}).converted || {};
      const alt = pick.newest || ((s.files || {}).clustered || {}).newest ||
                  ((s.files || {}).raw || {}).newest;
      if (alt) {
        VIEW.src = "file"; VIEW.path = alt;
        $("evd-src").value = "file";
        $("evd-path").style.display = ""; $("evd-kind").style.display = "";
        $("evd-path").value = alt;
      }
    }
  } catch (e) {}
  try {
    const saved = localStorage.getItem("larpix-evd-path");
    if (saved && !$("evd-path").value) $("evd-path").value = saved;
  } catch (e) {}
  await loadEvents();
  rearmTimer();
}
boot();

// state pill in the header bar, same as the other pages
api("/api/state").then((st) => {
  $("desc-status").innerHTML = st.active
    ? "Descriptor: <b>" + st.active + "</b>"
    : '<span style="color:var(--err)">No descriptor set — open the flow page first.</span>';
}).catch(() => {});
