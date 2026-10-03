# Contributing

## Send a real capture

The decoder has been seen locking on real hardware, but the one capture so far
was silent. A capture with a known test signal is the most valuable
contribution, because it settles four open points: bit order within a sample, channel order, user bit
order, and the `.dsl` layout read by the exporter.

Open an issue using the "Hardware capture" template. Useful details:

- the device, its firmware or settings, and the sample rate it was set to
- DSLogic model and capture sample rate
- the capture itself (`.dsl`), ideally a few seconds with a known test signal
  such as a sine on channel 1 and silence elsewhere
- for user bits: what the device was doing (MIDI traffic, S/MUX mode on or off,
  timecode)

## Reporting a bug

Include the DSView version, the decoder options used, and the annotation or
error text shown. A short capture that reproduces it is better than a
description.

## Changing the code

- Decoder code in `adat/` must run on Python 3.7, the version DSView embeds.
  No walrus operator, no `f"{x=}"`, no positional-only parameters.
- Run `python3 -m unittest discover -s tests` before sending a change. A change
  in behaviour comes with a test in the same commit.
- A new test should fail when the thing it checks is broken. Break the code on
  purpose once and confirm the test goes red.
- The test generator and the decoder were written from the same reading of the
  format. When changing either, say which source the new behaviour comes from.
- Commit messages: imperative subject, about 50 characters, one concern per
  commit.

## License

Contributions are accepted under GPL version 2 or later, as in `LICENSE`.
