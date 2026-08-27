#!/usr/bin/env python3
"""Scatter plot of electron lifetime (tau) across time -- one point per run.

Utilises ``quick_purity.py``: for each input LArPix file this runs the exact same
parse -> DBSCAN cluster -> anode-cathode select -> exponential-fit pipeline, extracts
tau (with the MINOS asymmetric errors from ``purity_core``), reads the run time from
the filename timestamp, and plots tau versus time so you can watch the argon purity
evolve over a data-taking period.

Each file is treated as one run / one point (clustering is per file, never pooled
across files -- see quick_purity for why). Give individual paths, glob pattern(s), or
a mix; quote globs so the shell doesn't pre-expand them.

Examples
--------
    # a whole day of converted runs -> one tau-vs-time plot
    python3 lifetime_vs_time.py \
        '/Volumes/KINGSTON/converted_.../converted_..._2026_06_13_*.h5' \
        --converted -E 465.5 -o lifetime_20260613

    # raw self-trigger files during data-taking
    python3 lifetime_vs_time.py 'raw_self_trigger_data/*-raw_*.h5' -E 465.5
"""
import argparse
import csv
import glob
import os
import re
import sys
from datetime import datetime

import numpy as np

import purity_core as pc
import quick_purity as qp

# Filename run timestamp, e.g. ..._2026_06_13_18_33_24_MDT.h5
_TS_RE = re.compile(r'(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})')


def parse_timestamp(path):
    """Pull a datetime out of the filename's ..._YYYY_MM_DD_HH_MM_SS_... stamp."""
    m = _TS_RE.search(os.path.basename(path))
    if not m:
        return None
    try:
        return datetime(*[int(g) for g in m.groups()])
    except ValueError:
        return None


def measure_file(path, cmap, efield, converted, max_messages):
    """Run the quick_purity pipeline on one file. Returns a result dict or None.

    Result keys: n_crossers, and (if the fit succeeded) tau_ms, tau_err_pos_ms,
    tau_err_neg_ms, dqdx_at_half_drift.
    """
    if converted:
        x, y, t, c = qp.parse_converted_hits(path, cmap, max_packets=max_messages)
    else:
        x, y, t, c = qp.parse_raw_hits(path, cmap, max_messages=max_messages)
    if x.size == 0:
        return {'n_crossers': 0}

    acc = qp.build_accumulator([(x, y, t, c)], efield=efield)
    n_crossers = acc.n_tracks
    if n_crossers < 2:
        return {'n_crossers': n_crossers}

    centers, med, err = acc.profile(cap=pc.DQDX_CAP_KE_PER_CM)
    try:
        fit = pc.fit_lifetime(centers, med, err,
                              drift_time_max_us=acc.drift_time_max_us)
    except RuntimeError:
        return {'n_crossers': n_crossers}

    return {
        'n_crossers': n_crossers,
        'tau_ms': fit['tau_ms'],
        'tau_err_pos_ms': fit['tau_err_pos_ms'],
        'tau_err_neg_ms': fit['tau_err_neg_ms'],
        'dqdx_at_half_drift': fit['dqdx_at_half_drift'],
    }


def plot_series(rows, out_png, title='', max_tau_ms=None):
    """Scatter tau vs run time with asymmetric error bars, coloured by crosser count."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    times = [r['time'] for r in rows]
    tau = np.array([r['tau_ms'] for r in rows])
    pos = np.array([r['tau_err_pos_ms'] for r in rows])
    neg = np.array([r['tau_err_neg_ms'] for r in rows])
    ncr = np.array([r['n_crossers'] for r in rows])

    # cap non-finite / absurd upper errors so the plot stays readable
    ymax = max_tau_ms if max_tau_ms is not None else float(np.nanmax(tau)) * 2.0
    pos_capped = np.where(np.isfinite(pos), pos, ymax)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.errorbar(times, tau, yerr=[neg, pos_capped], fmt='none',
                ecolor='0.6', elinewidth=1, capsize=3, zorder=1)
    sc = ax.scatter(times, tau, c=ncr, cmap='viridis', s=45, zorder=2,
                    edgecolor='k', linewidth=0.4)
    cbar = fig.colorbar(sc, ax=ax)
    cbar.set_label('anode-cathode crossers')

    ax.set_ylabel(r'Electron lifetime  $\tau$  [ms]')
    ax.set_xlabel('Run time')
    if title:
        ax.set_title(title)
    ax.set_ylim(bottom=0.0)
    if max_tau_ms is not None:
        ax.set_ylim(top=max_tau_ms)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return out_png


def save_csv(rows, path):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['run_time', 'tau_ms', 'tau_err_pos_ms', 'tau_err_neg_ms',
                    'n_crossers', 'file'])
        for r in rows:
            w.writerow([r['time'].isoformat(), f"{r['tau_ms']:.6g}",
                        f"{r['tau_err_pos_ms']:.6g}", f"{r['tau_err_neg_ms']:.6g}",
                        r['n_crossers'], r['file']])
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('inputs', nargs='+',
                    help="LArPix .h5 file(s), glob pattern(s), or a mix -- one run "
                         "per file (quote globs so the shell doesn't expand them)")
    ap.add_argument('-o', '--out_prefix', default='lifetime_vs_time',
                    help="output prefix for the plot + .csv (default: lifetime_vs_time)")
    ap.add_argument('--channelmap', default=qp.DEFAULT_CHANNELMAP,
                    help="channelmap.dat (chip channel X Y)")
    ap.add_argument('--converted', action='store_true',
                    help="inputs are converted packets .h5 files (not raw msgs)")
    ap.add_argument('--max_messages', type=int, default=None,
                    help="cap messages/packets parsed per file (default: all)")
    ap.add_argument('-E', '--efield', type=float, default=500.0,
                    help="drift E-field [V/cm]; must match the run (default: 500)")
    ap.add_argument('--min_crossers', type=int, default=30,
                    help="skip a run with fewer than this many AC crossers "
                         "(default: 30)")
    ap.add_argument('--max_tau_ms', type=float, default=None,
                    help="clamp the y-axis (and unbounded error bars) to this tau "
                         "in ms (default: auto)")
    args = ap.parse_args()

    # expand globs / dedupe (same pattern as quick_purity / purity_from_ac)
    files = []
    for p in args.inputs:
        matched = glob.glob(p)
        files.extend(matched if matched else [p])
    files = sorted(set(f for f in files if os.path.isfile(f)))
    if not files:
        sys.exit("No input files found.")
    if not os.path.isfile(args.channelmap):
        sys.exit(f"channelmap not found: {args.channelmap}")

    cmap = qp.load_channelmap(args.channelmap)
    print(f"Measuring lifetime for {len(files)} run(s) at E = {args.efield} V/cm ...")

    rows = []
    n_skipped = 0
    for i, f in enumerate(files, 1):
        ts = parse_timestamp(f)
        tag = os.path.basename(f)
        if ts is None:
            print(f"  [{i}/{len(files)}] SKIP (no timestamp in name): {tag}")
            n_skipped += 1
            continue
        res = measure_file(f, cmap, args.efield, args.converted, args.max_messages)
        n = res.get('n_crossers', 0)
        if 'tau_ms' not in res or n < args.min_crossers:
            print(f"  [{i}/{len(files)}] SKIP ({n} crossers < {args.min_crossers} "
                  f"or fit failed): {tag}")
            n_skipped += 1
            continue
        pos = res['tau_err_pos_ms']
        pos_str = 'inf' if not np.isfinite(pos) else f"{pos:.3f}"
        print(f"  [{i}/{len(files)}] {ts:%Y-%m-%d %H:%M:%S}  "
              f"tau = {res['tau_ms']:.3f} +{pos_str} -{res['tau_err_neg_ms']:.3f} ms  "
              f"({n} crossers)")
        res['time'] = ts
        res['file'] = f
        rows.append(res)

    if not rows:
        sys.exit(f"No runs produced a lifetime (all {len(files)} skipped). "
                 f"Check --efield, --min_crossers, and that the files have crossers.")
    rows.sort(key=lambda r: r['time'])

    png = plot_series(rows, f"{args.out_prefix}.png",
                      title=f'Electron lifetime vs time (E = {args.efield:g} V/cm)',
                      max_tau_ms=args.max_tau_ms)
    csv_path = save_csv(rows, f"{args.out_prefix}.csv")
    print(f"\n{len(rows)} run(s) plotted, {n_skipped} skipped.")
    print(f"Wrote {png}")
    print(f"Wrote {csv_path}")


if __name__ == '__main__':
    main()
