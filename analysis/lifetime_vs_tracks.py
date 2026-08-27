#!/usr/bin/env python3
"""Electron lifetime (tau) vs time, one point per N anode-cathode tracks.

Unlike ``lifetime_vs_time.py`` (one point per run file), this bins the anode-cathode
crossers themselves: it pools all AC-crossing tracks from the input run(s), orders
them in time, and fits the electron lifetime for every consecutive group of
``--bin_size`` tracks (default 100). Each group becomes one (time, tau) point, so you
get a finer time series of the argon purity within and across runs. Pass
``--group_by file`` to switch back to one point per input file / data run, which is
the natural view when comparing runs to each other rather than tracking purity
within a run.

Pixels tagged hot in the run database can be dropped before clustering with
``--exclude_pixels`` (a JSON map of file basename -> ["chip-ch", ...]), so a noisy
pixel cannot pull DBSCAN clusters or bias the dQ/dx fit.

It reuses the exact ``quick_purity.py`` pipeline for everything up to the fit --
parse -> DBSCAN cluster (TrackMaker recipe) -> anode-cathode select -- and the shared
``purity_core`` accumulator + exponential fit (with MINOS asymmetric tau errors), so a
100-track point here is the same measurement ``quick_purity`` would report if you fed
it exactly those 100 tracks.

Time axis
---------
Each track is stamped with its run's wall-clock time from the filename
(``..._YYYY_MM_DD_HH_MM_SS_...``). When a single run yields many crossers, its tracks
are spread linearly across ``--file_seconds`` (the run duration) so the points don't
all stack on one x; across runs the real filename timestamps space them out. Each
plotted point sits at the mean time of its group.

Examples
--------
    # a day of converted runs, one point per 100 AC tracks
    python3 lifetime_vs_tracks.py '/data/converted_..._2026_06_13_*.h5' \\
        --converted -E 465.5 --bin_size 100 -o lifetime_tracks_20260613

    # raw self-trigger files, 60 s runs, spread within each file
    python3 lifetime_vs_tracks.py 'raw_self_trigger_data/*-raw_*.h5' \\
        -E 465.5 --file_seconds 60
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

import numpy as np

import purity_core as pc
import quick_purity as qp

# Filename run timestamp, e.g. ..._2026_06_13_18_33_24_MDT.h5
_TS_RE = re.compile(r'(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})')


def parse_timestamp(path):
    """datetime from the filename's ..._YYYY_MM_DD_HH_MM_SS_... stamp, else file mtime."""
    m = _TS_RE.search(os.path.basename(path))
    if m:
        try:
            return datetime(*[int(g) for g in m.groups()])
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return None


def load_exclude_pixels(path):
    """Load a {basename: ["chip-ch", ...]} JSON -> {basename: {(chip, ch), ...}}.

    Written by the GUI from the run database's per-run hot-pixel tags, so each
    file drops only the pixels marked hot on its own run. Files absent from the
    map are parsed unfiltered.
    """
    with open(path) as f:
        raw = json.load(f)
    out = {}
    for name, keys in (raw or {}).items():
        pix = set()
        for k in keys or []:
            try:
                chip, ch = str(k).split('-')
                pix.add((int(chip), int(ch)))
            except (ValueError, TypeError):
                continue
        if pix:
            out[name] = pix
    return out


def excl_hash(pixels):
    """Short stable hash of an exclusion set, for the track cache key."""
    if not pixels:
        return ''
    joined = ','.join('%d-%d' % p for p in sorted(pixels))
    return hashlib.sha1(joined.encode()).hexdigest()[:12]


def collect_ac_tracks(x, y, t, c, efield, file_time_s):
    """Cluster one file's hits and collect its anode-cathode crossers.

    Mirrors ``quick_purity._cluster_one_file`` but, instead of folding each track
    straight into a shared accumulator, keeps the per-track data so we can regroup
    tracks into fixed-count time bins afterwards. Returns ``(tracks, n_clusters)``.

    Each track dict holds ``file_time_s`` (this run's wall-clock base), ``frac``
    (0..1 position of the track within the file, for optional intra-file time
    spread applied later), ``file_seq``, and the hit data (drift us, charge ADC,
    hx/hy mm, dxy_mm, span_us). The absolute ``time_s`` and global ``seq`` are
    assigned by the caller so this per-file result can be cached independently of
    ``--file_seconds`` and of what other files are present.
    """
    _, drift_time_max_us, drift_time_range_us = pc.drift_params(efield)
    raw = []
    n_clusters = 0
    n = x.size
    for s in range(0, n, qp.DATA_CHUNK_SIZE):
        e = min(s + qp.DATA_CHUNK_SIZE, n)
        cx, cy, ct, cc = x[s:e], y[s:e], t[s:e], c[s:e]
        for members in qp.find_clusters(cx, cy, ct):
            n_clusters += 1
            mt = ct[members]
            t_min = mt.min()
            span_us = (mt.max() - t_min) * qp.TICK_US
            if not pc.is_ac_crosser(span_us, drift_time_max_us, drift_time_range_us):
                continue
            drift_time_us = (mt - t_min) * qp.TICK_US
            i_min = np.argmin(mt)
            i_max = np.argmax(mt)
            dxy_mm = float(np.hypot(cx[members][i_max] - cx[members][i_min],
                                    cy[members][i_max] - cy[members][i_min]))
            raw.append((drift_time_us, cc[members], dxy_mm, span_us,
                        cx[members], cy[members]))

    m = len(raw)
    tracks = []
    for i, (drift, charge, dxy, span, hx, hy) in enumerate(raw):
        tracks.append({
            'file_time_s': file_time_s,
            'frac': (i / (m - 1)) if m > 1 else 0.0,
            'file_seq': i,
            'drift': drift, 'charge': charge, 'dxy': dxy, 'span': span,
            'hx': hx, 'hy': hy,
        })
    return tracks, n_clusters


class TrackCache:
    """SQLite cache of per-file anode-cathode tracks so files are parsed once.

    Keyed by (absolute path, efield) with the file's mtime+size, so an unchanged
    file is loaded from the cache instead of re-parsed/re-clustered on the next
    plot. Only the E-field affects which clusters count as AC crossers, so it is
    part of the key; ``--bin_size``, ``--group_by`` and ``--file_seconds`` are
    applied at assembly time and never invalidate the cache.

    Excluded (hot) pixels change which *hits* are parsed, so they must invalidate
    too: each row stores an ``excl_key`` hash of that file's exclusion list and a
    row whose hash no longer matches is treated as a miss and re-parsed. Only one
    exclusion variant is cached per (path, efield) at a time, which is fine --
    hot-pixel tags change rarely and ``put()`` already replaces the row.
    """

    def __init__(self, path, efield):
        self.efield = float(efield)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS files(path TEXT, efield REAL, mtime REAL, "
            "size INTEGER, n_tracks INTEGER, file_time_s REAL, added TEXT, "
            "excl_key TEXT DEFAULT '', PRIMARY KEY(path, efield))")
        # migrate caches written before excl_key existed
        try:
            self.conn.execute("ALTER TABLE files ADD COLUMN excl_key TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass                      # column already present
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS tracks(path TEXT, efield REAL, "
            "file_seq INTEGER, frac REAL, dxy REAL, span REAL, "
            "drift BLOB, charge BLOB, hx BLOB, hy BLOB)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS tracks_key ON tracks(path, efield)")
        self.conn.commit()

    def get(self, path, excl_key=''):
        """Cached tracks for ``path`` if present and unchanged, else None."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        row = self.conn.execute(
            "SELECT mtime, size, file_time_s, excl_key FROM files "
            "WHERE path=? AND efield=?", (path, self.efield)).fetchone()
        if not row or abs(row[0] - st.st_mtime) > 1e-6 or int(row[1]) != st.st_size:
            return None
        if (row[3] or '') != excl_key:
            return None               # hot-pixel set changed -> must re-parse
        file_time_s = row[2]
        out = []
        for r in self.conn.execute(
                "SELECT file_seq, frac, dxy, span, drift, charge, hx, hy "
                "FROM tracks WHERE path=? AND efield=? ORDER BY file_seq",
                (path, self.efield)):
            out.append({
                'file_time_s': file_time_s, 'frac': r[1], 'file_seq': r[0],
                'dxy': r[2], 'span': r[3],
                'drift': np.frombuffer(r[4], 'f4').astype('f8'),
                'charge': np.frombuffer(r[5], 'f4').astype('f8'),
                'hx': np.frombuffer(r[6], 'f4'),
                'hy': np.frombuffer(r[7], 'f4')})
        return out

    def put(self, path, file_time_s, tracks, excl_key=''):
        try:
            st = os.stat(path)
        except OSError:
            return
        self.conn.execute("DELETE FROM files WHERE path=? AND efield=?",
                          (path, self.efield))
        self.conn.execute("DELETE FROM tracks WHERE path=? AND efield=?",
                          (path, self.efield))
        self.conn.execute(
            "INSERT INTO files(path, efield, mtime, size, n_tracks, file_time_s, "
            "added, excl_key) VALUES(?,?,?,?,?,?,?,?)",
            (path, self.efield, st.st_mtime, st.st_size, len(tracks),
             file_time_s, datetime.now().isoformat(timespec='seconds'), excl_key))
        rows = [(path, self.efield, tr['file_seq'], tr['frac'], tr['dxy'],
                 tr['span'],
                 np.asarray(tr['drift'], 'f4').tobytes(),
                 np.asarray(tr['charge'], 'f4').tobytes(),
                 np.asarray(tr['hx'], 'f4').tobytes(),
                 np.asarray(tr['hy'], 'f4').tobytes()) for tr in tracks]
        self.conn.executemany(
            "INSERT INTO tracks VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


def fit_group(group, efield):
    """Fit one group of tracks -> fit dict (or None if the fit fails)."""
    drift_vel, drift_time_max_us, _ = pc.drift_params(efield)
    acc = pc.LifetimeAccumulator(charge_scale=pc.GAIN_E_PER_ADC / 1000.0,
                                 drift_time_max_us=drift_time_max_us,
                                 v_drift=drift_vel)
    used = 0
    for tr in group:
        if acc.add_track(tr['drift'], tr['charge'], tr['dxy'], tr['span']):
            used += 1
    if used < 2:
        return None
    centers, med, err = acc.profile(cap=pc.DQDX_CAP_KE_PER_CM)
    try:
        fit = pc.fit_lifetime(centers, med, err,
                              drift_time_max_us=acc.drift_time_max_us)
    except Exception:
        # curve_fit can also raise ValueError (e.g. a degenerate profile from a
        # thin/edge-case group) on top of the documented convergence RuntimeError --
        # either way this group just doesn't produce a point, same as `used < 2`
        # above. Narrower catches let a single bad group take down build_series
        # for the whole run (see write_partial's blanket except in main()).
        return None
    fit['n_tracks'] = used
    return fit


def _iter_groups(tracks, bin_size, group_by):
    """Yield the track groups that each become one series point.

    ``group_by='tracks'``: consecutive slices of ``bin_size`` tracks (the default
    fixed-statistics binning). ``group_by='file'``: one group per input file, so
    every data run becomes a single point regardless of how many tracks it has.
    """
    if group_by == 'file':
        by_file = {}
        for tr in tracks:
            by_file.setdefault(tr['file_idx'], []).append(tr)
        for idx in sorted(by_file):
            yield by_file[idx]
    else:
        for start in range(0, len(tracks), bin_size):
            yield tracks[start:start + bin_size]


def build_series(tracks, bin_size, efield, min_tracks, group_by='tracks'):
    """Sort tracks by time and fit each group (``bin_size`` tracks, or per file).

    Side effect: every track dict gets a ``group`` key -- the 0-based index of
    the series point it contributed to (matching JSON ``point`` - 1), or ``-1``
    if its group was dropped (too small) or its fit failed. This is what lets
    ``--save_tracks`` link saved tracks back to plot points.
    """
    tracks.sort(key=lambda r: (r['time_s'], r['seq']))
    for tr in tracks:
        tr['group'] = -1
    points = []
    for group in _iter_groups(tracks, bin_size, group_by):
        if len(group) < min_tracks:
            # a short trailing bin ends the series; a thin file is just skipped
            if group_by == 'file':
                continue
            break
        fit = fit_group(group, efield)
        if fit is None:
            continue
        for tr in group:
            tr['group'] = len(points)
        times = np.array([tr['time_s'] for tr in group])
        points.append({
            'time_s': float(times.mean()),
            't_start_s': float(times.min()),
            't_end_s': float(times.max()),
            'tau_ms': fit['tau_ms'],
            'tau_err_pos_ms': fit['tau_err_pos_ms'],
            'tau_err_neg_ms': fit['tau_err_neg_ms'],
            'dqdx_at_half_drift': fit['dqdx_at_half_drift'],
            'n_tracks': fit['n_tracks'],
        })
    return points


def plot_series(points, out_png, title='', max_tau_ms=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    times = [datetime.fromtimestamp(p['time_s']) for p in points]
    tau = np.array([p['tau_ms'] for p in points])
    pos = np.array([p['tau_err_pos_ms'] for p in points])
    neg = np.array([p['tau_err_neg_ms'] for p in points])
    idx = np.arange(1, len(points) + 1)

    ymax = max_tau_ms if max_tau_ms is not None else float(np.nanmax(tau)) * 2.0
    pos_capped = np.where(np.isfinite(pos), pos, ymax)

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.errorbar(times, tau, yerr=[neg, pos_capped], fmt='none',
                ecolor='0.6', elinewidth=1, capsize=3, zorder=1)
    sc = ax.scatter(times, tau, c=idx, cmap='viridis', s=45, zorder=2,
                    edgecolor='k', linewidth=0.4)
    fig.colorbar(sc, ax=ax, label='group index (time order)')
    ax.set_ylabel(r'Electron lifetime  $\tau$  [ms]')
    ax.set_xlabel('Time')
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


def save_csv(points, path):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['point', 'time', 'time_start', 'time_end', 'tau_ms',
                    'tau_err_pos_ms', 'tau_err_neg_ms', 'n_tracks'])
        for i, p in enumerate(points, 1):
            w.writerow([i, datetime.fromtimestamp(p['time_s']).isoformat(),
                        datetime.fromtimestamp(p['t_start_s']).isoformat(),
                        datetime.fromtimestamp(p['t_end_s']).isoformat(),
                        f"{p['tau_ms']:.6g}", f"{p['tau_err_pos_ms']:.6g}",
                        f"{p['tau_err_neg_ms']:.6g}", p['n_tracks']])
    return path


def save_tracks_npz(tracks, path, meta):
    """Save per-track hits + metadata so a GUI can display tracks and refit groups.

    Variable-length hit arrays are stored concatenated with an ``offsets`` index:
    track ``i``'s hits are ``hits_*[offsets[i]:offsets[i+1]]``. ``group`` links each
    track to its series point (0-based; -1 = not in any plotted point).
    """
    n = len(tracks)
    offsets = np.zeros(n + 1, dtype='i8')
    for i, tr in enumerate(tracks):
        offsets[i + 1] = offsets[i] + len(tr['drift'])
    total = int(offsets[-1])
    hits_x = np.empty(total, dtype='f4')
    hits_y = np.empty(total, dtype='f4')
    hits_drift = np.empty(total, dtype='f4')
    hits_q = np.empty(total, dtype='f4')
    for i, tr in enumerate(tracks):
        s, e = offsets[i], offsets[i + 1]
        hits_x[s:e] = tr['hx']
        hits_y[s:e] = tr['hy']
        hits_drift[s:e] = tr['drift']
        hits_q[s:e] = tr['charge']
    # atomic write: a poller (the GUI's TrackStore) must never see a torn zip
    # -- np.savez_compressed given a bare path would write it in place, so we
    # write through a .tmp file object (a string tmp path would get numpy's
    # own ".npz" auto-appended) and os.replace it into place.
    tmp = path + '.tmp'
    with open(tmp, 'wb') as fh:
        np.savez_compressed(
            fh,
            hits_x=hits_x, hits_y=hits_y, hits_drift_us=hits_drift, hits_q=hits_q,
            offsets=offsets,
            seq=np.array([tr['seq'] for tr in tracks], dtype='i4'),
            group=np.array([tr.get('group', -1) for tr in tracks], dtype='i4'),
            time_s=np.array([tr['time_s'] for tr in tracks], dtype='f8'),
            dxy_mm=np.array([tr['dxy'] for tr in tracks], dtype='f8'),
            span_us=np.array([tr['span'] for tr in tracks], dtype='f8'),
            efield=float(meta.get('efield', 500.0)),
            bin_size=int(meta.get('bin_size', 100)),
            group_by=str(meta.get('group_by', 'tracks')))
    os.replace(tmp, path)
    return path


def save_json(points, path, meta):
    def iso(s):
        return datetime.fromtimestamp(s).isoformat()
    out = {'meta': meta,
           'points': [{
               'point': i + 1,
               'time': iso(p['time_s']),
               'time_start': iso(p['t_start_s']),
               'time_end': iso(p['t_end_s']),
               'tau_ms': p['tau_ms'],
               'tau_err_pos_ms': (None if not np.isfinite(p['tau_err_pos_ms'])
                                  else p['tau_err_pos_ms']),
               'tau_err_neg_ms': p['tau_err_neg_ms'],
               'n_tracks': p['n_tracks'],
           } for i, p in enumerate(points)]}
    # atomic write: pollers (the GUI dashboard) must never see a torn file
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, path)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('inputs', nargs='+',
                    help="LArPix .h5 file(s), glob pattern(s), or a mix "
                         "(quote globs so the shell doesn't expand them)")
    ap.add_argument('-o', '--out_prefix', default='lifetime_vs_tracks',
                    help="output prefix for the .png/.csv (default: lifetime_vs_tracks)")
    ap.add_argument('--json', default=None,
                    help="also write the series as JSON to this path")
    ap.add_argument('--save_tracks', default=None,
                    help="also save the per-track hits + group assignment as a "
                         ".npz to this path (enables event display / refits)")
    ap.add_argument('--channelmap', default=qp.DEFAULT_CHANNELMAP)
    ap.add_argument('--converted', action='store_true',
                    help="inputs are converted packets .h5 files (not raw msgs)")
    ap.add_argument('--max_messages', type=int, default=None,
                    help="cap messages/packets parsed per file (default: all)")
    ap.add_argument('-E', '--efield', type=float, default=500.0,
                    help="drift E-field [V/cm]; must match the run (default: 500)")
    ap.add_argument('--bin_size', type=int, default=100,
                    help="anode-cathode tracks per lifetime point (default: 100); "
                         "ignored when --group_by file")
    ap.add_argument('--group_by', choices=('tracks', 'file'), default='tracks',
                    help="what one plotted point is: a fixed number of AC tracks "
                         "(default) or one input file / data run")
    ap.add_argument('--min_tracks', type=int, default=None,
                    help="minimum tracks for a point (default: 1 -- no lower "
                         "limit beyond the >=2 the exponential fit itself needs). "
                         "In tracks mode a short trailing group still becomes a "
                         "point; in file mode a thin file still plots. Raise this "
                         "to require more statistics per point.")
    ap.add_argument('--exclude_pixels', default=None,
                    help="JSON file mapping each input file's basename to a list "
                         "of \"chip-ch\" pixels to drop before clustering (hot "
                         "pixels tagged in the run database)")
    ap.add_argument('--file_seconds', type=float, default=0.0,
                    help="run duration used to spread one file's tracks along the "
                         "time axis (default: 0 = stack at the run timestamp)")
    ap.add_argument('--max_tau_ms', type=float, default=None,
                    help="clamp the y-axis to this tau in ms (default: auto)")
    ap.add_argument('--cache', default=None,
                    help="SQLite cache of per-file anode-cathode tracks. Files "
                         "already cached (same mtime/size/E-field) are loaded "
                         "instead of re-parsed, so re-plotting or adding a run is "
                         "fast and doesn't re-process every file.")
    args = ap.parse_args()

    # No lower limit on tracks per point: a group becomes a point as long as the
    # fit is possible (fit_group still requires >=2 tracks). --min_tracks raises
    # the bar explicitly when more statistics per point are wanted.
    min_tracks = args.min_tracks if args.min_tracks is not None else 1

    files = []
    for p in args.inputs:
        matched = glob.glob(p)
        files.extend(matched if matched else [p])
    files = sorted(set(f for f in files if os.path.isfile(f)))
    if not files:
        sys.exit("No input files found.")

    excl_map = {}
    if args.exclude_pixels:
        try:
            excl_map = load_exclude_pixels(args.exclude_pixels)
        except (OSError, ValueError) as exc:
            sys.exit(f"Could not read --exclude_pixels {args.exclude_pixels}: {exc}")
        if excl_map:
            print(f"Hot-pixel exclusions loaded for {len(excl_map)} file(s).")

    cache = None
    if args.cache:
        cache = TrackCache(args.cache, args.efield)
    cmap = None                    # loaded lazily only if we actually parse
    binning = ("one point per file/run" if args.group_by == 'file'
               else f"{args.bin_size} AC tracks/point")
    print(f"Measuring lifetime vs time at E = {args.efield} V/cm, "
          f"{binning}, over {len(files)} file(s)"
          + (" (cached)" if cache else "") + ".")

    all_tracks = []
    n_clusters_total = 0
    n_cached = n_parsed = 0
    gseq = 0

    def write_partial(n_done, current):
        """Refit + rewrite the series JSON after each file so a dashboard
        polling the file can update the plot progressively. Fitting is cheap
        (ms per point) next to parsing, and save_json is atomic."""
        if not args.json:
            return
        try:
            pts = build_series(list(all_tracks), args.bin_size, args.efield,
                               min_tracks, args.group_by)
        except Exception as exc:
            # Leave the last-written series.json alone rather than clobbering an
            # already-good plot with an empty one -- a polling dashboard would
            # otherwise see the points vanish on a single bad refresh.
            print(f"  [WARN] partial refit after file {n_done} failed ({exc}); "
                  f"keeping the previous plot state.")
            return
        save_json(pts, args.json, {
            'efield': args.efield, 'bin_size': args.bin_size,
            'group_by': args.group_by,
            'n_points': len(pts), 'n_tracks': len(all_tracks),
            'n_files': len(files), 'partial': True,
            'n_files_done': n_done, 'n_files_total': len(files),
            'current_file': current,
            'generated': datetime.now().isoformat(timespec='seconds')})

    for i, f in enumerate(files, 1):
        fabs = os.path.abspath(f)
        ts = parse_timestamp(f)
        if ts is None:
            print(f"  [{i}/{len(files)}] SKIP (no usable time): {os.path.basename(f)}")
            continue
        file_time_s = ts.timestamp()
        excl = excl_map.get(os.path.basename(f), set())
        ekey = excl_hash(excl)

        tracks = cache.get(fabs, ekey) if cache else None
        if tracks is not None:
            n_cached += 1
            tag = "cached"
        else:
            if cmap is None:
                if not os.path.isfile(args.channelmap):
                    sys.exit(f"channelmap not found: {args.channelmap}")
                cmap = qp.load_channelmap(args.channelmap)
            if args.converted:
                x, y, t, c = qp.parse_converted_hits(f, cmap,
                                                     max_packets=args.max_messages,
                                                     exclude_pixels=excl)
            else:
                x, y, t, c = qp.parse_raw_hits(f, cmap,
                                               max_messages=args.max_messages,
                                               exclude_pixels=excl)
            if x.size == 0:
                continue
            tracks, n_clusters = collect_ac_tracks(x, y, t, c, args.efield,
                                                   file_time_s)
            n_clusters_total += n_clusters
            n_parsed += 1
            tag = "parsed" + (f", {len(excl)} hot px" if excl else "")
            if cache:
                cache.put(fabs, file_time_s, tracks, ekey)

        # apply the intra-file time spread + assign a global seq (both cheap,
        # done every run so --file_seconds and the file set can change freely)
        for tr in tracks:
            tr['time_s'] = tr['file_time_s'] + tr['frac'] * args.file_seconds
            tr['seq'] = gseq
            tr['file_idx'] = i          # which run this track came from
            gseq += 1
        all_tracks.extend(tracks)
        print(f"  [{i}/{len(files)}] {ts:%Y-%m-%d %H:%M:%S}  "
              f"{len(tracks)} AC crossers [{tag}]  (running total {len(all_tracks)})")
        if len(files) > 1:
            write_partial(i, os.path.basename(f))

    if cache:
        cache.close()
        print(f"Cache: {n_cached} file(s) reused, {n_parsed} newly parsed.")
    print(f"Total: {n_clusters_total} new clusters, {len(all_tracks)} anode-cathode "
          f"crossers.")
    if len(all_tracks) < min_tracks:
        sys.exit(f"Only {len(all_tracks)} AC crossers (< {min_tracks}); need more "
                 f"data for even one point.")

    points = build_series(all_tracks, args.bin_size, args.efield, min_tracks,
                          args.group_by)
    if not points:
        sys.exit("No lifetime points could be fit from the collected tracks.")

    for i, p in enumerate(points, 1):
        pos = p['tau_err_pos_ms']
        pos_str = 'inf' if not np.isfinite(pos) else f"{pos:.3f}"
        print(f"  point {i}: {datetime.fromtimestamp(p['time_s']):%Y-%m-%d %H:%M:%S}  "
              f"tau = {p['tau_ms']:.3f} +{pos_str} -{p['tau_err_neg_ms']:.3f} ms  "
              f"({p['n_tracks']} tracks)")

    meta = {'efield': args.efield, 'bin_size': args.bin_size,
            'group_by': args.group_by,
            'n_points': len(points), 'n_tracks': len(all_tracks),
            'n_files': len(files),
            'generated': datetime.now().isoformat(timespec='seconds')}
    png = plot_series(points, f"{args.out_prefix}.png",
                      title=f'Electron lifetime vs time '
                            f'({binning}, E = {args.efield:g} V/cm)',
                      max_tau_ms=args.max_tau_ms)
    csv_path = save_csv(points, f"{args.out_prefix}.csv")
    print(f"\n{len(points)} lifetime point(s) from {len(all_tracks)} AC tracks.")
    print(f"Wrote {png}")
    print(f"Wrote {csv_path}")
    if args.json:
        save_json(points, args.json, meta)
        print(f"Wrote {args.json}")
    if args.save_tracks:
        save_tracks_npz(all_tracks, args.save_tracks, meta)
        print(f"Wrote {args.save_tracks}")


if __name__ == '__main__':
    main()
