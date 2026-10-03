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
  - User bit order: `first sent = bit 3` (default, the nibble goes out MSB
    first) or `first sent = bit 0`. See "User bits" below.
  - MIDI bit as 31250 baud UART (experimental): `off` (default),
    `idle high`, `idle low`.
  - Show individual bits: one annotation per bit, 256 per frame. Off by
    default, it is slow on long captures.

Rows: Frames (measured frame rate, bit rate, S/MUX), Sync / User, User bits
(timecode, MIDI, S/MUX, reserved, one cell each), Samples (one colour per
channel), MIDI events, MIDI bytes, Bits, Errors.

Python output for stacking: `['SAMPLE', [channel 1..8, signed value]]`,
`['USER', 0..15]`, `['FLAGS', {'timecode', 'midi', 'smux', 'reserved'}]`,
`['MIDI', byte, alternatives]` and `['MIDI_EVENT', {'bytes', 'text',
'ambiguous'}]`.

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

Each frame carries four user bits. They are the bits of a 4-bit value that is
sent MSB first, like every other nibble in the frame: bit 0 timecode
transport, bit 1 MIDI transport, bit 2 S/MUX indication, bit 3 reserved (0).
They are shown as four cells, in the order they are sent (reserved, S/MUX,
MIDI, timecode).

The order comes from source code, not from a hardware measurement:

- XMOS `lib_adat` (`adat_tx_port.xc`) transmits the S/MUX 2 header with user
  bits `0100` and no S/MUX with `0000`: the flag is the second bit sent, which
  is bit 2 of the value, the S/MUX bit in the bit numbering above.
- `amaranth-farm/adat-core` builds the header word `0b100000000001uuuu` and
  sends it MSB first, so `u[3]` leads.

The first version of this decoder assumed bit 0 first. A user report (their
MIDI line was labelled S/MUX) led to checking those two sources, which agree
with each other and contradict that guess. The old reading is still available
as `User bit order = first sent = bit 0`. Equipment from another vendor is
what would settle it. How timecode and MIDI data are spread over successive
frames is not documented in anything I found; no timecode decoding is
attempted beyond the bit value.

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
right in 32 of 51 cases. After a damaged frame or a byte with a bad stop bit no byte is output until
ten high bits have been seen, so back-to-back bytes lose the rest of the
burst. Whether real hardware uses this encoding is unchecked.

The **MIDI events** lane assembles the bytes into messages, each spanning
from its first to its last byte:

- `Note On ch1 C4 (60) vel 100`, `Note Off ...` (a Note On with velocity 0 is
  shown as Note Off), `Poly Pressure`, `Channel Pressure`
- `CC7 Volume ch1 = 100` (about 40 controller names, others as `CC3 ch1 = 5`)
- `Program Change ch6 -> 12`, `Pitch Bend ch1 +1234` (14 bit, centred on 0)
- `Clock`, `Start`, `Continue`, `Stop`, `Active Sensing`, `System Reset`
  (realtime bytes may sit inside another message and do not interrupt it)
- `SysEx Roland (4 bytes)`, with the manufacturer for a few known ids
- `Song Position`, `Song Select`, `Tune Request`, `MTC Quarter Frame`

Running status is followed; system common messages and any lost byte cancel
it. A message with an ambiguous byte ends in `?`. A data byte with no status
(a capture that starts mid-message) shows as `Data 0x40 without status`.
Note numbering is C4 = 60. The exporter lists the events with their times in
its report and in the `--json` output.

## Exporting the audio (WAV, CSV, ...)

DSView cannot save what a decoder produces (its only export is the annotation
table; binary output of decoders is not wired to anything in the UI). The
exporter runs the same `adat/pd.py` on a saved capture instead:

    python3 tools/adat_export.py capture.dsl --channel ADAT -o out

writes `out.wav`, an 8-channel 24-bit WAV (WAVE_FORMAT_EXTENSIBLE). Needs
numpy. Save the capture from DSView as a `.dsl` file; `--channel` is the probe
index or name (optional if only one probe was saved).

| `--format` | output |
|---|---|
| `wav` (default) | `out.wav`, 8 channels |
| `wav-split` | `out_ch1.wav` .. `out_ch8.wav`, mono |
| `csv` | frame, sample index, time, ch1..ch8, user nibble, the four flags, filled |
| `raw` | `out.s24le.raw`, interleaved signed 24-bit little endian |
| `npy` | `out.npy`, int32 array of shape (frames, 8) |

Combine with commas, e.g. `--format wav,csv`. Other options: `--rate`,
`--user-order` and `--midi` as in the decoder, `--wav-rate` (default: the
nearest of 32000/44100/48000 to the measured frame rate, so an external clock
that is slightly off still gives a normal WAV), `--no-fill`, `--max-fill
SECONDS`, `--json PATH` for the report. A packed-bit file works too:
`--raw --samplerate 100M` (LSB = earliest sample).

Frames the decoder rejects are not output. By default each gap is filled with
silence of the same duration (up to 1 s), so the audio stays aligned with
capture time; the CSV marks those rows `filled=1`. The report lists decoder
errors, user-bit counts and, with `--midi`, the MIDI bytes.

Speed and memory, measured: 0.25 s of audio (25 M samples at 100 MS/s) takes
about 1 s and 96 MB, roughly 4x slower than real time. The output matched the
source samples bit for bit.

Not verified against DSView itself: the `.dsl` reader follows the layout in
DSView's `StoreSession` source (zip, INI `header`, bit-packed chunks
`L-<probe>/<block>`, LSB first, header version 2). No real capture was
available, the tests write files in that layout. Older header versions are
rejected with a message.

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
`tests/adat_signal.py` generates sampled waveforms and `.dsl` files (with jitter, varispeed,
corruption, MIDI at all start phases) and `.dsl` files. The suite was
mutation-checked: 70 deliberate breaks of the decoder and 32 of the exporter
each turn at least one test red. The exporter's WAV output is read back with
ffmpeg when it is installed (those tests are skipped otherwise).

GPLv2+, as the DSView decoders.
