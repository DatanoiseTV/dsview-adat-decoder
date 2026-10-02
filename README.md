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
  - Sample format: hex, signed decimal, or both. Every sample also shows its
    level in dBFS.
  - User bit order: `first sent = bit 0` (default) or `first sent = bit 3`.
    See "User bits" below, the wire order is not documented anywhere I found.
  - MIDI bit as 31250 baud UART (experimental): `off` (default),
    `idle high`, `idle low`.
  - Show individual bits: one annotation per bit, 256 per frame. Off by
    default, it is slow on long captures.

Rows: Frames (measured frame rate, bit rate, S/MUX), Sync / User, User bits
(timecode, MIDI, S/MUX, reserved, one cell each), Samples (one colour per
channel), MIDI bytes, Bits, Errors.

Python output for stacking: `['SAMPLE', [channel 1..8, signed value]]`,
`['USER', 0..15]`, `['FLAGS', {'timecode', 'midi', 'smux', 'reserved'}]` and
`['MIDI', byte, alternatives]`.

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

## User bits

Each frame carries four user bits. Public descriptions name them: timecode
transport, MIDI transport, S/MUX indication, and a reserved bit that should be
0. They are decoded per frame and shown as four cells. What is not documented
anywhere I could find, and therefore not verified: which one is sent first
(hence the `User bit order` option; the default assumes they are sent in the
order timecode, MIDI, S/MUX, reserved), and how timecode and MIDI data are
spread over successive frames. No timecode decoding is attempted beyond the
bit value.

### MIDI over the user bit (experimental, off by default)

Assumption, not documented: the MIDI bit, read once per frame, is the line of
a 31250 baud 8N1 UART (idle high, LSB first). Because a frame (20.8 us at
48 kHz) is shorter than a UART bit (32 us) by only a factor of 1.5, the bytes
cannot always be recovered uniquely. The decoder finds every start-edge
position consistent with the samples and, when they disagree, shows the byte
as `0x3C? or 0x1E` and lists the alternatives in the Python output.

Measured on synthetic streams (random bytes, 20 start phases each, 100 MS/s):

| frame rate | bytes | exact | flagged ambiguous (true byte listed) | wrong, unflagged |
|---|---|---|---|---|
| 48 kHz | 120 | 116 | 4 | 0 |
| 44.1 kHz | 120 | 109 | 11 | 0 |
| 32 kHz | 120 | 2 | 118 | 0 |

At 32 kHz a frame is almost exactly one UART bit, so nearly every byte is
ambiguous. Over both 48 and 44.1 kHz the first guess of an ambiguous byte was
right in 32 of 51 cases. After a damaged frame no byte is output until ten
high bits have been seen, so back-to-back bytes lose the rest of the burst.
Whether real hardware uses this encoding is unchecked.

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
corruption, MIDI at all start phases). The suite was mutation-checked: 47
deliberate breaks of the decoder each turn at least one test red.

GPLv2+, as the DSView decoders.
