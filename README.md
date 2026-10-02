# ADAT decoder for DSView

Protocol decoder for ADAT Lightpipe (8 channels, 24 bit, one NRZI line) for
DSLogic analyzers running DSView. API version 3, written against the decoders
shipped in DSView (identical to the upstream `libsigrokdecode4DSL` ones).

## Install

Copy the `adat` directory into DSView's `decoders` directory, next to `i2s`
and `spdif`:

- macOS: `/Applications/DSView.app/Contents/MacOS/decoders` (the app is code
  signed; modifying the bundle may make macOS refuse to launch it, so re-sign
  or install from a build you control)
- Linux: `<prefix>/share/DSView/decoders`

Restart DSView, add the decoder "ADAT" and assign the receiver output to
`Data`.

## Use

- Probe the digital output of an optical receiver (or the electrical
  equivalent). No clock line: the bit clock is recovered from the NRZI edges.
- Sample rate: at least 100 MS/s for a 48 kHz frame rate (bit clock
  12.288 MHz, 8 samples per bit). Below 4 samples per bit the decoder reports
  an error instead of guessing.
- Options:
  - Frame rate (start value): `auto` (default) finds the sync by its absolute
    duration and works from 32 kHz to 48 kHz including varispeed. A fixed
    value only seeds the first sync search (accepts about +-27 %).
  - Sample format: hex, signed decimal, or both.
  - Show individual bits: one annotation per bit, 256 per frame. Off by
    default, it is slow on long captures.

Rows: Frames (with the measured frame rate), Sync / User, Samples (Ch1..Ch8),
Bits, Errors. Python output for stacking: `['SAMPLE', [channel 1..8, signed
value]]` and `['USER', 0..15]`.

## Frame format implemented

256 bit cells per frame at 256 x fs, NRZI (a `1` is a transition):
sync = `1` + ten `0`; then 49 nibbles, each preceded by a `1` marker bit:
the user nibble, then 8 channels x 6 nibbles (24 bit, MSB first, channel 1
first). 11 + 49 x 5 = 256.

Checked against public descriptions for the 256-bit total, the 11-bit sync,
the 16-bit header plus 8 x 30-bit words, and NRZI. Not checked against a
hardware capture: MSB-first bit order, channel order, and the order of the
four user bits come from convention, and the tests use a generator written
from the same understanding. A capture from real hardware is the missing
verification.

## Behaviour and limits

- A frame is emitted when the next sync arrives, so the last frame of a
  capture is not decoded.
- Frames whose length is not 256 cells, whose marker bits are not `1`, or
  that contain an edge glitch produce an error annotation and no samples.
- A gap longer than 14 cell times is reported as lost signal; the decoder
  relocks on the next sync, including at a different frame rate.
- S/MUX (96/192 kHz over several channels) is decoded as 8 plain channels;
  no sample re-interleaving.
- Embedded Python in DSView is 3.7; the code uses nothing newer.

## Tests

    python3 -m unittest discover -s tests

`tests/sigrokdecode.py` is a host stand-in for DSView's module and
`tests/adat_signal.py` generates sampled waveforms (with jitter, varispeed,
corruption). The suite was mutation-checked: 24 deliberate breaks of the
decoder each turn at least one test red.

GPLv2+, as the DSView decoders.
