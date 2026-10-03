# Exporting audio from a capture

DSView cannot save what a protocol decoder produces. Its only export is the
annotation table, and the binary output of decoders is not connected to
anything in the UI. `tools/adat_export.py` runs the same `adat/pd.py`,
unmodified, on a saved capture and writes the decoded audio to files.

## Requirements

Python 3 and NumPy. The exporter runs on the host, not inside DSView, so it is
not limited to Python 3.7.

## Usage

    python3 tools/adat_export.py capture.dsl --channel ADAT -o out

Save the capture from DSView as a `.dsl` file. `--channel` is the probe index
or name; it is optional if only one probe was saved. Without `-o`, the output
prefix is the input name without its extension.

A file of packed bits works too:

    python3 tools/adat_export.py capture.bin --raw --samplerate 100M -o out

One byte holds eight samples, the earliest in the least significant bit.

## Output formats

Select with `--format`, a comma-separated list, for example `--format wav,csv`.

| `--format` | Output |
|---|---|
| `wav` (default) | `out.wav`, one 8-channel 24-bit WAV (WAVE_FORMAT_EXTENSIBLE) |
| `wav-split` | `out_ch1.wav` .. `out_ch8.wav`, mono |
| `csv` | frame, sample index, time, ch1..ch8, user nibble, the four flags, `filled` |
| `raw` | `out.s24le.raw`, interleaved signed 24-bit little endian, no header |
| `npy` | `out.npy`, int32 array of shape (frames, 8) |

## Options

| Option | Meaning |
|---|---|
| `--rate` | decoder start value for the frame rate, as in the decoder (`auto`, `32 kHz`, `44.1 kHz`, `48 kHz`) |
| `--user-order` | user bit order, as in the decoder |
| `--midi` | `off`, `idle high`, `idle low`; MIDI bytes and events go into the report |
| `--wav-rate` | sample rate written to the WAV. Default: the nearest of 32000, 44100, 48000 to the measured frame rate, so an external clock that is slightly off still gives a normal WAV |
| `--no-fill` | leave gaps out instead of filling them |
| `--max-fill SECONDS` | longest gap that is filled with silence (default 1.0) |
| `--json PATH` | also write the report as JSON |

## Gaps

Frames the decoder rejects are not output. By default each gap is filled with
silence of the same duration, up to `--max-fill`, so the audio stays aligned
with capture time. In the CSV these rows have `filled` = 1. With `--no-fill`
the gaps are removed and the audio is shorter than the capture.

## Report

The exporter prints a report and, with `--json`, writes it to a file. It lists
decoder errors, user-bit counts and, with `--midi`, the MIDI bytes and events
with their times.

## Performance

Measured: 0.25 s of audio (25 M samples at 100 MS/s) takes about 1 s and
96 MB, roughly 4x slower than real time. The output matched the source
samples bit for bit in that test.

## What is not verified

The `.dsl` reader follows the layout in DSView's `StoreSession` source: a zip
file with an INI `header`, bit-packed chunks named `L-<probe>/<block>`, LSB
first, header version 2. No real capture was available; the tests write files
in that layout. Older header versions are rejected with a message.

If the reader fails on a real file, `--raw` with a packed-bit dump is the
fallback, and a sample file attached to an issue would let the reader be
fixed.
