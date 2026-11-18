# Integrity model

Goal: a third party should be able to check, with standard tools and without trusting this
app, that a recording has not been altered since it was made, and that it existed no later
than a stated time.

## What sealing does (at the end of every recording)

1. `audio.wav` is written directly by the capture thread as 24-bit PCM: no resampling,
   normalising, denoising or compression. RIFF INFO tags (title, start time, software)
   are embedded in the file itself.
2. `recording.json` records start/end in UTC and local time, duration, frame count, sample
   rate, the capture device (PortAudio name, ALSA card, USB vendor:product ID and USB
   stream descriptor where available), whether it was captured direct (ALSA `hw:`) or via
   the sound server, overflow events, clipping, calibration and software version.
3. `analysis/` holds `analysis.json` and plots computed from the WAV.
4. `manifest.json` lists every one of those files with SHA-256 and size, plus the full
   recording metadata, the host and the tool version. Keys are sorted for stable output.
5. `SHA256SUMS` repeats the hashes (and adds the manifest's own) in coreutils format.
6. `timestamp/manifest.json.tsr` is an RFC 3161 time-stamp token from a public Time
   Stamping Authority (default FreeTSA, `https://freetsa.org/tsr`) over the SHA-512 of
   `manifest.json`. Only that hash leaves the machine. The TSA's CA and signing
   certificates are saved beside it (`cacert.pem`, `tsa.crt`) so verification works offline.
7. Optional: `manifest.json.asc`, a detached GPG signature by the operator's key
   (Settings → GPG signing key).

The seal is never rewritten. Later work goes into `addenda/<name>/` (AI evaluations,
re-analysis against a background take), each with an `addendum.json` that lists its own
file hashes and pins `parent_manifest_sha256`, and gets its own timestamp and signature.

## Verifying a run

With the app: Analysis tab → *Verify integrity*, or `nnrec verify <run folder>` (JSON,
exit code 0 = OK).

Without the app, from inside the run folder:

```bash
sha256sum -c SHA256SUMS
openssl ts -verify -data manifest.json -in timestamp/manifest.json.tsr \
  -CAfile timestamp/cacert.pem -untrusted timestamp/tsa.crt
openssl ts -reply -in timestamp/manifest.json.tsr -text | grep 'Time stamp'
gpg --verify manifest.json.asc manifest.json   # if signed
```

## What this does and does not prove

- **Proves:** the files are bit-identical to what was sealed; the manifest (and so the
  audio hash and metadata) existed no later than the TSA's time; if signed, the holder of
  that GPG key sealed it.
- **Does not prove:** that the system clock was right at the start time (the TSA time
  bounds it from above only; the gap between `start_utc` and the TSA time should be about
  the recording length plus analysis time, and is worth pointing out), or what made the
  sound. Keep the original run folder untouched; export copies with *Export ZIP*.
- **Strengthen it** with a second TSA (change the URL and re-seal a copy), by emailing
  `SHA256SUMS` to yourself or a third party right after recording, and by taking repeated
  recordings over days.
