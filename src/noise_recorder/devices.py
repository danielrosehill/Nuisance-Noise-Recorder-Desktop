"""Input device listing and hardware metadata for the recording record."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import sounddevice as sd


def list_inputs() -> list[dict]:
    """Input devices; output monitors (loopbacks of speakers) are left out."""
    hostapis = sd.query_hostapis()
    labels = _source_labels()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and not d["name"].endswith(".monitor"):
            out.append({
                "index": i,
                "name": d["name"],
                "label": labels.get(d["name"], d["name"]),
                "hostapi": hostapis[d["hostapi"]]["name"],
                "max_input_channels": d["max_input_channels"],
                "default_samplerate": d["default_samplerate"],
            })
    return out


def find(name: str) -> int | None:
    """Device index by exact name, or None for the system default."""
    if not name:
        return None
    for d in list_inputs():
        if d["name"] == name:
            return d["index"]
    return None


def _alsa_card(name: str) -> int | None:
    m = re.search(r"\(hw:(\d+),\d+\)", name)
    return int(m.group(1)) if m else None


def _read(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def describe(index: int | None) -> dict:
    """Everything we can learn about the capture device, for recording.json."""
    d = sd.query_devices(index if index is not None else sd.default.device[0])
    info = {
        "portaudio_name": d["name"],
        "portaudio_index": d["index"] if "index" in d else index,
        "hostapi": sd.query_hostapis()[d["hostapi"]]["name"],
        "max_input_channels": d["max_input_channels"],
        "default_samplerate": d["default_samplerate"],
        "default_low_input_latency_s": d["default_low_input_latency"],
        "path": "direct ALSA hw (bypasses PipeWire/PulseAudio processing)"
        if _alsa_card(d["name"]) is not None else "sound server (PipeWire/PulseAudio)",
    }
    card = _alsa_card(d["name"])
    if card is not None:
        base = Path(f"/proc/asound/card{card}")
        info["alsa"] = {
            "card": card,
            "id": _read(base / "id"),
            "usb_id": _read(base / "usbid"),
            "usb_stream": _read(base / "stream0"),
        }
        cards = _read(Path("/proc/asound/cards")) or ""
        for line_no, line in enumerate(cards.splitlines()):
            if re.match(rf"\s*{card}\s+\[", line):
                nxt = cards.splitlines()[line_no + 1] if line_no + 1 < len(cards.splitlines()) else ""
                info["alsa"]["long_name"] = (line.split("]:", 1)[-1].strip() + " " + nxt.strip()).strip()
    elif shutil.which("pactl"):
        name = d["name"]
        if not name.startswith("alsa_input"):
            name = _run(["pactl", "get-default-source"]) or name
        info["sound_server_source"] = _pactl_source(name)
        mods = _run(["pactl", "list", "modules", "short"]) or ""
        info["sound_server_processing_modules"] = [
            l.split("\t")[1] for l in mods.splitlines()
            if any(k in l for k in ("echo-cancel", "rnnoise", "noise", "filter-chain", "ladspa"))]
    return info


def _run(cmd) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


_KEEP = ("device.description", "device.vendor.id", "device.vendor.name", "device.product.id",
         "device.product.name", "device.serial", "device.bus", "alsa.long_card_name", "alsa.resolution_bits",
         "alsa.components", "api.alsa.card", "device.profile.name")


def _pactl_source(name: str) -> dict | None:
    """PipeWire/PulseAudio view of the source: hardware IDs and the input volume at capture time."""
    out = _run(["pactl", "-f", "json", "list", "sources"])
    if not out:
        return None
    try:
        for src in json.loads(out):
            if src.get("name") == name:
                props = src.get("properties", {})
                return {
                    "name": src["name"],
                    "description": src.get("description"),
                    "sample_spec": src.get("sample_specification"),
                    "volume": src.get("volume"),
                    "base_volume": src.get("base_volume"),
                    "mute": src.get("mute"),
                    "active_port": src.get("active_port"),
                    "properties": {k: props[k] for k in _KEEP if k in props},
                }
    except (json.JSONDecodeError, KeyError):
        return None
    return None


def _source_labels() -> dict:
    """PipeWire node name -> readable label, e.g. 'SC230 Mono (OHW325)'."""
    out = _run(["pactl", "-f", "json", "list", "sources"]) if shutil.which("pactl") else None
    try:
        return {src["name"]: f"{src.get('description')} ({src.get('properties', {}).get('device.nick', 'sound server')})"
                for src in json.loads(out or "[]")}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}
