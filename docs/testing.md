# Testing

    python3 -m unittest discover -s tests

112 tests, about 5 s. The exporter tests need NumPy. The tests that read the
exporter's WAV back with `ffmpeg` and `ffprobe` are skipped when those are not
installed.

## Layout

| File | Purpose |
|---|---|
| `tests/sigrokdecode.py` | host stand-in for DSView's `sigrokdecode` module, so `adat/pd.py` can run outside DSView |
| `tests/adat_signal.py` | generates sampled waveforms and `.dsl` files, with jitter, varispeed, corruption, and MIDI at all start phases |
| `tests/test_adat.py` | the decoder: clean streams, user bits, MIDI over the user bit, MIDI events, fault handling |
| `tests/test_export.py` | the exporter: every output format, gap filling, `.dsl` and raw input, WAV read back by ffmpeg |

## What the tests cover

- Clean streams decode to the samples that generated them, at 32, 44.1 and
  48 kHz and with varispeed.
- Damaged frames, glitches, lost signal and relock at a different rate produce
  the documented errors and no samples.
- User bit order in both settings.
- MIDI byte recovery at every start phase, ambiguity flagging, and message
  assembly including running status and realtime bytes inside messages.
- Exporter output is bit-exact against the source samples.

## Mutation checking

The suite was mutation-checked: 70 deliberate breaks of the decoder and 32 of
the exporter each turn at least one test red. The `frame bit` MIDI decoder
was checked separately with 20 more breaks (inversion, bit order, stop bit,
start edge, idle run of ten frames, error handling), each turning at least one
test red; three of them survived the first version of the tests and led to the
stuck-high-line and exact-ten-frame rows.

## What the tests cannot tell you

The generator in `tests/adat_signal.py` and the decoder were written from the
same understanding of the format. A test passing means the decoder agrees with
the generator, not that either agrees with an ADAT device. Sync and rate
tracking were seen on hardware; the points still open are listed in
[Status](../README.md#status): bit order, channel order, user bit order, and
the `.dsl` layout. Tests for these will become meaningful
only once a real capture exists to compare against.

## Python 3.7

DSView embeds Python 3.7. CI parses `adat/` with
`ast.parse(source, feature_version=(3, 7))` to catch newer syntax. Running the
decoder on a real 3.7 interpreter is not part of CI.
