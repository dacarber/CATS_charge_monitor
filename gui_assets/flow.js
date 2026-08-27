"use strict";

mountThemeToggle($("theme-slot"));

const SVGNS = "http://www.w3.org/2000/svg";
let STATE = null;

function canvasSize() {
  let w = 0, h = 0;
  Object.values(STEPS).forEach((s) => {
    w = Math.max(w, s.x + NODE_W);
    h = Math.max(h, s.y + NODE_H);
  });
  return { w: w + 20, h: h + 20 };
}

function elbow(a, b) {
  // exit right edge of a, enter left edge of b, orthogonal elbow
  const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
  const x2 = b.x, y2 = b.y + NODE_H / 2;
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} H ${mx} V ${y2} H ${x2}`;
}

function buildFlow() {
  const flow = $("flow");
  const { w, h } = canvasSize();
  flow.style.width = w + "px";
  flow.style.height = h + "px";
  flow.innerHTML = "";

  // edges layer
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", "edges");
  svg.setAttribute("width", w);
  svg.setAttribute("height", h);
  const defs = document.createElementNS(SVGNS, "defs");
  defs.innerHTML =
    '<marker id="arrow" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">' +
    '<path d="M0,0 L7,3 L0,6 Z" fill="var(--axis)"/></marker>';
  svg.appendChild(defs);
  FLOW_EDGES.forEach(([from, to]) => {
    const p = document.createElementNS(SVGNS, "path");
    p.setAttribute("class", "edge");
    p.setAttribute("d", elbow(STEPS[from], STEPS[to]));
    p.setAttribute("marker-end", "url(#arrow)");
    svg.appendChild(p);
  });
  flow.appendChild(svg);

  // nodes
  Object.entries(STEPS).forEach(([key, s]) => {
    const a = document.createElement("a");
    a.className = "node idle";
    a.id = "node-" + key;
    a.href = "/step?step=" + key;
    a.style.left = s.x + "px";
    a.style.top = s.y + "px";
    a.innerHTML =
      '<div class="nt"><span class="num">' + s.num + "</span>" + s.title + "</div>" +
      '<div class="nmeta"><span class="status-dot" id="dot-' + key + '"></span>' +
      '<span id="st-' + key + '">idle</span>' +
      (s.hw ? '<span class="badge-hw" title="hardware-exclusive">HW</span>' : "") +
      "</div>";
    flow.appendChild(a);
  });
}

function applyStatus(stepStatus) {
  Object.keys(STEPS).forEach((key) => {
    const st = (stepStatus && stepStatus[key]) || "idle";
    const node = $("node-" + key);
    const dot = $("dot-" + key);
    const lab = $("st-" + key);
    if (!node) return;
    node.classList.remove("running", "done", "error", "stopped", "idle");
    node.classList.add(st);
    dot.className = "status-dot " + (st === "idle" ? "" : st);
    lab.textContent = st;
  });
}

async function refresh() {
  try { STATE = await api("/api/state"); } catch (e) { return; }
  if (STATE.active) {
    $("desc-status").innerHTML = "Active: <b>" + STATE.active + "</b> &nbsp; " +
      '<span class="cryo">' + (STATE.cryo_flag ? "CRYO" : "WARM") + "</span>";
    if (!$("descriptor").value) $("descriptor").value = STATE.active;
  } else if (STATE.last_descriptor) {
    $("desc-status").textContent = "Last used: " + STATE.last_descriptor + " (not set)";
    if (!$("descriptor").value) $("descriptor").value = STATE.last_descriptor;
  }
  applyStatus(STATE.step_status);
}

$("set-descriptor").onclick = async () => {
  const descriptor = $("descriptor").value.trim();
  if (!descriptor) { toast("Enter a descriptor"); return; }
  const cryo = document.querySelector("input[name=cryo]:checked").value === "c";
  await withBusy($("set-descriptor"), "Setting up…", async () => {
    try {
      await postJSON("/api/descriptor", { descriptor, cryo });
      toast("Descriptor ready: " + descriptor);
      await refresh();
    } catch (e) { toast(e.message); }
  });
};

buildFlow();
refresh();
setInterval(refresh, 1500);
