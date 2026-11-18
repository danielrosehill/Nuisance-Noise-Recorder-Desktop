# Nuisance Noise Recorder (Desktop)

An Ubuntu desktop app for documenting noise nuisance (a bar's music, building works,
mechanical hum) with an external microphone. It records a fixed-length sample, by
default 60 s, and shows the spectrum and sound level live while it records. It then
analyses the sample at the frequency level and can ask an audio-capable AI model on
OpenRouter what it hears. Every run is sealed with SHA-256 hashes, an RFC 3161 trusted
timestamp and, optionally, a GPG signature, so you can later show that the recording
was not altered.

| Tab | What it does |
|---|---|
| **Record** | Mic picker, duration, title/location/windows/notes. *Monitor* checks levels without saving; *Record* captures 24-bit WAV. Live log-frequency spectrum with peak hold, a scrolling spectrogram (0-500 Hz / 2 k / 8 k / full), and fast A- and Z-weighted level, running Leq, Max A, peak and a clipping warning. |
| **Analysis** | Long-term spectrum (~0.5 Hz bins) with labelled tonal peaks, 1/3-octave bands unweighted and A-weighted, low-frequency and full spectrograms, level over time with L10/L50/L90, and a tonal peak table. Also: re-analyse against a background take, verify integrity, export a ZIP, open the folder. |
| **AI evaluation** | Sends a compressed MP3 copy, the measured metrics and your prompt to an audio-input model. Presets cover general identification, construction, bar/music, mechanical hum and traffic, and you can save your own. Structured JSON output gives sources, confidence, time ranges, evidence, tags and next steps. The result is saved and sealed in the run folder. |
| **Settings** | Runs folder, sample rate, calibration offset and mic response file, OpenRouter key and model, TSA URL, GPG key. |
| **About & mic guide** | How to choose and set up a microphone for ambient and nuisance noise (also at [`src/noise_recorder/data/microphone-guide.md`](src/noise_recorder/data/microphone-guide.md)). Buying advice by tier: [docs/hardware-guide.md](docs/hardware-guide.md). |

## Install (Ubuntu)

```bash
sudo apt install libportaudio2 ffmpeg openssl     # ffmpeg: MP3 for the AI upload; openssl: timestamps
uv tool install git+https://github.com/danielrosehill/Nuisance-Noise-Recorder-Desktop
nnrec install-desktop                              # adds "Nuisance Noise Recorder" to the app menu
```

If your OpenRouter key comes from a secrets wrapper instead of the environment, wrap the
launcher with it: `nnrec install-desktop --exec-prefix "with-secrets"`.

## Run folder

```
~/Noise-Recordings/2026-11-18T20-04-21Z_bar-music/
  audio.wav                 24-bit PCM original, RIFF INFO tags embedded
  recording.json            start/end UTC+local, frames, rate, device (USB IDs, PipeWire source, input volume, noise-suppression modules), xruns, clipping, calibration
  analysis/analysis.json    all measurements (+ spectrum/thirds/spectrogram/level PNGs)
  manifest.json             SHA-256 + size of every file above, metadata, tool version
  SHA256SUMS                same, `sha256sum -c` format
  timestamp/                RFC 3161 query/reply over manifest.json + TSA certs for offline verification
  manifest.json.asc         optional GPG detached signature
  addenda/ai-<UTC>/         AI evaluation: prompt, request (no key/audio), response, evaluation.json/.md, upload.mp3, own seal
  addenda/analysis-vs-background-<UTC>/
```

The seal is never rewritten; later work is added as addenda that pin the manifest hash.
See [docs/integrity.md](docs/integrity.md) for the model, how to verify with only
`sha256sum` and `openssl`, and what it does and does not prove.

## CLI (JSON output, for scripts and agents)

```bash
nnrec                                   # GUI
nnrec devices                           # input devices with PortAudio indexes
nnrec record --seconds 60 --device 15 --title "bar music" --windows closed --notes "..."
nnrec analyse <run-dir|file.wav> [--background <run-dir|wav>]
nnrec ai <run-dir> [--preset "Bar / amplified music / crowd"] [--prompt "..."] [--model google/gemini-3.8-flash]
nnrec verify <run-dir>                  # exit 0 = intact
nnrec export <run-dir> [--dest x.zip]
```

## Notes

- **Levels are dBFS** unless you set a calibration offset. A full-scale sine reads -3 dBFS
  RMS. Use an acoustic calibrator (94 dB @ 1 kHz) to set the offset: `94 - Level Z`.
- **Mic response files** in UMIK/REW format (`freq dB` lines) are divided out of the
  analysed spectra. The file is copied into the run and sealed.
- **Capture path.** On PipeWire desktops PortAudio usually lists only sound-server sources.
  The app records the source's volume and any loaded noise-suppression or echo-cancel
  modules, so the processing chain is on file. Direct ALSA `hw:` devices are used, and
  labelled, when they are available.
- **Analysis layer** comes from [Noise-Pollution-Analysis](https://github.com/danielrosehill/Noise-Pollution-Analysis)
  (numpy/scipy: Welch spectrum, IEC 61672 A-weighting curve, tonal prominence, harmonic
  families, envelope periodicity). Plots use matplotlib (exported PNGs) and pyqtgraph (live).
- **AI default model:** `google/gemini-3.8-flash`, chosen 2026-11-18 from OpenRouter models
  whose `input_modalities` include `audio`. *List audio models* refreshes the list. The
  request uses an `input_audio` content part (base64 MP3) with a strict `json_schema`
  response format. If a model rejects the schema, the app retries once with the schema in
  the prompt. A 5 s pilot cost well under $0.01.
- **Network use:** the RFC 3161 request sends only a SHA-512 hash, to FreeTSA by default
  (you can change the URL or turn it off). The AI evaluation uploads the compressed audio
  and metadata to OpenRouter, and only when you press *Send*.

## Verified (2026-11-18, Ubuntu 26.04, PipeWire, Python 3.14)

A 5 s capture from a USB mic produced a frame-exact file (240000 frames at 48 kHz). The
TSA token verified with `openssl ts -verify`, and `sha256sum -c` passed. Flipping one bit
in `audio.wav` made `nnrec verify` report `hash mismatch: audio.wav`. The AI evaluation
returned structured JSON, saved as a sealed addendum. All GUI tabs were rendered offscreen
(`QT_QPA_PLATFORM=offscreen`) and checked. Not yet tested: a long (60 s+) take through the
GUI on a live desktop, GPG signing, a calibration file.

MIT licence.
