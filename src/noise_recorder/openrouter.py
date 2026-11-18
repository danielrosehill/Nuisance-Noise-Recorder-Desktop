"""Send a compressed copy of a run's audio to an audio-capable model on OpenRouter.

The request uses the OpenAI-style `input_audio` content part (base64 mp3), which
OpenRouter forwards to models whose input_modalities include "audio". Results are saved
as an addendum: prompt, request (no key, no audio bytes), raw response, parsed JSON and
a Markdown rendering.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
import soundfile as sf
from scipy import signal

from . import APP_NAME
from .analysis import summary_text
from .manifest import add_addendum, sha256_file

API = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You are an acoustic consultant helping a resident document a noise nuisance in their home.
You receive a recording made indoors with a fixed microphone, plus measured metrics computed from the
original uncompressed file. The audio you hear is a compressed copy; trust the metrics for levels and
exact frequencies, and your listening for source identification.
Be concrete and honest about uncertainty. Name likely sources, say what in the audio supports each,
give approximate time ranges (seconds from start) where events occur, and say what would confirm or
rule each source out. Never invent sound pressure levels: levels are dBFS unless a calibration
offset is stated. Do not give legal conclusions."""

PRESETS = {
    "Identify the noise (general)":
        "Listen to this recording and identify what is making the noise. Tag every distinct sound source you "
        "can hear, how confident you are, and when it occurs. Say whether the main nuisance is tonal, "
        "broadband, impulsive or intermittent, and whether it sounds airborne or structure-borne.",
    "Building / construction works":
        "I suspect building or construction work nearby. Identify any construction-type sources (drilling, "
        "hammering, jackhammer, angle grinder, concrete pump or mixer, generator, excavator, reversing alarm, "
        "crane). For each, give confidence, time ranges and the evidence. Note any impulsive character.",
    "Bar / amplified music / crowd":
        "I suspect noise from a bar or venue nearby. Identify amplified music (and genre if possible), bass "
        "beat, crowd talk, shouting, glassware or other venue sounds. Estimate the tempo if there is a beat, "
        "and say whether low-frequency bass dominates what reaches the room.",
    "Mechanical hum / HVAC / pumps":
        "I suspect mechanical plant: AC units, compressors, fans, pumps, transformers or extractors. "
        "Describe the hum or drone, relate it to the tonal peaks and harmonic families in the metrics, "
        "note any cycling, and suggest what kind of machine fits.",
    "Traffic / transport":
        "Identify road traffic, motorbikes, buses, trucks, sirens, horns, aircraft or trains. Count "
        "distinct pass-by events with time ranges and say which category dominates.",
}

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "primary_source": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "time_ranges_s": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                    "how_to_confirm": {"type": "string"},
                },
                "required": ["source", "confidence", "time_ranges_s", "evidence", "how_to_confirm"],
            },
        },
        "tags": {"type": "array", "items": {"type": "string"}},
        "character": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tonal": {"type": "boolean"},
                "impulsive": {"type": "boolean"},
                "intermittent": {"type": "boolean"},
                "low_frequency_dominant": {"type": "boolean"},
                "likely_path": {"type": "string", "enum": ["airborne", "structure-borne", "both", "unclear"]},
            },
            "required": ["tonal", "impulsive", "intermittent", "low_frequency_dominant", "likely_path"],
        },
        "speech_present": {"type": "boolean"},
        "music_present": {"type": "boolean"},
        "recording_quality_notes": {"type": "string"},
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "primary_source", "sources", "tags", "character", "speech_present",
                 "music_present", "recording_quality_notes", "next_steps"],
}


def compress(wav: Path, out: Path) -> Path:
    """Mono 128 kb/s MP3 at 44.1 kHz via ffmpeg; 16-bit 22.05 kHz WAV if ffmpeg is missing."""
    if shutil.which("ffmpeg"):
        mp3 = out.with_suffix(".mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ac", "1", "-ar", "44100",
                        "-b:a", "128k", str(mp3)], check=True)
        return mp3
    x, sr = sf.read(str(wav), always_2d=True)
    y = signal.resample_poly(x.mean(axis=1), 22050, sr)
    w = out.with_suffix(".wav")
    sf.write(str(w), np.clip(y, -1, 1), 22050, subtype="PCM_16")
    return w


def audio_models(timeout: int = 30) -> list[str]:
    r = requests.get(f"{API}/models", timeout=timeout)
    r.raise_for_status()
    return sorted(m["id"] for m in r.json()["data"]
                  if "audio" in (m.get("architecture") or {}).get("input_modalities", [])
                  and not m["id"].endswith(":batch") and not m["id"].startswith("openrouter/"))


def to_markdown(parsed: dict | None, raw_text: str, model: str, prompt: str) -> str:
    head = f"# AI evaluation\n\n- Model: `{model}`\n- Created: {datetime.now(timezone.utc).isoformat()}\n\n" \
           f"## Prompt\n\n{prompt}\n\n"
    if not parsed:
        return head + "## Response\n\n" + raw_text + "\n"
    s = [head, "## Summary\n", parsed.get("summary", ""), "",
         f"**Primary source:** {parsed.get('primary_source', '')}", ""]
    if parsed.get("tags"):
        s.append("**Tags:** " + ", ".join(f"`{t}`" for t in parsed["tags"]) + "\n")
    s.append("## Sources\n")
    s.append("| Source | Confidence | When (s) | Evidence | To confirm |\n|---|---|---|---|---|")
    for src in parsed.get("sources", []):
        cells = [src.get("source", ""), src.get("confidence", ""), "; ".join(src.get("time_ranges_s", [])),
                 src.get("evidence", ""), src.get("how_to_confirm", "")]
        s.append("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in cells) + " |")
    ch = parsed.get("character") or {}
    if ch:
        s.append("\n## Character\n")
        s += [f"- {k.replace('_', ' ')}: {v}" for k, v in ch.items()]
    s.append(f"\n- Speech present: {parsed.get('speech_present')}\n- Music present: {parsed.get('music_present')}")
    if parsed.get("recording_quality_notes"):
        s.append(f"\n## Recording quality\n\n{parsed['recording_quality_notes']}")
    if parsed.get("next_steps"):
        s.append("\n## Next steps\n")
        s += [f"- {n}" for n in parsed["next_steps"]]
    return "\n".join(s) + "\n"


def _post(payload: dict, key: str) -> dict:
    r = requests.post(f"{API}/chat/completions", json=payload, timeout=300, headers={
        "Authorization": f"Bearer {key}",
        "X-Title": APP_NAME,
        "HTTP-Referer": "https://github.com/danielrosehill/Nuisance-Noise-Recorder-Desktop",
    })
    if r.status_code >= 400:
        raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {r.text[:600]}")
    return r.json()


def evaluate(run_dir: Path, prompt: str, model: str, key: str, cfg: dict,
             include_metrics: bool = True, structured: bool = True) -> dict:
    """Run one evaluation and save it as addenda/ai-<UTC stamp>/. Returns a result dict."""
    run_dir = Path(run_dir)
    if not key:
        raise RuntimeError("No OpenRouter API key: set OPENROUTER_API_KEY or add it in Settings.")
    wav = next(run_dir.glob("*.wav"), None)
    if wav is None:
        raise FileNotFoundError(f"No .wav in {run_dir}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    adir = run_dir / "addenda" / f"ai-{stamp}"
    adir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        comp = compress(wav, Path(tmp) / "upload")
        audio_b64 = base64.b64encode(comp.read_bytes()).decode()
        upload_name = "upload" + comp.suffix
        shutil.copy(comp, adir / upload_name)

    text = prompt
    rec = run_dir / "recording.json"
    if rec.exists():
        meta = json.loads(rec.read_text())
        ctx = {k: meta.get(k) for k in ("start_local", "duration_s", "location", "notes", "windows")}
        text += "\n\nRecording context: " + json.dumps(ctx)
    ana = run_dir / "analysis" / "analysis.json"
    if include_metrics and ana.exists():
        text += "\n\nMeasured metrics from the original file:\n" + summary_text(json.loads(ana.read_text()))
    if not structured:
        text += "\n\nAnswer in Markdown."

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "input_audio", "input_audio": {"data": audio_b64, "format": comp.suffix.lstrip(".")}},
            ]},
        ],
    }
    if structured:
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "noise_evaluation", "strict": True, "schema": RESPONSE_SCHEMA}}

    fallback = None
    try:
        resp = _post(payload, key)
    except RuntimeError as e:
        if not structured:
            raise
        # Some models reject json_schema; retry once with the schema in the prompt instead.
        fallback = str(e)
        payload.pop("response_format")
        payload["messages"][1]["content"][0]["text"] = text + (
            "\n\nReply with ONLY a JSON object matching this JSON Schema, no prose:\n" + json.dumps(RESPONSE_SCHEMA))
        resp = _post(payload, key)

    content = resp["choices"][0]["message"].get("content") or ""
    parsed = None
    if structured:
        raw = content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").split("\n", 1)[1] if "\n" in raw else raw
            raw = raw.rsplit("```", 1)[0]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None

    logged = json.loads(json.dumps(payload))
    logged["messages"][1]["content"][1]["input_audio"]["data"] = (
        f"<base64 of {upload_name}, sha256 {sha256_file(adir / upload_name)}>")
    (adir / "prompt.txt").write_text(prompt + "\n")
    (adir / "request.json").write_text(json.dumps(logged, indent=2))
    (adir / "response.json").write_text(json.dumps(resp, indent=2))
    if parsed is not None:
        (adir / "evaluation.json").write_text(json.dumps(parsed, indent=2))
    md = to_markdown(parsed, content, resp.get("model", model), prompt)
    (adir / "evaluation.md").write_text(md)
    seal = add_addendum(run_dir, adir.name, cfg, {
        "kind": "ai-evaluation", "model": resp.get("model", model), "usage": resp.get("usage"),
        "structured_fallback_error": fallback})
    return {"dir": str(adir), "markdown": md, "parsed": parsed, "usage": resp.get("usage"),
            "model": resp.get("model", model), "seal": seal}
