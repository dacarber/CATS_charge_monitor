# LArPix charge monitoring

Self-contained browser GUI + dashboards for running and monitoring the LArPix
10x10 charge pipeline. This directory holds everything needed to run the
monitor; it can be moved or copied as a unit.

## Contents

```
larpix_charge_monitor/
├── larpix_gui.py       # stdlib http.server app (control panel + all dashboards)
├── gui_assets/         # HTML / JS / CSS served by larpix_gui.py
│   ├── index.html, flow.js            # flow-diagram control panel
│   ├── step.html, step.js             # per-step run pages
│   ├── dashboard.html, charts.js      # data-rate dashboards
│   ├── pixelmap.html, pixelmap.js     # pixel hit-density map + threshold edits
│   ├── rundb.html                     # self-trigger run database
│   ├── lifetime.html                  # electron-lifetime vs time
│   └── common.js, style.css           # shared helpers + theme
├── vendor/             # bundled pure-Python packages (import larpix, larpixgeometry)
├── analysis/           # bundled lifetime tools (purity_core, quick_purity, lifetime_vs_*)
├── data/               # bundled channelmap.dat + layout-2.4.0.yaml (geometry)
├── requirements.txt    # third-party Python deps (numpy, h5py, scipy, ...)
├── setup_env.sh        # create a venv with the deps
└── README.md
```

Everything the **offline** features need (event display, pixel-density maps,
the lifetime dashboard) ships inside this folder, so the app runs from a bare
download with no external repo checkout. `vendor/`, `analysis/`, and `data/`
are used as a fallback: if a `SingleCube/repos/` checkout *is* present nearby
(the DAQ machine), those canonical copies are preferred and behavior is
unchanged.

## Requirements

Python 3 (the server itself is stdlib-only). The analysis features and the
wrapped pipeline scripts need the third-party packages in `requirements.txt`
(numpy, h5py, scikit-learn, scipy, matplotlib, PyYAML, ...). The `larpix` /
`larpixgeometry` packages are **vendored** under `vendor/`, so they do *not*
need to be installed separately. Create a venv with:

```
./setup_env.sh
source .venv/bin/activate
```

## Running

### Offline analysis (a downloaded copy, no DAQ hardware)

Just launch it from this folder — everything resolves to the bundled copies:

```
python3 larpix_gui.py            # http://127.0.0.1:8000
```

Point the pages at converted/raw `.h5` files and use the event display,
pixel-density maps, and lifetime dashboard. The DAQ control actions (which drive
real PACMAN/LArPix hardware) will report that the pipeline scripts aren't present
rather than erroring out.

### On the DAQ machine (full pipeline)

Launch **from the directory that holds the pipeline scripts** (`check_power.py`,
`layout-2.4.0.yaml`, `larpix-control/scripts/...`, `./larpix-monitor/...`,
`./dg_ctrl_pps`, etc.) so the preserved relative command paths resolve:

```
cd <larpix-10x10-scripts dir>
python3 /path/to/larpix_charge_monitor/larpix_gui.py
```

`larpix_gui.py` finds its own `gui_assets/` next to itself, so it can live here
while still running from the pipeline directory. Each descriptor's output goes
under `runs/<descriptor>/` in the launch directory.

## Notes

- The command-line invocations of the pipeline scripts are unchanged from the
  original `run_larpix_scripts.py` text menu — only the interface and the
  `runs/<descriptor>/` output layout differ.
- The lifetime dashboard shells out to `lifetime_vs_tracks.py` / `quick_purity.py`
  and imports `purity_core.py`. It prefers the canonical copies under
  `ndlar_39Ar_reco/charge_reco/CATS_analysis/lifetime/` when a repo checkout is
  present, and falls back to the bundled copies in `analysis/`.
- `vendor/`, `analysis/`, and `data/` are self-contained snapshots. If you update
  the upstream `larpix-control` / lifetime tools and want the bundle to match,
  re-copy them into those folders.
