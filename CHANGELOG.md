# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/). Versions before 1.0 may change the
decoder's annotations or Python output.

## [0.1.0] - 2026-10-03

First public release. Tested on synthetic streams only; see the README for
what is unverified against hardware.

### Added

- ADAT Lightpipe decoder for DSView (decoder API version 3): frame sync search
  by absolute duration, 32 to 48 kHz including varispeed, relock after signal
  loss, per-frame errors.
- Annotations for frames, sync, user nibble, individual user bits, samples
  (hex, signed, dBFS), and optionally every bit.
- Python output for stacked decoders: `SAMPLE`, `USER`, `FLAGS`, `MIDI`,
  `MIDI_EVENT`.
- Experimental MIDI decoding from the user bit as a 31250 baud UART (`idle
  high`, `idle low`) and a MIDI events lane that assembles messages.
- `tools/adat_export.py`: export a `.dsl` or packed-bit capture to WAV (8
  channel or split), CSV, raw 24-bit or NumPy, with silence filling of gaps and
  a JSON report.
- Test suite with a host stand-in for DSView's `sigrokdecode` module.

### Changed

- The user nibble is read MSB first by default, matching XMOS `lib_adat` and
  `amaranth-farm/adat-core`. The earlier LSB-first reading remains available as
  the `first sent = bit 0` option.

[0.1.0]: https://github.com/DatanoiseTV/dsview-adat-decoder/releases/tag/v0.1.0
