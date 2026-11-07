"""MCP server for the NK TECHNOLOGIES DHO924S oscilloscope firmware (modded Rigol DHO800/900) over LAN SCPI."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import time
from typing import Literal

from mcp.server.fastmcp import FastMCP, Image

from .scope import Scope, ScopeError

mcp = FastMCP("nk-dho924s", instructions=(
    "Controls an NK TECHNOLOGIES DHO924S oscilloscope (modded Rigol DHO800/900, 4 channels) over LAN SCPI. "
    "Use `screenshot` to see the screen, `status` for settings, `measure` for numbers (computed from the "
    "downloaded waveform: the firmware's own :MEAS queries crash its SCPI server and are blocked). "
    "Changing settings affects the user's bench: say what you change, and restore settings you changed "
    "temporarily (status before, then set back)."))

_scope: Scope | None = None
Chan = Literal[1, 2, 3, 4]


def scope() -> Scope:
    global _scope
    if _scope is None:
        _scope = Scope()
    return _scope


def _err(e: Exception) -> str:
    return f"error: {e}"


@mcp.tool()
def status(channels: list[int] | None = None) -> str:
    """Identity plus acquisition, timebase, trigger and channel settings (default: all 4 channels)."""
    s = scope()
    try:
        out = {"idn": s.idn(), "acquisition": s.acquisition_state(), "timebase": s.timebase_state(),
               "trigger": s.trigger_state(),
               "channels": {ch: s.channel_state(ch) for ch in (channels or [1, 2, 3, 4])}}
        return json.dumps(out, indent=1)
    except ScopeError as e:
        return _err(e)


@mcp.tool()
def screenshot() -> Image:
    """Capture the scope screen as a PNG image (1024x600)."""
    return Image(data=scope().screenshot_png(), format="png")


@mcp.tool()
def set_channel(channel: Chan, display: bool | None = None, scale_v_div: float | None = None,
                offset_v: float | None = None, coupling: Literal["DC", "AC", "GND"] | None = None,
                probe: float | None = None, invert: bool | None = None) -> str:
    """Change channel settings; only the given ones are changed. Returns the new channel state."""
    s = scope()
    try:
        s.set_channel(channel, display=display, scale=scale_v_div, offset=offset_v, coupling=coupling,
                      probe=probe, invert=invert)
        return json.dumps(s.channel_state(channel))
    except ScopeError as e:
        return _err(e)


@mcp.tool()
def set_timebase(scale_s_div: float | None = None, offset_s: float | None = None) -> str:
    """Set horizontal scale (s/div) and/or trigger position offset (s). Returns the new timebase."""
    s = scope()
    try:
        if scale_s_div is not None:
            s.write(f":TIM:SCAL {scale_s_div:.6e}")
        if offset_s is not None:
            s.write(f":TIM:OFFS {offset_s:.6e}")
        return json.dumps(s.timebase_state())
    except ScopeError as e:
        return _err(e)


@mcp.tool()
def set_trigger(source: Literal["CHAN1", "CHAN2", "CHAN3", "CHAN4", "EXT", "ACL"] | None = None,
                level_v: float | None = None, slope: Literal["POS", "NEG", "RFAL"] | None = None,
                sweep: Literal["AUTO", "NORM", "SING"] | None = None) -> str:
    """Edge trigger settings; only given ones are changed. Returns the trigger state."""
    s = scope()
    try:
        s.write(":TRIG:MODE EDGE")
        if source:
            s.write(f":TRIG:EDGE:SOUR {source}")
        if level_v is not None:
            s.write(f":TRIG:EDGE:LEV {level_v:g}")
        if slope:
            s.write(f":TRIG:EDGE:SLOP {slope}")
        if sweep:
            s.write(f":TRIG:SWE {sweep}")
        return json.dumps(s.trigger_state())
    except ScopeError as e:
        return _err(e)


@mcp.tool()
def acquire(action: Literal["run", "stop", "single", "force", "autoscale", "clear"]) -> str:
    """Run/stop/single acquisition, force a trigger, autoscale (changes many settings!) or clear the display."""
    cmd = {"run": ":RUN", "stop": ":STOP", "single": ":SING", "force": ":TFOR", "autoscale": ":AUT",
           "clear": ":CLE"}[action]
    s = scope()
    try:
        s.write(cmd)
        if action == "autoscale":
            time.sleep(3)
        return json.dumps({"done": cmd, "trigger_status": s.query(":TRIG:STAT?")})
    except ScopeError as e:
        return _err(e)


@mcp.tool()
def measure(channels: list[int], frequency_hint_hz: float | None = None, stop_first: bool = True) -> str:
    """Measure channels from one downloaded acquisition: Vpp, robust Vpp, min/max/mean, RMS, frequency (FFT),
    fundamental amplitude/phase, crest factor, THD+N, clipping. stop_first freezes one acquisition so
    channels are comparable (acquisition is resumed afterwards if it was running)."""
    s = scope()
    try:
        was = s.query(":TRIG:STAT?")
        if stop_first:
            s.write(":STOP")
            time.sleep(0.1)
        res = {}
        for ch in channels:
            res[f"CHAN{ch}"] = s.waveform(f"CHAN{ch}").measure(frequency_hint_hz)
        if stop_first and was != "STOP":
            s.write(":RUN")
            time.sleep(0.3)
        phases = {k: v.get("fundamental_phase_deg") for k, v in res.items()}
        if len(channels) >= 2 and all(p is not None for p in phases.values()):
            ref = phases[f"CHAN{channels[0]}"]
            res["phase_vs_first_deg"] = {k: (p - ref + 180) % 360 - 180 for k, p in phases.items()}
        return json.dumps(res, indent=1)
    except ScopeError as e:
        return _err(e)


@mcp.tool()
def get_waveform(channel: Chan, save_csv: bool = True, max_points_inline: int = 0) -> str:
    """Download the displayed record (~1000 points) of a channel. Saves time,volts CSV to a temp file and returns its
    path plus measurements; optionally include up to max_points_inline decimated points inline."""
    s = scope()
    try:
        w = s.waveform(f"CHAN{channel}")
        out = {"measure": w.measure()}
        if save_csv:
            fd, path = tempfile.mkstemp(prefix=f"dho_ch{channel}_", suffix=".csv")
            with os.fdopen(fd, "w", newline="") as fh:
                wr = csv.writer(fh)
                wr.writerow(["time_s", "volts"])
                for t, v in zip(w.times, w.volts):
                    wr.writerow([f"{t:.9e}", f"{v:.6e}"])
            out["csv"] = path
        if max_points_inline:
            step = max(1, len(w.volts) // max_points_inline)
            out["points"] = [[float(t), float(v)] for t, v in zip(w.times[::step], w.volts[::step])]
        return json.dumps(out, indent=1)
    except ScopeError as e:
        return _err(e)


@mcp.tool()
def scpi(command: str) -> str:
    """Send a raw SCPI command. Commands ending in '?' return the answer. :MEAS:ITEM? is blocked (crashes the
    firmware's SCPI server). Unsupported queries time out after ~6 s and the connection is re-opened."""
    s = scope()
    try:
        if command.strip().endswith("?"):
            return s.query(command)
        s.write(command)
        err = s.query(":SYST:ERR?")
        return f"ok ({err})"
    except ScopeError as e:
        return _err(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
