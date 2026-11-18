"""Capture from a PortAudio input to 24-bit WAV, with a live buffer for the meters.

The callback only copies blocks into a queue; a writer thread puts them on disk, so
the GUI never touches the audio thread. Monitor mode (path=None) feeds the meters
without writing anything.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf


@dataclass
class CaptureStats:
    frames: int = 0
    sum_sq: float = 0.0
    peak: float = 0.0
    clipped: int = 0
    overflows: int = 0
    status_messages: list = field(default_factory=list)


class Recorder:
    def __init__(self, device: int | None, samplerate: int, channels: int = 1,
                 path: Path | None = None, duration_s: float | None = None,
                 wav_tags: dict | None = None, live_seconds: float = 4.0):
        self.device = device
        self.requested_samplerate = samplerate
        self.channels = channels
        self.path = path
        self.duration_s = duration_s
        self.wav_tags = wav_tags or {}
        self.live_seconds = live_seconds
        self.stats = CaptureStats()
        self.samplerate = samplerate
        self.start_utc: datetime | None = None
        self.end_utc: datetime | None = None
        self.start_monotonic: float | None = None
        self.error: str | None = None
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._live = np.zeros(1)
        self._stream: sd.InputStream | None = None
        self._writer: threading.Thread | None = None
        self._done = threading.Event()
        self._target_frames: int | None = None

    # ---- lifecycle -------------------------------------------------------------
    def _open_stream(self, rate: int) -> sd.InputStream:
        return sd.InputStream(device=self.device, samplerate=rate, channels=self.channels,
                              dtype="float32", callback=self._callback, blocksize=0)

    def start(self) -> None:
        try:
            self._stream = self._open_stream(self.requested_samplerate)
            self.samplerate = self.requested_samplerate
        except sd.PortAudioError:
            # Device can't do the requested rate; fall back to its native rate.
            rate = int(sd.query_devices(self.device, "input")["default_samplerate"])
            self._stream = self._open_stream(rate)
            self.samplerate = rate
        self._live = np.zeros(int(self.samplerate * self.live_seconds), dtype=np.float32)
        if self.duration_s:
            self._target_frames = int(round(self.duration_s * self.samplerate))
        if self.path is not None:
            self._writer = threading.Thread(target=self._write_loop, daemon=True)
            self._writer.start()
        self.start_utc = datetime.now(timezone.utc)
        self.start_monotonic = time.monotonic()
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except sd.PortAudioError:
                pass
        if self.end_utc is None:
            self.end_utc = datetime.now(timezone.utc)
        self._q.put(None)
        if self._writer is not None:
            self._writer.join(timeout=10)
        self._done.set()

    @property
    def finished(self) -> bool:
        """True once a timed recording has captured all its frames."""
        return self._target_frames is not None and self.stats.frames >= self._target_frames

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.finished:
            if deadline and time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        self.stop()
        return True

    # ---- audio thread ----------------------------------------------------------
    def _callback(self, indata, frames, time_info, status):
        if status:
            self.stats.overflows += 1
            if len(self.stats.status_messages) < 50:
                self.stats.status_messages.append(str(status))
        block = indata.copy()
        if self._target_frames is not None:
            remaining = self._target_frames - self.stats.frames
            if remaining <= 0:
                raise sd.CallbackStop
            block = block[:remaining]
        mono = block.mean(axis=1)
        st = self.stats
        st.frames += len(block)
        st.sum_sq += float(np.dot(mono, mono))
        a = float(np.max(np.abs(block))) if len(block) else 0.0
        st.peak = max(st.peak, a)
        st.clipped += int(np.sum(np.abs(block) >= 0.999))
        with self._lock:
            n = len(mono)
            if n >= len(self._live):
                self._live[:] = mono[-len(self._live):]
            else:
                self._live = np.roll(self._live, -n)
                self._live[-n:] = mono
        if self.path is not None:
            self._q.put(block)
        if self._target_frames is not None and st.frames >= self._target_frames:
            self.end_utc = datetime.now(timezone.utc)
            raise sd.CallbackStop

    def _write_loop(self):
        try:
            with sf.SoundFile(str(self.path), "w", samplerate=self.samplerate,
                              channels=self.channels, subtype="PCM_24", format="WAV") as f:
                for k, v in self.wav_tags.items():
                    try:
                        setattr(f, k, str(v)[:1000])
                    except Exception:  # libsndfile refuses some tags for some formats
                        pass
                while True:
                    block = self._q.get()
                    if block is None:
                        break
                    f.write(block)
        except Exception as e:  # surfaced to the GUI
            self.error = f"writer: {e}"

    # ---- live data -------------------------------------------------------------
    def live(self, n: int) -> np.ndarray:
        with self._lock:
            return self._live[-n:].copy()

    def elapsed(self) -> float:
        return self.stats.frames / self.samplerate if self.samplerate else 0.0

    def leq_dbfs(self) -> float:
        if not self.stats.frames:
            return float("-inf")
        return 10 * np.log10(max(self.stats.sum_sq / self.stats.frames, 1e-20))
