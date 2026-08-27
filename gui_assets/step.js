"use strict";

mountThemeToggle($("theme-slot"));

const STEP = new URLSearchParams(location.search).get("step");
if (!STEP || !STEPS[STEP]) { location.href = "/"; }

const META = STEPS[STEP];
let STATE = null;
let activeJob = null;
let logOffset = 0;
let attachedRunning = false;

document.title = META.num + ". " + META.title;
$("step-title").textContent = META.num + ". " + META.title;
$("form-title").textContent = META.desc;

$("hwnote").className = "hwnote " + (META.hw ? "hw" : "off");
$("hwnote").textContent = META.hw
  ? "Hardware step — drives the LArPix controller and runs exclusively (one hardware step at a time)."
  : "Offline step — pure file/CPU work; can run alongside other offline steps and a live self-trigger run.";

// build the form
$("form").innerHTML = STEP_FORMS[STEP]();
$("desc-status").innerHTML = '<span class="muted">' + SPINNER_SVG + " Loading…</span>";

// restore the last-used HV value (persisted until changed)
if (STEP === "self_trigger") {
  try {
    const hv = localStorage.getItem("larpix-hv");
    if (hv) $("st-hv").value = hv;
  } catch (e) {}
}

// dynamic form wiring
if (STEP === "thresholds") {
  $("thr-sub").onchange = () => {
    const v = $("thr-sub").value;
    document.querySelectorAll("[data-thr]").forEach((d) => {
      d.style.display = d.dataset.thr === v ? "" : "none";
    });
  };
}
if (STEP === "plot_metrics") {
  document.querySelectorAll("input[name=pm-type]").forEach((r) => {
    r.onchange = updatePmFileSelect;
  });
}

function updatePmFileSelect() {
  if (!STATE || !STATE.files) return;
  const type = document.querySelector("input[name=pm-type]:checked").value;
  fillSelect($("sel-file-pm"), type === "s" ? STATE.files.raw : STATE.files.pedestal_runs);
}

function populateSelects() {
  if (!STATE || !STATE.files) return;
  document.querySelectorAll("select[data-files]").forEach((sel) => {
    fillSelect(sel, STATE.files[sel.dataset.files] || []);
  });
  if (STEP === "plot_metrics") updatePmFileSelect();
}

function setChip(status) {
  const chip = $("chip");
  chip.className = "chip " + (status === "idle" ? "" : status);
  chip.textContent = status;
  $("run-btn").disabled = status === "running";
  $("stop-btn").disabled = status !== "running";
}

function appendLog(lines) {
  const log = $("log");
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  lines.forEach((line) => {
    const d = document.createElement("div");
    if (line.startsWith("$ ")) d.className = "cmd";
    else if (line.startsWith("[ERROR]") || line.startsWith("[WARN]")) d.className = "err";
    else if (line.startsWith("[")) d.className = "meta";
    d.textContent = line;
    log.appendChild(d);
  });
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

function renderPlots(plots) {
  const box = $("plots");
  (plots || []).forEach((p) => {
    if (box.querySelector('img[data-p="' + p + '"]')) return;
    const img = document.createElement("img");
    img.dataset.p = p;
    img.src = "/api/plot?path=" + encodeURIComponent(p) + "&t=" + Date.now();
    box.appendChild(img);
  });
}

function attach(jobId) {
  activeJob = jobId;
  logOffset = 0;
  $("log").innerHTML = "";
  $("plots").innerHTML = "";
}

async function pollJob() {
  if (!activeJob) return;
  let snap;
  try { snap = await api("/api/job/" + activeJob + "?offset=" + logOffset); }
  catch (e) { return; }
  if (snap.lines && snap.lines.length) { appendLog(snap.lines); logOffset = snap.total; }
  setChip(snap.status);
  if (snap.extra && snap.extra.plots) renderPlots(snap.extra.plots);
}

async function refreshState() {
  try { STATE = await api("/api/state"); } catch (e) { return; }
  $("desc-status").innerHTML = STATE.active
    ? "Descriptor: <b>" + STATE.active + "</b> &nbsp;<span class='cryo'>" +
      (STATE.cryo_flag ? "CRYO" : "WARM") + "</span>"
    : "<span style='color:var(--err)'>No descriptor set — go back to the flow and set one.</span>";
  populateSelects();

  // reflect last status for this step; attach to a running job if present
  const st = (STATE.step_status && STATE.step_status[STEP]) || "idle";
  if (!activeJob) setChip(st);
  if (!attachedRunning && st === "running") {
    const jobs = (STATE.jobs || []).filter((j) => j.action === STEP && j.status === "running");
    if (jobs.length) { attachedRunning = true; attach(jobs[jobs.length - 1].id); }
  }
}

$("run-btn").onclick = async () => {
  if (!STATE || !STATE.active) { toast("Set a descriptor first (on the flow page)"); return; }
  let params;
  try { params = gatherParams(STEP); } catch (e) { toast(e.message); return; }
  // Manage disabled state by hand rather than withBusy: setChip("running") on
  // success also disables the button, and we must not clobber that afterward.
  const btn = $("run-btn");
  const prevHTML = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = SPINNER_SVG + " Starting…";
  try {
    const r = await postJSON("/api/run/" + STEP, params);
    attach(r.job_id);
    attachedRunning = true;
    setChip("running");
    toast("Started");
  } catch (e) {
    toast(e.message);
    btn.disabled = false;   // job never started -- let the user retry
  } finally {
    btn.innerHTML = prevHTML;
  }
};

$("stop-btn").onclick = async () => {
  if (!activeJob) return;
  try { await postJSON("/api/job/" + activeJob + "/stop", {}); }
  catch (e) { toast(e.message); }
};

refreshState();
setInterval(refreshState, 3000);
setInterval(pollJob, 600);
