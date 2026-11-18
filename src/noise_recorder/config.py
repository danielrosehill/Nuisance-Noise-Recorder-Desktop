"""User settings, stored as JSON in $XDG_CONFIG_HOME/nuisance-noise-recorder/config.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import APP_ID

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_ID
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS: dict = {
    "runs_dir": str(Path.home() / "Noise-Recordings"),
    "duration_s": 60,
    "sample_rate": 48000,
    "channels": 1,
    "device": "",  # device name as listed by PortAudio; "" = system default
    "operator": "",
    "location": "",
    # Levels are dBFS unless an offset is set (dB to add to dBFS to get dB SPL).
    "calibration_offset_db": None,
    # Optional per-unit frequency-response file (UMIK/REW style "freq dB" lines).
    "calibration_file": "",
    "openrouter_model": "google/gemini-3.8-flash",
    # OPENROUTER_API_KEY in the environment wins over this field.
    "openrouter_api_key": "",
    "rfc3161_enabled": True,
    "rfc3161_tsa_url": "https://freetsa.org/tsr",
    "gpg_sign_key": "",
    "custom_prompts": {},
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except json.JSONDecodeError:
            pass
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    CONFIG_FILE.chmod(0o600)


def api_key(cfg: dict) -> str:
    return os.environ.get("OPENROUTER_API_KEY") or cfg.get("openrouter_api_key", "")
