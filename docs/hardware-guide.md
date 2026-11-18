# Hardware guide: microphones for full-spectrum noise capture

**Recommendation:** an **omnidirectional measurement microphone that comes with a per-unit
calibration file**. For most people that means a USB measurement mic (miniDSP UMIK-1 or
UMIK-2). It plugs straight into Ubuntu, needs no interface, and its calibration file loads
into this app (Settings → *Mic response file*).

Why this type: it is built to tell the truth about a sound field, and every other mic type
is built to flatter a voice.

| Property | Measurement mic | Studio condenser (e.g. AT2020) | Dynamic voice mic (e.g. Samson Q2U) |
|---|---|---|---|
| Pickup pattern | Omni: room noise and structure-borne noise from every direction | Cardioid: depends on aim | Cardioid, close-talk |
| Low-frequency response | Flat to ~20 Hz or lower | Usable to ~20-50 Hz, not flat | Rolls off; voice-tuned |
| High-frequency response | Flat to ~20 kHz | Presence lift | Presence peak |
| Sensitivity / self-noise | Adequate for quiet rooms | Good | Low sensitivity; gain hiss dominates quiet rooms |
| Calibration file | Yes, per unit | No | No |
| Suitable for | Evidence, comparing spectra, dB SPL with a calibrator | Good second best: relative comparisons | Loud events only |

## Tiers

Prices are rough 2026 street prices in USD. Check current prices before buying.

1. **USB measurement mic (recommended).** No interface, no phantom power, calibration file included.
   - **miniDSP UMIK-1** (~$100): 20 Hz-20 kHz, the standard choice for room acoustics.
   - **miniDSP UMIK-2** (~$200+): higher maximum SPL and resolution. Worth it for loud venues
     or bass-heavy music.
   - **Dayton Audio UMM-6** (~$80): similar idea.

2. **XLR measurement mic + USB interface.** Choose this if you already own an interface
   such as a Focusrite Scarlett Solo.
   - **Behringer ECM8000** (~$50), **Dayton Audio EMM-6** (~$70; buy a version that comes with a
     calibration file), **iSEMcon EMX-7150** (~$200, individually calibrated).
   - These need 48 V phantom power. Mark the interface's gain knob and never move it between takes.

3. **Wide-bandwidth / low-frequency measurement mics.** Choose this when the complaint is
   a drone or bass you *feel*.
   - **Earthworks M23** (~9 Hz-23 kHz) or **M30** (~3 Hz-30 kHz), several hundred dollars and
     up, plus an interface.
   - Below ~10 Hz (true infrasound, building vibration), use an accelerometer or geophone
     on the structure rather than a microphone.

4. **Sound level meter (formal evidence).** An IEC 61672 **Class 1** (or Class 2) meter gives
   dB(A)/dB(C)/Leq values that authorities accept. Some meters have an audio output this app
   can record from, so you get calibrated readings plus the audio and its spectrum. Use this
   tier when a regulator, landlord or court will scrutinise the numbers.

**Add-on: acoustic calibrator** (94 dB SPL @ 1 kHz, ~$100-200). With it, any of tiers 1-3
read in dB SPL instead of relative dBFS: set Settings → *Offset* to `94 - Level Z`.

## Mics you may already own

- **Audio-Technica AT2020** (cardioid condenser): **usable**. It is sensitive enough for
  quiet rooms and reasonably extended in the bass. Aim it at the wall or window the noise
  comes through and keep the position and gain fixed. Treat its results as relative
  comparisons, not calibrated levels.
- **Samson Q2U** (dynamic): **not recommended** except for loud events. Its low sensitivity
  means high gain, and the interface hiss then masks quiet hums and bass.
- **Laptop built-in mic, webcam, Bluetooth headset, USB headset/conference mic:** avoid all of these.
  They apply noise suppression, automatic gain and high-pass filtering that remove exactly
  what you are trying to document.

## Accessories

- A floor stand or tripod with a mic clip, so the mic is never hand-held. Measurement mics
  need a slim clip.
- A foam windscreen, for recording at an open window or outdoors.
- A long USB or XLR cable, so you can stand away from the mic while it records.

See also [`src/noise_recorder/data/microphone-guide.md`](../src/noise_recorder/data/microphone-guide.md)
(shown in the app's About tab) for Ubuntu setup, placement and recording protocol.
