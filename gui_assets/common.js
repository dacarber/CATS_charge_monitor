"use strict";

/* ---- theme -------------------------------------------------------------- */
function initTheme() {
  const t = localStorage.getItem("larpix-theme") || "dark";
  document.documentElement.setAttribute("data-theme", t);
  return t;
}
function setTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  localStorage.setItem("larpix-theme", t);
  document.dispatchEvent(new CustomEvent("themechange", { detail: t }));
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  setTheme(cur);
  return cur;
}
function mountThemeToggle(container) {
  const btn = document.createElement("button");
  btn.className = "theme-toggle";
  const label = () => (document.documentElement.getAttribute("data-theme") === "dark"
    ? "☀ Light mode" : "☾ Dark mode");
  btn.textContent = label();
  btn.onclick = () => { toggleTheme(); btn.textContent = label(); };
  container.appendChild(btn);
}

/* ---- misc helpers ------------------------------------------------------- */
const $ = (id) => document.getElementById(id);
function basename(p) { return (p || "").split("/").pop(); }

function toast(msg) {
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast"; t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2800);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) {}
  if (!res.ok || data.error) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
function postJSON(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

/* ---- loading / busy indicators ------------------------------------------- */
// Inline spinner glyph (SVG, no external asset) for buttons and placeholders.
const SPINNER_SVG =
  '<svg class="spin" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">' +
  '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="3" ' +
  'stroke-linecap="round" stroke-dasharray="42" stroke-dashoffset="14"/></svg>';

// Placeholder markup for a container's first load, before any data has arrived.
function loadingHTML(text) {
  return '<div class="loading-block">' + SPINNER_SVG +
    '<span>' + (text || "Loading…") + "</span></div>";
}

// Disable a button and show a spinner + busy text while an async action runs;
// restores the original label and enabled state afterward (even on error).
async function withBusy(btn, busyText, fn) {
  if (!btn) return fn();
  const prevHTML = btn.innerHTML;
  const prevDisabled = btn.disabled;
  btn.disabled = true;
  btn.innerHTML = SPINNER_SVG + " " + (busyText || "Working…");
  try {
    return await fn();
  } finally {
    btn.disabled = prevDisabled;
    btn.innerHTML = prevHTML;
  }
}

function fillSelect(sel, items, keepValue = true) {
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = "";
  (items || []).forEach((p) => {
    const o = document.createElement("option");
    o.value = p; o.textContent = basename(p);
    sel.appendChild(o);
  });
  if (keepValue && prev && items && items.includes(prev)) sel.value = prev;
}

/* ---- step definitions (shared by flow diagram + step page) -------------- */
// x,y are pixel positions on the flow canvas. hw = hardware-exclusive.
const STEPS = {
  check_power:    { num: "1",  title: "Check power",        hw: true,  x: 20,   y: 150, desc: "Runs check_power.py" },
  make_hydra:     { num: "2",  title: "Make hydra network", hw: true,  x: 215,  y: 150, desc: "Runs map_uart_links_qc.py" },
  plot_hydra:     { num: "3",  title: "Plot hydra network", hw: false, x: 410,  y: 30,  desc: "Plot a hydra network file" },
  trigger_rate:   { num: "4",  title: "Trigger-rate list",  hw: true,  x: 410,  y: 150, desc: "Make trigger-rate disabled list" },
  pedestal:       { num: "5",  title: "Pedestal run",       hw: true,  x: 605,  y: 150, desc: "Run pedestal acquisition" },
  plot_disabled:  { num: "6",  title: "Plot disabled chans",hw: false, x: 605,  y: 270, desc: "Plot disabled channels map" },
  find_thresholds:{ num: "7",  title: "Find thresholds",    hw: true,  x: 800,  y: 150, desc: "Find thresholds, write ASIC configs" },
  thresholds:     { num: "12", title: "Adjust thresholds",  hw: false, x: 995,  y: 30,  desc: "Raise/lower thresholds in configs" },
  self_trigger:   { num: "9",  title: "Self-trigger run",   hw: true,  x: 995,  y: 150, desc: "Acquire self-trigger data" },
  convert:        { num: "10", title: "Convert raw files",  hw: false, x: 1190, y: 150, desc: "Convert raw -> packets" },
  plot_metrics:   { num: "11", title: "Plot mean/stdev/rate",hw: false,x: 1190, y: 270, desc: "Run larpix-monitor plots" },
  clustering:     { num: "13", title: "Charge clustering",  hw: false, x: 1385, y: 150, desc: "Cluster converted files" },
};
const FLOW_EDGES = [
  ["check_power", "make_hydra"],
  ["make_hydra", "plot_hydra"],
  ["make_hydra", "trigger_rate"],
  ["trigger_rate", "pedestal"],
  ["trigger_rate", "plot_disabled"],
  ["pedestal", "plot_disabled"],
  ["pedestal", "find_thresholds"],
  ["find_thresholds", "thresholds"],
  ["find_thresholds", "self_trigger"],
  ["thresholds", "self_trigger"],
  ["self_trigger", "convert"],
  ["self_trigger", "plot_metrics"],
  ["convert", "clustering"],
];
const NODE_W = 158, NODE_H = 56;

/* ---- per-step form builders (input IDs match gatherParams) -------------- */
const STEP_FORMS = {
  check_power: () => `<div class="hint">No parameters. Reads PACMAN power registers.</div>`,
  make_hydra:  () => `<div class="hint">No parameters. Maps the UART links.</div>`,
  plot_hydra: () => `
    <label class="field">Hydra network file</label>
    <select data-files="hydra" id="sel-hydra-plot"></select>`,
  trigger_rate: () => `
    <label class="field">Hydra network file</label>
    <select data-files="hydra" id="sel-hydra-trig"></select>`,
  pedestal: () => `
    <label class="field">Hydra network file</label>
    <select data-files="hydra" id="sel-hydra-ped"></select>
    <label class="field">Trigger-rate disabled list</label>
    <select data-files="trigger_rate" id="sel-trig-ped"></select>
    <label class="field">Run time (seconds)</label>
    <input type="number" id="ped-runtime" value="60" min="1">`,
  plot_disabled: () => `
    <label class="field">Trigger-rate disabled list</label>
    <select data-files="trigger_rate" id="sel-trig-dis"></select>
    <label class="field">Pedestal disabled (second) list</label>
    <select data-files="pedestal_second" id="sel-pedsec-dis"></select>`,
  find_thresholds: () => `
    <label class="field">Hydra network file</label>
    <select data-files="hydra" id="sel-hydra-thr"></select>
    <label class="field">Pedestal disabled (second) list</label>
    <select data-files="pedestal_second" id="sel-pedsec-thr"></select>
    <label class="field">Pedestal run file</label>
    <select data-files="pedestal_runs" id="sel-pedrun-thr"></select>`,
  self_trigger: () => `
    <label class="field">Hydra network file</label>
    <select data-files="hydra" id="sel-hydra-st"></select>
    <label class="field">ASIC config folder</label>
    <select data-files="asic_folders" id="sel-asic-st"></select>
    <label class="field">Run time per file (seconds)</label>
    <input type="number" id="st-runtime" value="60" min="1">
    <label class="field">HV (recorded in the run database, e.g. "27.5 kV")</label>
    <input type="text" id="st-hv" placeholder="e.g. 27.5 kV">
    <div class="inline" style="margin-top:8px">
      <label><input type="checkbox" id="st-repeat"> Repeat runs (use Stop to halt)</label>
    </div>`,
  convert: () => `
    <div class="inline">
      <label><input type="radio" name="conv-mode" value="all" checked> Process all (watch)</label>
      <label><input type="radio" name="conv-mode" value="single"> Single file</label>
    </div>
    <label class="field">Raw file (single mode)</label>
    <select data-files="raw" id="sel-raw-conv"></select>`,
  plot_metrics: () => `
    <div class="inline">
      <label><input type="radio" name="pm-type" value="s" checked> Self-trigger</label>
      <label><input type="radio" name="pm-type" value="p"> Pedestal</label>
    </div>
    <div class="inline" style="margin-top:6px">
      <label><input type="radio" name="pm-which" value="latest" checked> Latest file</label>
      <label><input type="radio" name="pm-which" value="chosen"> Choose file</label>
    </div>
    <label class="field">File (choose mode)</label>
    <select id="sel-file-pm"></select>`,
  thresholds: () => `
    <label class="field">ASIC config folder</label>
    <select data-files="asic_folders" id="sel-asic-thr2"></select>
    <label class="field">Operation</label>
    <select id="thr-sub">
      <option value="1">1 - Global threshold (all chips)</option>
      <option value="2">2 - Global threshold (one chip)</option>
      <option value="3">3 - Individual channel properties</option>
      <option value="4">4 - One channel across all chips</option>
    </select>
    <div data-thr="1">
      <label class="field">Increment (e.g. -1, 1)</label>
      <input type="number" id="thr1-inc" value="-1">
    </div>
    <div data-thr="2" style="display:none">
      <label class="field">Chip id (11-110)</label>
      <input type="number" id="thr2-chip" value="11" min="11" max="110">
      <label class="field">Increment</label>
      <input type="number" id="thr2-inc" value="-1">
    </div>
    <div data-thr="3" style="display:none">
      <label class="field">chip-channel combos (e.g. 11-0,110-55)</label>
      <input type="text" id="thr3-channels" placeholder="11-0,110-55">
      <label class="field">Action</label>
      <select id="thr3-option">
        <option value="1">1 - Change channel trim threshold</option>
        <option value="2">2 - Disable channels</option>
        <option value="3">3 - Enable channels</option>
      </select>
      <label class="field">Increment (action 1 only)</label>
      <input type="number" id="thr3-inc" value="-1">
    </div>
    <div data-thr="4" style="display:none">
      <label class="field">Channel id (0-63)</label>
      <input type="number" id="thr4-channel" value="0" min="0" max="63">
      <label class="field">Action</label>
      <select id="thr4-option">
        <option value="3">3 - Change threshold (recommended)</option>
        <option value="1">1 - Disable in all chips</option>
        <option value="2">2 - Enable in all chips</option>
      </select>
      <label class="field">Increment (action 3 only)</label>
      <input type="number" id="thr4-inc" value="-1">
      <div class="hint">Actions 1/2 cannot be perfectly undone.</div>
    </div>`,
  clustering: () => `
    <div class="inline">
      <label><input type="radio" name="clu-mode" value="all" checked> All (watch)</label>
      <label><input type="radio" name="clu-mode" value="single"> Single</label>
      <label><input type="radio" name="clu-mode" value="multiple"> Multiple</label>
    </div>
    <label class="field">Converted file (single mode)</label>
    <select data-files="converted" id="sel-conv-clu"></select>
    <label class="field">Converted files (multiple mode)</label>
    <select data-files="converted" id="sel-conv-clu-multi" multiple></select>`,
};

/* ---- param gathering (matches the input IDs above) --------------------- */
function req(id) {
  const e = $(id);
  if (!e || !e.value) throw new Error("Please choose a value for this field");
  return e.value;
}
function gatherParams(action) {
  switch (action) {
    case "check_power":
    case "make_hydra": return {};
    case "plot_hydra": return { hydra_file: req("sel-hydra-plot") };
    case "trigger_rate": return { hydra_file: req("sel-hydra-trig") };
    case "pedestal": return {
      hydra_file: req("sel-hydra-ped"), trigger_rate_file: req("sel-trig-ped"),
      runtime: $("ped-runtime").value || "60" };
    case "plot_disabled": return {
      trigger_rate_file: req("sel-trig-dis"),
      pedestal_disabled_file: req("sel-pedsec-dis") };
    case "find_thresholds": return {
      hydra_file: req("sel-hydra-thr"),
      pedestal_disabled_file: req("sel-pedsec-thr"),
      pedestal_run_file: req("sel-pedrun-thr") };
    case "self_trigger": {
      const hv = $("st-hv") ? $("st-hv").value.trim() : "";
      try { localStorage.setItem("larpix-hv", hv); } catch (e) {}
      return {
        hydra_file: req("sel-hydra-st"), asic_config_folder: req("sel-asic-st"),
        runtime: $("st-runtime").value || "60", repeat: $("st-repeat").checked,
        hv };
    }
    case "convert": {
      const mode = document.querySelector("input[name=conv-mode]:checked").value;
      const p = { mode };
      if (mode === "single") p.raw_file = req("sel-raw-conv");
      return p;
    }
    case "plot_metrics": {
      const file_type = document.querySelector("input[name=pm-type]:checked").value;
      const which = document.querySelector("input[name=pm-which]:checked").value;
      const p = { file_type, which };
      if (which === "chosen") p.raw_file = req("sel-file-pm");
      return p;
    }
    case "thresholds": {
      const sub = $("thr-sub").value;
      const p = { asic_config_folder: req("sel-asic-thr2"), sub_option: sub };
      if (sub === "1") p.inc = $("thr1-inc").value;
      else if (sub === "2") { p.chip_id = $("thr2-chip").value; p.inc = $("thr2-inc").value; }
      else if (sub === "3") {
        p.channels = $("thr3-channels").value.trim();
        if (!p.channels) throw new Error("Enter chip-channel combos");
        p.option = $("thr3-option").value; p.inc = $("thr3-inc").value;
      } else if (sub === "4") {
        p.channel_id = $("thr4-channel").value;
        p.option = $("thr4-option").value; p.inc = $("thr4-inc").value;
      }
      return p;
    }
    case "clustering": {
      const mode = document.querySelector("input[name=clu-mode]:checked").value;
      const p = { mode };
      if (mode === "single") p.converted_file = req("sel-conv-clu");
      else if (mode === "multiple") {
        p.files = Array.from($("sel-conv-clu-multi").selectedOptions).map((o) => o.value);
        if (!p.files.length) throw new Error("Select at least one converted file");
      }
      return p;
    }
    default: return {};
  }
}

initTheme();
