"""Run folders: create, describe, finalise (metadata + analysis + seal), list, export."""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, analysis, devices, manifest


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:40]


def new_run_dir(runs_dir: Path, title: str = "") -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    d = Path(runs_dir).expanduser() / (stamp + (f"_{slug(title)}" if title else ""))
    d.mkdir(parents=True, exist_ok=False)
    return d


def wav_tags(title: str, start: datetime) -> dict:
    """RIFF LIST/INFO tags embedded in the WAV so the file carries its own context."""
    return {"title": title or "Noise recording", "date": start.isoformat(),
            "software": f"nuisance-noise-recorder {__version__}",
            "comment": "Sealed by manifest.json in the run folder; see SHA256SUMS."}


def finalise(run_dir: Path, rec, device_index: int | None, cfg: dict, form: dict) -> dict:
    """After capture: write recording.json, analyse, seal. `rec` is a stopped Recorder."""
    run_dir = Path(run_dir)
    start, end = rec.start_utc, rec.end_utc or datetime.now(timezone.utc)
    meta = {
        "title": form.get("title", ""),
        "operator": form.get("operator", ""),
        "location": form.get("location", ""),
        "notes": form.get("notes", ""),
        "windows": form.get("windows", ""),
        "start_utc": start.isoformat(),
        "start_local": start.astimezone().isoformat(),
        "end_utc": end.isoformat(),
        "timezone": str(start.astimezone().tzinfo),
        "requested_duration_s": rec.duration_s,
        "duration_s": round(rec.stats.frames / rec.samplerate, 4),
        "frames": rec.stats.frames,
        "sample_rate_hz": rec.samplerate,
        "channels": rec.channels,
        "sample_format": "PCM 24-bit WAV",
        "device": devices.describe(device_index),
        "capture": {
            "overflow_events": rec.stats.overflows,
            "status_messages": rec.stats.status_messages,
            "clipped_samples": rec.stats.clipped,
            "peak_dbfs": round(20 * __import__("math").log10(max(rec.stats.peak, 1e-12)), 2),
            "leq_dbfs": round(rec.leq_dbfs(), 2),
            "stopped_early": rec.duration_s is not None and rec.stats.frames < round(rec.duration_s * rec.samplerate),
            "writer_error": rec.error,
        },
        "calibration": {"offset_db": cfg.get("calibration_offset_db"),
                        "file": Path(cfg["calibration_file"]).name if cfg.get("calibration_file") else None},
        "software": manifest.tool_info(),
    }
    if cfg.get("calibration_file") and Path(cfg["calibration_file"]).exists():
        shutil.copy(cfg["calibration_file"], run_dir / ("calibration-" + Path(cfg["calibration_file"]).name))
    (run_dir / "recording.json").write_text(json.dumps(meta, indent=2))
    wav = run_dir / "audio.wav"
    analysis.analyse(wav, run_dir / "analysis", calibration_offset=cfg.get("calibration_offset_db"),
                     calibration_file=cfg.get("calibration_file") or None)
    report = manifest.seal(run_dir, meta, cfg)
    return {"run_dir": str(run_dir), "seal": report}


def list_runs(runs_dir: Path) -> list[Path]:
    d = Path(runs_dir).expanduser()
    if not d.exists():
        return []
    return sorted((p for p in d.iterdir() if (p / "manifest.json").exists() or (p / "audio.wav").exists()),
                  reverse=True)


def export_zip(run_dir: Path, dest: Path) -> Path:
    """Zip the whole run folder (audio, metadata, analysis, seal, addenda) without recompression."""
    run_dir = Path(run_dir)
    dest = Path(dest)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as z:
        for p in sorted(run_dir.rglob("*")):
            if p.is_file():
                z.write(p, Path(run_dir.name) / p.relative_to(run_dir))
    return dest
