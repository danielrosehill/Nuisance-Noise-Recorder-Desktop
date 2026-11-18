# Nuisance-Noise-Recorder-Desktop

Release tier: **small** (`v1.0`, `v1.1`, ...). Version lives in `pyproject.toml` and `src/noise_recorder/__init__.py`.

- Python package `noise_recorder` (src layout), run with `uv run nnrec`. PySide6 + pyqtgraph GUI in `gui.py`; everything else is GUI-free and has a CLI subcommand in `cli.py`.
- `analysis.py` is a port of `scripts/analyse_noise.py` from danielrosehill/Noise-Pollution-Analysis; keep the two in step when changing the measurement logic.
- Integrity: `manifest.py`. Never rewrite a sealed `manifest.json`; new outputs go in `addenda/<name>/` via `add_addendum`. Model in `docs/integrity.md`.
- GUI tests: `QT_QPA_PLATFORM=offscreen` with a temp `XDG_CONFIG_HOME`, `widget.grab().save()` screenshots. Not on the live desktop.
- OpenRouter: `openrouter.py`, `input_audio` content part; key from `OPENROUTER_API_KEY` (run under `with-secrets` locally).
- Companion: Android capture app danielrosehill/Nuisance-Noise-Recorder (private); agent skills in Noise-Pollution-Analysis.
