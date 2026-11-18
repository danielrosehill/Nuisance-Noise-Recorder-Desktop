# Microphones for recording noise nuisance

The goal is a recording whose spectrum reflects the noise in the room, not the
microphone's voice tuning or a sound server's noise suppression. Speech microphones are
built to do the opposite of what you need here: they cut low frequencies, boost the
"presence" range and, on laptops and headsets, actively delete steady background noise.

## What matters, in order

1. **No processing.** No automatic gain (AGC), no noise suppression, no echo
   cancellation, no low-cut filter. A mic that "cleans up" audio will erase hums and drones.
2. **Flat frequency response down to 20 Hz.** Most indoor nuisance noise (bass from a bar,
   mechanical plant, construction rumble, traffic) carries much of its energy below 200 Hz.
   Many consumer mics roll off steeply below 80-100 Hz.
3. **Omnidirectional pattern.** Room noise arrives from everywhere, often through the
   structure. Cardioid (directional) mics colour low frequencies with distance.
4. **Low self-noise.** A quiet bedroom at night can be 25-30 dBA. A mic whose own hiss is
   above that records itself, not the room.
5. **Known sensitivity (calibration).** Without it, levels are relative (dBFS): fine for
   comparing takes made with the same mic and gain, not for stating dB SPL.
6. **Fixed gain.** Set the input gain once and never change it between a nuisance take and
   its background take.

## Recommended options

**USB measurement microphones (best value, plug and play on Ubuntu).** Omnidirectional
measurement capsules with a built-in USB interface, sold with a per-unit calibration file.
Examples: miniDSP UMIK-1 and UMIK-2, Dayton Audio UMM-6. They show up as standard USB
audio devices with no driver. Load the calibration file in Settings → *Mic response
file* to correct the frequency response.

**XLR measurement microphones + a USB audio interface.** E.g. Behringer ECM8000, Dayton
Audio EMM-6, or higher-grade capsules from iSEMcon or Earthworks, plugged into a simple
interface (Focusrite Scarlett Solo or similar) with phantom power on and the interface's
gain knob left at a fixed, marked position. Turn off any "Air", auto-gain or low-cut
switch on the interface.

**Sound level meters (when numbers must stand up).** An IEC 61672 Class 1 or Class 2
meter gives traceable dB(A)/dB(C) values. Some have an AC or USB audio output you can
record from this app. For formal complaints, many authorities only accept readings from
certified Class 1 instruments operated to a defined procedure; recordings from this app
are supporting evidence for that, not a replacement.

**Acoustic calibrator.** A 94 dB SPL / 1 kHz calibrator lets you turn dBFS into dB SPL:
put it on the mic, press Monitor, read the Level Z value, and set Settings → *Offset* to
`94 - reading`. Recalibrate if you change the gain, interface or mic.

**Very low frequency and vibration.** If the nuisance is something you feel more than
hear (sub-20 Hz, building vibration), a microphone is the wrong sensor: use an
accelerometer or geophone on the floor or wall.

## Avoid

- **The laptop's built-in microphone.** Usually a beamforming array with noise suppression
  and a high-pass filter. It will under-report rumble and may remove steady hum entirely.
- **USB podcast/streaming mics** (cardioid condensers tuned for voice): directional, voice EQ,
  often a built-in low-cut.
- **Bluetooth headsets and earbuds.** 16 kHz or lower sample rate in mic mode, heavy processing.
- **Webcam microphones.** Noise suppression and AGC in firmware.
- **Phones**, unless the recorder app can use the *Unprocessed* audio source and writes WAV.

## Setting up Ubuntu for clean capture

- If the Record tab lists an entry marked **[direct, unprocessed]** (an ALSA
  `hw:` device), prefer it: it bypasses PipeWire/PulseAudio entirely, so no desktop noise
  suppression or resampling can touch the signal. If it reports "device busy", close other
  apps using the mic or use the sound-server entry. On most PipeWire desktops only the
  sound-server entries are listed; the app then records the input volume and any loaded
  noise-suppression modules into recording.json so the capture path is documented.
- If you use the sound-server path: in *Settings → Sound* or `pavucontrol`, set the mic's input
  volume to 100 % (0 dB) and leave it there, and make sure no noise-suppression filter
  (EasyEffects, a PipeWire `echo-cancel` or RNNoise module) sits in the chain.
- Record at 48 kHz (default). 96 kHz adds nothing for nuisance noise.

## Placement and protocol

- **Tripod or a soft surface**, never hand-held. 1.2-1.5 m high (ear height), at least 1 m
  from walls and large furniture, away from your own fridge, fans or computer.
- Turn off what you can of your own noise sources; write down what stays on.
- **Record at least 60 s**, longer (2-5 min) for anything that cycles on and off.
- **Record a background take** of the same spot with the same gain when the nuisance is
  absent. Then use *Re-analyse vs background* on the Analysis tab: it shows exactly which
  frequency bands the nuisance adds.
- Note **windows and doors** state: louder with windows shut points to structure-borne
  low-frequency noise; louder with them open points to airborne.
- Don't talk or move during the take; put observations in the Notes field.
- For a pattern over time (a bar every weekend night), make repeated runs at the same spot
  and time; each run is independently sealed and timestamped.
