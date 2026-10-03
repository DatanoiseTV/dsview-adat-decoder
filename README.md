# dsview-adat-decoder

ADAT Lightpipe protocol decoder for [DSView](https://github.com/DreamSourceLab/DSView)
and DSLogic logic analyzers, plus a command line exporter that turns a saved
capture into WAV, CSV, raw or NumPy files.

ADAT carries 8 channels of 24-bit audio on one optical NRZI line with no
separate clock. The decoder recovers the bit clock from the edges, finds the
frame sync, and shows every frame, sample, user bit and error as annotations.
It is written against decoder API version 3 and runs on the Python 3.7 that
DSView embeds.

## Status

Tested on synthetic streams only. The test generator and the decoder share one
author's reading of the format, so these points are unverified against real
hardware:

- MSB-first bit order within a sample
- channel order (channel 1 first)
- order of the four user bits (see [user bits and MIDI](docs/user-bits-and-midi.md))
- the `.dsl` file layout read by the exporter (taken from DSView's source, not
  from a real file)

A capture from an ADAT device would settle all four. If you have one, see
[Contributing](CONTRIBUTING.md); it is the most useful thing you can send.

## What it decodes

| Lane | Content |
|---|---|
| Frames | measured frame rate, bit rate, S/MUX indication |
| Sync / User | frame sync and the user nibble |
| User bits | timecode, MIDI, S/MUX and reserved bits, one cell each |
| Samples | 8 channels, hex and/or signed decimal, level in dBFS, one colour per channel |
| MIDI events | assembled messages such as `Note On ch1 C4 (60) vel 100` (experimental) |
| MIDI bytes | bytes recovered from the MIDI user bit (experimental) |
| Bits | one annotation per bit, optional |
| Errors | bad frame length, bad marker bits, edge glitches, lost signal |

Frame rates from 32 kHz to 48 kHz are found automatically, including
varispeed. A gap longer than 14 bit cells is reported as lost signal and the
decoder relocks on the next sync, even at a different rate.

## Quick start

1. Copy the `adat` directory into DSView's `decoders` directory, next to `i2s`
   and `spdif`.
2. Restart DSView, add the decoder "ADAT" and assign the receiver output to
   `Data`.
3. Capture at 100 MS/s or more for a 48 kHz frame rate (bit clock 12.288 MHz,
   8 samples per bit).

Details, including the macOS code signing caveat, are in
[docs/install.md](docs/install.md).

To export audio from a capture saved as `.dsl`:

    python3 tools/adat_export.py capture.dsl --channel ADAT -o out

This writes `out.wav`, an 8-channel 24-bit WAV. See
[docs/export.md](docs/export.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/install.md](docs/install.md) | installing into DSView on macOS and Linux |
| [docs/usage.md](docs/usage.md) | capture settings, decoder options, annotation rows, Python output for stacked decoders |
| [docs/format.md](docs/format.md) | the frame format as implemented, with the bit layout and the sync search |
| [docs/user-bits-and-midi.md](docs/user-bits-and-midi.md) | user bit order and where it comes from, the experimental MIDI decoding and its measured accuracy |
| [docs/export.md](docs/export.md) | `tools/adat_export.py`, output formats, gap handling, performance |
| [docs/testing.md](docs/testing.md) | the test suite, the host stand-in for DSView, mutation checks, what is not covered |
| [CHANGELOG.md](CHANGELOG.md) | release history |

## Limits

- A frame is emitted when the next sync arrives, so the last frame of a
  capture is not decoded.
- S/MUX (96 and 192 kHz spread over several channels) is decoded as 8 plain
  channels; samples are not re-interleaved.
- Timecode is not decoded beyond the bit value.
- The MIDI decoding rests on an assumption about the encoding that is not
  documented anywhere I found.

## Tests

    python3 -m unittest discover -s tests

The exporter tests need NumPy. See [docs/testing.md](docs/testing.md).

## License

GPL version 2 or later, the same as the DSView decoders. See [LICENSE](LICENSE).
