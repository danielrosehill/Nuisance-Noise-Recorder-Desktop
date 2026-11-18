"""Frequency-level analysis of a recording.

Ported from the measurement layer of Noise-Pollution-Analysis
(github.com/danielrosehill/Noise-Pollution-Analysis, scripts/analyse_noise.py):
Welch long-term spectrum (~0.5 Hz bins), 1/3-octave bands unweighted and A-weighted,
tonal peaks >= 8 dB over the local floor, harmonic families, envelope periodicity,
L10/L50/L90, excess over a background take. Extended here with an optional per-unit
microphone calibration curve and in-memory results for the GUI.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf
from scipy import signal

THIRD_OCTAVE_CENTRES = [
    12.5, 16, 20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500,
    630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000,
    12500, 16000, 20000,
]

# Rough bands people describe indoor nuisance noise in. Used for the summary only.
BANDS = {
    "infrasound_lt20": (1, 20),
    "low_20_200": (20, 200),
    "lower_mid_200_500": (200, 500),
    "mid_500_2k": (500, 2000),
    "upper_2k_6k": (2000, 6000),
    "high_6k_plus": (6000, 24000),
}


def db(x, floor=1e-12):
    return 10 * np.log10(np.maximum(x, floor))


def a_weight_db(f):
    f = np.asarray(f, dtype=float)
    f2 = f ** 2
    ra = (12194 ** 2 * f2 ** 2) / (
        (f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194 ** 2)
    )
    with np.errstate(divide="ignore"):
        return 20 * np.log10(np.maximum(ra, 1e-12)) + 2.0


def welch(x, sr):
    # ~0.5 Hz resolution at 48 kHz is enough to separate 50/60 Hz mains harmonics.
    nperseg = min(len(x), 2 ** int(np.ceil(np.log2(sr * 2))))
    f, p = signal.welch(x, fs=sr, nperseg=nperseg, window="hann", scaling="spectrum")
    return f, p


def third_octaves(f, p, sr):
    rows = []
    for fc in THIRD_OCTAVE_CENTRES:
        lo, hi = fc / 2 ** (1 / 6), fc * 2 ** (1 / 6)
        if hi > sr / 2:
            break
        m = (f >= lo) & (f < hi)
        if not m.any():
            continue
        power = p[m].sum()
        rows.append({
            "centre_hz": fc,
            "level_db": round(float(db(power)), 1),
            "level_dba": round(float(db(power) + a_weight_db(fc)), 1),
        })
    return rows


def tonal_peaks(f, p, max_peaks=12):
    """Narrow peaks standing clear of their local spectral floor (tones, hums, whines)."""
    ldb = db(p)
    df = f[1] - f[0]
    # Local floor = running median over ~1/3 octave, approximated with a fixed window
    # that widens with frequency by working on a log-frequency resample.
    keep = f > 10
    fl, ll = f[keep], ldb[keep]
    logf = np.geomspace(fl[0], fl[-1], 4000)
    logl = np.interp(logf, fl, ll)
    win = 133  # ~1/3 octave in 4000 log steps over ~11 octaves
    floor_log = signal.medfilt(logl, kernel_size=win if win % 2 else win + 1)
    floor = np.interp(fl, logf, floor_log)
    prominence = ll - floor
    idx, props = signal.find_peaks(prominence, height=8, distance=max(1, int(2 / df)))
    peaks = []
    kept = []
    for i in sorted(idx, key=lambda k: -prominence[k]):
        # Drop sidebands/leakage skirts sitting next to a stronger peak.
        if any(abs(fl[i] - fl[j]) <= max(3.0, 0.03 * fl[j]) for j in kept):
            continue
        if len(kept) == max_peaks:
            break
        kept.append(i)
        peaks.append({
            "freq_hz": round(float(fl[i]), 1),
            "level_db": round(float(ll[i]), 1),
            "prominence_db": round(float(prominence[i]), 1),
            "a_weighting_db": round(float(a_weight_db(fl[i])), 1),
        })
    return peaks


def harmonic_families(peaks, tol=0.03):
    """Group peaks that are integer multiples of a lower peak (mains hum, motors, fans)."""
    freqs = sorted(p["freq_hz"] for p in peaks)
    families = []
    used = set()
    for base in freqs:
        if base in used or base < 15:
            continue
        members = [base]
        for g in freqs:
            if g <= base or g in used:
                continue
            n = round(g / base)
            if n >= 2 and abs(g - n * base) <= tol * g:
                members.append(g)
        if len(members) >= 2:
            used.update(members)
            families.append({"fundamental_hz": base, "members_hz": members})
    return families


def short_term_levels(x, sr, step=0.125):
    n = int(sr * step)
    frames = len(x) // n
    if frames == 0:
        return np.array([]), np.array([])
    blocks = x[: frames * n].reshape(frames, n)
    lev = db(np.mean(blocks ** 2, axis=1))
    t = np.arange(frames) * step
    return t, lev


def periodicity(lev, step=0.125):
    """Dominant repetition period of the level envelope (cycling pumps, beats, thumps)."""
    if len(lev) < 32:
        return None
    e = lev - lev.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    ac /= ac[0] if ac[0] else 1
    min_lag = int(0.25 / step)  # ignore < 0.25 s
    peaks, props = signal.find_peaks(ac[min_lag:], height=0.3)
    if not len(peaks):
        return None
    k = peaks[np.argmax(props["peak_heights"])] + min_lag
    return {"period_s": round(k * step, 2), "strength": round(float(ac[k]), 2)}


def band_summary(f, p):
    out = {}
    for name, (lo, hi) in BANDS.items():
        m = (f >= lo) & (f < hi)
        out[name] = round(float(db(p[m].sum())), 1) if m.any() else None
    return out


def crest_and_impulsiveness(x, lev):
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    rms = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    return {
        "peak_dbfs": round(float(20 * np.log10(max(peak, 1e-12))), 1),
        "rms_dbfs": round(float(20 * np.log10(max(rms, 1e-12))), 1),
        "crest_factor_db": round(float(20 * np.log10(max(peak, 1e-12) / max(rms, 1e-12))), 1),
        "clipped_samples": int(np.sum(np.abs(x) >= 0.999)),
        "l10_minus_l90_db": round(float(np.percentile(lev, 90) - np.percentile(lev, 10)), 1) if len(lev) else None,
    }


def plot_all(out, f, p, bg, thirds, x, sr, t, lev, cal):
    unit = "dB SPL" if cal else "dBFS"
    off = cal or 0.0

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.semilogx(f[1:], db(p[1:]) + off, lw=0.8, label="recording")
    if bg is not None:
        ax.semilogx(bg[0][1:], db(bg[1][1:]) + off, lw=0.8, alpha=0.7, label="background")
    ax.set_xlim(10, sr / 2)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel(f"Level ({unit}, per bin)")
    ax.set_title("Long-term average spectrum")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "spectrum.png", dpi=130)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(11, 5))
    labels = [str(r["centre_hz"]) for r in thirds]
    xs = np.arange(len(thirds))
    ax.bar(xs - 0.2, [r["level_db"] + off for r in thirds], 0.4, label="unweighted")
    ax.bar(xs + 0.2, [r["level_dba"] + off for r in thirds], 0.4, label="A-weighted")
    ax.set_xticks(xs, labels, rotation=60, fontsize=8)
    ax.set_xlabel("1/3-octave band centre (Hz)")
    ax.set_ylabel(unit)
    ax.set_title("1/3-octave band levels")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out / "thirds.png", dpi=130)
    plt.close(fig)

    for name, fmax, nper in (("spectrogram.png", sr / 2, 2048), ("spectrogram_lf.png", 500, int(2 ** np.ceil(np.log2(sr)))) ):
        nper = min(nper, len(x))
        ff, tt, S = signal.spectrogram(x, fs=sr, nperseg=nper, noverlap=nper * 3 // 4)
        m = ff <= fmax
        fig, ax = plt.subplots(figsize=(11, 5))
        mesh = ax.pcolormesh(tt, ff[m], db(S[m]) + off, shading="auto", cmap="magma")
        if fmax > 1000:
            ax.set_yscale("symlog", linthresh=100)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Frequency (Hz)")
        ax.set_title("Spectrogram" + (" 0-500 Hz" if fmax == 500 else ""))
        fig.colorbar(mesh, ax=ax, label=unit)
        fig.tight_layout()
        fig.savefig(out / name, dpi=130)
        plt.close(fig)

    if len(lev):
        fig, ax = plt.subplots(figsize=(11, 4))
        ax.plot(t, lev + off, lw=0.8)
        for pct, ls in ((90, "--"), (50, "-."), (10, ":")):
            ax.axhline(np.percentile(lev, 100 - pct) + off, ls=ls, lw=0.8, color="k", label=f"L{pct}")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel(unit)
        ax.set_title("Short-term level (125 ms)")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(out / "level_time.png", dpi=130)
        plt.close(fig)




# ---- additions for the desktop app ---------------------------------------------

def load(path: Path):
    """Mono float signal, sample rate and format metadata. Non-soundfile formats via ffmpeg."""
    try:
        data, sr = sf.read(str(path), always_2d=True)
        info = sf.info(str(path))
        meta = {"container": info.format, "subtype": info.subtype}
    except Exception:
        if not shutil.which("ffmpeg"):
            raise RuntimeError(f"Cannot read {path} and ffmpeg is not installed to decode it.")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "decoded.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-c:a", "pcm_f32le", str(wav)],
                           check=True)
            data, sr = sf.read(str(wav), always_2d=True)
        meta = {"container": path.suffix.lstrip(".").lower(), "subtype": "decoded-by-ffmpeg (lossy source?)"}
    meta["channels"] = data.shape[1]
    return data.mean(axis=1), sr, meta


def load_calibration_curve(path: str | Path | None):
    """Per-unit mic response file (miniDSP UMIK / REW style): lines of `freq dB [phase]`.
    Header lines (e.g. `"Sens Factor =-1.2dB, SERNO: 700..."`) are skipped.
    Returns (freqs, dB) or None."""
    if not path:
        return None
    pts = []
    for line in Path(path).read_text(errors="replace").splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) >= 2:
            try:
                pts.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
    if len(pts) < 2:
        raise ValueError(f"No frequency/dB pairs found in calibration file {path}")
    a = np.array(sorted(pts))
    return a[:, 0], a[:, 1]


def apply_curve(f, p, curve):
    """Divide out the mic's deviation from flat (power domain)."""
    if curve is None:
        return p
    corr_db = np.interp(f, curve[0], curve[1], left=curve[1][0], right=curve[1][-1])
    return p / 10 ** (corr_db / 10)


@dataclass
class Analysis:
    result: dict
    f: np.ndarray
    p: np.ndarray
    thirds: list
    t: np.ndarray
    lev: np.ndarray
    x: np.ndarray
    sr: int
    bg: tuple | None
    offset: float


def analyse(recording: Path, out: Path | None = None, background: Path | None = None,
            calibration_offset: float | None = None, calibration_file: str | None = None,
            plots: bool = True) -> Analysis:
    """Run the analysis; if `out` is given write analysis.json and the PNGs there."""
    recording = Path(recording)
    x, sr, meta = load(recording)
    if len(x) < sr:
        raise ValueError("Recording is shorter than 1 second; record at least 30 s.")
    curve = load_calibration_curve(calibration_file)

    f, p = welch(x, sr)
    p = apply_curve(f, p, curve)
    thirds = third_octaves(f, p, sr)
    peaks = tonal_peaks(f, p)
    t, lev = short_term_levels(x, sr)

    result = {
        "recording": recording.name,
        "format": meta,
        "sample_rate_hz": sr,
        "duration_s": round(len(x) / sr, 2),
        "units": "dB SPL" if calibration_offset is not None else "dBFS (uncalibrated; compare relatively)",
        "calibration_offset_db": calibration_offset,
        "calibration_file": Path(calibration_file).name if calibration_file else None,
        "note": "All levels below are dBFS; add calibration_offset_db for dB SPL.",
        "spectral_resolution_hz": round(float(f[1] - f[0]), 3),
        "overall": crest_and_impulsiveness(x, lev),
        "levels_percentile_dbfs": {
            f"L{k}": round(float(np.percentile(lev, 100 - k)), 1) for k in (10, 50, 90)
        } if len(lev) else None,
        "band_energy_dbfs": band_summary(f, p),
        "spectral_centroid_hz": round(float((f * p).sum() / p.sum()), 1),
        "third_octave_bands": thirds,
        "loudest_third_octave_unweighted": max(thirds, key=lambda r: r["level_db"]) if thirds else None,
        "loudest_third_octave_a_weighted": max(thirds, key=lambda r: r["level_dba"]) if thirds else None,
        "tonal_peaks": peaks,
        "harmonic_families": harmonic_families(peaks),
        "periodicity": periodicity(lev),
        "warnings": [],
    }
    w = result["warnings"]
    if sr < 44100:
        w.append(f"Sample rate {sr} Hz: content above {sr // 2} Hz is not captured.")
    if "decoded-by-ffmpeg" in meta["subtype"]:
        w.append("Compressed source format; codecs can discard quiet low-frequency detail. Prefer WAV/FLAC.")
    if result["overall"]["clipped_samples"]:
        w.append("Clipping detected; peak levels and harmonics above the clip point are unreliable. Lower the input gain.")
    if result["overall"]["rms_dbfs"] < -70:
        w.append("Very low level (< -70 dBFS RMS): the mic/interface noise floor may dominate. Raise the input gain.")
    lf = [r for r in thirds if r["centre_hz"] <= 40]
    mid = [r for r in thirds if 200 <= r["centre_hz"] <= 1000]
    if lf and mid and max(r["level_db"] for r in lf) < np.mean([r["level_db"] for r in mid]) - 30:
        w.append("Very little energy below 40 Hz. Either the noise has none, or the mic/interface high-pass "
                 "filters it. Do not conclude 'no rumble' from this recording alone.")

    bg = None
    if background:
        xb, srb, _ = load(Path(background))
        if srb != sr:
            xb = signal.resample_poly(xb, sr, srb)
        fb, pb = welch(xb, sr)
        pb = apply_curve(fb, pb, curve)
        bg = (fb, pb)
        tb = {r["centre_hz"]: r for r in third_octaves(fb, pb, sr)}
        deltas = [{"centre_hz": r["centre_hz"], "excess_db": round(r["level_db"] - tb[r["centre_hz"]]["level_db"], 1)}
                  for r in thirds if r["centre_hz"] in tb]
        result["background"] = {
            "recording": Path(background).name,
            "third_octave_excess_over_background": deltas,
            "bands_elevated_6db_or_more": [d for d in deltas if d["excess_db"] >= 6],
            "overall_excess_db": round(float(db(p.sum()) - db(pb.sum())), 1),
        }

    if out is not None:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        if plots:
            plot_all(out, f, p, bg, thirds, x, sr, t, lev, calibration_offset)
        (out / "analysis.json").write_text(json.dumps(result, indent=2))
    return Analysis(result, f, p, thirds, t, lev, x, sr, bg, calibration_offset or 0.0)


def summary_text(result: dict) -> str:
    """Compact plain-text digest of analysis.json, for humans and for the model prompt."""
    off = result.get("calibration_offset_db") or 0.0
    unit = "dB SPL" if result.get("calibration_offset_db") is not None else "dBFS"
    o = result["overall"]
    lines = [
        f"Duration {result['duration_s']} s at {result['sample_rate_hz']} Hz. Units: {unit}.",
        f"RMS {o['rms_dbfs'] + off:.1f} {unit}, peak {o['peak_dbfs'] + off:.1f}, crest factor {o['crest_factor_db']} dB, "
        f"L10-L90 spread {o['l10_minus_l90_db']} dB, clipped samples {o['clipped_samples']}.",
    ]
    if result.get("levels_percentile_dbfs"):
        lp = result["levels_percentile_dbfs"]
        lines.append("Short-term levels (125 ms): " + ", ".join(f"{k} {v + off:.1f}" for k, v in lp.items()) + f" {unit}.")
    lu, la = result.get("loudest_third_octave_unweighted"), result.get("loudest_third_octave_a_weighted")
    if lu:
        lines.append(f"Loudest 1/3-octave band: {lu['centre_hz']} Hz unweighted, {la['centre_hz']} Hz A-weighted.")
    lines.append(f"Spectral centroid {result['spectral_centroid_hz']} Hz.")
    be = result.get("band_energy_dbfs") or {}
    lines.append("Band energy: " + ", ".join(f"{k} {v + off:.1f}" for k, v in be.items() if v is not None) + ".")
    pk = result.get("tonal_peaks") or []
    if pk:
        lines.append("Tonal peaks (freq Hz / prominence dB): " +
                     ", ".join(f"{p['freq_hz']}/{p['prominence_db']}" for p in pk[:8]) + ".")
    for fam in result.get("harmonic_families") or []:
        lines.append(f"Harmonic family on {fam['fundamental_hz']} Hz: {fam['members_hz']}.")
    if result.get("periodicity"):
        pr = result["periodicity"]
        lines.append(f"Level envelope repeats every {pr['period_s']} s (autocorrelation {pr['strength']}).")
    if result.get("background"):
        b = result["background"]
        lines.append(f"Excess over background take: {b['overall_excess_db']} dB overall; bands >= 6 dB above: "
                     + ", ".join(f"{d['centre_hz']} Hz (+{d['excess_db']})" for d in b["bands_elevated_6db_or_more"]) + ".")
    for w in result.get("warnings") or []:
        lines.append("Warning: " + w)
    return "\n".join(lines)
