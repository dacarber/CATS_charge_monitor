#!/usr/bin/env python3
"""Tool 2 -- lightweight electron lifetime ("purity") straight from a RAW LArPix file.

Built for a fast quick-look *during* data-taking: it reads the raw PACMAN
``msgs``/``msg_headers`` file (the first thing written by a self-trigger run, before
``convert_rawhdf5_to_hdf5.py`` and before the charge-clustering chain) and produces
an electron-lifetime number in a single pass -- no intermediate files on disk.

Pipeline (mirrors the SCPurityTool C++ chain, all in memory):
  1. Parse raw messages with the same larpix calls the converter uses
     (``rawhdf5format.from_rawfile`` + ``pacman_msg_format.parse``); keep data
     packets (type 0) with valid parity.
  2. Map ``(chip_id, channel_id) -> (X, Y)`` mm via ``channelmap.dat`` (same map
     TrackMaker uses).
  3. Cluster in fixed packet chunks with DBSCAN on ``(x, y, t/timeSF)`` and keep
     50-200-hit clusters -- the TrackMaker recipe.
  4. Select anode-cathode crossers (full-drift time span), bin charge by drift time,
     and fit ``dQ/dx(t) = A*exp(-t/tau)`` via ``purity_core``.

Charge is ``dataword - pedestal`` with the flat pedestal from PurityStudy.cpp; the
lifetime depends only on the falloff shape, so this is plenty for a quick-look.

Multiple files (or a glob pattern) can be given at once; all their hits are pooled
into one dQ/dx-vs-drift-time fit for better statistics (the same "combine, then fit"
behaviour as ``purity_from_ac.py``).

Example
-------
    python3 quick_purity.py raw_self_trigger_data/tile-id-10x10-raw_..._MDT.h5
    python3 quick_purity.py run.h5 --max_messages 200000 -o quick_purity_run13

    # several converted files / a glob, combined into one fit
    python3 quick_purity.py '/Volumes/KINGSTON/converted_.../converted_..._2026_06_13_*.h5' \\
        --converted -E 465.5 -o quick_purity_20260613
"""
import argparse
import glob
import os
import sys
import time

import numpy as np

import purity_core as pc

# TrackMaker.cpp clustering constants
TIME_SF = 6.5            # scales time onto the spatial (mm) scale for DBSCAN
SCAN_RADIUS = 10.0       # mm, DBSCAN eps in the scaled space
MIN_CLUSTER_SIZE = 50
MAX_CLUSTER_SIZE = 200
DATA_CHUNK_SIZE = 38400  # packets per "event" chunk
DBSCAN_MIN_SAMPLES = 4   # 4th arg to dbscan.Run in TrackMaker

TICK_US = 0.1            # one LArPix timestamp tick = 0.1 us (100 ns)

def _find_upward(rel_path, start=None):
    """Walk up from ``start`` (default: this file's dir) looking for rel_path."""
    d = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    for _ in range(8):
        candidate = os.path.join(d, rel_path)
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # fall back to the path relative to this file, even if it doesn't exist --
    # callers check os.path.isfile() and give a clear error.
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel_path)


# NOTE: this is the copy bundled inside larpix_charge_monitor/analysis/ (see the
# app's data/ and vendor/ siblings). It differs from the canonical
# CATS_analysis/lifetime/quick_purity.py only in how these defaults resolve:
# the external checkout is preferred (unchanged behavior when the repos are
# present), but if it isn't found we fall back to the files shipped with the app
# so a bare download runs with no SingleCube/repos checkout.
_REL_CHANNELMAP = os.path.join('SingleCube', 'repos', 'SCPurityTool_lane',
                               'channelmap.dat')
_REL_LARPIX = os.path.join('SingleCube', 'repos', 'larpix-control')


def _bundled(*parts):
    """Path to a file shipped with the app: analysis/ -> app root -> parts."""
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(app, *parts)


def _resolve(rel_path, *bundled_parts):
    """External checkout (walk-up) if present, else the app's bundled copy."""
    found = _find_upward(rel_path)
    if os.path.exists(found):
        return found
    b = _bundled(*bundled_parts)
    return b if os.path.exists(b) else found


DEFAULT_CHANNELMAP = _resolve(_REL_CHANNELMAP, 'data', 'channelmap.dat')
DEFAULT_LARPIX = _resolve(_REL_LARPIX, 'vendor')


def load_channelmap(path):
    """Load channelmap.dat -> dict {(chip_id, channel_id): (x_mm, y_mm)}."""
    cmap = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) != 4:
                continue
            chip, chan, x, y = parts
            cmap[(int(chip), int(chan))] = (float(x), float(y))
    if not cmap:
        raise RuntimeError(f"no entries parsed from channelmap {path}")
    return cmap


def parse_raw_hits(raw_path, cmap, max_messages=None, block_size=10240,
                   exclude_pixels=None):
    """Parse the raw PACMAN file into per-hit arrays (x, y, t_ticks, charge).

    Returns four parallel numpy arrays. Only data packets (type 0) with valid parity,
    a mapped pixel, and a non-(0,0) position are kept -- matching TrackMaker's hit
    selection. Charge is ``dataword - pedestal``.

    ``exclude_pixels`` is an optional set of ``(chip_id, channel_id)`` tuples (e.g.
    pixels tagged hot in the run database) whose hits are dropped before clustering,
    so a noisy pixel cannot pull DBSCAN clusters or bias the dQ/dx fit. ``None``
    (the default) keeps every mapped hit.
    """
    if DEFAULT_LARPIX not in sys.path:
        sys.path.insert(0, DEFAULT_LARPIX)
    from larpix.format.rawhdf5format import from_rawfile, len_rawfile
    from larpix.format.pacman_msg_format import parse

    total = len_rawfile(raw_path)
    if max_messages is not None:
        total = min(total, max_messages)
    print(f"Parsing up to {total} raw messages from {os.path.basename(raw_path)} ...")

    excl = exclude_pixels or ()
    n_excluded = 0
    xs, ys, ts, cs = [], [], [], []
    last = time.time()
    start = 0
    while start < total:
        end = min(start + block_size, total)
        rd = from_rawfile(raw_path, start=start, end=end)
        for io_group, msg in zip(rd['msg_headers']['io_groups'], rd['msgs']):
            for p in parse(msg, io_group=io_group):
                if getattr(p, 'packet_type', None) != 0:
                    continue
                if not p.has_valid_parity():
                    continue
                key = (p.chip_id, p.channel_id)
                if key in excl:
                    n_excluded += 1
                    continue
                xy = cmap.get(key)
                if xy is None or (xy[0] == 0.0 and xy[1] == 0.0):
                    continue
                xs.append(xy[0]); ys.append(xy[1])
                ts.append(int(p.timestamp))
                cs.append(int(p.dataword) - pc.PEDESTAL_ADC)
        start = end
        if time.time() > last + 2:
            print(f"  ...{end}/{total} messages, {len(xs)} hits", end='\r')
            last = time.time()
    print(f"  parsed {len(xs)} data hits from {total} messages"
          + (f" ({n_excluded} dropped: {len(excl)} hot pixel(s))" if excl else "")
          + "." + " " * 20)
    return (np.asarray(xs, 'f8'), np.asarray(ys, 'f8'),
            np.asarray(ts, 'f8'), np.asarray(cs, 'f8'))


def parse_converted_hits(converted_path, cmap, max_packets=None,
                         exclude_pixels=None):
    """Parse a *converted* ``packets``-table .h5 into the same (x,y,t,c) arrays.

    Same selection as ``parse_raw_hits`` (packet_type==0, valid_parity==1, mapped
    pixel) but reads the already-converted ``packets`` dataset directly -- useful
    when the true raw msgs file is no longer on disk but the converted file is.

    ``exclude_pixels`` behaves exactly as in ``parse_raw_hits``.
    """
    import h5py

    print(f"Reading packets from {os.path.basename(converted_path)} ...")
    with h5py.File(converted_path, 'r') as f:
        d = f['packets'][:max_packets] if max_packets is not None else f['packets'][:]

    m = (d['packet_type'] == 0) & (d['valid_parity'] == 1)
    d = d[m]

    excl = exclude_pixels or ()
    n_excluded = 0
    xs = np.empty(d.shape[0], dtype='f8')
    ys = np.empty(d.shape[0], dtype='f8')
    ok = np.zeros(d.shape[0], dtype=bool)
    for i in range(d.shape[0]):
        key = (int(d['chip_id'][i]), int(d['channel_id'][i]))
        if key in excl:
            n_excluded += 1
            continue
        xy = cmap.get(key)
        if xy is None or (xy[0] == 0.0 and xy[1] == 0.0):
            continue
        xs[i], ys[i] = xy
        ok[i] = True

    x = xs[ok]
    y = ys[ok]
    t = d['timestamp'][ok].astype('f8')
    c = d['dataword'][ok].astype('f8') - pc.PEDESTAL_ADC
    print(f"  parsed {x.size} data hits from {d.shape[0]} valid-parity data packets"
          + (f" ({n_excluded} dropped: {len(excl)} hot pixel(s))" if excl else "")
          + ".")
    return x, y, t, c


def find_clusters(x, y, t):
    """DBSCAN one chunk (TrackMaker recipe). Yields hit-index arrays of kept clusters."""
    from sklearn.cluster import DBSCAN
    feats = np.column_stack([x, y, t / TIME_SF])
    labels = DBSCAN(eps=SCAN_RADIUS, min_samples=DBSCAN_MIN_SAMPLES).fit_predict(feats)
    for lab in np.unique(labels):
        if lab == -1:
            continue
        members = np.where(labels == lab)[0]
        if MIN_CLUSTER_SIZE < members.size < MAX_CLUSTER_SIZE:
            yield members


def _cluster_one_file(x, y, t, c, acc, drift_time_max_us, drift_time_range_us):
    """DBSCAN-chunk one file's hits into ``acc``. Returns (n_clusters, n_ac) for it."""
    n_clusters = 0
    n_ac = 0
    n = x.size
    for s in range(0, n, DATA_CHUNK_SIZE):
        e = min(s + DATA_CHUNK_SIZE, n)
        cx, cy, ct, cc = x[s:e], y[s:e], t[s:e], c[s:e]
        for members in find_clusters(cx, cy, ct):
            n_clusters += 1
            mt = ct[members]
            t_min = mt.min()
            span_us = (mt.max() - t_min) * TICK_US
            if not pc.is_ac_crosser(span_us, drift_time_max_us, drift_time_range_us):
                continue
            drift_time_us = (mt - t_min) * TICK_US
            i_min = np.argmin(mt); i_max = np.argmax(mt)
            dxy_mm = float(np.hypot(cx[members][i_max] - cx[members][i_min],
                                    cy[members][i_max] - cy[members][i_min]))
            if acc.add_track(drift_time_us, cc[members], dxy_mm, span_us):
                n_ac += 1
    return n_clusters, n_ac


def build_accumulator(hits_by_file, efield=500.0):
    """Cluster each file's hits separately and pool into one shared accumulator.

    ``hits_by_file`` is a list of ``(x, y, t, c)`` tuples, one per input file.
    Clustering (DBSCAN) is run **per file** rather than on hits pooled across files:
    LArPix timestamps commonly restart near zero at the start of each file, so a
    chunk straddling a file boundary could otherwise spuriously merge unrelated hits
    from two different files into one fake cluster. Only the resulting per-track
    dQ/dx values are pooled, exactly like ``purity_from_ac.py`` pools already-formed
    clusters across multiple ``*_ac_track_data.h5`` files.

    Drift constants and the anode-cathode window come from ``pc.drift_params(efield)``,
    so with a matched E-field this reproduces PurityStudy.cpp's selection and axis
    scaling (this path also shares TrackMaker's clustering + raw-ADC charge).
    """
    drift_vel, drift_time_max_us, drift_time_range_us = pc.drift_params(efield)
    acc = pc.LifetimeAccumulator(charge_scale=pc.GAIN_E_PER_ADC / 1000.0,  # -> ke-/cm
                                 drift_time_max_us=drift_time_max_us,
                                 v_drift=drift_vel)
    n_clusters = 0
    n_ac = 0
    for x, y, t, c in hits_by_file:
        fc, fa = _cluster_one_file(x, y, t, c, acc, drift_time_max_us,
                                   drift_time_range_us)
        n_clusters += fc
        n_ac += fa

    acc._n_clusters = n_clusters
    acc._n_ac = n_ac
    return acc


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('inputs', nargs='+',
                    help="raw LArPix PACMAN .h5 file(s) (msgs/msg_headers), or "
                         "converted packets .h5 file(s) if --converted is given -- "
                         "individual paths, glob pattern(s), or a mix of both. "
                         "All matched files' hits are pooled into one fit.")
    ap.add_argument('-o', '--out_prefix', default='quick_purity',
                    help="output prefix for the plot/.npz (default: quick_purity)")
    ap.add_argument('--channelmap', default=DEFAULT_CHANNELMAP,
                    help="channelmap.dat (chip channel X Y)")
    ap.add_argument('--max_messages', type=int, default=None,
                    help="cap messages/packets parsed PER FILE for an even faster "
                         "look (default: all)")
    ap.add_argument('--converted', action='store_true',
                    help="input is an already-converted packets .h5 (has a 'packets' "
                         "table) instead of the truly-raw msgs/msg_headers file -- "
                         "use this if the raw file is no longer on disk")
    ap.add_argument('-E', '--efield', type=float, default=500.0,
                    help="drift E-field [V/cm]; sets drift velocity, full-drift time "
                         "and the anode-cathode window, matching PurityStudy.cpp. Use "
                         "the SAME value you pass to PurityStudy (default: 500)")
    args = ap.parse_args()

    # expand globs / dedupe, same pattern as purity_from_ac.py
    files = []
    for p in args.inputs:
        matched = glob.glob(p)
        files.extend(matched if matched else [p])
    files = sorted(set(f for f in files if os.path.isfile(f)))
    if not files:
        sys.exit("No input files found.")
    if not os.path.isfile(args.channelmap):
        sys.exit(f"channelmap not found: {args.channelmap}")
    print(f"Reading {len(files)} input file(s):")
    for f in files:
        print(f"  {f}")

    cmap = load_channelmap(args.channelmap)
    if args.converted:
        def parse(f):
            return parse_converted_hits(f, cmap, max_packets=args.max_messages)
    else:
        def parse(f):
            return parse_raw_hits(f, cmap, max_messages=args.max_messages)

    hits_by_file = []
    total_hits = 0
    for f in files:
        fx, fy, ft, fc = parse(f)
        if fx.size:
            hits_by_file.append((fx, fy, ft, fc))
            total_hits += fx.size
    if total_hits == 0:
        sys.exit("No usable data hits parsed from the input file(s).")
    if len(files) > 1:
        print(f"Pooled {total_hits} hits from {len(hits_by_file)} file(s) "
             "(clustered per-file, then pooled).")

    acc = build_accumulator(hits_by_file, efield=args.efield)
    print(f"Clusters (50-200 hits): {getattr(acc, '_n_clusters', 0)}  |  "
          f"anode-cathode crossers: {getattr(acc, '_n_ac', 0)}")
    if acc.n_tracks == 0:
        sys.exit("No anode-cathode crossers found -- need more data (or check the run "
                 f"and that --efield {args.efield} matches it).")
    if acc.n_tracks < 100:
        print(f"WARNING: only {acc.n_tracks} crossers; >=100 recommended for a "
              f"reliable lifetime (see SCPurityTool README).")

    # RMS error over dQ/dx in [0, 160] ke-/cm, matching PurityStudy's capped TProfile.
    centers, med, err = acc.profile(cap=pc.DQDX_CAP_KE_PER_CM)
    fit = pc.fit_lifetime(centers, med, err, drift_time_max_us=acc.drift_time_max_us)
    fit['n_tracks'] = acc.n_tracks

    pc.print_result(fit, n_tracks=acc.n_tracks, ylabel_unit='ke-/cm')

    ylabel = 'dQ/dx [ke⁻/cm]'

    png = pc.plot_lifetime(centers, med, err, fit, f"{args.out_prefix}.png",
                           title='Quick purity from raw LArPix data',
                           ylabel=f'Median {ylabel}',
                           drift_time_max_us=acc.drift_time_max_us)
    png2d = pc.plot_dqdx_2d(acc, f"{args.out_prefix}_2d.png", fit=fit,
                             title='dQ/dx vs drift time (all track bins)',
                             ylabel=ylabel,
                             drift_time_max_us=acc.drift_time_max_us)
    png1d = pc.plot_dqdx_1d(acc, f"{args.out_prefix}_1d.png",
                             title='dQ/dx distribution',
                             xlabel=ylabel)
    npz = pc.save_results(f"{args.out_prefix}.npz", centers, med, err, fit)
    print(f"Wrote {png}")
    print(f"Wrote {png2d}")
    print(f"Wrote {png1d}")
    print(f"Wrote {npz}")


if __name__ == '__main__':
    main()
