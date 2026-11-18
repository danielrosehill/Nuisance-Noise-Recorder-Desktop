"""PySide6 + pyqtgraph desktop app: Record, Analysis, AI evaluation, Settings, About."""

from __future__ import annotations

import json
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)
from scipy import signal

from . import APP_NAME, __version__, analysis, config, devices, manifest, openrouter, runs
from .recorder import Recorder

pg.setConfigOptions(antialias=True, background="#111418", foreground="#d0d4da")

FFT_N = 8192
WATERFALL_ROWS = 200  # at 20 fps = 10 s of history
RANGES = {"0-500 Hz": 500, "0-2 kHz": 2000, "0-8 kHz": 8000, "Full": None}


def _db(x):
    return 10 * np.log10(np.maximum(x, 1e-20))


def big_label(text="--", size=20):
    lab = QLabel(text)
    f = QFont()
    f.setPointSize(size)
    f.setBold(True)
    lab.setFont(f)
    lab.setAlignment(Qt.AlignCenter)
    return lab


class Background:
    """Run callables off the GUI thread; deliver results on it via a polling timer."""

    def __init__(self, parent):
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.jobs: list[tuple[Future, callable, callable]] = []
        self.timer = QTimer(parent)
        self.timer.timeout.connect(self._poll)
        self.timer.start(100)

    def run(self, fn, on_done, on_fail):
        self.jobs.append((self.pool.submit(fn), on_done, on_fail))

    def _poll(self):
        for job in [j for j in self.jobs if j[0].done()]:
            self.jobs.remove(job)
            fut, ok, bad = job
            e = fut.exception()
            if e:
                bad(f"{type(e).__name__}: {e}")
            else:
                ok(fut.result())


# =====================================================================================
class RecordTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.rec: Recorder | None = None
        self.run_dir: Path | None = None
        self.max_a = -np.inf
        self.peak_hold = None
        self.window = np.hanning(FFT_N).astype(np.float32)
        self.enbw = FFT_N * float(np.sum(self.window ** 2)) / float(np.sum(self.window)) ** 2

        # --- controls
        self.device = QComboBox()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.load_devices)
        self.duration = QSpinBox()
        self.duration.setRange(5, 3600)
        self.duration.setSuffix(" s")
        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. bar music, bedroom")
        self.location = QLineEdit()
        self.windows = QComboBox()
        self.windows.addItems(["", "closed", "open", "partly open"])
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("What you hear, what is switched off, mic position...")
        self.notes.setMaximumHeight(70)

        form = QFormLayout()
        dev_row = QHBoxLayout()
        dev_row.addWidget(self.device, 1)
        dev_row.addWidget(refresh)
        form.addRow("Microphone", dev_row)
        form.addRow("Duration", self.duration)
        form.addRow("Title", self.title)
        form.addRow("Location", self.location)
        form.addRow("Windows/doors", self.windows)
        form.addRow("Notes", self.notes)

        self.monitor_btn = QPushButton("Monitor")
        self.monitor_btn.setCheckable(True)
        self.monitor_btn.toggled.connect(self.toggle_monitor)
        self.record_btn = QPushButton("● Record")
        self.record_btn.setStyleSheet("QPushButton{background:#b3261e;color:white;font-weight:bold;padding:8px}")
        self.record_btn.clicked.connect(self.start_recording)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_recording)
        btns = QHBoxLayout()
        for b in (self.monitor_btn, self.record_btn, self.stop_btn):
            btns.addWidget(b)
        self.progress = QProgressBar()
        self.status = QLabel("Idle. Press Monitor to check levels before recording.")
        self.status.setWordWrap(True)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addLayout(btns)
        left.addWidget(self.progress)
        left.addWidget(self.status)

        # --- readouts
        self.lvl_a, self.lvl_z, self.leq, self.lmax, self.peak = (big_label() for _ in range(5))
        self.unit = QLabel()
        self.clip = QLabel("")
        self.clip.setStyleSheet("color:#ff5252;font-weight:bold")
        grid = QGridLayout()
        for i, (name, lab) in enumerate((("Level A", self.lvl_a), ("Level Z", self.lvl_z), ("Leq Z", self.leq),
                                         ("Max A", self.lmax), ("Peak", self.peak))):
            r, col = divmod(i, 3)
            cap = QLabel(name)
            cap.setAlignment(Qt.AlignCenter)
            lab.setMinimumWidth(110)
            grid.addWidget(cap, r * 2, col)
            grid.addWidget(lab, r * 2 + 1, col)
        self.unit.setWordWrap(True)
        grid.addWidget(self.unit, 4, 0, 1, 3)
        grid.addWidget(self.clip, 5, 0, 1, 3)
        meters = QGroupBox("Sound level (fast, ~170 ms)")
        meters.setLayout(grid)
        left.addWidget(meters)
        left.addStretch(1)

        # --- plots
        self.spec = pg.PlotWidget(title="Live spectrum")
        self.spec.setLogMode(x=True, y=False)
        self.spec.setLabel("bottom", "Frequency", units="Hz")
        self.spec.setLabel("left", "Level (dB per bin)")
        self.spec.setYRange(-130, -10)
        self.spec.setXRange(np.log10(10), np.log10(24000), padding=0)
        self.spec.showGrid(x=True, y=True, alpha=0.3)
        self.curve = self.spec.plot(pen=pg.mkPen("#4fc3f7", width=1.2))
        self.hold_curve = self.spec.plot(pen=pg.mkPen("#ffb74d", width=0.8, style=Qt.DashLine))
        self.wf_range = QComboBox()
        self.wf_range.addItems(list(RANGES))
        self.wf_range.setCurrentText("0-2 kHz")
        self.wf = pg.PlotWidget(title="Spectrogram (last 10 s)")
        self.wf.setLabel("bottom", "Time", units="s")
        self.wf.setLabel("left", "Frequency", units="Hz")
        self.wf_img = pg.ImageItem()
        self.wf_img.setColorMap(pg.colormap.get("inferno"))
        self.wf.addItem(self.wf_img)
        self.wf_data = None

        right = QVBoxLayout()
        right.addWidget(self.spec, 3)
        r = QHBoxLayout()
        r.addWidget(QLabel("Spectrogram range"))
        r.addWidget(self.wf_range)
        hold_reset = QPushButton("Reset peak hold")
        hold_reset.clicked.connect(lambda: setattr(self, "peak_hold", None))
        r.addWidget(hold_reset)
        r.addStretch(1)
        right.addLayout(r)
        right.addWidget(self.wf, 2)

        lw, rw = QWidget(), QWidget()
        lw.setLayout(left)
        rw.setLayout(right)
        lw.setMaximumWidth(460)
        split = QSplitter()
        split.addWidget(lw)
        split.addWidget(rw)
        split.setStretchFactor(1, 1)
        lay = QHBoxLayout(self)
        lay.addWidget(split)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.load_devices()
        self.apply_cfg()

    # -------------------------------------------------------------------------------
    def apply_cfg(self):
        cfg = self.win.cfg
        self.duration.setValue(int(cfg.get("duration_s", 60)))
        self.location.setText(cfg.get("location", ""))
        off = cfg.get("calibration_offset_db")
        self.unit.setText("Units: dB SPL (calibration offset applied)" if off is not None
                          else "Units: dBFS (uncalibrated; compare takes relatively)")

    def load_devices(self):
        self.device.clear()
        self.device.addItem("System default (sound server)", None)
        for d in devices.list_inputs():
            tag = " [direct, unprocessed]" if "(hw:" in d["name"] else ""
            self.device.addItem(f"{d['label']}{tag}  ·  {d['max_input_channels']} ch", d["name"])
        want = self.win.cfg.get("device", "")
        for i in range(self.device.count()):
            if self.device.itemData(i) == want:
                self.device.setCurrentIndex(i)

    def device_index(self):
        name = self.device.currentData()
        return devices.find(name) if name else None

    def offset(self):
        return self.win.cfg.get("calibration_offset_db") or 0.0

    # -------------------------------------------------------------------------------
    def _open(self, path=None, duration=None, tags=None):
        cfg = self.win.cfg
        rec = Recorder(self.device_index(), int(cfg.get("sample_rate", 48000)), int(cfg.get("channels", 1)),
                       path, duration, tags)
        rec.start()
        self.rec = rec
        self.max_a = -np.inf
        self.wf_data = None
        self.timer.start(50)
        return rec

    def toggle_monitor(self, on):
        if on:
            if self.rec:
                return
            try:
                self._open()
                self.status.setText(f"Monitoring at {self.rec.samplerate} Hz. Nothing is being saved.")
                self.record_btn.setEnabled(True)
            except Exception as e:
                self.monitor_btn.setChecked(False)
                QMessageBox.critical(self, "Cannot open device", str(e))
        elif self.rec and self.rec.path is None:
            self.rec.stop()
            self.rec = None
            self.timer.stop()
            self.status.setText("Idle.")

    def start_recording(self):
        if self.rec and self.rec.path is None:
            self.monitor_btn.setChecked(False)
        cfg = self.win.cfg
        cfg["device"] = self.device.currentData() or ""
        config.save(cfg)
        try:
            self.run_dir = runs.new_run_dir(Path(cfg["runs_dir"]), self.title.text())
            self._open(self.run_dir / "audio.wav", float(self.duration.value()),
                       runs.wav_tags(self.title.text(), datetime.now(timezone.utc)))
        except Exception as e:
            QMessageBox.critical(self, "Cannot start recording", str(e))
            return
        self.record_btn.setEnabled(False)
        self.monitor_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setRange(0, self.duration.value())
        self.status.setText(f"Recording to {self.run_dir} at {self.rec.samplerate} Hz. Keep still and quiet.")

    def stop_recording(self):
        if self.rec and self.rec.path is not None:
            self._finish()

    def _finish(self):
        rec, run_dir = self.rec, self.run_dir
        rec.stop()
        self.rec = None
        self.timer.stop()
        self.stop_btn.setEnabled(False)
        self.status.setText("Analysing and sealing (hashes, RFC 3161 timestamp)...")
        form = {"title": self.title.text(), "location": self.location.text(), "notes": self.notes.toPlainText(),
                "windows": self.windows.currentText(), "operator": self.win.cfg.get("operator", "")}
        idx = self.device_index()
        cfg = dict(self.win.cfg)
        self.win.bg.run(lambda: runs.finalise(run_dir, rec, idx, cfg, form), self._finalised, self._failed)

    def _finalised(self, res):
        self.record_btn.setEnabled(True)
        self.monitor_btn.setEnabled(True)
        ts = res["seal"]["timestamp"]
        self.status.setText(f"Saved and sealed: {res['run_dir']}\nManifest SHA-256 {res['seal']['manifest_sha256'][:16]}..., "
                            f"timestamp: {ts.get('status')} {ts.get('gen_time') or ''}")
        self.win.analysis_tab.refresh_runs(select=Path(res["run_dir"]))
        self.win.tabs.setCurrentWidget(self.win.analysis_tab)

    def _failed(self, err):
        self.record_btn.setEnabled(True)
        self.monitor_btn.setEnabled(True)
        self.status.setText("Finalising failed: " + err)
        QMessageBox.critical(self, "Finalising failed", err)

    # -------------------------------------------------------------------------------
    def tick(self):
        rec = self.rec
        if rec is None:
            return
        if rec.error:
            self.status.setText("Error: " + rec.error)
        x = rec.live(FFT_N)
        if len(x) < FFT_N:
            return
        sr = rec.samplerate
        X = np.fft.rfft(x * self.window)
        p = (np.abs(X) ** 2) * 2 / (self.window.sum() ** 2)
        f = np.fft.rfftfreq(FFT_N, 1 / sr)
        off = self.offset()
        pdb = _db(p) + off
        self.curve.setData(f[1:], pdb[1:])
        self.peak_hold = pdb if self.peak_hold is None or len(self.peak_hold) != len(pdb) else np.maximum(self.peak_hold, pdb)
        self.hold_curve.setData(f[1:], self.peak_hold[1:])

        z = _db(np.mean(x ** 2)) + off
        # Summed Hann bins over-count power by the window's equivalent noise bandwidth.
        a = _db(np.sum(p[1:] * 10 ** (analysis.a_weight_db(f[1:]) / 10)) / self.enbw) + off
        self.max_a = max(self.max_a, a) if rec.elapsed() > 0.3 else self.max_a
        self.lvl_a.setText(f"{a:.1f}")
        self.lvl_z.setText(f"{z:.1f}")
        self.leq.setText(f"{rec.leq_dbfs() + off:.1f}")
        self.lmax.setText(f"{self.max_a:.1f}" if np.isfinite(self.max_a) else "--")
        self.peak.setText(f"{20 * np.log10(max(rec.stats.peak, 1e-12)):.1f}")
        self.clip.setText(f"CLIPPING ({rec.stats.clipped} samples): lower the gain" if rec.stats.clipped else "")

        fmax = RANGES[self.wf_range.currentText()] or sr / 2
        m = f <= fmax
        row = pdb[m]
        if self.wf_data is None or self.wf_data.shape[1] != len(row):
            self.wf_data = np.full((WATERFALL_ROWS, len(row)), -140.0, dtype=np.float32)
        self.wf_data = np.roll(self.wf_data, -1, axis=0)
        self.wf_data[-1] = row
        self.wf_img.setImage(self.wf_data, autoLevels=False, levels=(-120 + off, -30 + off))
        self.wf_img.setRect(QRectF(-WATERFALL_ROWS * 0.05, 0, WATERFALL_ROWS * 0.05, f[m][-1]))

        if rec.path is not None:
            self.progress.setValue(int(rec.elapsed()))
            if rec.finished:
                self._finish()


# =====================================================================================
class AnalysisTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.run_dir: Path | None = None
        self.res: analysis.Analysis | None = None

        self.runs = QComboBox()
        self.runs.currentIndexChanged.connect(self._run_changed)
        browse = QPushButton("Open folder...")
        browse.clicked.connect(self.browse)
        self.bg_runs = QComboBox()
        reanalyse = QPushButton("Re-analyse vs background")
        reanalyse.clicked.connect(self.reanalyse)
        verify = QPushButton("Verify integrity")
        verify.clicked.connect(self.verify)
        export = QPushButton("Export ZIP...")
        export.clicked.connect(self.export)
        openf = QPushButton("Open in file manager")
        openf.clicked.connect(lambda: self.run_dir and QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.run_dir))))

        top = QHBoxLayout()
        top.addWidget(QLabel("Run"))
        top.addWidget(self.runs, 2)
        top.addWidget(browse)
        top.addWidget(QLabel("Background"))
        top.addWidget(self.bg_runs, 1)
        top.addWidget(reanalyse)
        bot = QHBoxLayout()
        for b in (verify, export, openf):
            bot.addWidget(b)
        bot.addStretch(1)

        self.summary = QTextBrowser()
        self.plots = QTabWidget()
        self.p_spec = pg.PlotWidget(title="Long-term average spectrum (Welch, ~0.5 Hz bins)")
        self.p_spec.setLogMode(x=True, y=False)
        self.p_spec.showGrid(x=True, y=True, alpha=0.3)
        self.p_spec.setLabel("bottom", "Frequency", units="Hz")
        self.p_spec.addLegend()
        self.p_thirds = pg.PlotWidget(title="1/3-octave bands: unweighted (blue) vs A-weighted (orange)")
        self.p_thirds.showGrid(y=True, alpha=0.3)
        self.p_sg = pg.PlotWidget(title="Spectrogram 0-500 Hz (where most indoor nuisance noise lives)")
        self.p_sg.setLabel("bottom", "Time", units="s")
        self.p_sg.setLabel("left", "Frequency", units="Hz")
        self.sg_img = pg.ImageItem()
        self.sg_img.setColorMap(pg.colormap.get("inferno"))
        self.p_sg.addItem(self.sg_img)
        self.p_sgfull = pg.PlotWidget(title="Spectrogram, full range")
        self.p_sgfull.setLabel("bottom", "Time", units="s")
        self.p_sgfull.setLabel("left", "Frequency", units="Hz")
        self.sgf_img = pg.ImageItem()
        self.sgf_img.setColorMap(pg.colormap.get("inferno"))
        self.p_sgfull.addItem(self.sgf_img)
        self.p_lev = pg.PlotWidget(title="Short-term level (125 ms) with L10 / L50 / L90")
        self.p_lev.setLabel("bottom", "Time", units="s")
        self.p_lev.showGrid(x=True, y=True, alpha=0.3)
        self.peaks = QTableWidget(0, 4)
        self.peaks.setHorizontalHeaderLabels(["Frequency (Hz)", "Level", "Prominence (dB)", "A-weighting (dB)"])
        self.peaks.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for w, name in ((self.p_spec, "Spectrum"), (self.p_thirds, "1/3 octaves"), (self.p_sg, "Low-frequency spectrogram"),
                        (self.p_sgfull, "Full spectrogram"), (self.p_lev, "Level over time"), (self.peaks, "Tonal peaks")):
            self.plots.addTab(w, name)

        split = QSplitter()
        split.addWidget(self.summary)
        split.addWidget(self.plots)
        split.setSizes([380, 900])
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addLayout(bot)
        self.refresh_runs()

    def refresh_runs(self, select: Path | None = None):
        items = runs.list_runs(Path(self.win.cfg["runs_dir"]))
        self.runs.blockSignals(True)
        self.runs.clear()
        self.bg_runs.clear()
        self.bg_runs.addItem("(none)", None)
        for p in items:
            self.runs.addItem(p.name, str(p))
            self.bg_runs.addItem(p.name, str(p))
        if select is not None and str(select) not in [self.runs.itemData(i) for i in range(self.runs.count())]:
            self.runs.insertItem(0, select.name, str(select))
        self.runs.blockSignals(False)
        if select is not None:
            self.runs.setCurrentIndex([self.runs.itemData(i) for i in range(self.runs.count())].index(str(select)))
            self._run_changed()
        elif self.runs.count():
            self._run_changed()

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Open run folder", self.win.cfg["runs_dir"])
        if d:
            self.refresh_runs(select=Path(d))

    def _run_changed(self, *_):
        d = self.runs.currentData()
        if d:
            self.load(Path(d))

    def load(self, run_dir: Path, background: Path | None = None):
        self.run_dir = run_dir
        self.win.ai_tab.set_run(run_dir)
        wav = next(run_dir.glob("*.wav"), None)
        if wav is None:
            self.summary.setPlainText(f"No WAV in {run_dir}")
            return
        cfg = self.win.cfg
        bgwav = next(background.glob("*.wav"), None) if background else None
        self.summary.setPlainText("Analysing...")
        self.win.bg.run(lambda: analysis.analyse(wav, None, bgwav, cfg.get("calibration_offset_db"),
                                                 cfg.get("calibration_file") or None),
                        lambda res: self.show(res), lambda e: self.summary.setPlainText("Analysis failed: " + e))

    def show(self, res: analysis.Analysis):
        self.res = res
        r, off = res.result, res.offset
        rd = self.run_dir
        md = [f"## {rd.name}\n"]
        rec = rd / "recording.json"
        if rec.exists():
            m = json.loads(rec.read_text())
            dev = m.get("device", {})
            md += [f"- **Start:** {m.get('start_local')}", f"- **Duration:** {m.get('duration_s')} s @ {m.get('sample_rate_hz')} Hz",
                   f"- **Mic:** {dev.get('portaudio_name')} ({dev.get('path')})",
                   f"- **Location:** {m.get('location') or '-'} · windows: {m.get('windows') or '-'}"]
            if m.get("notes"):
                md.append(f"- **Notes:** {m['notes']}")
        sr = rd / "timestamp" / "seal-report.json"
        if sr.exists():
            s = json.loads(sr.read_text())
            md.append(f"- **Seal:** manifest `{s['manifest_sha256'][:16]}…`, timestamp {s['timestamp'].get('status')} "
                      f"{s['timestamp'].get('gen_time') or ''}, signature {s['signature'].get('status')}")
        md.append("\n### Measurements\n")
        md += [f"- {line}" for line in analysis.summary_text(r).splitlines()]
        self.summary.setMarkdown("\n".join(md))

        unit = "dB SPL" if r.get("calibration_offset_db") is not None else "dBFS"
        self.p_spec.clear()
        self.p_spec.plot(res.f[1:], analysis.db(res.p[1:]) + off, pen=pg.mkPen("#4fc3f7", width=1), name="recording")
        if res.bg is not None:
            self.p_spec.plot(res.bg[0][1:], analysis.db(res.bg[1][1:]) + off, pen=pg.mkPen("#9e9e9e", width=1), name="background")
        for pk in r["tonal_peaks"][:8]:
            t = pg.TextItem(f"{pk['freq_hz']:g}", color="#ffb74d", anchor=(0.5, 1))
            t.setPos(np.log10(pk["freq_hz"]), pk["level_db"] + off)
            self.p_spec.addItem(t)
        self.p_spec.setLabel("left", f"Level ({unit} per bin)")
        self.p_spec.setXRange(np.log10(10), np.log10(res.sr / 2), padding=0)

        self.p_thirds.clear()
        th = res.thirds
        xs = np.arange(len(th))
        self.p_thirds.addItem(pg.BarGraphItem(x=xs - 0.2, height=[t["level_db"] + off - (-140) for t in th], y0=-140,
                                              width=0.4, brush="#4fc3f7"))
        self.p_thirds.addItem(pg.BarGraphItem(x=xs + 0.2, height=[t["level_dba"] + off - (-140) for t in th], y0=-140,
                                              width=0.4, brush="#ffb74d"))
        lo = min(min(t["level_dba"], t["level_db"]) for t in th) + off - 5
        self.p_thirds.setYRange(lo, max(t["level_db"] for t in th) + off + 5)
        self.p_thirds.getAxis("bottom").setTicks([[(i, f"{t['centre_hz'] / 1000:g}k" if t["centre_hz"] >= 1000 else f"{t['centre_hz']:g}")
                                                   for i, t in enumerate(th)]])
        self.p_thirds.setLabel("left", unit)

        for img, fmax, nper in ((self.sg_img, 500, int(2 ** np.ceil(np.log2(res.sr)))), (self.sgf_img, res.sr / 2, 2048)):
            nper = min(nper, len(res.x))
            ff, tt, S = signal.spectrogram(res.x, fs=res.sr, nperseg=nper, noverlap=nper * 3 // 4)
            m = ff <= fmax
            data = (analysis.db(S[m]) + off).T
            lvl = np.percentile(data, [5, 99.9])
            img.setImage(data, autoLevels=False, levels=(lvl[0], lvl[1]))
            img.setRect(QRectF(0, 0, tt[-1] if len(tt) else 1, ff[m][-1]))

        self.p_lev.clear()
        if len(res.lev):
            self.p_lev.plot(res.t, res.lev + off, pen=pg.mkPen("#4fc3f7", width=1))
            for pct, color in ((90, "#e57373"), (50, "#fff176"), (10, "#81c784")):
                v = np.percentile(res.lev, 100 - pct) + off
                self.p_lev.addItem(pg.InfiniteLine(v, angle=0, pen=pg.mkPen(color, style=Qt.DashLine),
                                                   label=f"L{pct} {v:.1f}", labelOpts={"color": color, "position": 0.95}))
        self.p_lev.setLabel("left", unit)

        self.peaks.setRowCount(len(r["tonal_peaks"]))
        for i, pk in enumerate(r["tonal_peaks"]):
            for j, v in enumerate((pk["freq_hz"], round(pk["level_db"] + off, 1), pk["prominence_db"], pk["a_weighting_db"])):
                self.peaks.setItem(i, j, QTableWidgetItem(str(v)))

    def reanalyse(self):
        if not self.run_dir:
            return
        bg = self.bg_runs.currentData()
        if not bg:
            QMessageBox.information(self, "Background", "Pick a background run: the same spot, same gain, nuisance absent.")
            return
        rd, cfg = self.run_dir, dict(self.win.cfg)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        def job():
            out = rd / "addenda" / f"analysis-vs-background-{stamp}"
            res = analysis.analyse(next(rd.glob("*.wav")), out, next(Path(bg).glob("*.wav")),
                                   cfg.get("calibration_offset_db"), cfg.get("calibration_file") or None)
            manifest.add_addendum(rd, out.name, cfg, {"kind": "analysis-vs-background", "background_run": Path(bg).name,
                                                      "background_manifest_sha256": manifest.sha256_file(Path(bg) / "manifest.json")
                                                      if (Path(bg) / "manifest.json").exists() else None})
            return res
        self.summary.setPlainText("Analysing against background and sealing addendum...")
        self.win.bg.run(job, self.show, lambda e: self.summary.setPlainText("Failed: " + e))

    def verify(self):
        if not self.run_dir:
            return
        rd = self.run_dir
        self.win.bg.run(lambda: manifest.verify(rd), self._verified, lambda e: QMessageBox.critical(self, "Verify", e))

    def _verified(self, rep):
        box = QMessageBox(self)
        box.setWindowTitle("Integrity check")
        box.setIcon(QMessageBox.Information if rep["ok"] else QMessageBox.Critical)
        ts = rep.get("timestamp", {})
        box.setText(("✔ All files match the sealed manifest." if rep["ok"] else "✘ Problems found:\n" + "\n".join(rep["problems"]))
                    + f"\n\nFiles checked: {rep.get('files_checked')}\nManifest SHA-256: {rep.get('manifest_sha256')}"
                    + f"\nRFC 3161 timestamp: {ts.get('status')}\nGPG signature: {rep.get('signature', {}).get('status')}"
                    + f"\nAddenda: {len(rep.get('addenda', []))}")
        box.setDetailedText(json.dumps(rep, indent=2))
        box.exec()

    def export(self):
        if not self.run_dir:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Export run", str(self.run_dir.with_suffix(".zip")), "ZIP (*.zip)")
        if dest:
            runs.export_zip(self.run_dir, Path(dest))
            QMessageBox.information(self, "Exported", dest)


# =====================================================================================
class AITab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.run_dir: Path | None = None
        self.run_label = QLabel("No run selected (pick one on the Analysis tab).")
        self.model = QComboBox()
        self.model.setEditable(True)
        refresh = QPushButton("List audio models")
        refresh.clicked.connect(self.refresh_models)
        self.preset = QComboBox()
        self.preset.currentTextChanged.connect(self._preset_changed)
        save_preset = QPushButton("Save prompt as preset")
        save_preset.clicked.connect(self.save_preset)
        self.prompt = QPlainTextEdit()
        self.metrics = QCheckBox("Include measured metrics (tonal peaks, bands, levels)")
        self.metrics.setChecked(True)
        self.structured = QCheckBox("Structured output (JSON: sources, tags, character, next steps)")
        self.structured.setChecked(True)
        self.send = QPushButton("Send to model")
        self.send.clicked.connect(self.go)
        self.previous = QComboBox()
        self.previous.currentIndexChanged.connect(self._show_previous)
        self.out = QTextBrowser()
        self.out.setOpenExternalLinks(True)
        self.status = QLabel("")

        form = QFormLayout()
        mrow = QHBoxLayout()
        mrow.addWidget(self.model, 1)
        mrow.addWidget(refresh)
        form.addRow("Model", mrow)
        prow = QHBoxLayout()
        prow.addWidget(self.preset, 1)
        prow.addWidget(save_preset)
        form.addRow("Preset", prow)
        left = QVBoxLayout()
        left.addWidget(self.run_label)
        left.addLayout(form)
        left.addWidget(QLabel("Prompt"))
        left.addWidget(self.prompt, 1)
        left.addWidget(self.metrics)
        left.addWidget(self.structured)
        left.addWidget(self.send)
        left.addWidget(self.status)
        right = QVBoxLayout()
        pr = QHBoxLayout()
        pr.addWidget(QLabel("Saved evaluations"))
        pr.addWidget(self.previous, 1)
        right.addLayout(pr)
        right.addWidget(self.out, 1)
        lw, rw = QWidget(), QWidget()
        lw.setLayout(left)
        rw.setLayout(right)
        split = QSplitter()
        split.addWidget(lw)
        split.addWidget(rw)
        split.setSizes([450, 800])
        QVBoxLayout(self).addWidget(split)
        self.load_presets()
        self.model.addItem(win.cfg.get("openrouter_model", ""))

    def load_presets(self):
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItems(list(openrouter.PRESETS) + list(self.win.cfg.get("custom_prompts", {})))
        self.preset.blockSignals(False)
        self._preset_changed(self.preset.currentText())

    def _preset_changed(self, name):
        text = openrouter.PRESETS.get(name) or self.win.cfg.get("custom_prompts", {}).get(name, "")
        self.prompt.setPlainText(text)

    def save_preset(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name")
        if ok and name:
            self.win.cfg.setdefault("custom_prompts", {})[name] = self.prompt.toPlainText()
            config.save(self.win.cfg)
            self.load_presets()
            self.preset.setCurrentText(name)

    def refresh_models(self):
        self.status.setText("Fetching models with audio input...")
        cur = self.model.currentText()

        def done(ids):
            self.model.clear()
            self.model.addItems(ids)
            self.model.setCurrentText(cur)
            self.status.setText(f"{len(ids)} audio-capable models.")
        self.win.bg.run(openrouter.audio_models, done, lambda e: self.status.setText("Model list failed: " + e))

    def set_run(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_label.setText(f"Run: <b>{run_dir.name}</b>")
        self.previous.blockSignals(True)
        self.previous.clear()
        for d in sorted((run_dir / "addenda").glob("ai-*"), reverse=True) if (run_dir / "addenda").exists() else []:
            if (d / "evaluation.md").exists():
                self.previous.addItem(d.name, str(d))
        self.previous.blockSignals(False)
        self._show_previous()

    def _show_previous(self, *_):
        d = self.previous.currentData()
        self.out.setMarkdown(Path(d, "evaluation.md").read_text() if d else "")

    def go(self):
        if not self.run_dir:
            QMessageBox.information(self, "No run", "Record or open a run first.")
            return
        cfg = self.win.cfg
        key = config.api_key(cfg)
        if not key:
            QMessageBox.warning(self, "No API key", "Set OPENROUTER_API_KEY in the environment or add a key in Settings.")
            return
        model = self.model.currentText().strip()
        cfg["openrouter_model"] = model
        config.save(cfg)
        rd, prompt = self.run_dir, self.prompt.toPlainText()
        metrics, structured = self.metrics.isChecked(), self.structured.isChecked()
        self.send.setEnabled(False)
        self.status.setText(f"Compressing and sending to {model}...")
        self.win.bg.run(lambda: openrouter.evaluate(rd, prompt, model, key, dict(cfg), metrics, structured),
                        self._done, self._fail)

    def _done(self, res):
        self.send.setEnabled(True)
        u = res.get("usage") or {}
        cost = u.get("cost")
        self.status.setText(f"Saved to {Path(res['dir']).name} · {u.get('prompt_tokens')} in / {u.get('completion_tokens')} out"
                            + (f" · ${cost:.4f}" if isinstance(cost, (int, float)) else ""))
        self.set_run(self.run_dir)

    def _fail(self, err):
        self.send.setEnabled(True)
        self.status.setText("Failed: " + err[:300])


# =====================================================================================
class SettingsTab(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        c = win.cfg
        self.runs_dir = QLineEdit(c["runs_dir"])
        pick = QPushButton("...")
        pick.clicked.connect(lambda: (d := QFileDialog.getExistingDirectory(self, "Runs folder", self.runs_dir.text()))
                             and self.runs_dir.setText(d))
        self.sr = QComboBox()
        self.sr.addItems(["44100", "48000", "96000", "192000"])
        self.sr.setCurrentText(str(c.get("sample_rate", 48000)))
        self.channels = QSpinBox()
        self.channels.setRange(1, 8)
        self.channels.setValue(int(c.get("channels", 1)))
        self.operator = QLineEdit(c.get("operator", ""))
        self.location = QLineEdit(c.get("location", ""))
        self.cal_on = QCheckBox("Apply calibration offset (dB added to dBFS to give dB SPL)")
        self.cal = QDoubleSpinBox()
        self.cal.setRange(-50, 200)
        self.cal.setDecimals(2)
        self.cal_on.setChecked(c.get("calibration_offset_db") is not None)
        self.cal.setValue(c.get("calibration_offset_db") or 0.0)
        self.cal_file = QLineEdit(c.get("calibration_file", ""))
        cal_pick = QPushButton("...")
        cal_pick.clicked.connect(lambda: (f := QFileDialog.getOpenFileName(self, "Mic calibration file")[0])
                                 and self.cal_file.setText(f))
        self.key = QLineEdit(c.get("openrouter_api_key", ""))
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Leave empty to use OPENROUTER_API_KEY from the environment")
        self.model = QLineEdit(c.get("openrouter_model", ""))
        self.ts_on = QCheckBox("Request an RFC 3161 timestamp when sealing (sends only a hash)")
        self.ts_on.setChecked(bool(c.get("rfc3161_enabled", True)))
        self.tsa = QLineEdit(c.get("rfc3161_tsa_url", ""))
        self.gpg = QLineEdit(c.get("gpg_sign_key", ""))
        self.gpg.setPlaceholderText("GPG key ID/email to sign manifests (optional)")

        def row(*ws):
            h = QHBoxLayout()
            for w in ws:
                h.addWidget(w)
            return h
        f = QFormLayout()
        f.addRow("Runs folder", row(self.runs_dir, pick))
        f.addRow("Sample rate (Hz)", self.sr)
        f.addRow("Channels", self.channels)
        f.addRow("Operator name", self.operator)
        f.addRow("Default location", self.location)
        f.addRow(self.cal_on)
        f.addRow("Offset (dB)", self.cal)
        f.addRow("Mic response file", row(self.cal_file, cal_pick))
        f.addRow("OpenRouter API key", self.key)
        f.addRow("Default model", self.model)
        f.addRow(self.ts_on)
        f.addRow("TSA URL", self.tsa)
        f.addRow("GPG signing key", self.gpg)
        save = QPushButton("Save settings")
        save.clicked.connect(self.save)
        f.addRow(save)
        f.addRow(QLabel(f"Config file: {config.CONFIG_FILE}"))
        w = QWidget()
        w.setLayout(f)
        w.setMaximumWidth(800)
        QVBoxLayout(self).addWidget(w)

    def save(self):
        c = self.win.cfg
        c.update({
            "runs_dir": self.runs_dir.text(), "sample_rate": int(self.sr.currentText()), "channels": self.channels.value(),
            "operator": self.operator.text(), "location": self.location.text(),
            "calibration_offset_db": self.cal.value() if self.cal_on.isChecked() else None,
            "calibration_file": self.cal_file.text(), "openrouter_api_key": self.key.text(),
            "openrouter_model": self.model.text(), "rfc3161_enabled": self.ts_on.isChecked(),
            "rfc3161_tsa_url": self.tsa.text(), "gpg_sign_key": self.gpg.text(),
        })
        config.save(c)
        self.win.record_tab.apply_cfg()
        self.win.analysis_tab.refresh_runs()
        QMessageBox.information(self, "Settings", "Saved.")


class AboutTab(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setOpenExternalLinks(True)
        guide = resources.files("noise_recorder").joinpath("data/microphone-guide.md").read_text()
        self.setMarkdown(
            f"# {APP_NAME} {__version__}\n\nRecord a fixed-length sample of a noise nuisance from an external microphone, "
            "watch its spectrum and level live, analyse it at the frequency level, ask an audio-capable AI model "
            "what it hears, and seal every run with SHA-256 hashes and an RFC 3161 timestamp so the recording can be "
            "shown to be unaltered.\n\nSource: https://github.com/danielrosehill/Nuisance-Noise-Recorder-Desktop\n\n---\n\n"
            + guide)


# =====================================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = config.load()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1400, 860)
        self.bg = Background(self)
        self.tabs = QTabWidget()
        self.ai_tab = AITab(self)
        self.record_tab = RecordTab(self)
        self.analysis_tab = AnalysisTab(self)
        self.tabs.addTab(self.record_tab, "Record")
        self.tabs.addTab(self.analysis_tab, "Analysis")
        self.tabs.addTab(self.ai_tab, "AI evaluation")
        self.tabs.addTab(SettingsTab(self), "Settings")
        self.tabs.addTab(AboutTab(), "About && mic guide")
        self.setCentralWidget(self.tabs)

    def closeEvent(self, e):
        if self.record_tab.rec:
            self.record_tab.rec.stop()
        super().closeEvent(e)


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("nuisance-noise-recorder")
    w = MainWindow()
    w.show()
    return app.exec()
