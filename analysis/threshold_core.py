"""Per-channel trigger-threshold estimation from clustered LArPix files.

Ported from ``charge_trigger_thresholds.py``: keep only hits whose cluster has
``min_hits <= nhit <= max_hits``, histogram each channel's charge, and take the
50% rising-edge crossing of that histogram as the channel's effective trigger
threshold.

Differences from the donor script, none of which change the numbers:

* No matplotlib / tqdm, so the GUI can import this in-process.
* ``file_charge_hist`` builds the per-channel histograms in one vectorized pass
  (a single ``bincount`` over ``pixel_row * n_bins + q_bin``) instead of
  accumulating every hit's charge in a ``defaultdict(list)``. Memory stays flat
  no matter how many files are pooled.
* Histograms are returned rather than thresholds, because histograms are
  **additive across files** -- that is what lets a folder be aggregated
  progressively while still pooling exactly like the script does.
"""

import numpy as np

try:
    import h5py
except ImportError:                                  # pragma: no cover
    h5py = None


# Charge binning, straight from the donor script.
VREF_MV = 1300.78125
VCM_MV = 288.28125
LSB = (VREF_MV - VCM_MV) / 256
BIN_WIDTH_FACTOR = 0.221        # bin width in LSB units
N_Q_BINS = 50
Q_TO_KE = 0.221                 # ADC -> ke- conversion applied to raw q values

MIN_HITS = 50
MAX_HITS = 350

# Pixel keys are packed as ``chip_id * 64 + channel_id``; chip_id is a byte
# (real tile chip ids run past 100), so the code space is 256*64.
N_CODES = 256 * 64


class ThresholdError(Exception):
    """A clustered file could not be read / used."""


def q_bin_edges(n_bins=N_Q_BINS, bin_width_factor=BIN_WIDTH_FACTOR):
    """Charge histogram bin edges [ke-], matching the donor script's binning."""
    bw = LSB * bin_width_factor
    return np.linspace(-0.5 * bw, n_bins * bw, int(n_bins) + 1)


def uid_to_chip_channel(uid):
    """unique_id -> (chip_id, channel_id).

    ``unique_id = ((io_group*256 + io_channel)*256 + chip_id)*64 + channel_id``
    (ndlar_39Ar_reco/charge_reco/build_events.py). The tile spans several
    io_channels but chip ids are unique across them, so -- as everywhere else in
    the GUI -- the pixel key is just (chip, channel). ``_geometry_check`` guards
    that assumption per file.
    """
    uid = np.asarray(uid, dtype=np.int64)
    return (uid >> 6) & 0xFF, uid & 0x3F


def uid_to_combined(uid):
    """unique_id -> the GUI's packed ``chip*64 + channel`` pixel code."""
    chip, ch = uid_to_chip_channel(uid)
    return chip * 64 + ch


def combined_to_key(code):
    """Packed ``chip*64 + channel`` -> the GUI's ``"chip-ch"`` string key."""
    code = int(code)
    return "%d-%d" % (code // 64, code % 64)


def _cluster_nhit_for_hits(cluster_idx, cluster_ids, nhit):
    """nhit of each hit's cluster, plus a mask of hits that matched a cluster.

    ``clusters['id']`` is not guaranteed to be ``0..n-1`` (it isn't in real
    files), so resolve through a sorted search rather than fancy-indexing.
    """
    if cluster_ids.size and np.array_equal(cluster_ids,
                                           np.arange(cluster_ids.size)):
        in_range = (cluster_idx >= 0) & (cluster_idx < cluster_ids.size)
        row = np.where(in_range, cluster_idx, 0)
        return np.where(in_range, nhit[row], -1), in_range
    if not cluster_ids.size:
        return np.full(cluster_idx.shape, -1), np.zeros(cluster_idx.shape, bool)
    sort_order = np.argsort(cluster_ids)
    sorted_ids = cluster_ids[sort_order]
    pos = np.clip(np.searchsorted(sorted_ids, cluster_idx), 0, sorted_ids.size - 1)
    row = sort_order[pos]
    matched = cluster_ids[row] == cluster_idx
    return np.where(matched, nhit[row], -1), matched


def _open(path):
    try:
        return h5py.File(path, "r", locking=False)
    except TypeError:                     # h5py < 3.5
        return h5py.File(path, "r")


def file_charge_hist(path, edges, min_hits=MIN_HITS, max_hits=MAX_HITS,
                     q_to_ke=Q_TO_KE, check_geometry=True):
    """Per-channel charge histograms for one clustered file.

    Returns ``(hist, counts, n_binned, n_unmatched, geom_warning)``. ``hist`` is
    an ``(N_CODES, n_bins)`` int64 array indexed by the packed ``chip*64 + channel``
    code -- a fixed shape, so histograms from different files can simply be
    added together. ``counts`` is the per-channel hit count *including* hits
    whose charge falls outside the histogram range: those contribute no bin (as
    in ``np.histogram``) but the channel still fired, so it must be reported as
    "no threshold found" rather than silently vanishing from the map.
    """
    if h5py is None:
        raise ThresholdError("h5py is not available in this environment")
    edges = np.asarray(edges, dtype="f8")
    n_bins = edges.size - 1
    try:
        with _open(path) as f:
            if "hits" not in f or "clusters" not in f:
                raise ThresholdError(
                    "not a clustered file: needs both 'hits' and 'clusters' "
                    "(keys: %s)" % (", ".join(list(f.keys())[:8]) or "none"))
            hits = f["hits"]
            names = set(hits.dtype.names or ())
            missing = {"unique_id", "q", "cluster_index"} - names
            if missing:
                raise ThresholdError("clustered 'hits' lacks %s"
                                     % ", ".join(sorted(missing)))
            uid = hits["unique_id"][:]
            q_raw = hits["q"][:]
            cluster_idx = hits["cluster_index"][:].astype(np.int64)
            hx = hits["x"][:] if "x" in names else None
            hy = hits["y"][:] if "y" in names else None
            clusters = f["clusters"]
            cluster_ids = clusters["id"][:].astype(np.int64)
            nhit = clusters["nhit"][:].astype(np.int64)
    except ThresholdError:
        raise
    except Exception as exc:
        raise ThresholdError("HDF5 read failed (%s)" % exc)

    nhit_for_hit, matched = _cluster_nhit_for_hits(cluster_idx, cluster_ids, nhit)
    keep = matched & (nhit_for_hit >= min_hits) & (nhit_for_hit <= max_hits)
    n_unmatched = int((~matched).sum())

    code = uid_to_combined(uid[keep])
    q = np.asarray(q_raw[keep], dtype="f8") * q_to_ke
    known = (code >= 0) & (code < N_CODES)
    counts = np.bincount(code[known], minlength=N_CODES).astype(np.int64)

    # np.digitize gives 0 below the first edge and n_bins+1 above the last; both
    # are out of range for the histogram, exactly as np.histogram would drop them.
    qbin = np.digitize(q, edges) - 1
    inside = known & (qbin >= 0) & (qbin < n_bins)

    hist = np.bincount(code[inside] * n_bins + qbin[inside],
                       minlength=N_CODES * n_bins).astype(np.int64)
    hist = hist.reshape(N_CODES, n_bins)

    geom_warning = None
    if check_geometry and hx is not None and hy is not None:
        geom_warning = _geometry_check(uid, hx, hy)
    return hist, counts, int(inside.sum()), n_unmatched, geom_warning


def _geometry_check(uid, hx, hy, sample=20000):
    """Warn if the unique_id decode disagrees with the file's own x/y.

    Every hit on one (chip, channel) must sit at one pixel position. If the
    decode were wrong (a different io_channel packing, say), one decoded key
    would collect hits from several positions -- and the map would be silently
    scrambled rather than obviously broken.
    """
    n = min(int(sample), uid.size)
    if n < 2:
        return None
    code = uid_to_combined(uid[:n])
    xy = np.round(np.stack([np.asarray(hx[:n], "f8"),
                            np.asarray(hy[:n], "f8")], axis=1), 2)
    order = np.argsort(code, kind="stable")
    code_s, xy_s = code[order], xy[order]
    starts = np.flatnonzero(np.r_[True, code_s[1:] != code_s[:-1]])
    bad = 0
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < starts.size else code_s.size
        if np.unique(xy_s[s:e], axis=0).shape[0] > 1:
            bad += 1
    if bad:
        return ("%d of %d decoded channels map to more than one (x, y) -- the "
                "unique_id decode may not match this file's channel mapping"
                % (bad, starts.size))
    return None


def find_threshold_50(bin_contents, bin_edges):
    """50% rising-edge threshold of a charge histogram.

    Locate the peak bin, then find where the histogram first crosses half the
    peak value on the way up, linearly interpolating between adjacent bin
    centers. Returns None if no valid crossing is found.
    """
    bin_contents = np.asarray(bin_contents, dtype=float)
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])

    peak_idx = int(np.argmax(bin_contents))
    peak_val = bin_contents[peak_idx]

    if peak_val <= 0:
        return None

    half_max = 0.5 * peak_val

    crossing_i = None
    for i in range(peak_idx):
        if bin_contents[i] < half_max <= bin_contents[i + 1]:
            crossing_i = i
            break

    if crossing_i is None:
        return None

    x0, x1 = bin_centers[crossing_i], bin_centers[crossing_i + 1]
    y0, y1 = bin_contents[crossing_i], bin_contents[crossing_i + 1]

    return x0 + (half_max - y0) * (x1 - x0) / (y1 - y0)


def thresholds_from_hist(hist, edges, counts):
    """Pooled histograms -> ``{"chip-ch": threshold_or_None}`` for live channels.

    Every channel that fired appears; one that has hits but no usable rising
    edge maps to None, so the caller can report it separately from a channel
    that simply never fired.
    """
    edges = np.asarray(edges, dtype="f8")
    out = {}
    for code in np.flatnonzero(np.asarray(counts) > 0):
        t = find_threshold_50(hist[code], edges)
        out[combined_to_key(code)] = None if t is None else float(t)
    return out


def hits_per_channel(counts):
    """``{"chip-ch": n_hits}`` for channels that contributed any hit."""
    counts = np.asarray(counts)
    return {combined_to_key(c): int(counts[c]) for c in np.flatnonzero(counts > 0)}


def summary_stats(thresholds):
    """mean/median/std/min/max over the channels that got a threshold."""
    vals = np.array([v for v in thresholds.values() if v is not None], dtype="f8")
    if not vals.size:
        return None
    return {"n": int(vals.size), "mean": float(vals.mean()),
            "median": float(np.median(vals)), "std": float(vals.std()),
            "min": float(vals.min()), "max": float(vals.max())}
