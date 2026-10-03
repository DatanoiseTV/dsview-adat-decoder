# Using the decoder

## Wiring and capture

- Probe the digital output of an optical receiver (a TOSLINK-style receiver
  module), or the electrical equivalent. ADAT has no clock line; the decoder
  recovers the bit clock from the NRZI edges.
- Sample rate: at least 100 MS/s for a 48 kHz frame rate. The bit clock is
  256 x 48 kHz = 12.288 MHz, so 100 MS/s gives 8 samples per bit.
- Below 4 samples per bit the decoder reports an error instead of guessing.
- One probe is enough. Assign it to the `Data` channel.

## Options

| Option | Values | Default | Meaning |
|---|---|---|---|
| Frame rate (start value) | `auto`, `32 kHz`, `44.1 kHz`, `48 kHz` | `auto` | `auto` finds the sync by its absolute duration and works from 32 kHz to 48 kHz including varispeed. A fixed value only seeds the first sync search (it accepts about +-27 %); the decoder keeps tracking after that. |
| Sample format | `hex`, `signed`, `hex+signed` | `hex+signed` | How samples are written. Every sample also shows its level in dBFS. |
| User bit order | `first sent = bit 3`, `first sent = bit 0` | `first sent = bit 3` | Which end of the user nibble goes out first. See [user bits](user-bits-and-midi.md#user-bits). |
| MIDI bit decoding (experimental) | `off`, `frame bit`, `idle high`, `idle low` | `off` | How to read the MIDI user bit: `frame bit` is one inverted UART bit per frame, `idle high` / `idle low` treat it as a sampled 31250 baud UART. See [MIDI](user-bits-and-midi.md#midi-over-the-user-bit). |
| Show individual bits | `no`, `yes` | `no` | One annotation per bit, 256 per frame. Slow on long captures. |

## Annotation rows

| Row | Content |
|---|---|
| Frames | one cell per frame, with the measured frame rate, bit rate and S/MUX state |
| Sync / User | the sync pattern and the user nibble |
| User bits | the four user bits as separate cells (timecode, MIDI, S/MUX, reserved), in the order they are sent |
| Samples | eight channels, one colour per channel; value as hex, signed decimal or both, plus dBFS |
| MIDI events | assembled MIDI messages (experimental) |
| MIDI bytes | individual MIDI bytes (experimental) |
| Bits | every bit cell, with the marker bits distinguished (only with `Show individual bits`) |
| Errors | see below |

## Errors and what they mean

- A frame whose length is not 256 bit cells, whose marker bits are not `1`, or
  that contains an edge glitch produces an error annotation and no samples.
- A gap longer than 14 bit cells is reported as lost signal. The decoder
  relocks on the next sync, including at a different frame rate.
- A frame is emitted when the next sync arrives, so the last frame in a
  capture is not decoded.

## Python output for stacked decoders

A decoder stacked on top of this one receives packets of the form
`[ptype, pdata]`:

| ptype | pdata |
|---|---|
| `'SAMPLE'` | `[channel, value]`, channel 1..8, value a signed 24-bit integer |
| `'USER'` | the four user bits of the frame as an integer 0..15 |
| `'FLAGS'` | `{'timecode': 0\|1, 'midi': 0\|1, 'smux': 0\|1, 'reserved': 0\|1}` |
| `'MIDI'` | `byte, [alternatives]`; `alternatives` is non-empty if the byte was ambiguous |
| `'MIDI_EVENT'` | `{'bytes': [...], 'text': str, 'ambiguous': bool}`, one assembled message |

The authoritative definition is the docstring at the top of
[`adat/pd.py`](../adat/pd.py).

## S/MUX

At 96 and 192 kHz, ADAT spreads one stream over several channels (S/MUX). The
decoder shows the S/MUX indication from the user bit but decodes the payload
as 8 plain channels; it does not re-interleave samples.
