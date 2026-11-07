"""SCPI client for the NK TECHNOLOGIES DHO924S firmware (modded Rigol DHO800/900 scope app 0.5.2).

Quirks found on this firmware (00.05.02):
- ":MEAS:ITEM? ..." makes the SCPI server drop the connection; it restarts after ~10 s. Measurements are therefore
  computed here from downloaded waveforms.
- Many quick connections also upset the server: keep one persistent socket and pace commands a little.
- A channel's V/div only changes while the channel is displayed.
- Unsupported queries (e.g. ":CHAN1:BWL?") never answer: they time out and leave the socket out of sync, so the
  client reconnects after any timeout.
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time
from dataclasses import dataclass

import numpy as np

DEFAULT_HOST = os.environ.get("DHO_HOST", "192.168.1.174")
DEFAULT_PORT = int(os.environ.get("DHO_PORT", "5555"))
PACE_S = 0.04
RECONNECT_WAIT_S = 12

# Commands that crash this firmware's SCPI server. Matched against the upper-cased command.
_DANGEROUS = [re.compile(r"^:?MEAS(URE)?:ITEM\?"), re.compile(r"^:?MEAS(URE)?:STAT(ISTIC)?:ITEM\?")]


class ScopeError(Exception):
    pass


def is_dangerous(cmd: str) -> bool:
    c = cmd.strip().upper()
    return any(p.match(c) for p in _DANGEROUS)


class Scope:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 6.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self._sock: socket.socket | None = None
        self._f = None
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- connection
    def _connect(self) -> None:
        self.close()
        last = None
        deadline = time.monotonic() + RECONNECT_WAIT_S
        while time.monotonic() < deadline:
            try:
                self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                self._f = self._sock.makefile("rb")
                return
            except OSError as e:          # server restarting: refused for a few seconds
                last = e
                time.sleep(1.0)
        raise ScopeError(f"cannot connect to {self.host}:{self.port}: {last}")

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = self._f = None

    def _ensure(self) -> None:
        if self._sock is None:
            self._connect()

    def _send(self, cmd: str) -> None:
        self._ensure()
        try:
            self._sock.sendall((cmd.strip() + "\n").encode())
        except OSError:
            self._connect()
            self._sock.sendall((cmd.strip() + "\n").encode())

    # ---------------------------------------------------------------- primitives
    def write(self, cmd: str) -> None:
        if is_dangerous(cmd):
            raise ScopeError(f"refusing {cmd!r}: it crashes this firmware's SCPI server")
        with self._lock:
            self._send(cmd)
            time.sleep(PACE_S)

    def query(self, cmd: str) -> str:
        if is_dangerous(cmd):
            raise ScopeError(f"refusing {cmd!r}: it crashes this firmware's SCPI server (use the measure tool)")
        with self._lock:
            self._send(cmd)
            try:
                line = self._f.readline()
            except socket.timeout:
                self.close()               # stream is now out of sync
                raise ScopeError(f"no answer to {cmd!r} (not supported by this firmware?)") from None
            if not line:
                self.close()
                raise ScopeError(f"scope closed the connection on {cmd!r}")
            time.sleep(PACE_S)
            return line.decode(errors="replace").strip()

    def qfloat(self, cmd: str) -> float:
        return float(self.query(cmd))

    def block(self, cmd: str) -> bytes:
        """Query returning an IEEE-488.2 definite-length block (#<n><len><data>)."""
        with self._lock:
            self._send(cmd)
            try:
                if self._f.read(1) != b"#":
                    self.close()
                    raise ScopeError(f"no data block for {cmd!r}")
                nd = int(self._f.read(1))
                n = int(self._f.read(nd))
                data = self._f.read(n)
                self._f.readline()
            except socket.timeout:
                self.close()
                raise ScopeError(f"timeout reading block for {cmd!r}") from None
            time.sleep(PACE_S)
            return data

    # ---------------------------------------------------------------- high level
    def idn(self) -> str:
        return self.query("*IDN?")

    def screenshot_png(self) -> bytes:
        return self.block(":DISP:DATA? PNG")

    def channel_state(self, ch: int) -> dict:
        q = lambda k: self.query(f":CHAN{ch}:{k}?")
        return {"display": q("DISP") == "1", "scale_v_div": float(q("SCAL")), "offset_v": float(q("OFFS")),
                "coupling": q("COUP"), "probe": float(q("PROB")), "invert": q("INV") == "1", "unit": q("UNIT")}

    def timebase_state(self) -> dict:
        return {"scale_s_div": self.qfloat(":TIM:SCAL?"), "offset_s": self.qfloat(":TIM:OFFS?"),
                "mode": self.query(":TIM:MODE?")}

    def trigger_state(self) -> dict:
        return {"mode": self.query(":TRIG:MODE?"), "sweep": self.query(":TRIG:SWE?"),
                "status": self.query(":TRIG:STAT?"), "source": self.query(":TRIG:EDGE:SOUR?"),
                "level_v": self.qfloat(":TRIG:EDGE:LEV?"), "slope": self.query(":TRIG:EDGE:SLOP?")}

    def acquisition_state(self) -> dict:
        return {"type": self.query(":ACQ:TYPE?"), "memory_depth": self.query(":ACQ:MDEP?"),
                "sample_rate": self.qfloat(":ACQ:SRAT?")}

    def set_channel(self, ch: int, *, display=None, scale=None, offset=None, coupling=None, probe=None,
                    invert=None) -> None:
        if display is not None:
            self.write(f":CHAN{ch}:DISP {int(bool(display))}")
        if probe is not None:
            self.write(f":CHAN{ch}:PROB {probe:g}")
        if scale is not None:
            shown = self.query(f":CHAN{ch}:DISP?") == "1"
            if not shown:
                self.write(f":CHAN{ch}:DISP 1")
            self.write(f":CHAN{ch}:SCAL {scale:g}")
            if not shown and not display:
                self.write(f":CHAN{ch}:DISP 0")
        if offset is not None:
            self.write(f":CHAN{ch}:OFFS {offset:g}")
        if coupling is not None:
            self.write(f":CHAN{ch}:COUP {coupling.upper()}")
        if invert is not None:
            self.write(f":CHAN{ch}:INV {int(bool(invert))}")

    def waveform(self, source: str) -> "Waveform":
        """Download the on-screen record of CHAN1..4 / MATH (NORM mode, BYTE format)."""
        self.write(f":WAV:SOUR {source}")
        self.write(":WAV:MODE NORM")
        self.write(":WAV:FORM BYTE")
        pre = [float(x) for x in self.query(":WAV:PRE?").split(",")]
        raw = np.frombuffer(self.block(":WAV:DATA?"), dtype=np.uint8)
        xinc, xorg, yinc, yorg, yref = pre[4], pre[5], pre[7], pre[8], pre[9]
        volts = (raw.astype(np.float64) - yorg - yref) * yinc
        return Waveform(source, volts, raw, xinc, xorg)


@dataclass
class Waveform:
    source: str
    volts: np.ndarray
    raw: np.ndarray
    xinc: float
    xorg: float

    @property
    def times(self) -> np.ndarray:
        return self.xorg + np.arange(len(self.volts)) * self.xinc

    def measure(self, freq_hint: float | None = None) -> dict:
        v = self.volts
        n = len(v)
        mean = float(v.mean())
        ac = v - mean
        clipped = int(np.count_nonzero((self.raw == 0) | (self.raw == 255)))
        # dominant frequency from the spectrum (Hann window, parabolic peak interpolation)
        spec = np.abs(np.fft.rfft(ac * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, self.xinc)
        k = int(np.argmax(spec[1:]) + 1) if n > 8 else 0
        f_est = None
        if 0 < k < len(spec) - 1 and spec[k] > 0:
            a, b, c = spec[k - 1], spec[k], spec[k + 1]
            d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
            df = freqs[1] - freqs[0]
            f_est = float((k + d) * df)
            # refine: maximise the Hann-windowed DFT magnitude on a fine grid around the peak
            win = ac * np.hanning(n)
            tt = np.arange(n) * self.xinc
            for span in (df, df / 20):
                grid = np.linspace(f_est - span, f_est + span, 41)
                mags = np.abs(np.exp(-2j * np.pi * np.outer(grid, tt)) @ win)
                f_est = float(grid[int(np.argmax(mags))])
        f = freq_hint or f_est
        out = {
            "source": self.source, "points": n, "x_increment_s": self.xinc, "record_s": n * self.xinc,
            "vmax": float(v.max()), "vmin": float(v.min()), "vpp": float(v.max() - v.min()),
            "vpp_robust_2pct": float(np.percentile(v, 98) - np.percentile(v, 2)),
            "vmean": mean, "vrms_total": float(np.sqrt((v ** 2).mean())), "vrms_ac": float(np.sqrt((ac ** 2).mean())),
            "clipped_samples": clipped, "frequency_hz": f_est,
        }
        if f and f > 0:
            # least-squares fit of DC + cos + sin at f over the whole record (no whole-period requirement)
            t = np.arange(n) * self.xinc
            basis = np.column_stack([np.ones(n), np.cos(2 * np.pi * f * t), np.sin(2 * np.pi * f * t)])
            coef, *_ = np.linalg.lstsq(basis, v, rcond=None)
            re, im = float(coef[1]), float(coef[2])
            fund_vpp = 2 * float(np.hypot(re, im))
            resid_rms = float(np.sqrt(((v - basis @ coef) ** 2).mean()))
            fund_rms = fund_vpp / 2 / np.sqrt(2)
            out.update({
                "analysis_frequency_hz": f, "periods_in_record": n * self.xinc * f,
                "fundamental_vpp": fund_vpp, "fundamental_phase_deg": float(np.degrees(np.arctan2(-im, re))),
                # shape hint: sine 1.414, triangle 1.732, square 1.0
                "crest_factor": float((v.max() - mean) / out["vrms_ac"]) if out["vrms_ac"] else None,
                "thd_n_pct": resid_rms / fund_rms * 100 if fund_rms else None,
            })
        return out
