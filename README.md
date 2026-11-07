# nk-dho924s-mcp

MCP server that lets an AI assistant (Claude Code, Claude Desktop, any MCP client) drive an oscilloscope running the
**NK TECHNOLOGIES DHO924S** firmware, a modded scope app (0.5.2, reports `NK TECHNOLOGIES,DHO924S,…,00.05.02`) for
Rigol DHO800/900-series scopes, over LAN SCPI (TCP port 5555).

It works around this firmware's SCPI quirks so the scope does not keep dropping the connection.

## Tools

| Tool | What it does |
|---|---|
| `status` | Identity, acquisition, timebase, trigger and per-channel settings |
| `screenshot` | Screen capture as a PNG image (1024x600, ~0.3 s) |
| `set_channel` | Display, V/div, offset, coupling, probe ratio, invert |
| `set_timebase` | s/div and horizontal offset |
| `set_trigger` | Edge trigger: source, level, slope, sweep (AUTO/NORM/SING) |
| `acquire` | run / stop / single / force / autoscale / clear |
| `measure` | Measurements computed from one frozen acquisition of one or more channels: Vpp, robust Vpp, min/max/mean, RMS, FFT frequency, fundamental amplitude and phase, phase between channels, crest factor, THD+N, clipping |
| `get_waveform` | Downloads the on-screen record as a time/volts CSV (path returned), optionally inline points |
| `scpi` | Raw SCPI command or query (dangerous queries blocked, see below) |

## Firmware quirks (00.05.02)

- **`:MEAS:ITEM? …` crashes the SCPI server**: the connection drops and the port refuses connections for about
  10 s. The server blocks those queries; `measure` computes everything from `:WAV:DATA?` instead.
- Many quick connections also upset it: the server keeps **one persistent socket** and paces commands.
- A channel's V/div only changes while the channel is displayed (`set_channel` handles this).
- Unsupported queries (e.g. `:CHAN1:BWL?`) never answer. After a timeout the client reconnects so replies stay in
  sync.

## Install

```sh
uv tool install git+https://github.com/bigtimer-dev/nk-dho924s-mcp
claude mcp add --scope user nk-dho924s -e DHO_HOST=192.168.1.174 -- nk-dho924s-mcp
```

Environment: `DHO_HOST` (default 192.168.1.174), `DHO_PORT` (default 5555).

Claude Desktop / other clients: run `nk-dho924s-mcp` as a stdio server with the same environment variables.

## Develop

```sh
uv venv && uv pip install -e .
.venv/bin/python -m unittest discover -s tests
```

## Safety

Changing scope settings changes the user's bench. The server's instructions ask the assistant to report what it
changes and to restore settings it changes temporarily. Nothing in this server writes to the scope's storage or
firmware.

## License

MIT
