#!/usr/bin/env python3
"""
larpix_gui.py -- Browser (HTML) front-end for the LArPix 10x10 run wrapper.

This is a web-based replacement for the text menu in ``run_larpix_scripts.py``.
It drives the EXACT SAME pipeline scripts with the EXACT SAME command-line
invocations.  The only structural change is that every descriptor's output now
lives under a single shared parent directory ``runs/<descriptor>/...`` instead of
each descriptor being its own top-level folder.

This app is self-contained in its own directory (``larpix_charge_monitor/``):
it needs only its sibling ``gui_assets/`` folder, which it locates next to
itself, so the whole directory can be moved/copied as a unit.

IMPORTANT
---------
Launch this from the SAME working directory you would launch
``run_larpix_scripts.py`` from (the ``larpix-10x10-scripts`` directory on the DAQ
machine), so that all the preserved relative paths resolve, e.g.::

    check_power.py, map_uart_links_qc.py, pedestal_qc.py, threshold_qc.py,
    start_run_log_raw.py, plot_hydra_network_v2a.py, plot_xy_disabled_channel.py,
    increment_global.py, layout-2.4.0.yaml, larpix-control/scripts/...,
    ./larpix-monitor/run_monitor.py, ./dg_ctrl_pps, larpix_script_settings.txt,
    dg_heartbeat.txt

The command-line invocations and the absolute clustering path are preserved
verbatim -- only where the GUI itself lives has changed, not what it runs.

Usage
-----
    # from the pipeline-scripts directory:
    python3 /path/to/larpix_charge_monitor/larpix_gui.py \
        [--host 127.0.0.1] [--port 8000] [--no-browser]

Stdlib only (http.server) -- no Flask required.
"""

import os
import re
import csv
import math
import sys
import glob
import json
import time
import zlib
import sqlite3
import shutil
import argparse
import threading
import subprocess
import webbrowser
from pathlib import Path
from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

try:
    import numpy as np  # noqa: E402
except Exception:  # pragma: no cover
    np = None

try:
    import h5py  # noqa: E402
except Exception:  # pragma: no cover - environment without h5py
    h5py = None


# ---------------------------------------------------------------------------
# Small filesystem helpers (kept identical to run_larpix_scripts.py so the two
# tools behave the same, but inlined here so this app is self-contained and can
# live in its own directory).
# ---------------------------------------------------------------------------
def settings(setting=None, value=None, settings_file="larpix_script_settings.txt",
             create=False, read=False):
    """Load, read, create, or update the JSON settings file."""
    def load(path):
        with open(path, "r") as fh:
            return json.load(fh)

    def dump(path, data):
        with open(path, "w") as fh:
            json.dump(data, fh, indent=4)

    if read:
        return load(settings_file)
    if create:
        data = {"descriptor": "", "cryo_flag": -1}
        dump(settings_file, data)
        return load(settings_file)
    if value is None:
        raise Exception("Settings value should be set to something other than None")
    data = load(settings_file)
    data[setting] = value
    dump(settings_file, data)


def get_latest_file(pattern, directory="."):
    """Latest file matching ``pattern`` in ``directory`` + a timestamped new name."""
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise ValueError("Provided path is not a directory: %s" % directory)
    files = [f for f in dir_path.glob(pattern) if f.is_file()]
    if not files:
        return None, None
    latest_file = max(files, key=lambda f: f.stat().st_ctime)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_name = "%s_%s%s" % (latest_file.stem, timestamp, latest_file.suffix)
    return latest_file, new_name


def get_files_by_creation(directory, ending):
    """Files in ``directory`` ending with ``ending``, sorted oldest-first."""
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise ValueError("%s is not a valid directory" % directory)
    files_sorted = sorted(
        (f for f in dir_path.iterdir() if f.is_file() and f.name.endswith(ending)),
        key=lambda f: f.stat().st_ctime)
    return [str(f.resolve()) for f in files_sorted]


# ---------------------------------------------------------------------------
# Constants -- mirrored verbatim from run_larpix_scripts.py
# ---------------------------------------------------------------------------
RUNS_PARENT = "runs"                       # NEW: single shared parent dir
SETTINGS_FILE = "larpix_script_settings.txt"

DG645_IP = "129.82.140.53"
DG_COMMAND = ["./dg_ctrl_pps", DG645_IP, "0"]
DG_HEARTBEAT_FILE = "dg_heartbeat.txt"

IO_GROUP = 1
PACMAN_TILE = 1
TILE_ID = 1
GEOMETRY_YAML = "layout-2.4.0.yaml"

CONVERTER_DIR = "larpix-control/scripts"
# CLUSTERING_DIR (charge_clustering.py location) is set below, once the repo
# checkout has been located -- it used to be a hardcoded absolute home path.

# LArPix v2a channels with no bonded pixel (49 of 64 channels are routed)
NONROUTED_V2A_CHANNELS = [6, 7, 8, 9, 22, 23, 24, 25, 38, 39, 40, 54, 55, 56, 57]

# Drift E-field [V/cm] = (power-supply HV value) * HV_TO_EFIELD. HV is taken as the
# number the user types into the HV field (whatever units they run the supply in).
HV_TO_EFIELD = 29.038
TICK_US = 0.1                  # one LArPix timestamp tick = 0.1 us
TIMESTAMP_ROLLOVER = 2 ** 32   # the LArPix timestamp counter wraps here


def _find_repo_root():
    """Walk up from this file looking for a checkout that holds SingleCube/repos.

    This app (larpix_gui.py) is launched from the pipeline-scripts directory
    (e.g. SingleCube/repos/larpix-10x10-scripts/), not from wherever it lives on
    disk, so the local-checkout fallback paths below must NOT be relative to the
    CWD -- they're anchored to this file's location instead (same walk-up
    setup_env.sh uses to find the repos to editable-install).
    """
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "SingleCube", "repos")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


_REPO_ROOT = _find_repo_root()

# Local fallbacks for GUI-internal reads only (never used in command argvs).
# Anchored to the repo checkout (see _find_repo_root), not the CWD, since the
# GUI is launched from the pipeline-scripts dir, not from next to SingleCube/.
if _REPO_ROOT:
    REPO_SCRIPTS_DIR = os.path.join(_REPO_ROOT, "SingleCube", "repos",
                                    "larpix-10x10-scripts")
    REPO_LARPIX_CONTROL = os.path.join(_REPO_ROOT, "SingleCube", "repos",
                                       "larpix-control")
    _REPO_CHARGE_RECO = os.path.join(_REPO_ROOT, "SingleCube", "repos",
                                     "ndlar_39Ar_reco", "charge_reco")
    REPO_LIFETIME_DIR = os.path.join(_REPO_CHARGE_RECO, "CATS_analysis",
                                     "lifetime")
    # charge_clustering.py lives at the top of charge_reco/ (was a hardcoded
    # /home/<user>/... path; derive it from the located checkout instead).
    CLUSTERING_DIR = _REPO_CHARGE_RECO
else:
    REPO_SCRIPTS_DIR = "SingleCube/repos/larpix-10x10-scripts"
    REPO_LARPIX_CONTROL = "SingleCube/repos/larpix-control"
    REPO_LIFETIME_DIR = ("SingleCube/repos/ndlar_39Ar_reco/charge_reco/"
                         "CATS_analysis/lifetime")
    CLUSTERING_DIR = "SingleCube/repos/ndlar_39Ar_reco/charge_reco"

# This app's own directory and the self-contained bundles inside it. Everything
# the offline features need ships here so a bare download runs with no external
# repo checkout: vendor/ holds the larpix / larpixgeometry packages, analysis/
# the lifetime tools, data/ the channelmap + geometry yaml. The repo/pip paths
# above are tried first (unchanged behavior on the DAQ machine); these bundled
# copies are the last-resort fallback used when the repos aren't present.
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(APP_DIR, "gui_assets")
VENDOR_DIR = os.path.join(APP_DIR, "vendor")        # bundled larpix / larpixgeometry
ANALYSIS_DIR = os.path.join(APP_DIR, "analysis")    # bundled lifetime tools
DATA_DIR = os.path.join(APP_DIR, "data")            # bundled channelmap.dat, layout yaml

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


# ---------------------------------------------------------------------------
# Run context (descriptor + directory tree)
# ---------------------------------------------------------------------------
class RunContext:
    """Holds the active descriptor, cryo flag and the runs/<descriptor>/ tree."""

    # subdir-key -> relative subpath (names identical to run_larpix_scripts.py)
    SUBDIRS = {
        "converted_data": "converted_data",
        "clustered_data": "clustered_data",
        "hydra_files": "hydra_files",
        "trigger_rate_disabled": "trigger_rate_disabled_lists",
        "pedestal_donotenable": "pedestal_donotenable_lists",
        "pedestal_first": "pedestal_disabled_first_lists",
        "pedestal_second": "pedestal_disabled_second_lists",
        "pedestal_runs": "pedestal_runs",
        "disabled_channels": "disabled_channels",
        "asic_configs": "asic_configs",
        "raw_self_trigger": "raw_self_trigger_data",
        "converted_self_trigger": "converted_self_trigger_data",
        # GUI-only bookkeeping dirs (do not affect any preserved path)
        "logs": "logs",
        "metrics": "metrics",
    }
    PLOT_SUBDIRS = ["hydra_files", "trigger_rate_disabled", "pedestal_runs"]

    def __init__(self):
        self.descriptor = None
        self.cryo_flag = False
        self.root = None
        self.dirs = {}

    def is_ready(self):
        return self.descriptor is not None

    def setup(self, descriptor, cryo_flag):
        """Create runs/<descriptor>/<subtree> and persist settings."""
        descriptor = descriptor.strip()
        if not descriptor:
            raise ValueError("Descriptor must not be empty")

        os.makedirs(RUNS_PARENT, exist_ok=True)
        root = os.path.join(RUNS_PARENT, descriptor)
        os.makedirs(root, exist_ok=True)

        dirs = {}
        for key, sub in self.SUBDIRS.items():
            d = os.path.join(root, sub)
            os.makedirs(d, exist_ok=True)
            dirs[key] = d
        for key in self.PLOT_SUBDIRS:
            os.makedirs(os.path.join(dirs[key], "plots"), exist_ok=True)

        # persist to the SAME settings file the text script uses
        if not os.path.exists(SETTINGS_FILE):
            settings(create=True, settings_file=SETTINGS_FILE)
        settings(setting="descriptor", value=descriptor, settings_file=SETTINGS_FILE)
        settings(setting="cryo_flag", value=1 if cryo_flag else 0,
                     settings_file=SETTINGS_FILE)

        self.descriptor = descriptor
        self.cryo_flag = bool(cryo_flag)
        self.root = root
        self.dirs = dirs

    def d(self, key):
        return self.dirs[key]


CTX = RunContext()


# ---------------------------------------------------------------------------
# Job manager -- background threads with streamed subprocess output
# ---------------------------------------------------------------------------
class Job:
    def __init__(self, job_id, name, action=None):
        self.id = job_id
        self.name = name
        self.action = action
        self.status = "running"          # running | done | error | stopped
        self.had_error = False           # any subprocess exited non-zero
        self.lines = []
        self.lock = threading.Lock()
        self.proc = None
        self.stop_event = threading.Event()
        self.start_time = time.time()
        self.end_time = None
        self.exit_code = None
        self.extra = {}                   # e.g. {"plots": [...], "files": [...]}

    def log(self, line):
        with self.lock:
            self.lines.append(str(line))

    def add_plot(self, path):
        if not path:
            return
        with self.lock:
            self.extra.setdefault("plots", [])
            rel = os.path.relpath(path)
            if rel not in self.extra["plots"]:
                self.extra["plots"].append(rel)

    def snapshot(self, offset=0):
        with self.lock:
            return {
                "id": self.id,
                "name": self.name,
                "action": self.action,
                "status": self.status,
                "lines": self.lines[offset:],
                "total": len(self.lines),
                "exit_code": self.exit_code,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "extra": dict(self.extra),
            }


class JobManager:
    def __init__(self):
        self.jobs = {}
        self.counter = 0
        self.lock = threading.Lock()

    def start(self, name, target, action=None):
        with self.lock:
            self.counter += 1
            job_id = "job%d" % self.counter
            job = Job(job_id, name, action=action)
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job, target), daemon=True).start()
        return job

    def _run(self, job, target):
        try:
            target(job)
            if job.status == "running":
                if job.stop_event.is_set():
                    job.status = "stopped"
                elif job.had_error:
                    job.status = "error"
                else:
                    job.status = "done"
        except Exception as exc:  # pragma: no cover - defensive
            job.log("[ERROR] %s" % exc)
            job.status = "error"
        finally:
            job.end_time = time.time()

    def stop(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.stop_event.set()
        if job.proc is not None and job.proc.poll() is None:
            try:
                job.proc.terminate()
            except Exception:
                pass
        return True

    def list(self):
        with self.lock:
            jobs = list(self.jobs.values())
        return [{"id": j.id, "name": j.name, "action": j.action,
                 "status": j.status, "start_time": j.start_time,
                 "end_time": j.end_time} for j in jobs]

    def latest_status_by_action(self):
        """Map action -> status of its most recent job (for step colouring)."""
        with self.lock:
            jobs = list(self.jobs.values())
        out = {}
        for j in jobs:               # dict preserves insertion order (ascending id)
            if j.action:
                out[j.action] = j.status
        return out

    def running_hardware(self):
        """Currently-running hardware-exclusive jobs."""
        with self.lock:
            return [j for j in self.jobs.values()
                    if j.status == "running" and j.action in HARDWARE_ACTIONS]


JOBS = JobManager()


def run_streamed(job, command, cwd=None):
    """Run ``command`` streaming its combined output into ``job``.

    Returns ``(exit_code, wall_seconds)``.  The argv is logged verbatim so it is
    obvious that the preserved commands are unchanged.
    """
    job.log("$ " + " ".join(command))
    start = time.time()
    try:
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        job.log("[ERROR] command not found: %s" % exc)
        return 1, time.time() - start
    job.proc = proc
    try:
        for line in iter(proc.stdout.readline, ""):
            job.log(line.rstrip("\n"))
            if job.stop_event.is_set():
                break
    finally:
        if job.stop_event.is_set() and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        proc.wait()
        try:
            proc.stdout.close()
        except Exception:
            pass
    job.proc = None
    wall = time.time() - start
    job.exit_code = proc.returncode
    if proc.returncode != 0 and not job.stop_event.is_set():
        job.had_error = True
    job.log("[exit code %s, %.1fs]" % (proc.returncode, wall))
    return proc.returncode, wall


# ---------------------------------------------------------------------------
# Helpers ported from the nested helpers of run_larpix_scripts.py
# ---------------------------------------------------------------------------
def move_file(job, pattern, dest_dir, since_time, add_ts=True):
    """Latest file matching ``pattern`` in CWD -> ``dest_dir`` (mirrors original)."""
    latest_file, new_file = get_latest_file(pattern)
    if latest_file is None or new_file is None:
        job.log("Looks like no files were made at the previous step...")
        return None
    creation_time = os.path.getctime(latest_file)
    if creation_time > since_time:
        if add_ts:
            dest = os.path.join(dest_dir, new_file)
        else:
            dest = os.path.join(dest_dir, os.path.basename(latest_file))
        shutil.move(latest_file, dest)
        job.log("Moved output file to: %s" % dest)
        return dest
    job.log("Looks like no files were made at the previous step...")
    return None


def find_list_of_files(directory, since_time):
    """All ``*config*.json`` in ``directory`` newer than ``since_time``."""
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise ValueError("%s is not a valid directory" % directory)
    matched = glob.glob(str(dir_path / "*config*.json"))
    return [str(Path(f).resolve()) for f in matched
            if os.path.getctime(f) > since_time]


def get_dirs_by_creation(directory):
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise ValueError("%s is not a valid directory" % directory)
    dirs_sorted = sorted((d for d in dir_path.iterdir() if d.is_dir()),
                         key=lambda d: d.stat().st_ctime)
    return [str(d.resolve()) for d in dirs_sorted]


def find_asic_configs_file(folder, chip_id):
    pattern = re.compile(r"tile-id-10x10-config-(\d+)-(\d+)-(\d+)-.*\.json$")
    found = None
    for filename in os.listdir(folder):
        match = pattern.match(filename)
        if match and int(match.group(3)) == int(chip_id):
            found = filename
    return os.path.join(folder, found) if found else None


# ---------------------------------------------------------------------------
# Metrics (event rate, counts/sec)
# ---------------------------------------------------------------------------
METRICS_LOCK = threading.Lock()

METRIC_SCHEMA = {
    "self_trigger": (["timestamp", "file", "messages", "runtime_s", "messages_per_s"],
                     "raw_self_trigger"),
    "convert":      (["timestamp", "file", "packets", "wall_s", "packets_per_s"],
                     "converted_self_trigger"),
    "clustering":   (["timestamp", "file", "clusters", "hits", "wall_s",
                      "clusters_per_s", "hits_per_s"],
                     "clustered_data"),
}


def count_h5(path, stage):
    """Return the relevant counts dict for ``path`` at ``stage`` (or None)."""
    if h5py is None:
        return None
    try:
        with h5py.File(path, "r") as f:
            if stage == "self_trigger":
                return {"messages": int(len(f["msgs"]))}
            if stage == "convert":
                return {"packets": int(len(f["packets"]))}
            if stage == "clustering":
                clusters = int(len(f["clusters"])) if "clusters" in f else 0
                hits = int(len(f["hits"])) if "hits" in f else 0
                return {"clusters": clusters, "hits": hits}
    except Exception:
        return None
    return None


def _metrics_csv(stage):
    return os.path.join(CTX.d("metrics"), "%s.csv" % stage)


def record_metric(stage, row):
    """Append one row (dict matching METRIC_SCHEMA) to the stage CSV."""
    if not CTX.is_ready():
        return
    cols = METRIC_SCHEMA[stage][0]
    path = _metrics_csv(stage)
    with METRICS_LOCK:
        new = not os.path.exists(path)
        with open(path, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols)
            if new:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in cols})


def record_self_trigger(file_path, runtime_s):
    counts = count_h5(file_path, "self_trigger") or {}
    messages = counts.get("messages", "")
    rate = ""
    if messages != "" and runtime_s:
        rate = round(messages / float(runtime_s), 3)
    record_metric("self_trigger", {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "file": os.path.basename(file_path),
        "messages": messages, "runtime_s": round(float(runtime_s), 3),
        "messages_per_s": rate,
    })


def record_convert(file_path, wall_s):
    counts = count_h5(file_path, "convert") or {}
    packets = counts.get("packets", "")
    rate = round(packets / wall_s, 3) if (packets != "" and wall_s > 0) else ""
    record_metric("convert", {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "file": os.path.basename(file_path),
        "packets": packets, "wall_s": round(wall_s, 3), "packets_per_s": rate,
    })
    try:
        RUNDB.link_converted(os.path.basename(file_path))
    except Exception:
        pass


def record_clustering(file_path, wall_s):
    counts = count_h5(file_path, "clustering") or {}
    clusters = counts.get("clusters", "")
    hits = counts.get("hits", "")
    crate = round(clusters / wall_s, 3) if (clusters != "" and wall_s > 0) else ""
    hrate = round(hits / wall_s, 3) if (hits != "" and wall_s > 0) else ""
    record_metric("clustering", {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "file": os.path.basename(file_path),
        "clusters": clusters, "hits": hits, "wall_s": round(wall_s, 3),
        "clusters_per_s": crate, "hits_per_s": hrate,
    })
    try:
        RUNDB.link_clustered(os.path.basename(file_path))
    except Exception:
        pass


def metrics_payload(stage):
    """Return dashboard JSON for ``stage``: logged rows + backfilled files."""
    cols, dir_key = METRIC_SCHEMA[stage]
    rows = []
    seen = set()
    if CTX.is_ready():
        path = _metrics_csv(stage)
        if os.path.exists(path):
            with METRICS_LOCK:
                with open(path, newline="") as fh:
                    for r in csv.DictReader(fh):
                        rows.append(r)
                        seen.add(r.get("file"))
        # Backfill files present on disk but not yet logged (counts only).
        out_dir = CTX.d(dir_key)
        for fp in get_files_by_creation(out_dir, ".h5"):
            base = os.path.basename(fp)
            if base in seen:
                continue
            counts = count_h5(fp, stage) or {}
            row = {"timestamp": datetime.fromtimestamp(
                       os.path.getctime(fp)).isoformat(timespec="seconds"),
                   "file": base}
            row.update({k: v for k, v in counts.items()})
            rows.append(row)
            seen.add(base)

    # numeric coercion + summary
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    rate_key = {"self_trigger": "messages_per_s",
                "convert": "packets_per_s",
                "clustering": "clusters_per_s"}[stage]
    count_key = {"self_trigger": "messages",
                 "convert": "packets",
                 "clustering": "clusters"}[stage]
    rates = [num(r.get(rate_key)) for r in rows]
    rates = [x for x in rates if x is not None]
    counts = [num(r.get(count_key)) for r in rows]
    counts = [x for x in counts if x is not None]
    summary = {
        "n_files": len(rows),
        "latest_rate": rates[-1] if rates else None,
        "mean_rate": round(sum(rates) / len(rates), 3) if rates else None,
        "max_rate": max(rates) if rates else None,
        "total_count": int(sum(counts)) if counts else 0,
        "rate_key": rate_key,
        "count_key": count_key,
    }
    return {"stage": stage, "descriptor": CTX.descriptor,
            "columns": cols, "rows": rows, "summary": summary}


# ---------------------------------------------------------------------------
# Pixel geometry (chip/channel -> x,y in mm from the layout yaml)
# ---------------------------------------------------------------------------
_GEOMETRY_CACHE = None
_EVD_PIXEL_LOOKUP_CACHE = None   # (key_arr, x_arr, y_arr) sorted by chip*64+ch, see _evd_pixel_lookup


def load_pixel_geometry():
    """Load layout yaml -> {pixels:[{k,chip,ch,x,y}...], width, height, x0, y0}.

    Uses GEOMETRY_YAML from the CWD (DAQ machine); falls back to the repo copy
    for local testing. GUI-internal read only -- command argvs are untouched.
    """
    global _GEOMETRY_CACHE
    if _GEOMETRY_CACHE is not None:
        return _GEOMETRY_CACHE
    import yaml
    # CWD (DAQ machine) -> repo copy -> the copy bundled in this app (data/).
    for path in (GEOMETRY_YAML,
                 os.path.join(REPO_SCRIPTS_DIR, GEOMETRY_YAML),
                 os.path.join(DATA_DIR, GEOMETRY_YAML)):
        if os.path.exists(path):
            break
    else:
        raise FileNotFoundError("Geometry yaml not found: %s" % GEOMETRY_YAML)
    with open(path) as fh:
        geo = yaml.safe_load(fh)
    chip_pix = dict((entry[0], entry[1]) for entry in geo["chips"])
    pixels = []
    for chip, mapping in chip_pix.items():
        for ch in range(64):
            if ch in NONROUTED_V2A_CHANNELS:
                continue
            if ch >= len(mapping) or mapping[ch] is None:
                continue
            px = geo["pixels"][mapping[ch]]
            pixels.append({"k": "%d-%d" % (chip, ch), "chip": int(chip),
                           "ch": ch, "x": float(px[1]), "y": float(px[2])})
    _GEOMETRY_CACHE = {
        "pixels": pixels,
        "width": float(geo.get("width", 0)),
        "height": float(geo.get("height", 0)),
        "x0": float(geo.get("x", 0)),
        "y0": float(geo.get("y", 0)),
    }
    return _GEOMETRY_CACHE


# ---------------------------------------------------------------------------
# Hit density (live tail of the active raw file, or newest converted file)
# ---------------------------------------------------------------------------
_LARPIX_FMT = "unset"          # "unset" | None | (rhdf5, pacman_msg_fmt)
LIVE_LOCK = threading.Lock()
LIVE_ACC = {}                  # raw path -> {"counts": {...}, "last": int}
LIVE_CHUNK = 20000             # max messages parsed per poll
_FILE_DENSITY_CACHE = {}       # (path, kind) -> (mtime, counts, total)
_FILE_DENSITY_MAX = 200        # in-memory entries kept (oldest evicted first)

# Counting a file means reading every packet row off disk, which on an external
# drive runs at tens of MB/s -- a folder of large runs costs hours, and the
# result (a few thousand per-pixel counts) is tiny and fully determined by the
# file. So it is also cached on disk, keyed by path+kind+mtime+size, and a
# re-load of the same folder becomes a few hundred small SQLite reads instead.
# Same idea as lifetime_vs_tracks.py's TrackCache, which caches AC tracks.
DENSITY_CACHE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "density_cache.sqlite")
DENSITY_CACHE_LOCK = threading.Lock()
_DENSITY_CACHE_OK = True       # flipped off if the DB can't be opened/created


def _density_cache_conn():
    conn = sqlite3.connect(DENSITY_CACHE_DB, timeout=5)
    conn.execute("CREATE TABLE IF NOT EXISTS density("
                 "path TEXT, kind TEXT, mtime REAL, size INTEGER, "
                 "total INTEGER, counts BLOB, added TEXT, "
                 "PRIMARY KEY(path, kind))")
    return conn


def _density_cache_get(path, kind, mtime, size):
    """Cached (counts, total) for this exact file version, or None."""
    global _DENSITY_CACHE_OK
    if not _DENSITY_CACHE_OK:
        return None
    try:
        with DENSITY_CACHE_LOCK:
            conn = _density_cache_conn()
            try:
                row = conn.execute(
                    "SELECT mtime, size, total, counts FROM density "
                    "WHERE path=? AND kind=?", (path, kind)).fetchone()
            finally:
                conn.close()
        if not row or row[0] != mtime or row[1] != size:
            return None            # file changed (or grew) -> recount
        counts = json.loads(zlib.decompress(row[3]).decode())
        return counts, int(row[2])
    except Exception:
        return None                # a bad cache must never break a load


def _density_cache_put(path, kind, mtime, size, counts, total):
    global _DENSITY_CACHE_OK
    if not _DENSITY_CACHE_OK:
        return
    try:
        blob = zlib.compress(json.dumps(counts).encode(), 1)
        with DENSITY_CACHE_LOCK:
            conn = _density_cache_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO density"
                    "(path, kind, mtime, size, total, counts, added) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (path, kind, mtime, size, int(total), blob,
                     datetime.now().isoformat(timespec="seconds")))
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:       # read-only dir, disk full, ...
        _DENSITY_CACHE_OK = False
        print("Density cache disabled (%s): counts will be recomputed each "
              "time." % exc)


# Per-file charge histograms for the trigger-threshold map, cached in the same
# SQLite file for the same reason: the read is the expensive part, the result is
# a few hundred kB, and it is fully determined by (file, cut parameters).
def _thresh_cache_conn():
    conn = sqlite3.connect(DENSITY_CACHE_DB, timeout=5)
    conn.execute("CREATE TABLE IF NOT EXISTS thresh_hist("
                 "path TEXT, params TEXT, mtime REAL, size INTEGER, "
                 "n_binned INTEGER, n_unmatched INTEGER, warning TEXT, "
                 "hist BLOB, counts BLOB, added TEXT, "
                 "PRIMARY KEY(path, params))")
    return conn


def _thresh_cache_get(path, params, mtime, size):
    """Cached (hist, counts, n_binned, n_unmatched, warning), or None."""
    if not _DENSITY_CACHE_OK:
        return None
    try:
        with DENSITY_CACHE_LOCK:
            conn = _thresh_cache_conn()
            try:
                row = conn.execute(
                    "SELECT mtime, size, n_binned, n_unmatched, warning, hist, "
                    "counts FROM thresh_hist WHERE path=? AND params=?",
                    (path, params)).fetchone()
            finally:
                conn.close()
        if not row or row[0] != mtime or row[1] != size:
            return None
        hist = np.frombuffer(zlib.decompress(row[5]), dtype="i8")
        counts = np.frombuffer(zlib.decompress(row[6]), dtype="i8")
        return (hist.reshape(counts.size, -1), counts,
                int(row[2]), int(row[3]), row[4] or None)
    except Exception:
        return None                # a bad cache must never break a load


def _thresh_cache_put(path, params, mtime, size, hist, counts,
                      n_binned, n_unmatched, warning):
    global _DENSITY_CACHE_OK
    if not _DENSITY_CACHE_OK:
        return
    try:
        hb = zlib.compress(np.ascontiguousarray(hist, dtype="i8").tobytes(), 1)
        cb = zlib.compress(np.ascontiguousarray(counts, dtype="i8").tobytes(), 1)
        with DENSITY_CACHE_LOCK:
            conn = _thresh_cache_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO thresh_hist"
                    "(path, params, mtime, size, n_binned, n_unmatched, "
                    "warning, hist, counts, added) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (path, params, mtime, size, int(n_binned), int(n_unmatched),
                     warning, hb, cb,
                     datetime.now().isoformat(timespec="seconds")))
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:
        _DENSITY_CACHE_OK = False
        print("Density cache disabled (%s): counts will be recomputed each "
              "time." % exc)


_THRESHOLD_CORE = "unset"


def _threshold_core():
    """Lazy import of the bundled threshold_core (analysis/)."""
    global _THRESHOLD_CORE
    if _THRESHOLD_CORE != "unset":
        return _THRESHOLD_CORE
    try:
        if ANALYSIS_DIR not in sys.path:
            sys.path.insert(0, ANALYSIS_DIR)
        import threshold_core
        _THRESHOLD_CORE = threshold_core
    except Exception as exc:
        print("threshold_core unavailable (%s): the trigger-threshold map and "
              "clustered hit counts are disabled." % exc)
        _THRESHOLD_CORE = None
    return _THRESHOLD_CORE


def _larpix_fmt():
    """Best-effort import of the larpix raw/pacman format modules."""
    global _LARPIX_FMT
    if _LARPIX_FMT != "unset":
        return _LARPIX_FMT
    try:
        import larpix.format.rawhdf5format as rhdf5
        import larpix.format.pacman_msg_format as pmf
        _LARPIX_FMT = (rhdf5, pmf)
    except Exception:
        # Not pip-installed: add the repo checkout if present, else the vendored
        # copy that ships in this app, then retry.
        for cand in (REPO_LARPIX_CONTROL, VENDOR_DIR):
            if os.path.isdir(cand):
                p = os.path.abspath(cand)
                if p not in sys.path:
                    sys.path.insert(0, p)
        try:
            import larpix.format.rawhdf5format as rhdf5
            import larpix.format.pacman_msg_format as pmf
            _LARPIX_FMT = (rhdf5, pmf)
        except Exception:
            _LARPIX_FMT = None
    return _LARPIX_FMT


def live_density():
    """Incrementally parse the growing raw file in CWD -> per-pixel counts."""
    fmt = _larpix_fmt()
    if fmt is None:
        return None
    rhdf5, pmf = fmt
    latest, _ = get_latest_file("*raw*.h5", directory=".")
    if latest is None:
        return None
    path = str(latest)
    try:
        n = rhdf5.len_rawfile(path, attempts=0)
    except Exception:
        return None
    with LIVE_LOCK:
        acc = LIVE_ACC.setdefault(path, {"counts": {}, "last": 0})
        start = acc["last"]
        if n > start:
            end = min(n, start + LIVE_CHUNK)
            try:
                rd = rhdf5.from_rawfile(path, start=start, end=end)
                for iog, msg in zip(rd["msg_headers"]["io_groups"], rd["msgs"]):
                    for pkt in pmf.parse(msg, io_group=iog):
                        if getattr(pkt, "packet_type", None) == 0:
                            key = "%d-%d" % (pkt.chip_id, pkt.channel_id)
                            acc["counts"][key] = acc["counts"].get(key, 0) + 1
                acc["last"] = end
            except Exception:
                pass  # file may be mid-write; retry next poll
        counts = dict(acc["counts"])
        parsed = acc["last"]
        # prune accumulators for files other than the newest few
        if len(LIVE_ACC) > 3:
            for old in sorted(LIVE_ACC)[:-3]:
                if old != path:
                    LIVE_ACC.pop(old, None)
    return {"source": "live", "file": os.path.basename(path),
            "counts": counts, "total": sum(counts.values()),
            "parsed_msgs": parsed, "total_msgs": n}


class DensityError(Exception):
    """A readable reason a file could not be turned into a hit-density map."""


def open_h5_read(path):
    """Open an HDF5 file read-only, disabling file locking when supported.

    LArPix data often lives on external / exFAT / network volumes (e.g. a USB
    stick) where HDF5's file locking fails with 'unable to lock file' even for a
    read; passing ``locking=False`` (h5py >= 3.5) avoids that. Older h5py falls
    back to a plain read (the HDF5_USE_FILE_LOCKING=FALSE env set at import still
    helps there).
    """
    try:
        return h5py.File(path, "r", locking=False)
    except TypeError:
        return h5py.File(path, "r")


def _detect_h5_kind(path):
    """'clustered' (hits+clusters), 'converted' (packets), 'raw' (msgs), or None."""
    if h5py is None:
        return None
    try:
        with open_h5_read(path) as f:
            if "hits" in f and "clusters" in f:
                return "clustered"
            if "packets" in f:
                return "converted"
            if "msgs" in f:
                return "raw"
    except Exception:
        return None
    return None


def _count_converted_file(path):
    try:
        with open_h5_read(path) as f:
            if "packets" not in f:
                raise DensityError("not a converted file: no 'packets' table "
                                   "(top-level keys: %s)"
                                   % (", ".join(list(f.keys())[:8]) or "none"))
            p = f["packets"]
            mask = p["packet_type"][:] == 0
            chips = p["chip_id"][:][mask].astype("i4")
            chans = p["channel_id"][:][mask].astype("i4")
    except DensityError:
        raise
    except Exception as exc:
        raise DensityError("HDF5 read failed (%s)" % exc)
    keys = chips * 64 + chans
    u, c = np.unique(keys, return_counts=True)
    counts = {"%d-%d" % (k // 64, k % 64): int(v) for k, v in zip(u, c)}
    return counts, int(mask.sum())


def _count_clustered_file(path):
    """Per-pixel hit counts from a clustered file's own ``hits`` table.

    These are reconstruction hits (post-clustering), not raw packets, so the
    totals are lower than the converted file they came from -- the kind badge on
    the map says which is being shown.
    """
    core = _threshold_core()
    if core is None:
        raise DensityError("threshold_core is not importable; cannot read "
                           "clustered files")
    try:
        with open_h5_read(path) as f:
            if "hits" not in f:
                raise DensityError("not a clustered file: no 'hits' table "
                                   "(top-level keys: %s)"
                                   % (", ".join(list(f.keys())[:8]) or "none"))
            if "unique_id" not in (f["hits"].dtype.names or ()):
                raise DensityError("clustered 'hits' lacks a 'unique_id' column")
            uid = f["hits"]["unique_id"][:]
    except DensityError:
        raise
    except Exception as exc:
        raise DensityError("HDF5 read failed (%s)" % exc)
    keys = core.uid_to_combined(uid)
    u, c = np.unique(keys, return_counts=True)
    counts = {"%d-%d" % (k // 64, k % 64): int(v) for k, v in zip(u, c)}
    return counts, int(uid.size)


def _count_raw_file(path):
    """Fully parse a completed raw PACMAN file -> per-pixel counts."""
    fmt = _larpix_fmt()
    if fmt is None:
        raise DensityError("the larpix raw reader isn't importable, so raw files "
                           "can't be parsed here (use a converted file instead)")
    rhdf5, pmf = fmt
    try:
        n = rhdf5.len_rawfile(path, attempts=0)
        counts = {}
        start = 0
        while start < n:
            end = min(start + LIVE_CHUNK, n)
            rd = rhdf5.from_rawfile(path, start=start, end=end)
            for iog, msg in zip(rd["msg_headers"]["io_groups"], rd["msgs"]):
                for pkt in pmf.parse(msg, io_group=iog):
                    if getattr(pkt, "packet_type", None) == 0:
                        key = "%d-%d" % (pkt.chip_id, pkt.channel_id)
                        counts[key] = counts.get(key, 0) + 1
            start = end
    except Exception as exc:
        raise DensityError("raw parse failed (%s)" % exc)
    return counts, int(sum(counts.values()))


def count_file_density(path, kind="auto"):
    """Per-pixel counts for one .h5 file (mtime-cached).

    Returns ``(counts, total, resolved_kind)``. Raises ``DensityError`` with a
    human-readable reason on failure.
    """
    if h5py is None:
        raise DensityError("h5py is not available in this environment")
    if kind in (None, "auto"):
        kind = _detect_h5_kind(path)
        if kind is None:
            # Open once more just to surface the real reason to the user.
            try:
                with open_h5_read(path) as f:
                    raise DensityError("unrecognized HDF5: no 'packets', 'msgs' "
                                       "or 'hits'+'clusters' dataset (keys: %s)"
                                       % (", ".join(list(f.keys())[:8]) or "none"))
            except DensityError:
                raise
            except Exception as exc:
                raise DensityError("cannot open as HDF5 (%s)" % exc)
    try:
        st = os.stat(path)
        mtime, size = st.st_mtime, st.st_size
    except OSError as exc:
        raise DensityError("cannot stat file (%s)" % exc)
    ck = (os.path.abspath(path), kind)
    cached = _FILE_DENSITY_CACHE.get(ck)
    if cached and cached[0] == mtime:
        return cached[1], cached[2], kind
    hit = _density_cache_get(ck[0], kind, mtime, size)
    if hit is None:
        counter = {"raw": _count_raw_file,
                   "clustered": _count_clustered_file}.get(
                       kind, _count_converted_file)
        counts, total = counter(path)
        _density_cache_put(ck[0], kind, mtime, size, counts, total)
    else:
        counts, total = hit
    # Evict oldest rather than clearing: a folder with more files than the cap
    # used to wipe its own cache partway through and re-read from the start.
    while len(_FILE_DENSITY_CACHE) >= _FILE_DENSITY_MAX:
        _FILE_DENSITY_CACHE.pop(next(iter(_FILE_DENSITY_CACHE)), None)
    _FILE_DENSITY_CACHE[ck] = (mtime, counts, total)
    return counts, total, kind


# Progressive folder aggregation: a background worker counts one file at a
# time into a shared accumulator, so the dashboard can draw partial results
# after every file instead of blocking until the whole folder is done.
FOLDER_LOCK = threading.Lock()
FOLDER_JOBS = {}   # (abspath, kind) -> state dict

RUNDB_LINK_LOCK = threading.Lock()
RUNDB_LINK_CACHE = {}   # (abspath, kind, descriptor) -> add_files() result


def _link_folder_once(abspath, kind):
    """Register a loaded folder's .h5 files as run-DB rows, once per
    (path, kind, descriptor). Kept independent of ``FOLDER_JOBS`` (which
    caches by path/kind only) so switching descriptors and re-loading the
    same folder still links it into the newly-active descriptor's DB, and so
    it still runs even when hit-density counting itself fails.
    """
    if not CTX.is_ready():
        return {"added": 0, "skipped": 0, "errors": ["no descriptor set"]}
    link_key = (abspath, kind, CTX.descriptor)
    with RUNDB_LINK_LOCK:
        cached = RUNDB_LINK_CACHE.get(link_key)
        if cached is not None:
            return cached
    result = RUNDB.add_files(abspath, kind)
    with RUNDB_LINK_LOCK:
        RUNDB_LINK_CACHE[link_key] = result
        for old in list(RUNDB_LINK_CACHE)[:-20]:   # bound memory
            RUNDB_LINK_CACHE.pop(old, None)
    return result


def _folder_sig(files):
    """Cheap change signature for a folder's .h5 files (path, mtime, size)."""
    sig = []
    for f in files:
        try:
            st = os.stat(f)
            sig.append((f, st.st_mtime, st.st_size))
        except OSError:
            sig.append((f, 0, 0))
    return tuple(sig)


def _folder_worker(state, files, kind):
    for fp in files:
        with state["lock"]:
            state["current"] = os.path.basename(fp)
        try:
            c, t, rk = count_file_density(fp, kind)
        except DensityError as exc:
            with state["lock"]:
                state["errors"].append("%s: %s" % (os.path.basename(fp), exc))
                state["n_done"] += 1
            continue
        with state["lock"]:
            agg = state["counts"]
            for k, v in c.items():
                agg[k] = agg.get(k, 0) + v
            state["total"] += t
            state["kinds"].add(rk)
            state["n_ok"] += 1
            state["n_done"] += 1
    with state["lock"]:
        state["current"] = None
        state["done"] = True


def path_density(path, kind="auto"):
    """Density from an arbitrary file or a folder of .h5 files.

    Folders aggregate **progressively**: the first call starts a background
    worker and every call returns the partial aggregate so far, with
    ``in_progress`` / ``n_done`` / ``n_total`` / ``current_file`` for the UI.
    Always returns a payload dict; on failure it carries a descriptive
    ``error`` so the dashboard can explain *why* nothing loaded.
    """
    fail = {"source": "none", "file": None, "counts": {}, "total": 0}
    if h5py is None:
        return dict(fail, error="h5py is not available in this environment")
    abspath = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abspath):
        return dict(fail, error="path not found by the server: %s  (if this is a "
                    "removable/network drive, make sure it is mounted on the "
                    "machine running the GUI)" % abspath)
    if os.path.isfile(abspath):
        try:
            counts, total, rk = count_file_density(abspath, kind)
        except DensityError as exc:
            return dict(fail, error="could not read %s: %s"
                        % (os.path.basename(abspath), exc))
        return {"source": "path", "mode": "file", "file": os.path.basename(abspath),
                "path": abspath, "kind": rk, "counts": counts, "total": total,
                "n_files": 1,
                "duration_s": RUNDB.runtime_for_converted(os.path.basename(abspath))}
    if os.path.isdir(abspath):
        files = sorted(glob.glob(os.path.join(abspath, "*.h5")))
        if not files:
            return dict(fail, error="no .h5 files in folder: %s" % abspath)
        key = (abspath, kind)
        sig = _folder_sig(files)
        rundb_link = _link_folder_once(abspath, kind)
        with FOLDER_LOCK:
            state = FOLDER_JOBS.get(key)
            if state is None or state["sig"] != sig:
                state = {"sig": sig, "lock": threading.Lock(), "counts": {},
                         "total": 0, "kinds": set(), "errors": [],
                         "n_done": 0, "n_ok": 0, "n_total": len(files),
                         "current": None, "done": False}
                FOLDER_JOBS[key] = state
                for old in list(FOLDER_JOBS)[:-4]:   # bound memory
                    if old != key:
                        FOLDER_JOBS.pop(old, None)
                threading.Thread(target=_folder_worker,
                                 args=(state, files, kind), daemon=True).start()
        with state["lock"]:
            if state["done"] and state["n_ok"] == 0:
                return dict(fail, error="none of the %d .h5 file(s) could be "
                            "read. First error -- %s"
                            % (state["n_total"],
                               state["errors"][0] if state["errors"] else "?"),
                            rundb_link=rundb_link)
            out = {"source": "path", "mode": "folder",
                   "file": os.path.basename(abspath.rstrip("/")) or abspath,
                   "path": abspath,
                   "kind": "/".join(sorted(state["kinds"])) or
                           (kind if kind != "auto" else "…"),
                   "counts": dict(state["counts"]), "total": state["total"],
                   "n_files": state["n_ok"],
                   "in_progress": not state["done"],
                   "n_done": state["n_done"], "n_total": state["n_total"],
                   "current_file": state["current"],
                   "rundb_link": rundb_link}
            if state["errors"]:
                out["warning"] = "%d file(s) skipped (e.g. %s)" \
                    % (len(state["errors"]), state["errors"][0])
            return out
    return dict(fail, error="not a file or folder: %s" % abspath)


def folder_listing(path):
    """The .h5 files inside a folder, in the same order the aggregate uses.

    Backs the pixel map's per-file stepper: loading a directory draws the
    whole-folder aggregate, and this list lets the page then walk the very
    same files one at a time. Ordering deliberately matches ``path_density``'s
    folder glob so "file 3 of 12" means the same thing in both views.
    """
    fail = {"is_dir": False, "files": [], "n": 0}
    if not path:
        return dict(fail, error="no path given")
    abspath = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(abspath):
        return dict(fail, error="path not found: %s" % abspath)
    if not os.path.isdir(abspath):
        return dict(fail, path=abspath)     # a plain file: nothing to step through
    out = []
    for fp in sorted(glob.glob(os.path.join(abspath, "*.h5"))):
        try:
            size_mb = round(os.path.getsize(fp) / 1e6, 1)
        except OSError:
            size_mb = 0.0
        out.append({"name": os.path.basename(fp), "path": fp,
                    "size_mb": size_mb, "ts": RUNDB._ts_for(fp)})
    return {"is_dir": True, "path": abspath, "files": out, "n": len(out)}


def file_density():
    """Per-pixel counts of the newest converted file in the current run."""
    if not CTX.is_ready():
        return None
    files = get_files_by_creation(CTX.d("converted_self_trigger"), ".h5")
    if not files:
        return None
    path = files[-1]
    try:
        counts, total, _ = count_file_density(path, "converted")
    except DensityError:
        return None
    base = os.path.basename(path)
    return {"source": "file", "file": base, "counts": counts, "total": total,
            "duration_s": RUNDB.runtime_for_converted(base)}


def density_payload(path=None, kind="auto"):
    """Density for the pixel map.

    If ``path`` (a file or folder) is given, load its hit density. Otherwise use
    the current run: live tail of the active raw file, else the newest converted
    file.
    """
    if path:
        return path_density(path, kind)
    if JOBS.latest_status_by_action().get("self_trigger") == "running":
        live = live_density()
        if live is not None:
            return live
    fd = file_density()
    if fd is not None:
        return fd
    return {"source": "none", "file": None, "counts": {}, "total": 0}


# ---------------------------------------------------------------------------
# Time-resolved density: bin hits by reconstructed elapsed time so a slider can
# scrub the hit-density map through the run(s).
# ---------------------------------------------------------------------------
def reconstruct_elapsed_s(ts):
    """LArPix timestamps in acquisition order -> (monotonic elapsed seconds, ok).

    The 32-bit tick counter wraps at 2**32. Self-trigger packets from different
    chips are interleaved, so a naive "any big backward jump = rollover" badly
    over-counts. We only count a *true* wrap (previous near the top of the range,
    current near the bottom) and then enforce monotonicity with a running max, so
    residual chip-interleave jitter doesn't move the clock backward. ``ok`` is
    False if the reconstructed span is implausibly large (data too disordered).
    """
    ts = np.asarray(ts, dtype="f8")
    if ts.size == 0:
        return ts, False
    P = TIMESTAMP_ROLLOVER
    roll = np.where((ts[:-1] > 0.9 * P) & (ts[1:] < 0.1 * P), 1, 0)
    corrected = ts.copy()
    corrected[1:] += np.cumsum(roll) * P
    corrected = np.maximum.accumulate(corrected)          # ignore backward jitter
    elapsed = (corrected - corrected[0]) * TICK_US * 1e-6  # ticks -> seconds
    return elapsed, bool(elapsed[-1] < 6 * 3600)


def _timebinned_hits(path, kind):
    """(chip[], chan[], elapsed_s[]) for one file, hits in acquisition order."""
    if kind in (None, "auto"):
        kind = _detect_h5_kind(path) or "converted"
    if kind == "raw":
        fmt = _larpix_fmt()
        if fmt is None:
            raise DensityError("larpix raw reader unavailable for raw files")
        rhdf5, pmf = fmt
        n = rhdf5.len_rawfile(path, attempts=0)
        chips, chans, tss = [], [], []
        start = 0
        while start < n:
            end = min(start + LIVE_CHUNK, n)
            rd = rhdf5.from_rawfile(path, start=start, end=end)
            for iog, msg in zip(rd["msg_headers"]["io_groups"], rd["msgs"]):
                for pkt in pmf.parse(msg, io_group=iog):
                    if getattr(pkt, "packet_type", None) == 0:
                        chips.append(pkt.chip_id)
                        chans.append(pkt.channel_id)
                        tss.append(int(pkt.timestamp))
            start = end
        chips = np.asarray(chips, "i4"); chans = np.asarray(chans, "i4")
        ts = np.asarray(tss, "f8")
    elif kind == "clustered":
        # Clustered hits carry a per-hit wall-clock 'unix' second, so there is no
        # rollover to reconstruct here -- 1 s resolution is finer than the bins.
        core = _threshold_core()
        if core is None:
            raise DensityError("threshold_core unavailable for clustered files")
        with open_h5_read(path) as f:
            if "hits" not in f:
                raise DensityError("not a clustered file (no 'hits')")
            h = f["hits"]
            names = set(h.dtype.names or ())
            if not {"unique_id", "unix"} <= names:
                raise DensityError("clustered 'hits' lacks unique_id/unix")
            uid = h["unique_id"][:]
            unix = h["unix"][:].astype("f8")
        chip_arr, chan_arr = core.uid_to_chip_channel(uid)
        chips = np.asarray(chip_arr, "i4")
        chans = np.asarray(chan_arr, "i4")
        elapsed = unix - unix.min() if unix.size else unix
        return chips, chans, elapsed, True, kind
    else:
        with open_h5_read(path) as f:
            if "packets" not in f:
                raise DensityError("not a converted file (no 'packets')")
            p = f["packets"]
            mask = p["packet_type"][:] == 0
            chips = p["chip_id"][:][mask].astype("i4")
            chans = p["channel_id"][:][mask].astype("i4")
            ts = p["timestamp"][:][mask].astype("f8")
    elapsed, ok = reconstruct_elapsed_s(ts)
    return chips, chans, elapsed, ok, kind


TIMED_LOCK = threading.Lock()
TIMED_JOBS = {}   # (abspath, kind, bins) -> state


def _timed_worker(state, files, kind, bins):
    """Background: build per-time-bin per-pixel counts across files."""
    # First pass: get each file's elapsed span + wall-clock base, to lay out a
    # global timeline. Second pass would re-read; instead cache per-file hits.
    per_file = []          # (base_s, chips, chans, elapsed_s)
    t_end = 0.0
    reliable = True
    for fp in files:
        with state["lock"]:
            state["current"] = os.path.basename(fp)
        try:
            chips, chans, elapsed, ok, _ = _timebinned_hits(fp, kind)
        except DensityError as exc:
            with state["lock"]:
                state["errors"].append("%s: %s" % (os.path.basename(fp), exc))
                state["n_done"] += 1
            continue
        if not ok:                      # fall back to acquisition-order "time"
            reliable = False
            elapsed = np.arange(chips.size, dtype="f8")
        m = RunDB.TS_RE.search(os.path.basename(fp))
        if m:
            try:
                base = datetime(*[int(g) for g in m.groups()]).timestamp()
            except ValueError:
                base = None
        else:
            base = None
        if base is None:                      # cumulative fallback
            base = t_end
        per_file.append((base, chips, chans, elapsed))
        t_end = max(t_end, base + (float(elapsed[-1]) if elapsed.size else 0.0))
        with state["lock"]:
            state["n_done"] += 1

    if not per_file:
        with state["lock"]:
            state["done"] = True
            state["current"] = None
        return
    # Global timeline extent: reduce each file's elapsed array in numpy rather
    # than materializing a Python list over every hit (was O(total hits) in the
    # interpreter, and min()/max() over an empty list would raise).
    tmin = tmax = None
    for base, _, _, el in per_file:
        if not el.size:
            continue
        f_lo, f_hi = base + float(el.min()), base + float(el.max())
        tmin = f_lo if tmin is None else min(tmin, f_lo)
        tmax = f_hi if tmax is None else max(tmax, f_hi)
    if tmin is None:                      # every file had zero usable hits
        with state["lock"]:
            state["done"] = True
            state["current"] = None
        return
    if tmax <= tmin:
        tmax = tmin + 1.0
    width = (tmax - tmin) / bins
    per_bin = [dict() for _ in range(bins)]
    per_bin_total = [0] * bins
    for base, chips, chans, el in per_file:
        if not el.size:
            continue
        gt = base + el
        idx = np.clip(((gt - tmin) / width).astype(np.int64), 0, bins - 1)
        keys = chips.astype(np.int64) * 64 + chans
        # Count hits per (bin, pixel) in a single unique pass over a combined
        # code, instead of re-masking the whole hit array once per bin (which
        # was O(bins x hits)); per-bin totals come from one bincount.
        span_keys = int(keys.max()) + 1
        codes, counts = np.unique(idx * span_keys + keys, return_counts=True)
        for b, k, v in zip((codes // span_keys).tolist(),
                           (codes % span_keys).tolist(), counts.tolist()):
            d = per_bin[b]
            sk = "%d-%d" % (k // 64, k % 64)
            d[sk] = d.get(sk, 0) + v
        binc = np.bincount(idx, minlength=bins)
        for b in range(bins):
            per_bin_total[b] += int(binc[b])
    with state["lock"]:
        state["per_bin"] = per_bin
        state["per_bin_total"] = per_bin_total
        state["tmin"] = tmin
        state["tmax"] = tmax
        state["reliable"] = reliable
        state["done"] = True
        state["current"] = None


def timedensity_payload(path, kind="auto", bins=40):
    """Time-binned per-pixel density (progressive background build)."""
    fail = {"source": "none", "error": None}
    if h5py is None:
        return dict(fail, error="h5py not available")
    bins = max(4, min(int(bins), 120))
    if not path:
        if not CTX.is_ready():
            return dict(fail, error="load a file/folder or set a descriptor")
        base = CTX.d("converted_self_trigger")
        files = sorted(get_files_by_creation(base, ".h5"))
        kind = "converted"
        abspath = base
    else:
        abspath = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abspath):
            return dict(fail, error="path not found: %s" % abspath)
        if os.path.isdir(abspath):
            files = sorted(glob.glob(os.path.join(abspath, "*.h5")))
        else:
            files = [abspath]
    if not files:
        return dict(fail, error="no .h5 files found")
    key = (abspath, kind, bins)
    sig = _folder_sig(files)
    with TIMED_LOCK:
        state = TIMED_JOBS.get(key)
        if state is None or state["sig"] != sig:
            state = {"sig": sig, "lock": threading.Lock(), "n_done": 0,
                     "n_total": len(files), "current": None, "done": False,
                     "errors": [], "per_bin": None, "per_bin_total": None,
                     "tmin": 0.0, "tmax": 0.0, "reliable": True}
            TIMED_JOBS[key] = state
            for old in list(TIMED_JOBS)[:-3]:
                if old != key:
                    TIMED_JOBS.pop(old, None)
            threading.Thread(target=_timed_worker,
                             args=(state, files, kind, bins), daemon=True).start()
    with state["lock"]:
        out = {"source": "timed", "bins": bins, "n_done": state["n_done"],
               "n_total": state["n_total"], "current_file": state["current"],
               "in_progress": not state["done"], "path": abspath}
        if state["errors"]:
            out["warning"] = "%d file(s) skipped (e.g. %s)" % (
                len(state["errors"]), state["errors"][0])
        if state["done"] and state["per_bin"] is not None:
            span = state["tmax"] - state["tmin"]
            # bin centers as elapsed seconds from the start of the timeline
            out["bin_seconds"] = [round((i + 0.5) * span / bins, 3)
                                  for i in range(bins)]
            out["per_bin_total"] = state["per_bin_total"]
            out["total_seconds"] = round(span, 3)
            out["time_reliable"] = state["reliable"]
            # merge into pixels: key -> [count per bin]
            pixels = {}
            for b, d in enumerate(state["per_bin"]):
                for k, v in d.items():
                    arr = pixels.setdefault(k, [0] * bins)
                    arr[b] = v
            out["pixels"] = pixels
        return out


# ---------------------------------------------------------------------------
# Trigger-threshold map: per-channel charge histograms from clustered files ->
# the 50% rising-edge threshold of each channel's spectrum (see
# analysis/threshold_core.py, ported from charge_trigger_thresholds.py).
#
# Same progressive-worker shape as the time-resolved density above: a background
# thread pools one file at a time into a shared histogram and every poll returns
# the partial state. Pooling is exact rather than approximate because the
# per-channel histograms are simply added -- which is also what the donor script
# does, it just holds every hit in memory to do it.
# ---------------------------------------------------------------------------
THRESH_LOCK = threading.Lock()
THRESH_JOBS = {}   # (abspath, kind, params) -> state dict

THRESH_DEFAULTS = {"min_hits": 50, "max_hits": 350, "bins": 50,
                   "bin_width": 0.221, "q_to_ke": 0.221}


def threshold_params(qs=None):
    """Query-string (or defaults) -> the parameter dict the worker is keyed by."""
    out = dict(THRESH_DEFAULTS)
    if not qs:
        return out
    for name, cast in (("min_hits", int), ("max_hits", int), ("bins", int),
                       ("bin_width", float), ("q_to_ke", float)):
        raw = qs.get(name, [None])[0]
        if raw in (None, ""):
            continue
        try:
            out[name] = cast(raw)
        except ValueError:
            pass
    out["bins"] = max(4, min(int(out["bins"]), 400))
    if out["max_hits"] < out["min_hits"]:
        out["min_hits"], out["max_hits"] = out["max_hits"], out["min_hits"]
    return out


def _thresh_key(params):
    return "|".join("%s=%s" % (k, params[k]) for k in sorted(params))


def _threshold_worker(state, files, params):
    core = _threshold_core()
    edges = core.q_bin_edges(params["bins"], params["bin_width"])
    pkey = _thresh_key(params)
    hist = counts = None
    for fp in files:
        with state["lock"]:
            state["current"] = os.path.basename(fp)
        try:
            st = os.stat(fp)
            cached = _thresh_cache_get(os.path.abspath(fp), pkey,
                                       st.st_mtime, st.st_size)
            if cached is None:
                h, c, n_binned, n_unmatched, warn = core.file_charge_hist(
                    fp, edges, params["min_hits"], params["max_hits"],
                    params["q_to_ke"])
                _thresh_cache_put(os.path.abspath(fp), pkey, st.st_mtime,
                                  st.st_size, h, c, n_binned, n_unmatched, warn)
            else:
                h, c, n_binned, n_unmatched, warn = cached
        except Exception as exc:
            with state["lock"]:
                state["errors"].append("%s: %s" % (os.path.basename(fp), exc))
                state["n_done"] += 1
            continue
        hist = h.astype("i8") if hist is None else hist + h
        counts = c.astype("i8") if counts is None else counts + c
        with state["lock"]:
            state["n_binned"] += n_binned
            state["n_unmatched"] += n_unmatched
            if warn and warn not in state["geom_warnings"]:
                state["geom_warnings"].append(warn)
            state["n_ok"] += 1
            state["n_done"] += 1
    with state["lock"]:
        state["hist"] = hist
        state["counts"] = counts
        state["edges"] = edges
        state["current"] = None
        state["done"] = True


def _threshold_files(path, kind):
    """Input files for the threshold map: an explicit path, else the run's
    clustered_data. Returns (files, abspath) or raises ValueError."""
    if path:
        abspath = os.path.abspath(os.path.expanduser(path))
        if not os.path.exists(abspath):
            raise ValueError("path not found: %s" % abspath)
        files = (sorted(glob.glob(os.path.join(abspath, "*.h5")))
                 if os.path.isdir(abspath) else [abspath])
    else:
        if not CTX.is_ready():
            raise ValueError("load a clustered file/folder or set a descriptor")
        abspath = CTX.d("clustered_data")
        files = sorted(glob.glob(os.path.join(abspath, "*.h5")))
    if not files:
        raise ValueError("no .h5 files found in %s" % abspath)
    return files, abspath


def _threshold_state(path, kind, params):
    """The (possibly still-running) worker state for these inputs."""
    files, abspath = _threshold_files(path, kind)
    key = (abspath, kind, _thresh_key(params))
    sig = _folder_sig(files)
    with THRESH_LOCK:
        state = THRESH_JOBS.get(key)
        if state is None or state["sig"] != sig:
            state = {"sig": sig, "lock": threading.Lock(), "n_done": 0,
                     "n_ok": 0, "n_total": len(files), "current": None,
                     "done": False, "errors": [], "geom_warnings": [],
                     "n_binned": 0, "n_unmatched": 0,
                     "hist": None, "counts": None, "edges": None}
            THRESH_JOBS[key] = state
            for old in list(THRESH_JOBS)[:-3]:
                if old != key:
                    THRESH_JOBS.pop(old, None)
            threading.Thread(target=_threshold_worker,
                             args=(state, files, params), daemon=True).start()
    return state, abspath


def threshold_payload(path, kind="clustered", params=None):
    """Per-channel trigger thresholds (progressive background build)."""
    params = params or dict(THRESH_DEFAULTS)
    fail = {"source": "threshold", "error": None}
    if h5py is None:
        return dict(fail, error="h5py not available")
    if _threshold_core() is None:
        return dict(fail, error="threshold_core is not importable")
    try:
        state, abspath = _threshold_state(path, kind, params)
    except ValueError as exc:
        return dict(fail, error=str(exc))
    core = _threshold_core()
    with state["lock"]:
        out = {"source": "threshold", "path": abspath, "params": params,
               "n_done": state["n_done"], "n_total": state["n_total"],
               "current_file": state["current"],
               "in_progress": not state["done"]}
        warns = list(state["geom_warnings"])
        if state["errors"]:
            warns.append("%d file(s) skipped (e.g. %s)"
                         % (len(state["errors"]), state["errors"][0]))
        if warns:
            out["warning"] = "  ·  ".join(warns)
        if not state["done"]:
            return out
        if state["counts"] is None:
            return dict(out, error="none of the %d file(s) could be read. "
                        "First error -- %s" % (state["n_total"],
                        state["errors"][0] if state["errors"] else "?"))
        thresholds = core.thresholds_from_hist(state["hist"], state["edges"],
                                               state["counts"])
        hits = core.hits_per_channel(state["counts"])
        stats = core.summary_stats(thresholds)
        edges = state["edges"]
    values = {k: v for k, v in thresholds.items() if v is not None}
    out["values"] = values
    out["hits"] = hits
    out["n_channels"] = len(thresholds)
    out["n_missing"] = len(thresholds) - len(values)
    out["n_hits"] = int(sum(hits.values()))
    out["stats"] = stats
    out["bin_range"] = [float(edges[0]), float(edges[-1])]
    if values:
        arr = np.array(list(values.values()), dtype="f8")
        out["vmin"] = round(float(np.percentile(arr, 5)), 4)
        out["vmax"] = round(float(np.percentile(arr, 95)), 4)
        if out["vmax"] <= out["vmin"]:
            out["vmax"] = out["vmin"] + 1e-3
    return out


def threshold_pixel_payload(path, kind, params, chip, ch):
    """One channel's pooled charge spectrum + its fitted threshold."""
    core = _threshold_core()
    if core is None:
        return {"error": "threshold_core is not importable"}
    try:
        state, _ = _threshold_state(path, kind, params)
    except ValueError as exc:
        return {"error": str(exc)}
    key = "%d-%d" % (chip, ch)
    code = chip * 64 + ch
    with state["lock"]:
        if not state["done"]:
            return {"key": key, "in_progress": True}
        if state["hist"] is None or code >= state["hist"].shape[0]:
            return {"key": key, "error": "no data for this channel"}
        row = state["hist"][code]
        edges = state["edges"]
        n_hits = int(state["counts"][code])
    if not n_hits:
        return {"key": key, "counts": [], "n_hits": 0}
    thr = core.find_threshold_50(row, edges)
    peak = float(row.max())
    return {"key": key, "counts": [int(v) for v in row],
            "edges": [float(e) for e in edges], "n_hits": n_hits,
            "threshold": None if thr is None else float(thr),
            "half_max": peak / 2.0}


# ---------------------------------------------------------------------------
# Event display -- hits grouped into drift-time windows
#
# One "event" is every hit inside one full drift time of the TPC, so the window
# width follows the field: E = HV * HV_TO_EFIELD, and purity_core.drift_params()
# turns that into the full drift time. Works on a growing raw file (live, during
# a self-trigger run), a finished raw/converted file, or a clustered file (which
# already carries its own event grouping and reconstructed positions).
# ---------------------------------------------------------------------------
EVD_LOCK = threading.Lock()
EVD_LIVE = {}                  # raw path -> {"hits": [...], "last": int}
EVD_FILE_CACHE = {}            # (path, kind) -> (mtime, hits-dict)
EVD_LIVE_MAX_HITS = 400000     # rolling live buffer cap (~ tens of MB)
EVD_MAX_DRAW_HITS = 6000       # per-event hits sent to the browser


def evd_window_us(efield):
    """Full drift time [us] for an E-field -- the width of one event window."""
    core = _purity_core()
    if core is None:
        return 186.0 * (500.0 / float(efield or 500.0))   # scale the 500 V/cm value
    return float(core.drift_params(float(efield))[1])


def evd_drift_velocity(efield):
    """Drift velocity [cm/us] used to turn a hit's drift time into a z position."""
    core = _purity_core()
    if core is None:
        return 0.1544
    return float(core.drift_params(float(efield))[0])


def evd_detector_bounds(efield):
    """Physical (x, y, z) extent of the TPC volume, for drawing the detector
    outline in the event display.

    x/y come from the pixel geometry (anode plane); z is the full drift
    length -- ``window_us`` is defined as one full anode-to-cathode drift
    time (see ``evd_window_us``), so ``window_us * v_drift`` is the fixed
    physical drift distance and is (by construction) the same at any
    E-field, not actually field-dependent.
    """
    geo = load_pixel_geometry()
    xs = [p["x"] for p in geo["pixels"]]
    ys = [p["y"] for p in geo["pixels"]]
    ux = sorted(set(xs))
    pitch = min((b - a for a, b in zip(ux, ux[1:])), default=4.434)
    half = pitch / 2.0
    drift_mm = evd_window_us(efield) * evd_drift_velocity(efield) * 10.0
    return {"x": [round(min(xs) - half, 2), round(max(xs) + half, 2)],
            "y": [round(min(ys) - half, 2), round(max(ys) + half, 2)],
            "z": [0.0, round(drift_mm, 2)]}


def _evd_pixel_lookup():
    """Vectorized companion to ``_evd_pixel_xy``: (key_arr, x_arr, y_arr), sorted
    by the combined key ``chip*64+ch`` so hit arrays can be resolved via
    ``np.searchsorted`` instead of a per-hit dict lookup. Cached alongside the
    geometry it's derived from.
    """
    global _EVD_PIXEL_LOOKUP_CACHE
    if _EVD_PIXEL_LOOKUP_CACHE is not None:
        return _EVD_PIXEL_LOOKUP_CACHE
    geo = load_pixel_geometry()
    n = len(geo["pixels"])
    keys = np.empty(n, dtype="i8")
    xs = np.empty(n, dtype="f8")
    ys = np.empty(n, dtype="f8")
    for i, p in enumerate(geo["pixels"]):
        keys[i] = p["chip"] * 64 + p["ch"]
        xs[i] = p["x"]
        ys[i] = p["y"]
    order = np.argsort(keys)
    _EVD_PIXEL_LOOKUP_CACHE = (keys[order], xs[order], ys[order])
    return _EVD_PIXEL_LOOKUP_CACHE


def _evd_key_to_combined(keys):
    """"chip-ch" strings -> chip*64+ch int array, for matching against
    ``_evd_pixel_lookup``'s combined keys (e.g. to translate ``exclude`` sets)."""
    if not keys:
        return np.empty(0, dtype="i8")
    out = np.empty(len(keys), dtype="i8")
    for i, k in enumerate(keys):
        chip_s, ch_s = k.split("-")
        out[i] = int(chip_s) * 64 + int(ch_s)
    return out


def _evd_sorted_times(ts):
    """Rollover-corrected hit times [us], plus the order that sorts them.

    ``reconstruct_elapsed_s`` forces monotonicity, which is right for aggregate
    binning but wrong here: we need each hit's own time. So we undo the 2**32
    wrap the same way (a true wrap = previous near the top, current near the
    bottom) but then *sort* rather than clamp, because self-trigger packets from
    different chips arrive interleaved.
    """
    ts = np.asarray(ts, dtype="f8")
    if ts.size == 0:
        return ts, np.arange(0)
    P = TIMESTAMP_ROLLOVER
    roll = np.zeros(ts.size, dtype="i8")
    if ts.size > 1:
        wrapped = (ts[:-1] > 0.9 * P) & (ts[1:] < 0.1 * P)
        roll[1:] = np.cumsum(np.where(wrapped, 1, 0))
    corrected = ts + roll * P
    order = np.argsort(corrected, kind="stable")
    return corrected[order] * TICK_US, order


def _evd_group(times_us, window_us, mode, min_hits):
    """Split sorted hit times into events -> list of (start_idx, end_idx).

    ``window``: fixed slices one drift time wide, starting at the first hit.
    ``gap``: a new event whenever the gap to the previous hit exceeds one drift
    time, which tracks bursty self-trigger data more closely.
    """
    n = times_us.size
    if n == 0:
        return []
    if window_us <= 0:
        window_us = 186.0
    if mode == "gap":
        gaps = np.where(np.diff(times_us) > window_us)[0] + 1
        edges = np.concatenate(([0], gaps, [n]))
    else:
        bin_of = np.floor((times_us - times_us[0]) / window_us).astype("i8")
        starts = np.concatenate(([0], np.where(np.diff(bin_of) != 0)[0] + 1))
        edges = np.concatenate((starts, [n]))
    out = []
    for i in range(edges.size - 1):
        s, e = int(edges[i]), int(edges[i + 1])
        if e - s >= min_hits:
            out.append((s, e))
    return out


def _evd_hits_from_packets(chip, chan, ts, dataword, exclude=None):
    """Map packet arrays -> {x, y, t_us, q} hit arrays, dropping unmapped pixels.

    Vectorized: pixel geometry is resolved via ``np.searchsorted`` against the
    sorted (chip*64+ch) lookup from ``_evd_pixel_lookup``, instead of a
    per-hit Python dict lookup -- this loop runs on every event-display load
    and can be the hottest part of the pipeline for large hit counts.
    """
    key_arr, x_arr, y_arr = _evd_pixel_lookup()
    core = _purity_core()
    ped = float(getattr(core, "PEDESTAL_ADC", 78.0)) if core else 78.0
    t_us, order = _evd_sorted_times(ts)
    chip = np.asarray(chip)[order].astype("i8")
    chan = np.asarray(chan)[order].astype("i8")
    q = np.asarray(dataword, dtype="f8")[order] - ped

    hit_keys = chip * 64 + chan
    idx = np.searchsorted(key_arr, hit_keys)
    idx = np.clip(idx, 0, key_arr.size - 1)
    ok = key_arr[idx] == hit_keys
    if exclude:
        excl_keys = _evd_key_to_combined(list(exclude))
        ok &= ~np.isin(hit_keys, excl_keys)

    xs = x_arr[idx]
    ys = y_arr[idx]
    return {"x": xs[ok], "y": ys[ok], "t_us": t_us[ok], "q": q[ok]}


def _evd_read_converted(path, exclude=None, max_packets=None):
    if h5py is None:
        raise DensityError("h5py not installed; cannot read converted files")
    with open_h5_read(path) as f:
        if "packets" not in f:
            raise DensityError("no 'packets' dataset (is this a converted file?)")
        dset = f["packets"]
        # Pull only the 6 columns the event display needs instead of the full
        # ~20-field compound row -- a large converted file has tens of millions
        # of packets and this is the dominant cost of opening the display.
        cols = ["packet_type", "valid_parity", "chip_id", "channel_id",
                "timestamp", "dataword"]
        sl = slice(None, max_packets) if max_packets else slice(None)
        d = dset.fields(cols)[sl]
    m = (d["packet_type"] == 0) & (d["valid_parity"] == 1)
    d = d[m]
    return _evd_hits_from_packets(d["chip_id"], d["channel_id"], d["timestamp"],
                                  d["dataword"], exclude)


def _evd_usable(col):
    """True if a column actually varies (not a constant / all -1 placeholder)."""
    if col is None or col.size == 0:
        return False
    u = np.unique(col)
    return u.size > 1


def _evd_read_clustered(path):
    """Hits from a clustered file, using the file's own clustering as events.

    Real files vary in what the reco actually filled in: this sample has
    ``event_id`` all -1 and a constant ``z_drift``/``z_anode``, while
    ``cluster_index`` is the meaningful grouping. So each column is used only if
    it actually varies, and z falls back to the drift-time reconstruction the
    raw/converted path uses. ``t`` units also differ between reco versions, so
    the scale is picked from the data (see below).
    """
    if h5py is None:
        raise DensityError("h5py not installed; cannot read clustered files")
    with open_h5_read(path) as f:
        if "hits" not in f:
            raise DensityError("no 'hits' dataset (is this a clustered file?)")
        h = f["hits"][:]
    names = set(h.dtype.names or ())
    if not {"x", "y", "q"} <= names:
        raise DensityError("clustered 'hits' lacks x/y/q columns")

    # grouping column: prefer a real event id, else the cluster index
    grp = None
    for cand in ("event_id", "cluster_index"):
        if cand in names and _evd_usable(np.asarray(h[cand])):
            grp = np.asarray(h[cand])
            break

    t_raw = np.asarray(h["t"], dtype="f8") if "t" in names else None
    t_us = np.zeros(h.shape[0])
    if t_raw is not None and _evd_usable(t_raw):
        # ndlar_39Ar_reco writes ns in some versions and raw 0.1 us ticks in
        # others. Pick the scale that makes a cluster's span physically sane
        # (a track cannot last much more than one full drift time).
        for scale in (1e-3, TICK_US):            # ns -> us, ticks -> us
            cand = t_raw * scale
            if grp is not None:
                spans = []
                for g in np.unique(grp)[:200]:
                    m = cand[grp == g]
                    if m.size > 1:
                        spans.append(m.max() - m.min())
                typical = float(np.median(spans)) if spans else 0.0
            else:
                typical = 0.0
            t_us = cand
            if typical <= 1000.0:                # <= ~5x the full drift time
                break

    z = None
    if "z_drift" in names and _evd_usable(np.asarray(h["z_drift"])):
        z = np.asarray(h["z_drift"], dtype="f8") * 10.0      # cm -> mm

    # group hits contiguously but keep them time-ordered *within* each group, so
    # drift positions and spans come out right whichever grouping mode is used
    order = (np.lexsort((t_us, grp)) if grp is not None
             else np.argsort(t_us, kind="stable"))
    out = {"x": np.asarray(h["x"])[order].astype("f8"),
           "y": np.asarray(h["y"])[order].astype("f8"),
           "q": np.asarray(h["q"])[order].astype("f8"),
           "t_us": t_us[order]}
    if z is not None:
        out["z"] = z[order]
    if grp is not None:
        out["group_id"] = grp[order]
    return out


def _evd_read_raw(path, exclude=None, max_messages=None):
    fmt = _larpix_fmt()
    if fmt is None:
        raise DensityError("larpix-control not importable; cannot read raw files")
    rhdf5, pmf = fmt
    total = rhdf5.len_rawfile(path, attempts=0)
    if max_messages:
        total = min(total, max_messages)
    chip, chan, ts, dw = [], [], [], []
    start = 0
    while start < total:
        end = min(start + 20000, total)
        rd = rhdf5.from_rawfile(path, start=start, end=end)
        for iog, msg in zip(rd["msg_headers"]["io_groups"], rd["msgs"]):
            for p in pmf.parse(msg, io_group=iog):
                if getattr(p, "packet_type", None) != 0:
                    continue
                if not p.has_valid_parity():   # same cut as the converted path
                    continue
                chip.append(p.chip_id); chan.append(p.channel_id)
                ts.append(int(p.timestamp)); dw.append(int(p.dataword))
        start = end
    return _evd_hits_from_packets(chip, chan, ts, dw, exclude)


def evd_live_hits(exclude=None):
    """Incrementally tail the growing raw file -> rolling buffer of recent hits.

    Same mechanism as ``live_density``: ask how many messages exist right now and
    parse only the new ones, tolerating a mid-write file. Keeps the most recent
    ``EVD_LIVE_MAX_HITS`` hits so the page stays responsive during a long run.
    """
    fmt = _larpix_fmt()
    if fmt is None:
        return None
    rhdf5, pmf = fmt
    latest, _ = get_latest_file("*raw*.h5", directory=".")
    if latest is None:
        return None
    path = str(latest)
    try:
        n = rhdf5.len_rawfile(path, attempts=0)
    except Exception:
        return None
    with EVD_LOCK:
        acc = EVD_LIVE.setdefault(path, {"chip": [], "chan": [], "ts": [],
                                         "dw": [], "last": 0})
        start = acc["last"]
        if n > start:
            end = min(n, start + LIVE_CHUNK)
            try:
                rd = rhdf5.from_rawfile(path, start=start, end=end)
                for iog, msg in zip(rd["msg_headers"]["io_groups"], rd["msgs"]):
                    for p in pmf.parse(msg, io_group=iog):
                        if getattr(p, "packet_type", None) != 0:
                            continue
                        if not p.has_valid_parity():
                            continue
                        acc["chip"].append(p.chip_id)
                        acc["chan"].append(p.channel_id)
                        acc["ts"].append(int(p.timestamp))
                        acc["dw"].append(int(p.dataword))
                acc["last"] = end
            except Exception:
                pass                      # mid-write; retry on the next poll
        if len(acc["ts"]) > EVD_LIVE_MAX_HITS:
            keep = -EVD_LIVE_MAX_HITS
            for k in ("chip", "chan", "ts", "dw"):
                acc[k] = acc[k][keep:]
        snap = (list(acc["chip"]), list(acc["chan"]), list(acc["ts"]),
                list(acc["dw"]), acc["last"])
        for old in list(EVD_LIVE)[:-3]:
            if old != path:
                EVD_LIVE.pop(old, None)
    hits = _evd_hits_from_packets(snap[0], snap[1], snap[2], snap[3], exclude)
    hits["_file"] = os.path.basename(path)
    hits["_parsed_msgs"] = snap[4]
    hits["_total_msgs"] = n
    return hits


def evd_hits(src, path, kind, exclude=None):
    """Hits for one source, mtime-cached for files (live is always re-tailed)."""
    if src == "live":
        return evd_live_hits(exclude)
    if not path or not os.path.isfile(path):
        raise DensityError("file not found: %s" % (path or "(none)"))
    if kind == "auto":
        # a clustered file has a 'hits' table; otherwise fall back to the same
        # packets/msgs sniff the pixel map uses, then to the filename
        base = os.path.basename(path).lower()
        kind = None
        if h5py is not None:
            try:
                with open_h5_read(path) as f:
                    if "hits" in f and "clusters" in f:
                        kind = "clustered"
            except Exception:
                pass
        kind = kind or _detect_h5_kind(path) or (
            "clustered" if "cluster" in base
            else "raw" if "raw" in base else "converted")
    key = (os.path.abspath(path), kind, tuple(sorted(exclude or ())))
    mtime = os.path.getmtime(path)
    with EVD_LOCK:
        hit = EVD_FILE_CACHE.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    if kind == "clustered":
        hits = _evd_read_clustered(path)
    elif kind == "raw":
        hits = _evd_read_raw(path, exclude)
    else:
        hits = _evd_read_converted(path, exclude)
    hits["_kind"] = kind
    with EVD_LOCK:
        EVD_FILE_CACHE[key] = (mtime, hits)
        for old in list(EVD_FILE_CACHE)[:-4]:
            EVD_FILE_CACHE.pop(old, None)
    return hits


def _evd_events(hits, window_us, mode, min_hits):
    """(spans, order): event index ranges, and the hit ordering they index into.

    ``cluster`` mode uses the clustered file's own grouping (its clusters are
    already the physics events), which means the arrays are laid out by cluster,
    not by time. ``window``/``gap`` slice on hit time instead, so they need a
    time-sorted view -- hence the explicit ``order`` permutation the caller must
    apply rather than assuming the stored order is the right one.
    """
    n = hits["t_us"].size
    if mode == "cluster" and "group_id" in hits:
        ev = hits["group_id"]
        if ev.size == 0:
            return [], np.arange(0)
        starts = np.concatenate(([0], np.where(np.diff(ev) != 0)[0] + 1))
        edges = np.concatenate((starts, [ev.size]))
        spans = [(int(edges[i]), int(edges[i + 1]))
                 for i in range(edges.size - 1)
                 if edges[i + 1] - edges[i] >= min_hits]
        return spans, np.arange(n)
    t = hits["t_us"]
    if n > 1 and not bool((t[1:] >= t[:-1]).all()):
        order = np.argsort(t, kind="stable")
        t = t[order]
    else:
        order = np.arange(n)
    return _evd_group(t, window_us, mode, min_hits), order


def evd_resolve_field(hv=None, efield=None, path=None):
    """(efield, hv, source) from an explicit value, the run DB, or the default."""
    if efield not in (None, ""):
        try:
            return float(efield), hv, "manual"
        except (TypeError, ValueError):
            pass
    if hv not in (None, ""):
        e = hv_to_efield(hv)
        if e:
            return e, hv, "manual HV"
    db_hv = None
    if path:
        try:
            row = RUNDB.find_by_file(os.path.basename(path))
        except Exception:
            row = None
        if row:
            db_hv = row.get("hv")
    if db_hv:
        e = hv_to_efield(db_hv)
        if e:
            return e, db_hv, "db_hv (%s)" % db_hv
    return 500.0, None, "default"


def evd_index(src, path, kind, efield, mode="window", min_hits=20,
              exclude=None):
    """Event index (one summary row per event) for the browser's event list."""
    hits = evd_hits(src, path, kind, exclude)
    if hits is None:
        return {"error": "no live raw file yet (is a self-trigger run writing?)"}
    window_us = evd_window_us(efield)
    spans, order = _evd_events(hits, window_us, mode, int(min_hits))
    q = hits["q"][order]
    t = hits["t_us"][order]
    events = []
    for i, (s, e) in enumerate(spans):
        seg = t[s:e]
        events.append({"i": i, "n_hits": int(e - s),
                       "t_start_s": round(float(seg.min()) * 1e-6, 4),
                       "span_us": round(float(seg.max() - seg.min()), 2),
                       "q_total": round(float(q[s:e].sum()), 1)})
    out = {"events": events, "n_events": len(events),
           "window_us": round(window_us, 2), "efield": round(float(efield), 2),
           "n_hits_total": int(q.size),
           "has_clusters": "group_id" in hits,
           "z_from_file": "z" in hits,
           "kind": hits.get("_kind", src)}
    if src == "live":
        out.update({"file": hits.get("_file"),
                    "parsed_msgs": hits.get("_parsed_msgs"),
                    "total_msgs": hits.get("_total_msgs")})
    return out


def evd_event(src, path, kind, efield, index, mode="window", min_hits=20,
              exclude=None):
    """Hits of one event, ready to draw (decimated above EVD_MAX_DRAW_HITS)."""
    hits = evd_hits(src, path, kind, exclude)
    if hits is None:
        return {"error": "no live raw file yet"}
    window_us = evd_window_us(efield)
    spans, order = _evd_events(hits, window_us, mode, int(min_hits))
    if not spans:
        return {"error": "no event passed the minimum-hits cut", "n_events": 0}
    index = max(0, min(int(index), len(spans) - 1))
    s, e = spans[index]
    step = max(1, (e - s) // EVD_MAX_DRAW_HITS)
    pick = order[s:e:step]                 # this event's hits, in event order
    full = order[s:e]
    t = hits["t_us"][pick]
    t0 = float(hits["t_us"][full].min())   # event start, however hits are ordered
    if "z" in hits:                       # reco already gave us a drift position
        z = np.asarray(hits["z"])[pick]
        z_src = "file"
    else:                                 # drift time -> mm at this E-field
        z = (t - t0) * evd_drift_velocity(efield) * 10.0
        z_src = "drift-time"
    return {"index": index, "n_events": len(spans), "z_source": z_src,
            "n_hits": int(e - s), "n_drawn": int(len(t)),
            "t_start_s": round(t0 * 1e-6, 4),
            "span_us": round(float(hits["t_us"][full].max() - t0), 2),
            "window_us": round(window_us, 2),
            "x": np.round(np.asarray(hits["x"])[pick], 2).tolist(),
            "y": np.round(np.asarray(hits["y"])[pick], 2).tolist(),
            "z": np.round(z, 2).tolist(),
            "q": np.round(np.asarray(hits["q"])[pick], 1).tolist(),
            "t_us": np.round(t - t0, 2).tolist(),
            "detector": evd_detector_bounds(efield)}


def evd_sources():
    """What the page can open right now: live run + newest file of each kind."""
    out = {"live": JOBS.latest_status_by_action().get("self_trigger") == "running",
           "files": {}}
    if not CTX.is_ready():
        return out
    for key, dirkey in (("raw", "raw_self_trigger"),
                        ("converted", "converted_self_trigger"),
                        ("clustered", "clustered_data")):
        try:
            files = sorted(glob.glob(os.path.join(CTX.d(dirkey), "*.h5")))
        except Exception:
            files = []
        out["files"][key] = {"dir": CTX.d(dirkey), "n": len(files),
                             "newest": files[-1] if files else None}
    return out


# ---------------------------------------------------------------------------
# Per-pixel threshold changes (queued; applied between run files)
# ---------------------------------------------------------------------------
def read_pixel_config(folder, chip, ch):
    """Current pixel_trim_dac / csa_enable for one channel from the config JSON."""
    f = find_asic_configs_file(folder, chip)
    if not f:
        return None
    with open(f) as fh:
        data = json.load(fh)
    rv = data.get("register_values", {})
    try:
        return {"file": f,
                "pixel_trim_dac": rv["pixel_trim_dac"][ch],
                "csa_enable": rv["csa_enable"][ch]}
    except (KeyError, IndexError):
        return None


def _apply_pixel_change(folder, entry, log):
    """Apply one queued change to the chip's ASIC config file."""
    chip, ch = entry["chip"], entry["ch"]
    action = entry["action"]
    shift = int(entry.get("shift", 0))
    f = find_asic_configs_file(folder, chip)
    if not f:
        log("[WARN] no config file for chip %s in %s -- skipped" % (chip, folder))
        return False
    try:
        # Preferred path: same API the existing threshold menu uses.
        from larpix import Configuration_v2
        config = Configuration_v2()
        config.load(f)
        if action == "disable":
            config.pixel_trim_dac[ch] = 31
            config.csa_enable[ch] = 0
        else:
            v = config.pixel_trim_dac[ch] + shift
            config.pixel_trim_dac[ch] = max(0, min(31, v))
        config.write(f, force=True)
        trim, csa = config.pixel_trim_dac[ch], config.csa_enable[ch]
    except ImportError:
        # Fallback: edit register_values in the JSON directly.
        with open(f) as fh:
            data = json.load(fh)
        rv = data["register_values"]
        if action == "disable":
            rv["pixel_trim_dac"][ch] = 31
            rv["csa_enable"][ch] = 0
        else:
            rv["pixel_trim_dac"][ch] = max(0, min(31, rv["pixel_trim_dac"][ch] + shift))
        with open(f, "w") as fh:
            json.dump(data, fh, indent=4)
        trim, csa = rv["pixel_trim_dac"][ch], rv["csa_enable"][ch]
    log("pixel %d-%d: %s -> trim=%s csa_enable=%s [%s]"
        % (chip, ch, action if action == "disable" else "trim %+d" % shift,
           trim, csa, os.path.basename(f)))
    return True


class PixelQueue:
    def __init__(self):
        self.items = []
        self.lock = threading.Lock()

    def add(self, entry):
        chip, ch = int(entry["chip"]), int(entry["ch"])
        if chip < 11 or chip > 110:
            raise ValueError("chip must be 11-110")
        if ch < 0 or ch > 63 or ch in NONROUTED_V2A_CHANNELS:
            raise ValueError("channel %d is not a routed pixel" % ch)
        action = entry.get("action")
        if action not in ("trim", "disable"):
            raise ValueError("action must be 'trim' or 'disable'")
        item = {"chip": chip, "ch": ch, "action": action,
                "shift": int(entry.get("shift", 0)),
                "queued_at": datetime.now().isoformat(timespec="seconds")}
        with self.lock:
            self.items.append(item)
        return item

    def list(self):
        with self.lock:
            return [dict(i, index=n) for n, i in enumerate(self.items)]

    def remove(self, index):
        with self.lock:
            if 0 <= index < len(self.items):
                return self.items.pop(index)
        return None

    def pending(self):
        with self.lock:
            return len(self.items)

    def apply(self, folder, log):
        """Drain the queue, applying each change. Returns number applied."""
        with self.lock:
            items, self.items = self.items, []
        applied = 0
        for entry in items:
            try:
                if _apply_pixel_change(folder, entry, log):
                    applied += 1
            except Exception as exc:
                log("[WARN] pixel %(chip)s-%(ch)s change failed: " % entry + str(exc))
        return applied


PIXEL_QUEUE = PixelQueue()


# ---------------------------------------------------------------------------
# Run database (SQLite; one row per self-trigger run file)
# ---------------------------------------------------------------------------
class RunDB:
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            raw_file TEXT UNIQUE,
            converted_file TEXT,
            clustered_file TEXT,
            hv TEXT,
            runtime_s REAL,
            messages INTEGER,
            avg_rate REAL,
            notes TEXT DEFAULT '',
            hot_pixels TEXT DEFAULT '[]',
            manual INTEGER DEFAULT 0
        )
    """
    # Filename run timestamp, e.g. ..._2026_06_14_00_13_31_MDT.h5
    TS_RE = re.compile(r"(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})")

    def __init__(self):
        self.lock = threading.Lock()

    def _connect(self):
        path = os.path.join(CTX.root, "run_db.sqlite")
        conn = sqlite3.connect(path)
        conn.execute(self.SCHEMA)
        # Idempotent migrations so existing run_db.sqlite files upgrade in place.
        for ddl in ("ALTER TABLE runs ADD COLUMN hot_pixels TEXT DEFAULT '[]'",
                    "ALTER TABLE runs ADD COLUMN manual INTEGER DEFAULT 0"):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
        return conn

    def _ready(self):
        return CTX.is_ready()

    def insert_run(self, raw_file, hv, runtime_s, messages, avg_rate):
        if not self._ready():
            return
        with self.lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO runs "
                    "(ts, raw_file, hv, runtime_s, messages, avg_rate) "
                    "VALUES (?,?,?,?,?,?)",
                    (datetime.now().isoformat(timespec="seconds"),
                     os.path.basename(raw_file), hv, runtime_s, messages, avg_rate))
                conn.commit()
            finally:
                conn.close()

    def link_converted(self, converted_basename):
        if not self._ready():
            return
        raw = converted_basename
        if raw.startswith("converted_"):
            raw = raw[len("converted_"):]
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("UPDATE runs SET converted_file=? WHERE raw_file=?",
                             (converted_basename, raw))
                conn.commit()
            finally:
                conn.close()

    def link_clustered(self, clustered_basename):
        if not self._ready():
            return
        conv = clustered_basename
        if conv.startswith("clustered_"):
            conv = conv[len("clustered_"):]
        raw = conv
        if raw.startswith("converted_"):
            raw = raw[len("converted_"):]
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("UPDATE runs SET clustered_file=? "
                             "WHERE raw_file=? OR converted_file=?",
                             (clustered_basename, raw, conv))
                conn.commit()
            finally:
                conn.close()

    def runtime_for_converted(self, converted_basename):
        if not self._ready():
            return None
        raw = converted_basename
        if raw.startswith("converted_"):
            raw = raw[len("converted_"):]
        with self.lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT runtime_s FROM runs WHERE raw_file=?",
                                   (raw,)).fetchone()
            finally:
                conn.close()
        return row[0] if row else None

    def rows(self):
        if not self._ready():
            return []
        cols = ["id", "ts", "raw_file", "converted_file", "clustered_file",
                "hv", "runtime_s", "messages", "avg_rate", "notes",
                "hot_pixels", "manual"]
        with self.lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT %s FROM runs ORDER BY id DESC" % ",".join(cols))
                out = []
                for r in cur.fetchall():
                    d = dict(zip(cols, r))
                    try:
                        d["hot_pixels"] = json.loads(d.get("hot_pixels") or "[]")
                    except (ValueError, TypeError):
                        d["hot_pixels"] = []
                    out.append(d)
                return out
            finally:
                conn.close()

    def find_by_file(self, basename):
        """Newest run row whose raw/converted/clustered file matches ``basename``."""
        if not self._ready() or not basename:
            return None
        with self.lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, raw_file, hot_pixels, hv FROM runs "
                    "WHERE raw_file=? OR converted_file=? OR clustered_file=? "
                    "ORDER BY id DESC LIMIT 1",
                    (basename, basename, basename)).fetchone()
            finally:
                conn.close()
        if not row:
            return None
        try:
            hot = json.loads(row[2] or "[]")
        except (ValueError, TypeError):
            hot = []
        return {"id": row[0], "label": row[1], "hot_pixels": hot, "hv": row[3]}

    def hv_near(self, iso_time):
        """HV of the run whose filename timestamp is nearest ``iso_time`` (ISO).

        Used to auto-derive a point's E-field from the DB. Only rows that carry
        a non-empty HV are considered.
        """
        if not self._ready() or not iso_time:
            return None
        try:
            target = datetime.fromisoformat(iso_time).timestamp()
        except (ValueError, TypeError):
            return None
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT raw_file, hv FROM runs WHERE hv IS NOT NULL "
                    "AND hv != ''").fetchall()
            finally:
                conn.close()
        best_hv, best_dt = None, None
        for raw_file, hv in rows:
            m = self.TS_RE.search(raw_file or "")
            if not m:
                continue
            try:
                t = datetime(*[int(g) for g in m.groups()]).timestamp()
            except ValueError:
                continue
            dt = abs(t - target)
            if best_dt is None or dt < best_dt:
                best_dt, best_hv = dt, hv
        return best_hv

    def set_hot(self, run_id, pixels):
        """Replace a run's hot-pixel list. Returns True if a row was updated."""
        if not self._ready():
            return False
        pixels = [str(p) for p in (pixels or [])]
        with self.lock:
            conn = self._connect()
            try:
                cur = conn.execute("UPDATE runs SET hot_pixels=? WHERE id=?",
                                   (json.dumps(pixels), int(run_id)))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def _ts_for(self, path):
        m = self.TS_RE.search(os.path.basename(path))
        if m:
            try:
                return datetime(*[int(g) for g in m.groups()]).isoformat(
                    timespec="seconds")
            except ValueError:
                pass
        try:
            return datetime.fromtimestamp(
                os.path.getmtime(path)).isoformat(timespec="seconds")
        except OSError:
            return datetime.now().isoformat(timespec="seconds")

    def add_files(self, path, kind="auto"):
        """Manually register a file, or every *.h5 in a folder, as run rows.

        Returns {added, skipped, errors}. Fast: no hit counting. Detects raw vs
        converted; converted files derive raw_file by stripping ``converted_``
        (matching the auto-linking convention).
        """
        if not self._ready():
            return {"added": 0, "skipped": 0, "errors": ["no descriptor set"]}
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isfile(path):
            files = [path]
        elif os.path.isdir(path):
            files = sorted(glob.glob(os.path.join(path, "*.h5")))
        else:
            return {"added": 0, "skipped": 0, "errors": ["path not found: %s" % path]}
        if not files:
            return {"added": 0, "skipped": 0, "errors": ["no .h5 files at: %s" % path]}

        added = skipped = 0
        errors = []
        with self.lock:
            conn = self._connect()
            try:
                for fp in files:
                    base = os.path.basename(fp)
                    k = kind
                    if k in (None, "auto"):
                        k = _detect_h5_kind(fp) or (
                            "converted" if base.startswith("converted_") else "raw")
                    if k == "converted":
                        raw = base[len("converted_"):] if base.startswith("converted_") else base
                        conv = base
                    else:
                        raw, conv = base, None
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO runs "
                        "(ts, raw_file, converted_file, manual) VALUES (?,?,?,1)",
                        (self._ts_for(fp), raw, conv))
                    if cur.rowcount > 0:
                        added += 1
                    else:
                        skipped += 1
                conn.commit()
            finally:
                conn.close()
        return {"added": added, "skipped": skipped, "errors": errors}

    def delete(self, run_id):
        if not self._ready():
            return False
        with self.lock:
            conn = self._connect()
            try:
                cur = conn.execute("DELETE FROM runs WHERE id=?", (int(run_id),))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def update(self, run_id, notes=None, hv=None):
        if not self._ready():
            return False
        sets, vals = [], []
        if notes is not None:
            sets.append("notes=?"); vals.append(notes)
        if hv is not None:
            sets.append("hv=?"); vals.append(hv)
        if not sets:
            return False
        vals.append(int(run_id))
        with self.lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE runs SET %s WHERE id=?" % ",".join(sets), vals)
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()


RUNDB = RunDB()


# ---------------------------------------------------------------------------
# Operation handlers (one per pipeline step, argvs preserved verbatim)
# ---------------------------------------------------------------------------
def op_check_power(job, p):
    command = ["python3", "check_power.py",
               "--pacman_tile", str(PACMAN_TILE), "--io_group", str(IO_GROUP)]
    run_streamed(job, command)


def op_make_hydra(job, p):
    command = ["python3", "-u", "map_uart_links_qc.py",
               "--pacman_tile", str(PACMAN_TILE), "--tile_id", str(TILE_ID),
               "--io_group", str(IO_GROUP)]
    start = time.time()
    if run_streamed(job, command)[0]:
        return
    move_file(job, "*hydra*.json", CTX.d("hydra_files"), start)


def op_plot_hydra(job, p):
    hydra = p["hydra_file"]
    command = ["python3", "plot_hydra_network_v2a.py",
               "--controller_config", hydra,
               "--geometry_yaml", GEOMETRY_YAML, "--io_group", str(IO_GROUP)]
    start = time.time()
    if run_streamed(job, command)[0]:
        return
    dest = move_file(job, "*hydra*.png",
                     os.path.join(CTX.d("hydra_files"), "plots"), start)
    job.add_plot(dest)


def op_trigger_rate(job, p):
    hydra = p["hydra_file"]
    command = ["python3", "multi_trigger_rate_qc.py", "--controller_config", hydra]
    if CTX.cryo_flag:
        command.append("--cryo")
    start = time.time()
    if run_streamed(job, command)[0]:
        return
    move_file(job, "*DO-NOT-ENABLE*.json", CTX.d("trigger_rate_disabled"), start)


def op_pedestal(job, p):
    hydra = p["hydra_file"]
    trig = p["trigger_rate_file"]
    runtime = int(p["runtime"])
    command = ["python3", "pedestal_qc.py",
               "--controller_config", hydra,
               "--disabled_list", trig, "--runtime", str(runtime)]
    start = time.time()
    if run_streamed(job, command)[0]:
        return
    move_file(job, "*pedestal*DO-NOT-ENABLE*.h5", CTX.d("pedestal_donotenable"), start)
    move_file(job, "*pedestal-disabled*first*.json", CTX.d("pedestal_first"), start)
    move_file(job, "*pedestal-disabled*second*.json", CTX.d("pedestal_second"), start)
    move_file(job, "*recursive-pedestal*.h5", CTX.d("pedestal_runs"), start)


def op_plot_disabled(job, p):
    trig = p["trigger_rate_file"]
    ped = p["pedestal_disabled_file"]
    command = ["python3", "plot_xy_disabled_channel.py",
               "--pedestal_disabled", ped, "--trigger_disabled", trig]
    start = time.time()
    if run_streamed(job, command)[0]:
        return
    dest = move_file(job, "disabled-xy-map-tile-id*.png",
                     CTX.d("disabled_channels"), start)
    job.add_plot(dest)


def op_find_thresholds(job, p):
    hydra = p["hydra_file"]
    ped_disabled = p["pedestal_disabled_file"]
    ped_run = p["pedestal_run_file"]
    command = ["python3", "threshold_qc.py",
               "--controller_config", hydra,
               "--disabled_list", ped_disabled,
               "--pedestal_file", ped_run]
    if CTX.cryo_flag:
        command.append("--cryo")
    start = time.time()
    if run_streamed(job, command)[0]:
        return
    config_files = find_list_of_files(".", start)
    job.log("config_file_list=%r" % config_files)
    if not config_files:
        job.log("No config files were produced.")
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_dir = os.path.join(CTX.d("asic_configs"), "asic_configs_" + timestamp)
    os.makedirs(new_dir, exist_ok=True)
    for cf in config_files:
        shutil.move(cf, os.path.join(new_dir, os.path.basename(cf)))
    job.log("ASIC config files moved to %s" % new_dir)


def op_self_trigger(job, p):
    hydra = p["hydra_file"]
    folder = p["asic_config_folder"]
    runtime = int(p["runtime"])
    repeat = bool(p.get("repeat"))
    hv = str(p.get("hv", "") or "")
    command = ["python3", "start_run_log_raw.py",
               "--controller_config", hydra,
               "--config_name", folder, "--runtime", str(runtime)]
    while True:
        with open(DG_HEARTBEAT_FILE, "w") as fh:
            fh.write("True")
        start = time.time()
        run_streamed(job, command)
        dest = move_file(job, "*raw*.h5", CTX.d("raw_self_trigger"), start,
                         add_ts=False)
        if dest:
            try:
                record_self_trigger(dest, runtime)
                job.log("Recorded self-trigger metric for %s" % os.path.basename(dest))
            except Exception as exc:
                job.log("[WARN] metric record failed: %s" % exc)
            try:
                counts = count_h5(dest, "self_trigger") or {}
                messages = counts.get("messages")
                avg_rate = (round(messages / float(runtime), 3)
                            if messages is not None and runtime else None)
                RUNDB.insert_run(dest, hv, runtime, messages, avg_rate)
                job.log("Run recorded in database (HV=%s)" % (hv or "-"))
            except Exception as exc:
                job.log("[WARN] run DB insert failed: %s" % exc)
        # Apply any queued pixel threshold changes BETWEEN run files, then
        # restart the run (even in single-run mode) so the new configs take.
        applied = 0
        if PIXEL_QUEUE.pending() and not job.stop_event.is_set():
            job.log("Applying %d queued pixel change(s) to %s ..."
                    % (PIXEL_QUEUE.pending(), folder))
            applied = PIXEL_QUEUE.apply(folder, job.log)
            if applied:
                job.log("Pixel changes applied -- restarting run with updated configs.")
        if job.stop_event.is_set():
            break
        if not repeat and not applied:
            break
        job.log("Waiting 3 seconds before running again... press Stop to halt.")
        if job.stop_event.wait(3):
            break


def _convert_one(job, raw_path, conv_path):
    command = ["python3", os.path.join(CONVERTER_DIR, "convert_rawhdf5_to_hdf5.py"),
               "-i", raw_path, "-o", conv_path]
    code, wall = run_streamed(job, command)
    if code == 0 and os.path.exists(conv_path):
        job.log("Converted file moved to %s" % conv_path)
        try:
            record_convert(conv_path, wall)
            job.log("Recorded conversion metric for %s" % os.path.basename(conv_path))
        except Exception as exc:
            job.log("[WARN] metric record failed: %s" % exc)


def op_convert(job, p):
    raw_dir = CTX.d("raw_self_trigger")
    conv_dir = CTX.d("converted_self_trigger")
    if p.get("mode") == "single":
        raw_path = p["raw_file"]
        conv_path = os.path.join(conv_dir, "converted_" + os.path.basename(raw_path))
        _convert_one(job, raw_path, conv_path)
        return
    # mode == "all": watch loop (skip already-processed / unreadable files)
    while True:
        raw_files = [f for f in os.listdir(raw_dir) if f.endswith(".h5")]
        todo = []
        for name in raw_files:
            raw_path = os.path.join(raw_dir, name)
            conv_path = os.path.join(conv_dir, "converted_" + name)
            if os.path.exists(conv_path):
                continue
            if h5py is not None:
                try:
                    with h5py.File(raw_path, "r") as f:
                        f["msgs"], f["msg_headers"]
                except Exception:
                    continue
            todo.append((raw_path, conv_path))
        if not todo:
            job.log("No new raw files found, waiting 60 seconds before checking again")
            if job.stop_event.wait(60):
                break
            continue
        for raw_path, conv_path in todo:
            if job.stop_event.is_set():
                break
            _convert_one(job, raw_path, conv_path)
        if job.stop_event.is_set():
            break


def op_plot_metrics(job, p):
    file_type = p["file_type"]   # 'p' or 's'
    src_dir = CTX.d("raw_self_trigger") if file_type == "s" else CTX.d("pedestal_runs")
    if p.get("which") == "latest":
        chosen, _ = get_latest_file("*.h5", directory=src_dir)
        if chosen is None:
            job.log("No files found in %s" % src_dir)
            return
        chosen = str(chosen)
    else:
        chosen = p["raw_file"]
    if not os.path.exists(chosen):
        job.log("Chosen file does not exist: %s" % chosen)
        return
    command = ["python3", "./larpix-monitor/run_monitor.py", "--once", chosen]
    run_streamed(job, command)


def op_clustering(job, p):
    conv_dir = CTX.d("converted_self_trigger")
    clustered_dir = CTX.d("clustered_data")

    def cluster_one(in_path):
        out_path = os.path.join(clustered_dir,
                                "clustered_" + os.path.basename(in_path))
        command = ["python3", os.path.join(CLUSTERING_DIR, "charge_clustering.py"),
                   "SingleCube", in_path, out_path, "--save_hits=True"]
        code, wall = run_streamed(job, command)
        if code == 0 and os.path.exists(out_path):
            job.log("Clustered file moved to %s" % out_path)
            try:
                record_clustering(out_path, wall)
                job.log("Recorded clustering metric for %s" % os.path.basename(out_path))
            except Exception as exc:
                job.log("[WARN] metric record failed: %s" % exc)

    mode = p.get("mode", "single")
    if mode == "single":
        cluster_one(p["converted_file"])
    elif mode == "multiple":
        for f in p.get("files", []):
            if job.stop_event.is_set():
                break
            cluster_one(f)
    else:  # "all": watch loop
        while True:
            h5_files = [f for f in os.listdir(conv_dir) if f.endswith(".h5")]
            todo = []
            for name in h5_files:
                in_path = os.path.join(conv_dir, name)
                out_path = os.path.join(clustered_dir, "clustered_" + name)
                if os.path.exists(out_path):
                    continue
                if h5py is not None:
                    try:
                        with h5py.File(in_path, "r") as f:
                            f["packets"]
                    except Exception:
                        continue
                todo.append(in_path)
            if not todo:
                job.log("No new converted files found, waiting 60 seconds before "
                        "checking again")
                if job.stop_event.wait(60):
                    break
                continue
            for in_path in todo:
                if job.stop_event.is_set():
                    break
                cluster_one(in_path)
            if job.stop_event.is_set():
                break


# ---------------------------------------------------------------------------
# Electron-lifetime (purity) vs time -- runs lifetime_vs_tracks.py (which reuses
# quick_purity.py), one point per N anode-cathode tracks.
# ---------------------------------------------------------------------------
def find_lifetime_script():
    # Prefer the external repo copies (canonical, may be freshly edited on the
    # DAQ machine); fall back to the copy vendored into this app so a bare
    # download still works.
    candidates = [
        os.path.join(CLUSTERING_DIR, "CATS_analysis", "lifetime",
                     "lifetime_vs_tracks.py"),
        os.path.join(REPO_LIFETIME_DIR, "lifetime_vs_tracks.py"),
        os.path.join(ANALYSIS_DIR, "lifetime_vs_tracks.py"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return None


_PURITY_CORE = "unset"


def _purity_core():
    """Lazy import of purity_core from next to lifetime_vs_tracks.py."""
    global _PURITY_CORE
    if _PURITY_CORE != "unset":
        return _PURITY_CORE
    script = find_lifetime_script()
    try:
        if script:
            d = os.path.dirname(script)
            if d not in sys.path:
                sys.path.insert(0, d)
        import purity_core
        _PURITY_CORE = purity_core
    except Exception:
        _PURITY_CORE = None
    return _PURITY_CORE


def _lt_dir():
    return os.path.join(CTX.root, "lifetime")


def _lt_paths(series):
    """File paths for one lifetime series ('main' or 'overlay')."""
    pre = "" if series == "main" else "overlay_"
    d = _lt_dir()
    return {
        "series": os.path.join(d, pre + "series.json"),
        "tracks": os.path.join(d, pre + "tracks.npz"),
        "exclusions": os.path.join(d, pre + "exclusions.json"),
        "excluded_points": os.path.join(d, pre + "excluded_points.json"),
        "hot_pixels": os.path.join(d, pre + "hot_pixels.json"),
        "meta": os.path.join(d, pre + "meta.json"),
        "cache": os.path.join(d, pre + "track_cache.sqlite"),
        "prefix": os.path.join(d, pre + "lifetime_vs_tracks"),
    }


def _load_exclusions(series):
    """{point(int): set(seq)} for one series."""
    path = _lt_paths(series)["exclusions"]
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            raw = json.load(f)
        return {int(k): set(v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_exclusions(series, excl):
    path = _lt_paths(series)["exclusions"]
    data = {str(k): sorted(v) for k, v in excl.items() if v}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# which run dirs each DB file column can live in (first hit wins)
RUNDB_FILE_DIRS = {
    "raw_file": ("raw_self_trigger",),
    "converted_file": ("converted_self_trigger", "converted_data"),
    "clustered_file": ("clustered_data",),
}
RUNDB_KIND_COLS = {
    "raw": ("raw_file",),
    "converted": ("converted_file",),
    "clustered": ("clustered_file",),
    # "auto" prefers the processed file, falling back to the raw one
    "auto": ("converted_file", "raw_file", "clustered_file"),
}


def _rundb_resolve(row, kind="auto"):
    """(abs_path, column, exists) for a DB row's file of the requested kind.

    The database stores basenames only, so this maps each column back onto the
    run directory it is written to. Returns (None, None, False) when the row has
    no file of that kind at all.
    """
    for col in RUNDB_KIND_COLS.get(kind, RUNDB_KIND_COLS["auto"]):
        name = (row.get(col) or "").strip()
        if not name:
            continue
        for dirkey in RUNDB_FILE_DIRS[col]:
            try:
                p = os.path.join(CTX.d(dirkey), name)
            except Exception:
                continue
            if os.path.isfile(p):
                return os.path.abspath(p), col, True
        # remember the first named-but-missing candidate so the UI can say so
        try:
            p = os.path.join(CTX.d(RUNDB_FILE_DIRS[col][0]), name)
        except Exception:
            p = name
        return os.path.abspath(p), col, False
    return None, None, False


def rundb_files(kind="auto"):
    """Ordered list of the database's runs as loadable files, oldest first.

    Oldest-first so that "next" on the pixel map walks forward through the
    run in time, which is how the data was taken.
    """
    out = []
    for row in RUNDB.rows():
        path, col, exists = _rundb_resolve(row, kind)
        if path is None:
            continue
        out.append({"id": row.get("id"), "ts": row.get("ts"),
                    "name": os.path.basename(path), "path": path,
                    "column": col, "exists": exists, "hv": row.get("hv"),
                    "n_hot": len(row.get("hot_pixels") or [])})
    out.sort(key=lambda r: (r["ts"] or "", r["id"] or 0))
    return {"files": out, "n": len(out), "kind": kind,
            "n_missing": sum(1 for r in out if not r["exists"])}


def build_hot_pixel_map(files):
    """{basename: ["chip-ch", ...]} of hot pixels for each file, from the run DB.

    Per-file: a file inherits only the pixels tagged on its own run row, so a
    pixel tagged in one run never silently filters another. Files with no row (or
    no tags) are omitted, and the pipeline parses them unfiltered.
    """
    out = {}
    for path in files:
        name = os.path.basename(path)
        try:
            row = RUNDB.find_by_file(name)
        except Exception:
            row = None
        hot = (row or {}).get("hot_pixels") or []
        if hot:
            out[name] = sorted(str(k) for k in hot)
    return out


def _load_excluded_points(series):
    """set(point_idx) of whole points dropped from the plot (0-based)."""
    path = _lt_paths(series)["excluded_points"]
    if not os.path.exists(path):
        return set()
    try:
        with open(path) as f:
            return {int(x) for x in json.load(f)}
    except Exception:
        return set()


def _save_excluded_points(series, pts):
    path = _lt_paths(series)["excluded_points"]
    with open(path, "w") as f:
        json.dump(sorted(int(x) for x in pts), f, indent=2)


def hv_to_efield(hv_text):
    """Parse a free-text HV (e.g. '27.5 kV', '15000') -> E-field [V/cm], or None."""
    if hv_text is None:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", str(hv_text))
    if not m:
        return None
    try:
        return abs(float(m.group())) * HV_TO_EFIELD
    except ValueError:
        return None


def _efield_overrides_path(series):
    return _lt_paths(series)["cache"].replace("track_cache.sqlite",
                                              "efield_overrides.json")


def _load_efield_overrides(series):
    """{point(int): efield(float)} manual overrides for one series."""
    path = _efield_overrides_path(series)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return {int(k): float(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def _save_efield_overrides(series, ov):
    path = _efield_overrides_path(series)
    with open(path, "w") as f:
        json.dump({str(k): v for k, v in ov.items() if v}, f, indent=2)


def effective_efield(series, point_idx, default_efield, point_time=None,
                     overrides=None):
    """Resolve a point's E-field: manual override -> run-DB HV -> compute default.

    Returns (efield, source) where source is 'manual', 'db_hv (<hv>)', or 'default'.
    """
    ov = _load_efield_overrides(series) if overrides is None else overrides
    if point_idx in ov:
        return float(ov[point_idx]), "manual"
    if point_time:
        hv = RUNDB.hv_near(point_time)
        e = hv_to_efield(hv)
        if e is not None:
            return e, "db_hv (%s)" % hv
    return float(default_efield), "default"


class TrackStore:
    """Reads a tracks.npz (mtime-cached) and refits points minus exclusions.

    The refit reproduces ``fit_group()`` in lifetime_vs_tracks.py exactly:
    same LifetimeAccumulator config, same profile cap, same fit.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self._cache = {}        # series -> (mtime, data-dict)
        self._refits = {}       # (series, mtime, point, frozenset(excl)) -> fit

    def invalidate(self):
        """Drop in-memory caches (call after tracks.npz is rewritten)."""
        with self.lock:
            self._cache.clear()
            self._refits.clear()

    def _load(self, series):
        path = _lt_paths(series)["tracks"]
        if np is None or not os.path.exists(path):
            return None
        mtime = os.path.getmtime(path)
        with self.lock:
            cached = self._cache.get(series)
            if cached and cached[0] == mtime:
                return cached[1]
        d = np.load(path)
        data = {k: d[k] for k in d.files}
        data["_mtime"] = mtime
        with self.lock:
            self._cache[series] = (mtime, data)
        return data

    def point_track_indices(self, data, point):
        return np.where(data["group"] == int(point))[0]

    def point_tracks(self, series, point, max_hits=500):
        """Track list for one series point, hits decimated for display."""
        data = self._load(series)
        if data is None:
            return None
        core = _purity_core()
        efield = float(data["efield"])
        if core is not None:
            v_drift = core.drift_params(efield)[0]      # cm/us
        else:
            v_drift = 0.1544
        excl = _load_exclusions(series).get(int(point), set())
        out = []
        off = data["offsets"]
        for i in self.point_track_indices(data, point):
            s, e = int(off[i]), int(off[i + 1])
            step = max(1, (e - s) // max_hits)
            sl = slice(s, e, step)
            drift = data["hits_drift_us"][sl]
            out.append({
                "seq": int(data["seq"][i]),
                "n_hits": int(e - s),
                "span_us": float(data["span_us"][i]),
                "dxy_mm": float(data["dxy_mm"][i]),
                "excluded": int(data["seq"][i]) in excl,
                "x": np.round(data["hits_x"][sl], 2).tolist(),
                "y": np.round(data["hits_y"][sl], 2).tolist(),
                "z": np.round(drift * v_drift * 10.0, 2).tolist(),   # mm
                "q": np.round(data["hits_q"][sl], 1).tolist(),
            })
        return out

    def _build_point(self, data, point, excluded_seqs, efield):
        """Build the LifetimeAccumulator + fit for one point at a given E-field.

        Returns (acc, centers, med, err, full_fit, used) or None. This is the
        single source of truth reused by refit() and point_plots().
        """
        core = _purity_core()
        if data is None or core is None:
            return None
        drift_vel, drift_time_max_us, _ = core.drift_params(efield)
        acc = core.LifetimeAccumulator(charge_scale=core.GAIN_E_PER_ADC / 1000.0,
                                       drift_time_max_us=drift_time_max_us,
                                       v_drift=drift_vel)
        off = data["offsets"]
        used = 0
        for i in self.point_track_indices(data, point):
            if int(data["seq"][i]) in excluded_seqs:
                continue
            s, e = int(off[i]), int(off[i + 1])
            if acc.add_track(data["hits_drift_us"][s:e].astype("f8"),
                             data["hits_q"][s:e].astype("f8"),
                             float(data["dxy_mm"][i]), float(data["span_us"][i])):
                used += 1
        if used < 2:
            return acc, None, None, None, None, used
        centers, med, err = acc.profile(cap=core.DQDX_CAP_KE_PER_CM)
        try:
            fit = core.fit_lifetime(centers, med, err,
                                    drift_time_max_us=acc.drift_time_max_us)
        except Exception:
            fit = None
        return acc, centers, med, err, fit, used

    def refit(self, series, point, excluded_seqs, efield=None):
        """Refit one point (minus ``excluded_seqs``) at ``efield`` (default: the
        E-field the series was computed at). Returns fit dict or None."""
        data = self._load(series)
        if data is None:
            return None
        ef = float(efield) if efield else float(data["efield"])
        key = (series, data["_mtime"], int(point), frozenset(excluded_seqs),
               round(ef, 3))
        with self.lock:
            if key in self._refits:
                return self._refits[key]
        built = self._build_point(data, point, excluded_seqs, ef)
        fit = None
        if built is not None:
            _, _, _, _, f, used = built
            if f is not None:
                # Null out any non-finite fit value: the MINOS bounds can be inf
                # (unbounded side) and a degenerate covariance can be nan, and
                # either one becomes invalid JSON downstream if left as a float.
                def _fin(v):
                    return v if (v is not None and np.isfinite(v)) else None
                fit = {"tau_ms": _fin(f["tau_ms"]),
                       "tau_err_pos_ms": _fin(f["tau_err_pos_ms"]),
                       "tau_err_neg_ms": _fin(f["tau_err_neg_ms"]),
                       "n_tracks": used, "efield": ef}
        with self.lock:
            self._refits[key] = fit
        return fit

    def point_plots(self, series, point, excluded_seqs, efield):
        """Generate the per-point fit + dQ/dx-2D PNGs (cached). Returns
        {fit_png, dqdx2d_png} of runs/-relative paths, or {'error': ...}."""
        core = _purity_core()
        data = self._load(series)
        if core is None:
            return {"error": "purity_core not importable on this machine"}
        if data is None:
            return {"error": "no track data (recompute the series)"}
        ef = float(efield) if efield else float(data["efield"])
        pre = "" if series == "main" else "overlay_"
        fit_png = os.path.join(_lt_dir(), "%spoint%d_fit.png" % (pre, point))
        two_png = os.path.join(_lt_dir(), "%spoint%d_2d.png" % (pre, point))
        built = self._build_point(data, point, excluded_seqs, ef)
        if built is None:
            return {"error": "could not build this point"}
        acc, centers, med, err, fit, used = built
        if fit is None:
            return {"error": "too few tracks to fit (%d)" % used}
        try:
            core.plot_lifetime(centers, med, err, fit, fit_png,
                               title="Point %d fit  (E = %g V/cm, %d tracks)"
                               % (point + 1, ef, used),
                               ylabel="Median dQ/dx [ke-/cm]",
                               drift_time_max_us=acc.drift_time_max_us)
            core.plot_dqdx_2d(acc, two_png, fit=fit,
                              title="Point %d  dQ/dx vs drift time" % (point + 1),
                              ylabel="dQ/dx [ke-/cm]",
                              drift_time_max_us=acc.drift_time_max_us)
        except Exception as exc:
            return {"error": "plot generation failed: %s" % exc}
        return {"fit_png": os.path.relpath(fit_png),
                "dqdx2d_png": os.path.relpath(two_png),
                "efield": ef, "n_tracks": used, "tau_ms": fit["tau_ms"]}


TRACKS = TrackStore()


def op_lifetime(job, p):
    script = find_lifetime_script()
    if not script:
        job.log("[ERROR] lifetime_vs_tracks.py not found (looked in the clustering "
                "dir and the local repo copy).")
        job.had_error = True
        return
    series = p.get("series", "main")
    source = p.get("source", "converted")
    if source == "raw":
        pattern, converted_flag = "*raw*.h5", []
    else:
        pattern, converted_flag = "*.h5", ["--converted"]
    if series == "overlay":
        in_dir = p.get("folder", "")
        if not in_dir or not os.path.isdir(in_dir):
            job.log("[ERROR] Overlay folder not found: %r" % in_dir)
            job.had_error = True
            return
    else:
        in_dir = CTX.d("raw_self_trigger") if source == "raw" \
            else CTX.d("converted_self_trigger")
    files = sorted(os.path.abspath(f) for f in glob.glob(os.path.join(in_dir, pattern)))
    if not files:
        job.log("[ERROR] No %s .h5 files found in %s" % (source, in_dir))
        job.had_error = True
        return
    efield = str(p.get("efield", 500))
    bin_size = str(int(p.get("bin_size", 100)))
    group_by = "file" if p.get("group_by") == "file" else "tracks"
    file_seconds = str(p.get("file_seconds", 0) or 0)
    os.makedirs(_lt_dir(), exist_ok=True)
    paths = _lt_paths(series)
    command = [sys.executable, script, *files, *converted_flag,
               "-E", efield, "--bin_size", bin_size,
               "--group_by", group_by,
               "--file_seconds", file_seconds,
               "--cache", os.path.abspath(paths["cache"]),
               "--json", os.path.abspath(paths["series"]),
               "--save_tracks", os.path.abspath(paths["tracks"]),
               "-o", os.path.abspath(paths["prefix"])]
    # Drop each file's own hot pixels (as tagged in the run DB) before clustering,
    # so a noisy pixel can't pull DBSCAN clusters or bias the dQ/dx fit.
    hot_map = build_hot_pixel_map(files)
    if hot_map:
        try:
            with open(paths["hot_pixels"], "w") as fh:
                json.dump(hot_map, fh, indent=2)
            command += ["--exclude_pixels", os.path.abspath(paths["hot_pixels"])]
            job.log("Hot-pixel exclusions: %d of %d file(s) have tagged pixels "
                    "(%d pixel-tags total)."
                    % (len(hot_map), len(files),
                       sum(len(v) for v in hot_map.values())))
        except OSError as exc:
            job.log("[WARN] could not write hot-pixel map (%s); continuing "
                    "without exclusions." % exc)
    binning = ("one point per file/run" if group_by == "file"
               else "%s AC tracks/point" % bin_size)
    job.log("Running lifetime pipeline (%s series) over %d %s file(s) "
            "(%s, E=%s V/cm)... already-parsed files load from "
            "the cache." % (series, len(files), source, binning, efield))
    code, _ = run_streamed(job, command, cwd=os.path.dirname(script))
    if code == 0 and series == "overlay":
        try:
            with open(paths["meta"], "w") as f:
                json.dump({"label": in_dir}, f)
        except Exception:
            pass
    # Note: exclusions are intentionally preserved across recomputes -- adding
    # a new (later-timestamped) run appends tracks/points without shifting the
    # existing seq/point identities, so prior track exclusions still apply. The
    # TrackStore cache is invalidated automatically because tracks.npz's mtime
    # changed. A stale exclusion for a seq that no longer exists is ignored.
    TRACKS.invalidate()


def _series_payload(series):
    """Load one series' JSON and apply exclusion refits to its points."""
    paths = _lt_paths(series)
    if not os.path.exists(paths["series"]):
        return None
    try:
        with open(paths["series"]) as f:
            data = json.load(f)
    except Exception:
        return None
    excl = _load_exclusions(series)
    dropped = _load_excluded_points(series)
    ov = _load_efield_overrides(series)
    default_ef = float((data.get("meta") or {}).get("efield", 500) or 500)
    for pt in data.get("points", []):
        point_idx = int(pt.get("point", 0)) - 1     # JSON is 1-based
        if point_idx in dropped:
            pt["point_excluded"] = True             # dropped from the plot
            continue                                # skip refit; keep stored tau
        seqs = excl.get(point_idx, set())
        ef, src = effective_efield(series, point_idx, default_ef,
                                   point_time=pt.get("time"), overrides=ov)
        pt["efield_used"] = round(ef, 2)
        pt["efield_source"] = src
        # Refit when the effective E-field differs from the compute default, or
        # when tracks were excluded; otherwise keep the JSON's stored fit.
        need_refit = seqs or abs(ef - default_ef) > 1e-6
        if not need_refit:
            continue
        if seqs:
            pt["n_excluded"] = len(seqs)
        pt["tau_ms_orig"] = pt.get("tau_ms")
        refit = TRACKS.refit(series, point_idx, seqs, efield=ef)
        if refit is None:
            pt["fit_failed"] = True
        else:
            pt["tau_ms"] = refit["tau_ms"]
            pt["tau_err_pos_ms"] = refit["tau_err_pos_ms"]
            pt["tau_err_neg_ms"] = refit["tau_err_neg_ms"]
            pt["n_tracks"] = refit["n_tracks"]
    if series == "overlay":
        label = None
        if os.path.exists(paths["meta"]):
            try:
                with open(paths["meta"]) as f:
                    label = json.load(f).get("label")
            except Exception:
                pass
        data["label"] = label
    return data


def lifetime_series():
    """Both series (main + optional overlay) + compute status."""
    running = JOBS.latest_status_by_action().get("lifetime") == "running"
    payload = {"running": running, "descriptor": CTX.descriptor,
               "main": None, "overlay": None}
    if CTX.is_ready():
        payload["main"] = _series_payload("main")
        payload["overlay"] = _series_payload("overlay")
    if payload["main"] is None:
        payload["main"] = {"points": [], "meta": None}
    return payload


def _point_context(series, point):
    """(excluded_seqs, effective_efield, default_efield, point_info, source)."""
    point = int(point)
    data = TRACKS._load(series)
    default_ef = float(data["efield"]) if data is not None else 500.0
    seqs = _load_exclusions(series).get(point, set())
    orig = None
    for pt in (_series_payload(series) or {}).get("points", []):
        if int(pt.get("point", 0)) - 1 == point:
            orig = pt
            break
    ptime = orig.get("time") if orig else None
    ef, src = effective_efield(series, point, default_ef, point_time=ptime)
    return seqs, ef, default_ef, orig, src


def lifetime_pointmeta(series, point):
    """E-field context for a point (for the modal controls)."""
    seqs, ef, default_ef, orig, src = _point_context(series, point)
    db_hv = RUNDB.hv_near(orig.get("time")) if orig else None
    return {"series": series, "point": int(point),
            "efield_default": round(default_ef, 3),
            "efield_effective": round(ef, 3),
            "efield_source": src,
            "has_override": int(point) in _load_efield_overrides(series),
            "db_hv": db_hv, "db_efield": (round(hv_to_efield(db_hv), 3)
                                          if hv_to_efield(db_hv) else None),
            "hv_to_efield_factor": HV_TO_EFIELD}


def lifetime_tracks_payload(series, point):
    tracks = TRACKS.point_tracks(series, point)
    if tracks is None:
        return {"error": "no track data for this series (recompute to enable "
                         "the event display)"}
    seqs, ef, default_ef, orig, src = _point_context(series, point)
    fit = TRACKS.refit(series, point, seqs, efield=ef) \
        if (seqs or abs(ef - default_ef) > 1e-6) else None
    return {"series": series, "point": int(point), "tracks": tracks,
            "point_info": orig, "refit": fit, "n_excluded": len(seqs),
            "efield_used": round(ef, 3), "efield_source": src}


def lifetime_set_exclusion(series, point, seq, excluded):
    excl = _load_exclusions(series)
    point = int(point)
    seqs = excl.setdefault(point, set())
    if excluded:
        seqs.add(int(seq))
    else:
        seqs.discard(int(seq))
    _save_exclusions(series, excl)
    seqs = excl.get(point, set())
    _, ef, default_ef, _, _ = _point_context(series, point)
    need = seqs or abs(ef - default_ef) > 1e-6
    refit = TRACKS.refit(series, point, seqs, efield=ef) if need else "orig"
    return {"ok": True, "point": point, "n_excluded": len(seqs),
            "refit": (None if refit == "orig" else refit),
            "restored": refit == "orig",
            "fit_failed": need and refit is None}


def lifetime_set_point_excluded(series, point, excluded):
    """Drop / restore an entire point from the plot (0-based point index)."""
    point = int(point)
    dropped = _load_excluded_points(series)
    if excluded:
        dropped.add(point)
    else:
        dropped.discard(point)
    _save_excluded_points(series, dropped)
    return {"ok": True, "point": point, "excluded": point in dropped,
            "n_excluded_points": len(dropped)}


def lifetime_set_efield(series, point, efield=None, hv=None):
    """Set/clear a point's manual E-field override (hv converts via the factor)."""
    point = int(point)
    ov = _load_efield_overrides(series)
    if efield in (None, "") and hv in (None, ""):
        ov.pop(point, None)                       # clear -> back to auto/default
    else:
        ef = float(efield) if efield not in (None, "") else hv_to_efield(hv)
        if ef is None or ef <= 0:
            return {"error": "could not determine an E-field from that input"}
        ov[point] = float(ef)
    _save_efield_overrides(series, ov)
    seqs, ef, default_ef, _, src = _point_context(series, point)
    refit = TRACKS.refit(series, point, seqs, efield=ef)
    return {"ok": True, "point": point, "efield_used": round(ef, 3),
            "efield_source": src, "refit": refit}


def op_thresholds(job, p):
    """Raise/lower thresholds -- ports the in-process Configuration_v2 logic."""
    folder = p["asic_config_folder"]
    asic_config_files = glob.glob(os.path.join(folder, "*"))
    sub = int(p["sub_option"])

    if sub == 1:
        inc = str(p["inc"])
        command = ["python3", "increment_global.py", *asic_config_files, "--inc", inc]
        job.log("Changing global threshold by %s" % inc)
        run_streamed(job, command)
        return

    from larpix import Configuration_v2  # noqa: F401 (import path preserved)
    config = Configuration_v2()

    if sub == 2:
        chip = int(p["chip_id"])
        if chip > 110 or chip < 11:
            job.log("Invalid chip_id, must be >10 and <111")
            return
        f = find_asic_configs_file(folder, chip)
        if not f:
            job.log("No asic config file found for chip id %s" % chip)
            return
        config.load(f)
        shift = int(p["inc"])
        if config.threshold_global + shift >= 255:
            config.threshold_global = 255
        else:
            config.threshold_global += shift
        config.write(f, force=True)
        job.log("loaded %s" % f)
        job.log("set chip threshold to %s" % config.threshold_global)

    elif sub == 3:
        combos = p["channels"]            # e.g. "11-0,110-55"
        option = int(p["option"])         # 1 trim shift, 2 disable, 3 enable
        chip_ids, channel_ids = [], []
        for combo in combos.split(","):
            parts = combo.split("-")
            if len(parts) != 2:
                job.log("Invalid format: %r" % combo)
                continue
            chip_ids.append(int(parts[0]))
            channel_ids.append(int(parts[1]))
        if any(c > 110 or c < 11 for c in chip_ids):
            job.log("Invalid chip_id, must be >10 and <111")
            return
        if any(c > 63 for c in channel_ids):
            job.log("Invalid channel_id, must be < 64")
            return
        shift = int(p.get("inc", 0))
        for chip, chan in zip(chip_ids, channel_ids):
            f = find_asic_configs_file(folder, chip)
            if not f:
                job.log("No asic config file found for chip id %s" % chip)
                continue
            config.load(f)
            if option == 1:
                if config.pixel_trim_dac[chan] + shift >= 32:
                    config.pixel_trim_dac[chan] = 31
                else:
                    config.pixel_trim_dac[chan] += shift
                config.write(f, force=True)
                job.log("loaded %s -> trim[%d]=%s"
                        % (f, chan, config.pixel_trim_dac[chan]))
            elif option in (2, 3):
                config.csa_enable[chan] = 0 if option == 2 else 1
                config.write(f, force=True)
                job.log("loaded %s -> csa_enable[%d]=%s"
                        % (f, chan, config.csa_enable[chan]))
            else:
                job.log("Unrecognized option")

    elif sub == 4:
        chan = int(p["channel_id"])
        if chan > 63:
            job.log("Invalid channel_id, must be < 64")
            return
        option = int(p["option"])         # 1 disable-all, 2 enable-all, 3 trim shift
        shift = int(p.get("inc", 0))
        for chip in range(11, 111):
            f = find_asic_configs_file(folder, chip)
            if not f:
                continue
            config.load(f)
            if option == 1:
                config.csa_enable[chan] = 0
            elif option == 2:
                config.csa_enable[chan] = 1
            elif option == 3:
                if config.pixel_trim_dac[chan] + shift >= 32:
                    config.pixel_trim_dac[chan] = 31
                elif config.pixel_trim_dac[chan] + shift <= 0:
                    config.pixel_trim_dac[chan] = 0
                else:
                    config.pixel_trim_dac[chan] += shift
            config.write(f, force=True)
            job.log("chip %d updated" % chip)
        job.log("Done updating channel %d across all chips" % chan)
    else:
        job.log("Unrecognized sub-option %s" % sub)


ACTIONS = {
    "check_power": op_check_power,
    "make_hydra": op_make_hydra,
    "plot_hydra": op_plot_hydra,
    "trigger_rate": op_trigger_rate,
    "pedestal": op_pedestal,
    "plot_disabled": op_plot_disabled,
    "find_thresholds": op_find_thresholds,
    "self_trigger": op_self_trigger,
    "convert": op_convert,
    "plot_metrics": op_plot_metrics,
    "thresholds": op_thresholds,
    "clustering": op_clustering,
}

# Steps that drive the shared PACMAN/LArPix controller -- only one may run at a
# time.  Everything else is offline file/CPU work and may run concurrently
# (including alongside a live self-trigger acquisition).
HARDWARE_ACTIONS = {
    "check_power", "make_hydra", "trigger_rate", "pedestal",
    "find_thresholds", "self_trigger",
}

ACTION_LABELS = {
    "check_power": "Check power",
    "make_hydra": "Make hydra network",
    "plot_hydra": "Plot hydra network",
    "trigger_rate": "Trigger-rate disabled list",
    "pedestal": "Pedestal run",
    "plot_disabled": "Plot disabled channels",
    "find_thresholds": "Find thresholds",
    "self_trigger": "Self-trigger run",
    "convert": "Convert raw files",
    "plot_metrics": "Plot mean/stdev/rate",
    "thresholds": "Raise/lower thresholds",
    "clustering": "Charge clustering",
}


# ---------------------------------------------------------------------------
# State for the dropdowns
# ---------------------------------------------------------------------------
def _files(key, ext):
    try:
        return get_files_by_creation(CTX.d(key), ext)
    except Exception:
        return []


def build_state():
    last = {"descriptor": "", "cryo_flag": -1}
    if os.path.exists(SETTINGS_FILE):
        try:
            last = settings(read=True, settings_file=SETTINGS_FILE)
        except Exception:
            pass
    state = {
        "active": CTX.descriptor,
        "cryo_flag": CTX.cryo_flag,
        "runs_parent": RUNS_PARENT,
        "last_descriptor": last.get("descriptor", ""),
        "last_cryo": last.get("cryo_flag", -1),
        "jobs": JOBS.list(),
        "step_status": JOBS.latest_status_by_action(),
        "hardware": sorted(HARDWARE_ACTIONS),
        "files": {},
    }
    if CTX.is_ready():
        try:
            asic_folders = get_dirs_by_creation(CTX.d("asic_configs"))
        except Exception:
            asic_folders = []
        state["files"] = {
            "hydra": _files("hydra_files", "json"),
            "trigger_rate": _files("trigger_rate_disabled", "json"),
            "pedestal_second": _files("pedestal_second", "json"),
            "pedestal_runs": _files("pedestal_runs", "h5"),
            "raw": _files("raw_self_trigger", "h5"),
            "converted": _files("converted_self_trigger", "h5"),
            "asic_folders": asic_folders,
        }
        state["dirs"] = CTX.dirs
    return state


def _json_safe(obj):
    """Recursively replace non-finite floats (inf/-inf/nan) with None.

    Python's ``json.dumps`` emits ``Infinity``/``NaN`` for these by default, which
    are NOT valid JSON: the browser's ``res.json()`` then throws, ``api()`` in
    common.js swallows the error and returns ``{}``, and any view built from the
    response silently blanks out. The lifetime plot hits this whenever a fit's
    MINOS error is unbounded (``_minos_tau_bound`` returns ``inf``) or a
    covariance is degenerate (``nan``) -- one such point takes the whole plot
    down. Sanitizing here, at the single serialization boundary, protects every
    endpoint; the frontend already renders a null τ/error as "—".
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "LarpixGUI/1.0"

    def log_message(self, fmt, *args):  # quieter console
        pass

    # -- response helpers -------------------------------------------------
    def _send_json(self, obj, code=200):
        # allow_nan=False + a pre-pass to null out inf/nan so we can never emit
        # Infinity/NaN (invalid JSON that silently blanks the frontend).
        body = json.dumps(_json_safe(obj), allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", CONTENT_TYPES[".json"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type=None):
        if not os.path.isfile(path):
            self._send_json({"error": "not found: %s" % path}, 404)
            return
        if content_type is None:
            content_type = CONTENT_TYPES.get(os.path.splitext(path)[1],
                                             "application/octet-stream")
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/":
            return self._send_file(os.path.join(ASSETS_DIR, "index.html"))
        if path == "/dashboard":
            return self._send_file(os.path.join(ASSETS_DIR, "dashboard.html"))
        if path == "/step":
            return self._send_file(os.path.join(ASSETS_DIR, "step.html"))
        if path == "/pixelmap":
            return self._send_file(os.path.join(ASSETS_DIR, "pixelmap.html"))
        if path == "/rundb":
            return self._send_file(os.path.join(ASSETS_DIR, "rundb.html"))
        if path == "/lifetime":
            return self._send_file(os.path.join(ASSETS_DIR, "lifetime.html"))
        if path == "/eventdisplay":
            return self._send_file(os.path.join(ASSETS_DIR, "eventdisplay.html"))
        if path.startswith("/assets/"):
            name = os.path.basename(path)
            return self._send_file(os.path.join(ASSETS_DIR, name))
        if path == "/api/state":
            return self._send_json(build_state())
        if path.startswith("/api/job/"):
            job_id = path.split("/")[3]
            job = JOBS.jobs.get(job_id)
            if not job:
                return self._send_json({"error": "no such job"}, 404)
            offset = int(qs.get("offset", ["0"])[0])
            return self._send_json(job.snapshot(offset))
        if path.startswith("/api/metrics/"):
            stage = path.split("/")[3]
            if stage not in METRIC_SCHEMA:
                return self._send_json({"error": "bad stage"}, 400)
            return self._send_json(metrics_payload(stage))
        if path == "/api/pixelmap/geometry":
            try:
                return self._send_json(load_pixel_geometry())
            except Exception as exc:
                return self._send_json({"error": str(exc)}, 500)
        if path == "/api/pixelmap/density":
            src = qs.get("path", [""])[0].strip()
            kind = qs.get("kind", ["auto"])[0]
            # A loaded file/folder can be viewed without an active descriptor;
            # the default "current run" view still needs one.
            if not src and not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            return self._send_json(density_payload(src or None, kind))
        if path == "/api/pixelmap/folder":
            return self._send_json(
                folder_listing(qs.get("path", [""])[0].strip()))
        if path == "/api/pixel/config":
            folder = qs.get("folder", [""])[0]
            try:
                chip = int(qs.get("chip", ["0"])[0])
                ch = int(qs.get("ch", ["0"])[0])
            except ValueError:
                return self._send_json({"error": "bad chip/ch"}, 400)
            if not folder or not os.path.isdir(folder):
                return self._send_json({"error": "choose an ASIC config folder"}, 400)
            cfg = read_pixel_config(folder, chip, ch)
            if cfg is None:
                return self._send_json({"error": "no config for chip %d" % chip}, 404)
            return self._send_json(cfg)
        if path == "/api/pixel/queue":
            return self._send_json({"items": PIXEL_QUEUE.list()})
        if path == "/api/runs":
            return self._send_json({"rows": RUNDB.rows(),
                                    "descriptor": CTX.descriptor})
        if path == "/api/rundb/files":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            return self._send_json(rundb_files(qs.get("kind", ["auto"])[0]))
        if path == "/api/hotpixels":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            fn = qs.get("file", [""])[0]
            row = RUNDB.find_by_file(fn)
            if row is None:
                return self._send_json({"in_db": False, "file": fn, "hot": []})
            return self._send_json({"in_db": True, "run_id": row["id"],
                                    "label": row["label"], "hot": row["hot_pixels"]})
        if path == "/api/lifetime/series":
            return self._send_json(lifetime_series())
        if path == "/api/lifetime/tracks":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            series = qs.get("series", ["main"])[0]
            if series not in ("main", "overlay"):
                return self._send_json({"error": "bad series"}, 400)
            try:
                point = int(qs.get("point", ["-1"])[0])
            except ValueError:
                return self._send_json({"error": "bad point"}, 400)
            payload = lifetime_tracks_payload(series, point)
            if "error" in payload:
                return self._send_json(payload, 404)
            return self._send_json(payload)
        if path in ("/api/lifetime/pointmeta", "/api/lifetime/pointplots"):
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            series = qs.get("series", ["main"])[0]
            if series not in ("main", "overlay"):
                return self._send_json({"error": "bad series"}, 400)
            try:
                point = int(qs.get("point", ["-1"])[0])
            except ValueError:
                return self._send_json({"error": "bad point"}, 400)
            if path.endswith("pointmeta"):
                return self._send_json(lifetime_pointmeta(series, point))
            seqs, ef, _, _, _ = _point_context(series, point)
            res = TRACKS.point_plots(series, point, seqs, ef)
            return self._send_json(res, 404 if "error" in res else 200)
        if path == "/api/pixelmap/timedensity":
            return self._send_json(timedensity_payload(
                qs.get("path", [""])[0].strip(), qs.get("kind", ["auto"])[0],
                int(qs.get("bins", ["40"])[0])))
        if path == "/api/pixelmap/thresholds":
            return self._send_json(threshold_payload(
                qs.get("path", [""])[0].strip(),
                qs.get("kind", ["clustered"])[0], threshold_params(qs)))
        if path == "/api/pixelmap/threshold/pixel":
            try:
                chip = int(qs.get("chip", [""])[0])
                ch = int(qs.get("ch", [""])[0])
            except ValueError:
                return self._send_json({"error": "chip and ch required"}, 400)
            return self._send_json(threshold_pixel_payload(
                qs.get("path", [""])[0].strip(),
                qs.get("kind", ["clustered"])[0], threshold_params(qs),
                chip, ch))
        if path == "/api/evd/sources":
            return self._send_json(evd_sources())
        if path in ("/api/evd/events", "/api/evd/event"):
            src = qs.get("src", ["live"])[0]
            fpath = qs.get("path", [""])[0].strip()
            kind = qs.get("kind", ["auto"])[0]
            mode = qs.get("mode", ["window"])[0]
            if src == "live" and not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            try:
                min_hits = max(1, int(qs.get("min_hits", ["20"])[0]))
            except ValueError:
                min_hits = 20
            efield, hv, hv_src = evd_resolve_field(
                hv=qs.get("hv", [None])[0], efield=qs.get("efield", [None])[0],
                path=fpath)
            # a file's own hot pixels are dropped here too, matching the
            # lifetime pipeline's per-file exclusion
            exclude = set()
            if fpath:
                exclude = set(build_hot_pixel_map([fpath]).get(
                    os.path.basename(fpath), []))
            try:
                if path == "/api/evd/events":
                    out = evd_index(src, fpath, kind, efield, mode, min_hits,
                                    exclude)
                else:
                    out = evd_event(src, fpath, kind, efield,
                                    int(qs.get("index", ["0"])[0]), mode,
                                    min_hits, exclude)
            except DensityError as exc:
                return self._send_json({"error": str(exc)}, 400)
            except Exception as exc:
                return self._send_json({"error": "%s: %s" % (
                    type(exc).__name__, exc)}, 500)
            out.update({"hv": hv, "hv_source": hv_src,
                        "efield": round(float(efield), 2),
                        "hv_to_efield_factor": HV_TO_EFIELD})
            return self._send_json(out, 400 if "error" in out else 200)
        if path == "/api/plot":
            rel = qs.get("path", [""])[0]
            full = os.path.abspath(rel)
            runs_abs = os.path.abspath(RUNS_PARENT)
            if not full.startswith(runs_abs) or not full.endswith(".png"):
                return self._send_json({"error": "forbidden"}, 403)
            return self._send_file(full, CONTENT_TYPES[".png"])
        return self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()

        if path == "/api/descriptor":
            try:
                CTX.setup(body.get("descriptor", ""), bool(body.get("cryo")))
            except Exception as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json({"ok": True, "state": build_state()})

        if path.startswith("/api/run/"):
            action = path.split("/")[3]
            if action not in ACTIONS:
                return self._send_json({"error": "unknown action"}, 400)
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            if action in HARDWARE_ACTIONS:
                busy = JOBS.running_hardware()
                if busy:
                    return self._send_json(
                        {"error": "Hardware busy: '%s' is running. Hardware steps "
                                  "run one at a time." % busy[0].name}, 409)
            handler = ACTIONS[action]
            name = ACTION_LABELS.get(action, action)
            job = JOBS.start(name, partial(_invoke, handler, body), action=action)
            return self._send_json({"job_id": job.id})

        if path.startswith("/api/job/") and path.endswith("/stop"):
            job_id = path.split("/")[3]
            return self._send_json({"ok": JOBS.stop(job_id)})

        if path == "/api/pixel/queue":
            try:
                item = PIXEL_QUEUE.add(body)
            except (ValueError, KeyError) as exc:
                return self._send_json({"error": str(exc)}, 400)
            running = JOBS.latest_status_by_action().get("self_trigger") == "running"
            return self._send_json({"ok": True, "item": item,
                                    "will_apply": "between runs" if running
                                    else "on next run or Apply now"})

        if path == "/api/pixel/queue/batch":
            items = body.get("items") or []
            if not isinstance(items, list) or not items:
                return self._send_json({"error": "items must be a non-empty list"}, 400)
            added, errors = 0, []
            for it in items:
                try:
                    PIXEL_QUEUE.add(it)
                    added += 1
                except (ValueError, KeyError, TypeError) as exc:
                    errors.append("%s-%s: %s" % (it.get("chip"), it.get("ch"), exc))
            running = JOBS.latest_status_by_action().get("self_trigger") == "running"
            return self._send_json({"ok": True, "added": added, "errors": errors,
                                    "will_apply": "between runs" if running
                                    else "on next run or Apply now"})

        if path == "/api/pixel/queue/remove":
            removed = PIXEL_QUEUE.remove(int(body.get("index", -1)))
            return self._send_json({"ok": removed is not None})

        if path == "/api/pixel/queue/apply":
            folder = body.get("folder", "")
            if not folder or not os.path.isdir(folder):
                return self._send_json({"error": "choose an ASIC config folder"}, 400)
            if JOBS.latest_status_by_action().get("self_trigger") == "running":
                return self._send_json(
                    {"error": "A self-trigger run is active -- queued changes "
                              "will be applied automatically between run files."},
                    409)
            if not PIXEL_QUEUE.pending():
                return self._send_json({"error": "queue is empty"}, 400)
            log_lines = []
            n = PIXEL_QUEUE.apply(folder, log_lines.append)
            return self._send_json({"ok": True, "applied": n, "log": log_lines})

        if path == "/api/runs/update":
            try:
                ok = RUNDB.update(body.get("id"),
                                  notes=body.get("notes"),
                                  hv=body.get("hv"))
            except Exception as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json({"ok": ok})

        if path == "/api/runs/add":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            src = (body.get("path") or "").strip()
            if not src:
                return self._send_json({"error": "enter a file or folder path"}, 400)
            res = RUNDB.add_files(src, body.get("kind", "auto"))
            return self._send_json(res)

        if path == "/api/runs/delete":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            try:
                ok = RUNDB.delete(int(body.get("id")))
            except (TypeError, ValueError) as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json({"ok": ok})

        if path == "/api/hotpixels":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            run_id = body.get("run_id")
            if run_id is None:
                row = RUNDB.find_by_file(body.get("file", ""))
                if row is None:
                    return self._send_json(
                        {"error": "that file isn't in the run database yet -- add it "
                                  "from the Run database page first"}, 404)
                run_id = row["id"]
            ok = RUNDB.set_hot(run_id, body.get("hot", []))
            return self._send_json({"ok": ok, "run_id": run_id,
                                    "n_hot": len(body.get("hot", []))})

        if path == "/api/lifetime/compute":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            if JOBS.latest_status_by_action().get("lifetime") == "running":
                return self._send_json({"error": "a lifetime computation is "
                                        "already running"}, 409)
            series = body.get("series", "main")
            if series not in ("main", "overlay"):
                return self._send_json({"error": "bad series"}, 400)
            if series == "overlay":
                folder = body.get("folder", "")
                if not folder or not os.path.isdir(folder):
                    return self._send_json(
                        {"error": "overlay folder not found: %r" % folder}, 400)
            name = "Lifetime vs time" + (" (overlay)" if series == "overlay" else "")
            job = JOBS.start(name, partial(_invoke, op_lifetime, body),
                             action="lifetime")
            return self._send_json({"job_id": job.id})

        if path == "/api/lifetime/exclude":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            series = body.get("series", "main")
            if series not in ("main", "overlay"):
                return self._send_json({"error": "bad series"}, 400)
            try:
                res = lifetime_set_exclusion(series, int(body.get("point")),
                                             int(body.get("seq")),
                                             bool(body.get("excluded")))
            except (TypeError, ValueError) as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json(res)

        if path == "/api/lifetime/excludepoint":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            series = body.get("series", "main")
            if series not in ("main", "overlay"):
                return self._send_json({"error": "bad series"}, 400)
            try:
                res = lifetime_set_point_excluded(series, int(body.get("point")),
                                                  bool(body.get("excluded")))
            except (TypeError, ValueError) as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json(res)

        if path == "/api/lifetime/efield":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            series = body.get("series", "main")
            if series not in ("main", "overlay"):
                return self._send_json({"error": "bad series"}, 400)
            try:
                res = lifetime_set_efield(series, int(body.get("point")),
                                          efield=body.get("efield"),
                                          hv=body.get("hv"))
            except (TypeError, ValueError) as exc:
                return self._send_json({"error": str(exc)}, 400)
            return self._send_json(res, 400 if "error" in res else 200)

        if path == "/api/lifetime/overlay/clear":
            if not CTX.is_ready():
                return self._send_json({"error": "set a descriptor first"}, 400)
            paths = _lt_paths("overlay")
            removed = 0
            for key in ("series", "tracks", "exclusions", "excluded_points",
                        "meta", "cache"):
                try:
                    if os.path.exists(paths[key]):
                        os.remove(paths[key])
                        removed += 1
                except OSError:
                    pass
            TRACKS.invalidate()
            return self._send_json({"ok": True, "removed": removed})

        return self._send_json({"error": "not found"}, 404)


def _invoke(handler, params, job):
    """Wrap an op handler so exceptions surface in the job log."""
    handler(job, params)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def restore_last_descriptor():
    """Re-activate the descriptor in the settings file, if its run tree exists.

    Without this the active descriptor is lost on every restart (``CTX.setup``
    only ever ran from the /api/descriptor POST), so every page reported "no
    descriptor set" until it was re-set by hand on the flow page. Only an
    *existing* ``runs/<descriptor>/`` is restored, so a stale or deleted name
    never silently re-creates an empty run tree. Never fatal: any problem just
    leaves the descriptor unset, exactly as before.
    """
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        last = settings(read=True, settings_file=SETTINGS_FILE)
        name = (last.get("descriptor") or "").strip()
        if not name:
            return
        if not os.path.isdir(os.path.join(RUNS_PARENT, name)):
            print("Last descriptor %r has no %s/%s/ directory - not restored."
                  % (name, RUNS_PARENT, name))
            return
        CTX.setup(name, bool(last.get("cryo_flag", 0)))
        print("Restored last descriptor: %s" % name)
    except Exception as exc:
        print("Could not restore the last descriptor (%s); set one on the "
              "flow page." % exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--no-browser", action="store_true",
                        help="do not auto-open a web browser")
    parser.add_argument("--no-restore", action="store_true",
                        help="do not re-activate the last descriptor at startup")
    args = parser.parse_args()

    if not os.path.isdir(ASSETS_DIR):
        raise SystemExit("Missing assets directory: %s" % ASSETS_DIR)

    if not args.no_restore:
        restore_last_descriptor()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d/" % (args.host, args.port)
    print("LArPix GUI serving at %s" % url)
    print("Working directory: %s" % os.getcwd())
    print("Descriptor output root: %s/<descriptor>/" % RUNS_PARENT)
    print("Press Ctrl-C to stop.")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
