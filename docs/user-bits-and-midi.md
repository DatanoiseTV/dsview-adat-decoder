# User bits and MIDI

## User bits

Each frame carries four user bits. They are the bits of a 4-bit value that is
sent MSB first, like every other nibble in the frame:

| Bit | Meaning |
|---|---|
| 0 | timecode transport |
| 1 | MIDI transport |
| 2 | S/MUX indication |
| 3 | reserved (0) |

The decoder shows them as four cells in the order they are sent: reserved,
S/MUX, MIDI, timecode.

### Where the order comes from

The order comes from source code. A hardware capture decodes the nibble as
`0x2` and labels it MIDI under the default order, which fits those sources, but
what the device was sending is not recorded, so it is not a confirmation:

- XMOS `lib_adat` (`adat_tx_port.xc`) transmits the S/MUX 2 header with user
  bits `0100` and no S/MUX with `0000`. The flag is the second bit sent, which
  is bit 2 of the value in the numbering above.
- `amaranth-farm/adat-core` builds the header word `0b100000000001uuuu` and
  sends it MSB first, so `u[3]` leads.

The two sources agree with each other. The first version of this decoder
assumed bit 0 first; a user report (their MIDI line was labelled S/MUX) led to
checking them. The old reading is still available as
`User bit order = first sent = bit 0`.

Equipment from another vendor is what would settle the order. How timecode and
MIDI data are spread over successive frames is not documented in anything
found while writing this; no timecode decoding is attempted beyond the bit
value.

## MIDI over the user bit

Experimental, off by default. The ADAT encoding is not documented in a source
the author found, and whether real hardware uses either scheme below is
unchecked. Pick one with the `MIDI bit decoding` option.

### `frame bit`: one MIDI bit per frame

One bit of an inverted 8N1 UART per ADAT frame, written from the rules below
(the only documented real-world scheme for a MIDI bit in an audio stream is
RME's MIDI over MADI, where an RME administrator confirms the signal is
inverted and a user reports LSB first with pauses between the bytes; ADAT use
is a guess). The line idles at 0. A byte is ten frames:

1. start frame: 1
2. eight data frames, LSB first, each the inverse of the data bit
3. stop frame: 0

so `0x00` is `1 1 1 1 1 1 1 1 1 0` and `0xFF` is `1 0 0 0 0 0 0 0 0 0`.
Bytes are paced to average 31250 baud, so they start 15 or 16 frames apart at
48 kHz (14 or 15 at 44.1 kHz) with idle frames in between. Nothing is
ambiguous: one frame is one bit.

The decoder looks for a 0 followed by a 1 (the start frame), reads the next
nine frames and delivers the byte if the stop frame is 0. The next byte may
follow immediately. A bad stop frame is a framing error, a lost frame an
interrupted byte; after either, ten 0 frames in a row (the longest run inside a
byte is nine) must pass before a start is accepted, so the leftovers of a
broken byte are never read as a new one. A one-frame glitch on an idle line is
a valid `0xFF`, as on any UART. The Python output has no alternatives
(`'MIDI', byte, []`).

### `idle high` and `idle low`: a sampled 31250 baud UART

Assumption: the MIDI bit, read once per frame, is the line of a 31250 baud 8N1
UART (idle high, LSB first). `idle low` inverts the bit first. This is the
scheme the driver `rp1-adat` used before it gained the frame-bit scheme.

A frame (20.8 us at 48 kHz) is shorter than a UART bit (32 us) by only a factor
of 1.5, so the bytes cannot always be recovered uniquely. The decoder finds
every start-edge position consistent with the samples. When they disagree it
shows the byte as `0x3C? or 0x1E` and lists the alternatives in the Python
output.

Measured on synthetic streams (random bytes, 20 start phases each, 100 MS/s):

| Frame rate | Bytes | Exact | Flagged ambiguous (true byte listed) | Wrong, unflagged |
|---|---|---|---|---|
| 48 kHz | 120 | 116 | 4 | 0 |
| 44.1 kHz | 120 | 109 | 11 | 0 |
| 32 kHz | 120 | 2 | 118 | 0 |

At 32 kHz a frame is almost exactly one UART bit, so nearly every byte is
ambiguous. Over both 48 and 44.1 kHz the first guess of an ambiguous byte was
right in 32 of 51 cases.

After a damaged frame, or a byte with a bad stop bit, no byte is output until
ten high bits have been seen. Back-to-back bytes therefore lose the rest of the
burst.

### MIDI events lane

The `MIDI events` row assembles the bytes into messages, each
spanning from its first to its last byte:

- `Note On ch1 C4 (60) vel 100`, `Note Off ...` (a Note On with velocity 0 is
  shown as Note Off), `Poly Pressure`, `Channel Pressure`
- `CC7 Volume ch1 = 100` (about 40 controller names; others as `CC3 ch1 = 5`)
- `Program Change ch6 -> 12`, `Pitch Bend ch1 +1234` (14 bit, centred on 0)
- `Clock`, `Start`, `Continue`, `Stop`, `Active Sensing`, `System Reset`
  (realtime bytes may sit inside another message and do not interrupt it)
- `SysEx Roland (4 bytes)`, with the manufacturer name for a few known ids
- `Song Position`, `Song Select`, `Tune Request`, `MTC Quarter Frame`

Running status is followed; system common messages and any lost byte cancel
it. A message containing an ambiguous byte ends in `?`. A data byte with no
status (a capture that starts mid-message) shows as `Data 0x40 without status`.
Note numbering is C4 = 60.

The exporter lists the events with their times in its report and in the
`--json` output.
