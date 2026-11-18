"""Entry point. No arguments opens the GUI; subcommands print JSON for scripts and agents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import APP_ID, APP_NAME, __version__, config

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name={name}
GenericName=Noise recorder and analyser
Comment=Record, analyse and seal nuisance-noise samples
Exec={exec_}
Icon=audio-input-microphone
Terminal=false
Categories=AudioVideo;Audio;Utility;
Keywords=noise;nuisance;spectrum;decibel;microphone;
"""


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def cmd_devices(a):
    from . import devices
    _print(devices.list_inputs())


def cmd_record(a):
    from . import devices, runs
    from .recorder import Recorder
    cfg = config.load()
    idx = a.device if a.device is not None else devices.find(cfg.get("device", ""))
    run_dir = runs.new_run_dir(Path(a.runs_dir or cfg["runs_dir"]), a.title)
    from datetime import datetime, timezone
    rec = Recorder(idx, a.samplerate or cfg["sample_rate"], cfg.get("channels", 1), run_dir / "audio.wav",
                   a.seconds, runs.wav_tags(a.title, datetime.now(timezone.utc)))
    rec.start()
    rec.wait(timeout=a.seconds + 30)
    form = {"title": a.title, "location": a.location or cfg.get("location", ""), "notes": a.notes,
            "operator": cfg.get("operator", ""), "windows": a.windows}
    _print(runs.finalise(run_dir, rec, idx, cfg, form))


def cmd_analyse(a):
    from . import analysis
    cfg = config.load()
    src = Path(a.path)
    wav = next(src.glob("*.wav")) if src.is_dir() else src
    out = Path(a.out) if a.out else (src / "addenda" / "analysis-cli" if src.is_dir() else src.with_suffix("") )
    res = analysis.analyse(wav, out, background=a.background,
                           calibration_offset=cfg.get("calibration_offset_db"),
                           calibration_file=cfg.get("calibration_file") or None)
    _print({"out": str(out), "summary": analysis.summary_text(res.result).splitlines()})


def cmd_verify(a):
    from . import manifest
    rep = manifest.verify(Path(a.run_dir))
    _print(rep)
    sys.exit(0 if rep["ok"] else 1)


def cmd_ai(a):
    from . import openrouter
    cfg = config.load()
    prompt = a.prompt or openrouter.PRESETS[a.preset]
    res = openrouter.evaluate(Path(a.run_dir), prompt, a.model or cfg["openrouter_model"], config.api_key(cfg),
                              cfg, include_metrics=not a.no_metrics, structured=not a.markdown)
    _print({k: v for k, v in res.items() if k != "markdown"})


def cmd_export(a):
    from . import runs
    run = Path(a.run_dir)
    dest = Path(a.dest) if a.dest else run.with_suffix(".zip")
    _print({"zip": str(runs.export_zip(run, dest))})


def cmd_install_desktop(a):
    import shutil
    exe = shutil.which("nnrec") or f"{sys.executable} -m noise_recorder"
    exec_ = f"{a.exec_prefix} {exe}".strip()
    d = Path.home() / ".local/share/applications"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{APP_ID}.desktop"
    f.write_text(DESKTOP_ENTRY.format(name=APP_NAME, exec_=exec_))
    _print({"desktop_file": str(f), "exec": exec_})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nnrec", description=f"{APP_NAME} {__version__}. No subcommand opens the GUI.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("gui", help="open the desktop app (default)")
    sub.add_parser("devices", help="list input devices").set_defaults(fn=cmd_devices)

    r = sub.add_parser("record", help="record, analyse and seal a run without the GUI")
    r.add_argument("--seconds", type=float, default=60)
    r.add_argument("--device", type=int, help="PortAudio device index (see `nnrec devices`)")
    r.add_argument("--samplerate", type=int)
    r.add_argument("--title", default="")
    r.add_argument("--location", default="")
    r.add_argument("--notes", default="")
    r.add_argument("--windows", default="", help="window/door state, e.g. closed")
    r.add_argument("--runs-dir")
    r.set_defaults(fn=cmd_record)

    an = sub.add_parser("analyse", help="analyse a WAV or run folder (writes to addenda/ for runs)")
    an.add_argument("path")
    an.add_argument("--background", help="background take of the same spot without the nuisance")
    an.add_argument("--out")
    an.set_defaults(fn=cmd_analyse)

    v = sub.add_parser("verify", help="re-hash a run and check its timestamp and signature")
    v.add_argument("run_dir")
    v.set_defaults(fn=cmd_verify)

    ai = sub.add_parser("ai", help="ask an OpenRouter audio model to evaluate a run")
    ai.add_argument("run_dir")
    from .openrouter import PRESETS
    ai.add_argument("--preset", choices=list(PRESETS), default=next(iter(PRESETS)))
    ai.add_argument("--prompt", help="custom prompt (overrides --preset)")
    ai.add_argument("--model")
    ai.add_argument("--no-metrics", action="store_true", help="don't send the measured metrics")
    ai.add_argument("--markdown", action="store_true", help="free Markdown instead of structured JSON")
    ai.set_defaults(fn=cmd_ai)

    e = sub.add_parser("export", help="zip a run folder")
    e.add_argument("run_dir")
    e.add_argument("--dest")
    e.set_defaults(fn=cmd_export)

    i = sub.add_parser("install-desktop", help="add a launcher to the application menu")
    i.add_argument("--exec-prefix", default="", help="command to wrap the launch in, e.g. a secrets injector")
    i.set_defaults(fn=cmd_install_desktop)

    a = ap.parse_args(argv)
    if getattr(a, "fn", None):
        a.fn(a)
    else:
        from .gui import run
        sys.exit(run())


if __name__ == "__main__":
    main()
