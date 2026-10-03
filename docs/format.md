# ADAT frame format as implemented

This is what `adat/pd.py` decodes. It was checked against public descriptions
for the 256-bit total, the 11-bit sync, the 16-bit header plus 8 x 30-bit
words, and the NRZI line code. It has not been checked against a hardware
capture; see [Status](../README.md#status).

## Line code

NRZI: a `1` is a transition, a `0` is no transition. Bit rate is
256 x frame rate, so 12.288 Mbit/s at 48 kHz, 11.2896 Mbit/s at 44.1 kHz.

## Frame layout

One frame is 256 bit cells and carries one sample for each of 8 channels.

    cell   0..10    sync: a '1' followed by ten '0'
    cell   11       marker '1'
    cell   12..15   user nibble (4 bits)
    cell   16       marker '1'
    cell   17..20   data nibble 0      \
    cell   21       marker '1'          |  8 channels x 6 nibbles
    cell   22..25   data nibble 1       |  = 48 data nibbles,
    ...                                 |  each preceded by a marker
    cell   251      marker '1'          |
    cell   252..255 data nibble 47     /

In general, marker `m` (m = 0..48) sits at cell `11 + 5m`, and the nibble that
follows it occupies the next four cells. The arithmetic is
11 + 49 x 5 = 256: the sync, then 49 nibbles (one user nibble and 48 data
nibbles), each with its marker.

Data nibble `j` belongs to channel `j // 6` and is nibble `j % 6` of that
channel's 24-bit sample, most significant nibble first. Channel 1 comes first.
Samples are two's complement and are shown signed.

Because every nibble is preceded by a `1`, a run of zeros inside the payload is
at most four bits long, so the ten-zero sync cannot occur inside the data.

## Finding the sync

The decoder measures edge-to-edge intervals in sample counts. A sync appears
as an interval of about 11 bit cells (accepted between 8 and 14); payload
intervals never exceed 5. In `auto` mode the sync is found by its absolute
duration, so no frame rate has to be known. The bit cell width is then
estimated from the frame itself (one sync to the next, 256 cells) and tracked,
which is why varispeed and an external clock that is slightly off still work.

A frame is emitted when the next sync arrives. It is rejected, with an error
annotation and no samples, if:

- its length is not 256 cells,
- any marker bit is not `1`, or
- it contains an edge glitch (an interval too short to be a valid run).

A gap longer than 14 cell times is lost signal; the decoder drops its lock and
searches again, so a stream that resumes at a different frame rate is picked
up.

## What the decoder does not do

- No re-interleaving of S/MUX streams (96/192 kHz over several channels).
- No timecode decoding beyond the single bit value per frame.
- No check of sample content: ADAT has no frame CRC or parity, so a bit flip
  in the payload is not detectable and shows up as a wrong sample value.
