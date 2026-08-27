"""Shared electron-lifetime ("purity") math for the SingleCube purity tools.

This is the single source of truth for the lifetime extraction, factored out so the
two front-ends stay consistent with each other and with the original C++
``PurityStudy.cpp``:

  * ``purity_from_ac.py``  -- runs on the clustered anode-cathode selection output
                              (``Anode_cathode/*_ac_track_data.h5``).
  * ``quick_purity.py``    -- runs straight on the raw LArPix PACMAN file for a fast
                              quick-look during data-taking.

The recipe mirrors ``PurityStudy.cpp``: for each anode-cathode-crossing track, sum
charge into bins of drift time, normalise by an effective per-track pitch to form
dQ/dx, take the *median* dQ/dx in each drift-time bin across all tracks, then fit

    dQ/dx(t) = (dQ0/dx) * exp(-t / tau)

and report the electron lifetime ``tau``. Because tau comes only from the *shape* of
the falloff, any constant scale on the charge cancels -- the absolute charge
calibration is irrelevant for the lifetime (it only matters for a dE/dx cross-check).

Pure Python: numpy + scipy + matplotlib. No ROOT.
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Constants (mirroring consts.py and PurityStudy.cpp)
# ---------------------------------------------------------------------------
N_BINS = 15                      # PurityStudy.cpp numBins
V_DRIFT = 0.1544                 # cm/us   (consts.v_drift, ~465.5 V/cm)
DRIFT_LENGTH_CM = 30.27          # cm      (consts.drift_distance)
DRIFT_TIME_MAX_US = DRIFT_LENGTH_CM / V_DRIFT   # ~196.05 us, full anode->cathode drift

PEDESTAL_ADC = 78.0              # PurityStudy.cpp pedestal (only for the raw-ADC path)
GAIN_E_PER_ADC = 250.0 * 3.9     # PurityStudy.cpp gain (ADC -> e-); used only for a
                                 # ke-/cm y-axis on the raw path, never for tau.

# PurityStudy.cpp anchors at 500 V/cm; other fields are reached via a pol5 velocity
# model + two ad-hoc branches (see drift_params).
DRIFT_VEL_E500_CM_US = 0.155     # driftVelE500
DRIFT_TIME_MAX_E500_US = 186.0   # driftTimeMaxE500
DRIFT_TIME_RANGE_E500_US = 8.0   # driftTimeRangeE500 (AC-window half-width)

# pol5 drift-velocity fit coefficients (PurityStudy.cpp:99-105), argument x = E/1000.
_DRIFTVEL_POL5 = (0.0, 5.53416, -6.53093, 3.20752, 0.389696, -0.556184)

# CAP for the RMS error estimate on the ke-/cm path: PurityStudy fills LifetimeHist2D
# with y in [0, 160] ke-/cm, so its TProfile error excludes dQ/dx above this.
DQDX_CAP_KE_PER_CM = 160.0


def drift_params(efield=500.0):
    """Drift velocity / full-drift-time / AC-window half-width for an E-field.

    Faithfully reproduces PurityStudy.cpp:99-119: a pol5 model for the drift velocity
    plus the two ad-hoc >500 / <500 correction branches. Returns
    ``(drift_vel_cm_us, drift_time_max_us, drift_time_range_us)``.

    At 500 V/cm this is exactly ``(0.155, 186.0, 8.0)``.
    """
    def _pol5(x):
        return sum(c * x ** i for i, c in enumerate(_DRIFTVEL_POL5))

    corr = _pol5(efield / 1000.0) / _pol5(0.5)
    if efield > 500.0:
        corr *= 1.0 - ((141.5 - 140.325) / 141.5) * ((efield - 500.0) / (1000.0 - 500.0))
    elif efield < 500.0:
        corr *= 1.0 + ((330.0 - 320.0) / 320.0) * ((500.0 - efield) / (500.0 - 200.0))

    drift_vel = DRIFT_VEL_E500_CM_US * corr
    drift_time_max = DRIFT_TIME_MAX_E500_US / corr
    drift_time_range = DRIFT_TIME_RANGE_E500_US / corr
    return drift_vel, drift_time_max, drift_time_range


def is_ac_crosser(span_us, drift_time_max_us, drift_time_range_us):
    """True if a track's drift-time span marks it a full anode-cathode crosser.

    Mirrors the PurityStudy.cpp:169 window: ``dtm - range <= span <= dtm + range``.
    """
    return (drift_time_max_us - drift_time_range_us) <= span_us <= \
           (drift_time_max_us + drift_time_range_us)


# ---------------------------------------------------------------------------
# Per-track helpers
# ---------------------------------------------------------------------------
def bin_index(drift_time_us, n_bins=N_BINS, drift_time_max_us=DRIFT_TIME_MAX_US):
    """Drift-time bin index for each hit (matches PurityStudy's round(x-0.5) = floor)."""
    idx = np.floor(np.asarray(drift_time_us) / drift_time_max_us * n_bins).astype(int)
    return np.clip(idx, 0, n_bins - 1)


def track_pitch_cm(dxy_mm, dt_us, n_bins=N_BINS,
                   drift_time_max_us=DRIFT_TIME_MAX_US, v_drift=V_DRIFT):
    """Length (cm) of the track segment that spans a single drift-time bin.

    This is the "dx" in dQ/dx. It mirrors the per-track effective pitch in
    ``PurityStudy.cpp`` -- (fraction of the track contained in one drift-time bin) x
    (full 3D track length) -- but in self-consistent cm units (the C++ mixes mm and
    cm; harmless for tau since the pitch is constant across a track's bins, but we
    keep it clean here).

    ``dxy_mm``: transverse (anode-plane) start->end distance in mm.
    ``dt_us`` : the track's drift-time span (t_max - t_min) in us.
    """
    if dt_us <= 0:
        return np.nan
    bin_width_us = drift_time_max_us / n_bins
    dxy_cm = dxy_mm / 10.0
    dz_cm = v_drift * dt_us
    length_cm = np.hypot(dxy_cm, dz_cm)
    return (bin_width_us / dt_us) * length_cm


# ---------------------------------------------------------------------------
# Accumulator: collect dQ/dx per drift-time bin over many tracks
# ---------------------------------------------------------------------------
class LifetimeAccumulator:
    """Accumulate per-bin dQ/dx values across anode-cathode-crossing tracks."""

    def __init__(self, n_bins=N_BINS, drift_time_max_us=DRIFT_TIME_MAX_US,
                 charge_scale=1.0, v_drift=V_DRIFT):
        self.n_bins = n_bins
        self.drift_time_max_us = drift_time_max_us
        self.v_drift = v_drift                     # cm/us, for the pitch z-term
        self.charge_scale = charge_scale          # e.g. GAIN/1000 for ke-/cm on raw ADC
        self._bins = [[] for _ in range(n_bins)]
        self.n_tracks = 0

    def add_track(self, drift_time_us, charge, dxy_mm, dt_us):
        """Add one track. Returns True if it contributed.

        ``drift_time_us`` / ``charge``: per-hit arrays (drift time relative to the
        track's first hit, and the hit charge in any consistent unit).
        ``dxy_mm`` / ``dt_us``: the track's transverse extent and drift-time span,
        used for the pitch.
        """
        pitch = track_pitch_cm(dxy_mm, dt_us, self.n_bins, self.drift_time_max_us,
                               self.v_drift)
        if not np.isfinite(pitch) or pitch <= 0:
            return False
        idx = bin_index(drift_time_us, self.n_bins, self.drift_time_max_us)
        q = np.bincount(idx, weights=np.asarray(charge, dtype='f8'),
                        minlength=self.n_bins)
        # Drop the first and last bins, like PurityStudy.cpp (edge effects).
        for i in range(1, self.n_bins - 1):
            if q[i] > 0:
                self._bins[i].append(self.charge_scale * q[i] / pitch)
        self.n_tracks += 1
        return True

    def profile(self, robust=False, cap=None):
        """Return (bin_centers_us, median_dqdx, dqdx_err) over the bins.

        Error on the median ~ sqrt(pi/2) * sigma / sqrt(N). Two ways to estimate sigma:

        ``robust=False`` (default): the population RMS (std, ddof=0), matching the
        PurityStudy.cpp TProfile ``GetBinError`` (spread/sqrt(N)). If ``cap`` is given
        the RMS is computed only over ``0 <= v <= cap``, mirroring PurityStudy's
        LifetimeHist2D y-range of [0, 160] ke-/cm (values above go to overflow and are
        excluded from the profile error). The median itself always uses all values.

        ``robust=True``: sigma from the MAD (1.4826*MAD) so heavy dQ/dx tails don't
        inflate the error. Kept for a robust cross-check; the median is unaffected.
        """
        centers = (np.arange(self.n_bins) + 0.5) * self.drift_time_max_us / self.n_bins
        med = np.full(self.n_bins, np.nan)
        err = np.full(self.n_bins, np.nan)
        for i in range(self.n_bins):
            v = np.asarray(self._bins[i], dtype='f8')
            if v.size == 0:
                continue
            med[i] = np.median(v)
            if v.size <= 1:
                err[i] = med[i]
                continue
            if robust:
                mad = np.median(np.abs(v - med[i]))
                sigma = 1.4826 * mad if mad > 0 else np.std(v)
                n_eff = v.size
            else:
                vw = v[(v >= 0.0) & (v <= cap)] if cap is not None else v
                if vw.size < 1:
                    vw = v
                sigma = np.std(vw)            # population RMS (ddof=0), like TProfile
                n_eff = vw.size
            err[i] = np.sqrt(np.pi / 2.0) * sigma / np.sqrt(n_eff)
        return centers, med, err


# ---------------------------------------------------------------------------
# Fit
# ---------------------------------------------------------------------------
def _chi2_profiled_in_tau(tau, t, y, w):
    """chi2 of A*exp(-t/tau) at fixed tau, with A profiled out analytically.

    For fixed tau the model is linear in A, so the weighted-least-squares optimum is
    A_hat = sum(w y g) / sum(w g^2) with g = exp(-t/tau). Returns (chi2, A_hat).
    """
    g = np.exp(-t / tau)
    denom = np.sum(w * g * g)
    if denom <= 0 or not np.isfinite(denom):
        return np.inf, 0.0
    a_hat = np.sum(w * y * g) / denom
    r = y - a_hat * g
    return float(np.sum(w * r * r)), float(a_hat)


def _minos_tau_bound(t, y, w, tau0, chi2_min, sign, seed_step):
    """Find the tau where the profiled chi2 rises to chi2_min + 1, on one side.

    ``sign`` = +1 for the upper bound, -1 for the lower. Mirrors ROOT MINOS: profile
    the chi2 in tau (re-minimising A at each step) and locate the delta-chi2 = 1
    crossing. Returns the *offset* |tau_bound - tau0| (in the same units as tau), or
    ``inf`` if the parameter is unbounded on that side (no crossing found).
    """
    target = chi2_min + 1.0
    step = seed_step if (np.isfinite(seed_step) and seed_step > 0) else 0.1 * tau0
    step = max(step, 1e-6 * max(tau0, 1.0))

    # 1) expand outward until the crossing is bracketed
    lo = tau0                    # chi2(lo) < target
    hi = None                    # chi2(hi) >= target
    off = step
    for _ in range(200):
        cand = tau0 + sign * off
        if cand <= 0:            # tau must stay positive; low side saturates as tau->0+
            cand = 0.5 * (lo if sign < 0 else tau0)
            if cand <= 0:
                break
        c2, _ = _chi2_profiled_in_tau(cand, t, y, w)
        if c2 >= target:
            hi = cand
            break
        lo = cand
        off *= 2.0
    if hi is None:
        return np.inf            # unbounded on this side (e.g. tau -> infinity)

    # 2) bisect between lo (below target) and hi (at/above target)
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        c2, _ = _chi2_profiled_in_tau(mid, t, y, w)
        if c2 < target:
            lo = mid
        else:
            hi = mid
    return abs(0.5 * (lo + hi) - tau0)


def fit_lifetime(centers, med, err, drift_time_max_us=DRIFT_TIME_MAX_US,
                 absolute_sigma=True, minos=True):
    """Fit dQ/dx(t) = A * exp(-t/tau). Returns a dict with tau in us and ms.

    Uncertainty on tau is computed the same way as ROOT's ``PurityStudy.cpp`` fit:

      * the per-bin errors are treated as **absolute** (``absolute_sigma=True``), i.e.
        the covariance is NOT rescaled by the reduced chi2 -- ROOT's default;
      * asymmetric **MINOS-style** errors are found by profiling the chi2 in tau (with
        the amplitude A re-minimised at each step) and locating the delta-chi2 = 1
        crossing on each side (``minos=True``).

    ``tau_err_ms`` remains the symmetric parabolic (covariance) error for backward
    compatibility; ``tau_err_pos_ms`` / ``tau_err_neg_ms`` are the asymmetric MINOS
    errors and are what the plots / printout report. Set ``absolute_sigma=False`` and
    ``minos=False`` to recover the old scipy-default behaviour.
    """
    from scipy.optimize import curve_fit

    mask = np.isfinite(med) & (med > 0)
    t = np.asarray(centers)[mask]
    y = np.asarray(med)[mask]
    if t.size < 2:
        raise RuntimeError(f"not enough populated bins to fit (got {t.size})")

    sy = np.asarray(err, dtype='f8')[mask].copy()
    good = np.isfinite(sy) & (sy > 0)
    have_errors = bool(good.any())
    if have_errors:
        sy[~good] = np.median(sy[good])       # patch missing errors
    else:
        sy = None

    def model(x, A, tau):
        return A * np.exp(-x / tau)

    popt, pcov = curve_fit(model, t, y, p0=[float(np.max(y)), 1000.0],
                           sigma=sy, absolute_sigma=(absolute_sigma and have_errors),
                           maxfev=20000)
    A, tau = popt
    perr = np.sqrt(np.diag(pcov))
    tau_err = float(perr[1])                   # symmetric parabolic error

    tau_err_pos = tau_err
    tau_err_neg = tau_err
    if minos and have_errors and tau > 0:
        w = 1.0 / np.asarray(sy, dtype='f8') ** 2
        chi2_min, _ = _chi2_profiled_in_tau(tau, t, y, w)
        tau_err_pos = _minos_tau_bound(t, y, w, tau, chi2_min, +1, tau_err)
        tau_err_neg = _minos_tau_bound(t, y, w, tau, chi2_min, -1, tau_err)

    return {
        'A': float(A),
        'tau_us': float(tau),
        'tau_ms': float(tau / 1000.0),
        'tau_err_us': tau_err,
        'tau_err_ms': tau_err / 1000.0,
        'tau_err_pos_us': float(tau_err_pos),
        'tau_err_neg_us': float(tau_err_neg),
        'tau_err_pos_ms': float(tau_err_pos / 1000.0),
        'tau_err_neg_ms': float(tau_err_neg / 1000.0),
        'dqdx_at_half_drift': float(A * np.exp(-(drift_time_max_us / 2.0) / tau)),
        'n_points': int(t.size),
        'n_tracks': None,   # filled in by callers if desired
    }


def _tau_label(fit):
    """LaTeX-ish tau string, asymmetric when MINOS errors are present and differ."""
    pos = fit.get('tau_err_pos_ms')
    neg = fit.get('tau_err_neg_ms')
    if pos is not None and neg is not None and np.isfinite(pos) and np.isfinite(neg) \
            and abs(pos - neg) > 0.005 * max(abs(fit['tau_ms']), 1e-9):
        return (r"$\tau$ = "
                f"{fit['tau_ms']:.2f}$^{{+{pos:.2f}}}_{{-{neg:.2f}}}$ ms")
    return (r"$\tau$ = "
            f"{fit['tau_ms']:.2f} $\\pm$ {fit['tau_err_ms']:.2f} ms")


# ---------------------------------------------------------------------------
# Output: plot + small results file
# ---------------------------------------------------------------------------
def plot_lifetime(centers, med, err, fit, out_png, title='',
                  ylabel='Median dQ/dx [arb.]', drift_time_max_us=DRIFT_TIME_MAX_US):
    """Save the median-dQ/dx-vs-drift-time plot with the fitted exponential."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    centers = np.asarray(centers)
    med = np.asarray(med)
    err = np.asarray(err)
    mask = np.isfinite(med)

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.errorbar(centers[mask], med[mask],
                yerr=np.where(np.isfinite(err[mask]), err[mask], 0.0),
                fmt='ko', capsize=3, label='median dQ/dx')
    tt = np.linspace(0.0, drift_time_max_us, 200)
    ax.plot(tt, fit['A'] * np.exp(-tt / fit['tau_us']), 'r-', lw=2,
            label="fit  " + _tau_label(fit))
    ax.set_xlabel('Drift time [$\\mu$s]')
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    ax.set_ylim(bottom=0.0)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return out_png


def plot_dqdx_2d(acc, out_png, fit=None, title='',
                 ylabel='dQ/dx [arb.]', drift_time_max_us=DRIFT_TIME_MAX_US,
                 n_ybins=50):
    """Save a 2D histogram of all per-track-bin dQ/dx values vs drift time.

    Mirrors PurityStudy.cpp ``LifetimeHist2D.png``. Every individual (drift-time-bin,
    dQ/dx) data point from every track is entered; the color shows count density. If
    ``fit`` is provided the fitted exponential is overlaid.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    centers = (np.arange(acc.n_bins) + 0.5) * drift_time_max_us / acc.n_bins
    ts, qs = [], []
    for i in range(1, acc.n_bins - 1):      # skip edge bins (PurityStudy does the same)
        for q in acc._bins[i]:
            ts.append(centers[i])
            qs.append(q)
    ts = np.asarray(ts, 'f8')
    qs = np.asarray(qs, 'f8')

    if ts.size == 0:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, 'no data', ha='center', va='center', transform=ax.transAxes)
    else:
        ymax = float(np.percentile(qs, 98)) * 1.1
        fig, ax = plt.subplots(figsize=(8, 5))
        h, xedges, yedges, img = ax.hist2d(
            ts, qs, bins=[acc.n_bins, n_ybins],
            range=[[0.0, drift_time_max_us], [0.0, ymax]],
            cmap='viridis')
        fig.colorbar(img, ax=ax, label='Counts per bin')
        if fit is not None:
            tt = np.linspace(0.0, drift_time_max_us, 200)
            ax.plot(tt, fit['A'] * np.exp(-tt / fit['tau_us']), 'r-', lw=2,
                    label="fit  " + _tau_label(fit))
            ax.legend(framealpha=0.7)
        ax.set_xlim(0.0, drift_time_max_us)
        ax.set_ylim(0.0, ymax)

    ax.set_xlabel('Drift time [$\\mu$s]')
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return out_png


def plot_dqdx_1d(acc, out_png, title='', xlabel='dQ/dx [arb.]', n_bins=50):
    """Save a 1D histogram of all per-track-bin dQ/dx values.

    Mirrors the style of PurityStudy.cpp ``dEdxHist.png`` but for dQ/dx (no
    energy/recombination conversion, so it works for any charge unit). Only inner bins
    are included (first and last are dropped, consistent with the rest of the analysis).
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    qs = []
    for i in range(1, acc.n_bins - 1):
        qs.extend(acc._bins[i])
    qs = np.asarray(qs, 'f8')

    fig, ax = plt.subplots(figsize=(6, 4))
    if qs.size == 0:
        ax.text(0.5, 0.5, 'no data', ha='center', va='center', transform=ax.transAxes)
    else:
        qmax = float(np.percentile(qs, 99)) * 1.05
        ax.hist(qs[qs <= qmax], bins=n_bins, range=(0.0, qmax),
                color='steelblue', edgecolor='none')
        ax.axvline(float(np.median(qs)), color='r', linestyle='--', lw=1.5,
                   label=f'median = {np.median(qs):.2f}')
        ax.legend()
        ax.set_xlim(0.0, qmax)
    ax.set_xlabel(xlabel)
    ax.set_ylabel('Tracks / bin')
    if title:
        ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return out_png


def save_results(path, centers, med, err, fit):
    """Save the profile + fit to a small .npz so the plot can be reproduced/merged."""
    np.savez(path,
             bin_centers_us=np.asarray(centers),
             median_dqdx=np.asarray(med),
             dqdx_err=np.asarray(err),
             tau_ms=fit['tau_ms'],
             tau_err_ms=fit['tau_err_ms'],
             tau_err_pos_ms=fit.get('tau_err_pos_ms', fit['tau_err_ms']),
             tau_err_neg_ms=fit.get('tau_err_neg_ms', fit['tau_err_ms']),
             A=fit['A'],
             n_tracks=(fit.get('n_tracks') if fit.get('n_tracks') is not None else -1))
    return path


def print_result(fit, n_tracks=None, ylabel_unit='arb.'):
    """Print the headline lifetime result to the terminal (MINOS asymmetric errors)."""
    if n_tracks is not None:
        print(f"Anode-cathode crossers used: {n_tracks}")
    pos = fit.get('tau_err_pos_ms', fit['tau_err_ms'])
    neg = fit.get('tau_err_neg_ms', fit['tau_err_ms'])
    pos_str = 'inf' if not np.isfinite(pos) else f"{pos:.3f}"
    print(f"Electron lifetime (purity):  {fit['tau_ms']:.3f} +{pos_str} -{neg:.3f} ms")
    print(f"Fit dQ/dx at half drift:     {fit['dqdx_at_half_drift']:.2f} [{ylabel_unit}]")
    print(f"Populated drift-time bins:   {fit['n_points']}")
