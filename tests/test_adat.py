import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                    # host sigrokdecode stand-in
sys.path.insert(0, os.path.dirname(HERE))   # the adat package under test

import sigrokdecode as srd
from adat.pd import (Decoder, A_SYNC as SYNC, A_USER as USER,
                     A_FRAME as FRAME, A_ERROR as ERROR, A_BIT as BIT,
                     A_MARKER as MARKER, A_TIMECODE, A_MIDI, A_SMUX,
                     A_RESERVED, A_CH0, A_MIDIBYTE)
import adat_signal as sig

ANN, PY = srd.OUTPUT_ANN, srd.OUTPUT_PYTHON


def options(**kw):
    o = {'rate': 'auto', 'format': 'hex+signed', 'bits': 'no',
         'user_order': 'first sent = bit 0', 'midi_uart': 'off'}
    o.update(kw)
    return o


def decode(levels, samplerate, **kw):
    return Decoder().run(levels, samplerate, options(**kw))


def anns(puts, idx):
    return [(ss, es, d[1]) for ss, es, out, d in puts
            if out == ANN and d[0] == idx]


def sample_anns(puts):
    """Sample annotations of all channels in time order, with channel."""
    out = []
    for c in range(8):
        out += [(ss, es, c, t) for ss, es, t in anns(puts, A_CH0 + c)]
    return sorted(out)


def decoded_frames(puts):
    """Python output regrouped into one list of eight samples per frame."""
    vals = [d[1][1] for ss, es, out, d in puts
            if out == PY and d[0] == 'SAMPLE']
    assert len(vals) % 8 == 0, len(vals)
    return [vals[i:i + 8] for i in range(0, len(vals), 8)]


def random_frames(n, seed):
    rng = random.Random(seed)
    return [([rng.randint(-2 ** 23, 2 ** 23 - 1) for _ in range(8)],
             rng.randint(0, 15)) for _ in range(n)]


def expect(frames):
    return [f[0] for f in frames]


class CleanStream(unittest.TestCase):
    def check(self, samplerate, fs, nframes=12, **kw):
        frames = random_frames(nframes, 1)
        levels, _ = sig.stream(frames, samplerate, fs)
        puts = decode(levels, samplerate, **kw)
        # The last frame is only finalised by the sync that follows it.
        self.assertEqual(decoded_frames(puts), expect(frames[:-1]))
        self.assertEqual(anns(puts, ERROR), [])
        return frames, puts

    def test_48k_100msps_auto(self):
        self.check(100_000_000, 48000)

    def test_44k1_auto(self):
        self.check(100_000_000, 44100)

    def test_32k_auto(self):
        self.check(100_000_000, 32000)

    def test_higher_sample_rates(self):
        self.check(200_000_000, 48000, 6)
        self.check(400_000_000, 44100, 4)

    def test_seeded_rate(self):
        self.check(100_000_000, 48000, rate='48 kHz')
        self.check(100_000_000, 44100, rate='44.1 kHz')

    def test_seed_tolerates_varispeed(self):
        # 44.1 kHz stream against a 48 kHz start value: 11 cells measure as
        # 12, inside the sync window; the first frame refines the estimate.
        self.check(100_000_000, 44100, rate='48 kHz')

    def test_varispeed_auto(self):
        self.check(100_000_000, 51000)
        self.check(100_000_000, 42000)

    def test_tracks_a_changing_rate(self):
        # 40 kHz to 52 kHz over 40 frames. Each frame is 0.75 % faster than
        # the last, so any estimate frozen at the start is 30 % off by the end.
        frames = random_frames(40, 12)
        bits = []
        for s, u in frames:
            bits += sig.frame_bits(s, u)
        levels, _ = sig.from_bits(bits, 100_000_000, lambda f: 40000 + 300 * f)
        for kw in ({}, {'rate': '44.1 kHz'}):
            puts = decode(levels, 100_000_000, **kw)
            self.assertEqual(decoded_frames(puts), expect(frames[:-1]), kw)
            self.assertEqual(anns(puts, ERROR), [], kw)

    def test_silence(self):
        # Every nibble is 0000: all intervals are 5 cells, sync is 11. The
        # shortest interval is not one cell, so a min-interval rate guess
        # would be off by 5.
        frames = [([0] * 8, 0)] * 6
        levels, _ = sig.stream(frames, 100_000_000, 48000)
        puts = decode(levels, 100_000_000)
        self.assertEqual(decoded_frames(puts), [[0] * 8] * 5)
        self.assertEqual(anns(puts, ERROR), [])

    def test_extremes_and_sign(self):
        vals = [0x7FFFFF, -0x800000, -1, 1, 0, 0x400000, -0x400000, 0x123456]
        frames = [(vals, 0xF), (vals[::-1], 0x0), (vals, 0x5)]
        levels, _ = sig.stream(frames, 100_000_000, 48000)
        puts = decode(levels, 100_000_000)
        self.assertEqual(decoded_frames(puts), [vals, vals[::-1]])

    def test_user_bits(self):
        frames = random_frames(5, 3)
        levels, _ = sig.stream(frames, 100_000_000, 48000)
        puts = decode(levels, 100_000_000)
        users = [d[1] for ss, es, out, d in puts
                 if out == PY and d[0] == 'USER']
        self.assertEqual(users, [f[1] for f in frames[:-1]])

    def test_leading_partial_frame(self):
        # A capture starting mid-frame: the decoder must resync on the first
        # sync it sees and never output the partial frame.
        frames = random_frames(8, 4)
        bits = []
        for s, u in frames:
            bits += sig.frame_bits(s, u)
        bits = bits[256 * 0 + 137:]
        levels, _ = sig.from_bits(bits, 100_000_000, 48000)
        puts = decode(levels, 100_000_000)
        self.assertEqual(decoded_frames(puts), expect(frames[1:-1]))

    def test_jitter(self):
        frames = random_frames(120, 5)
        for sr, jit in ((100_000_000, 1), (200_000_000, 3)):
            levels, _ = sig.stream(frames, sr, 48000, jitter=jit, seed=9)
            for kw in ({}, {'rate': '48 kHz'}):
                puts = decode(levels, sr, **kw)
                self.assertEqual(decoded_frames(puts), expect(frames[:-1]),
                                 (sr, jit, kw))

    def test_timing_of_annotations(self):
        frames = random_frames(6, 6)
        levels, bt = sig.stream(frames, 100_000_000, 48000)
        puts = decode(levels, 100_000_000)
        samples = sample_anns(puts)
        self.assertEqual(len(samples), 5 * 8)
        for f in range(5):
            base = 256 * f
            for c in range(8):
                ss, es, ch, _ = samples[f * 8 + c]
                self.assertEqual(ch, c)
                first = base + 17 + 30 * c
                self.assertAlmostEqual(ss, bt(first), delta=2.5)
                self.assertAlmostEqual(es, bt(first + 29), delta=2.5)
            fr = anns(puts, FRAME)[f]
            self.assertAlmostEqual(fr[0], bt(base), delta=2.5)
            self.assertAlmostEqual(fr[1], bt(base + 256), delta=2.5)
        sync = anns(puts, SYNC)[0]
        self.assertAlmostEqual(sync[1] - sync[0], 11 * 100e6 / (48000 * 256),
                               delta=2.5)

    def test_timing_of_every_bit(self):
        frames = random_frames(4, 6)
        levels, bt = sig.stream(frames, 100_000_000, 48000)
        puts = decode(levels, 100_000_000, bits='yes')
        bits = sorted(anns(puts, BIT) + anns(puts, MARKER))
        self.assertEqual(len(bits), 3 * 256)
        for k, (ss, es, _) in enumerate(bits):
            self.assertAlmostEqual(ss, bt(k), delta=2.5, msg='bit %d' % k)
            self.assertAlmostEqual(es, bt(k + 1), delta=2.5, msg='bit %d' % k)

    def test_measured_frame_rate(self):
        for fs in (32000, 44100, 48000, 51000):
            levels, _ = sig.stream(random_frames(5, 7), 100_000_000, fs)
            text = anns(decode(levels, 100_000_000), FRAME)[1][2][0]
            # Sync edges land on whole samples: 1 part in ~2000 of a frame.
            got = float(text.split()[2])
            self.assertAlmostEqual(got, fs / 1000.0, delta=0.03, msg=text)

    def test_bits_option(self):
        frames = random_frames(3, 8)
        levels, _ = sig.stream(frames, 100_000_000, 48000)
        puts = decode(levels, 100_000_000, bits='yes')
        self.assertEqual(len(anns(puts, BIT)), 2 * 206)
        self.assertEqual(len(anns(puts, MARKER)), 2 * 50)
        puts = decode(levels, 100_000_000)
        self.assertEqual(anns(puts, BIT) + anns(puts, MARKER), [])

    def test_format_option(self):
        levels, _ = sig.stream([([-0x800000] * 8, 0)] * 3, 100_000_000, 48000)
        t = lambda fmt: anns(decode(levels, 100_000_000, format=fmt),
                             A_CH0)[0][2]
        self.assertEqual(t('hex')[0], 'Ch1: 0x800000  0.0 dBFS')
        self.assertEqual(t('signed')[0], 'Ch1: -8388608  0.0 dBFS')
        self.assertEqual(t('hex+signed')[0],
                         'Ch1: 0x800000 (-8388608)  0.0 dBFS')

    def test_level_in_dbfs(self):
        vals = [0, 0x400000, -0x200000, 1, 0x7FFFFF, 0, 0, 0]
        levels, _ = sig.stream([(vals, 0)] * 3, 100_000_000, 48000)
        puts = decode(levels, 100_000_000)
        got = [anns(puts, A_CH0 + c)[0][2][0].split('  ')[1] for c in range(5)]
        self.assertEqual(got, ['-inf dBFS', '-6.0 dBFS', '-12.0 dBFS',
                               '-138.5 dBFS', '-0.0 dBFS'])


class UserBits(unittest.TestCase):
    SR = 100_000_000

    def run_user(self, users, **kw):
        frames = [([0] * 8, u) for u in users]
        levels, _ = sig.stream(frames, self.SR, 48000)
        return decode(levels, self.SR, **kw)

    def flags(self, puts):
        return [d[1] for ss, es, out, d in puts
                if out == PY and d[0] == 'FLAGS']

    def test_flags_default_order(self):
        # User nibble is sent MSB first: sent[0]=timecode, [1]=MIDI,
        # [2]=S/MUX, [3]=reserved under 'first sent = bit 0'.
        users = [0b1000, 0b0100, 0b0010, 0b0001, 0b0000, 0b1111, 0b0110]
        got = self.flags(self.run_user(users))
        names = ('timecode', 'midi', 'smux', 'reserved')
        want = [dict(zip(names, [(u >> 3) & 1, (u >> 2) & 1,
                                 (u >> 1) & 1, u & 1])) for u in users[:-1]]
        self.assertEqual(got, want)

    def test_flags_reversed_order_option(self):
        users = [0b1000, 0b0100, 0b0010, 0b0001, 0]
        got = self.flags(self.run_user(users, user_order='first sent = bit 3'))
        self.assertEqual([[g[n] for n in ('timecode', 'midi', 'smux',
                                          'reserved')] for g in got],
                         [[0, 0, 0, 1], [0, 0, 1, 0],
                          [0, 1, 0, 0], [1, 0, 0, 0]])

    def test_flag_annotations_sit_on_their_bit_cells(self):
        levels, bt = sig.stream([([0] * 8, 0b1010)] * 3, self.SR, 48000)
        puts = decode(levels, self.SR)
        for cls, pos, text in ((A_TIMECODE, 12, 'Timecode: 1'),
                               (A_MIDI, 13, 'MIDI: 0'),
                               (A_SMUX, 14, 'S/MUX: 1'),
                               (A_RESERVED, 15, 'Reserved: 0')):
            ss, es, t = anns(puts, cls)[0]
            self.assertEqual(t[0], text)
            self.assertAlmostEqual(ss, bt(pos), delta=2.5)
            self.assertAlmostEqual(es, bt(pos + 1), delta=2.5)

    def test_flag_cells_follow_the_order_option(self):
        # 'first sent = bit 3': timecode is the last of the four cells.
        levels, bt = sig.stream([([0] * 8, 0b1000)] * 3, self.SR, 48000)
        puts = decode(levels, self.SR, user_order='first sent = bit 3')
        for cls, pos, text in ((A_TIMECODE, 15, 'Timecode: 0'),
                               (A_MIDI, 14, 'MIDI: 0'),
                               (A_SMUX, 13, 'S/MUX: 0'),
                               (A_RESERVED, 12, 'Reserved: 1 (expected 0)')):
            ss, es, t = anns(puts, cls)[0]
            self.assertEqual(t[0], text)
            self.assertAlmostEqual(ss, bt(pos), delta=2.5)

    def test_reserved_bit_set_is_called_out(self):
        puts = self.run_user([0b0001] * 3)
        self.assertEqual(anns(puts, A_RESERVED)[0][2][0],
                         'Reserved: 1 (expected 0)')

    def test_smux_shown_in_frame_and_user_summary(self):
        puts = self.run_user([0b0010] * 3)
        self.assertTrue(anns(puts, FRAME)[0][2][0].endswith(', S/MUX'))
        self.assertEqual(anns(puts, USER)[0][2][0], 'User 0x2: SMUX')
        puts = self.run_user([0b1100] * 3)
        self.assertNotIn('S/MUX', anns(puts, FRAME)[0][2][0])
        self.assertEqual(anns(puts, USER)[0][2][0], 'User 0xC: TC MIDI')
        self.assertEqual(anns(self.run_user([0] * 3), USER)[0][2][0],
                         'User 0x0: none')

    def test_frame_text_has_bit_rate(self):
        text = anns(self.run_user([0] * 3), FRAME)[0][2][0]
        rate = float(text.split()[4])   # 'Frame 1: 48.0 kHz, 12.288 Mbit/s'
        self.assertAlmostEqual(rate, 12.288, delta=0.01, msg=text)
        self.assertTrue(text.endswith('Mbit/s'), text)

    def test_channels_get_distinct_classes(self):
        puts = self.run_user([0] * 3)
        for c in range(8):
            self.assertEqual(len(anns(puts, A_CH0 + c)), 2)


class MidiUart(unittest.TestCase):
    SR = 100_000_000

    def run_midi(self, data, fs=48000, phase=0.0, mode='idle high', **kw):
        frames = sig.midi_frames(data, fs, phase, **kw)
        levels, _ = sig.stream(frames, self.SR, fs)
        return decode(levels, self.SR, midi_uart=mode)

    def midi(self, puts):
        return [(d[1], d[2]) for ss, es, out, d in puts
                if out == PY and d[0] == 'MIDI']

    def test_off_by_default(self):
        puts = self.run_midi([0x90, 0x3C], mode='off')
        self.assertEqual(self.midi(puts), [])
        self.assertEqual(anns(puts, A_MIDIBYTE), [])

    def test_unambiguous_bytes_are_exact(self):
        # 0x55 alternates every bit, so its edges pin the sampling phase.
        for phase in (0.0, 0.3, 0.6, 0.9):
            got = self.midi(self.run_midi([0x55, 0x55, 0xAA, 0x55],
                                          phase=phase))
            self.assertEqual(got, [(0x55, []), (0x55, []), (0xAA, []),
                                   (0x55, [])], phase)

    def test_never_wrong_without_saying_so(self):
        # One sample per frame is 1.5 samples per baud, so some bytes are
        # genuinely ambiguous. The decoder must then flag it and list the
        # true byte among the candidates; unflagged results must be exact.
        rng = random.Random(5)
        exact = flagged = 0
        for i in range(20):
            data = [rng.randrange(256) for _ in range(6)]
            got = self.midi(self.run_midi(data, phase=i / 20))
            self.assertEqual(len(got), len(data), (i, data))
            for (byte, alts), want in zip(got, data):
                if alts:
                    flagged += 1
                    self.assertIn(want, [byte] + alts, (i, want, byte, alts))
                else:
                    self.assertEqual(byte, want, (i, want))
                    exact += 1
        self.assertGreaterEqual(exact, 0.9 * 120)

    def test_ambiguity_is_shown_in_the_annotation(self):
        # 0x00 and 0x80 cannot be told apart at some phases.
        seen = False
        for i in range(20):
            puts = self.run_midi([0x00, 0x80], phase=i / 20)
            for ss, es, t in anns(puts, A_MIDIBYTE):
                if '?' in t[0]:
                    seen = True
                    self.assertIn(' or 0x', t[0])
        self.assertTrue(seen)

    def test_idle_low_polarity(self):
        frames = sig.midi_frames([0x55, 0xAA], 48000, 0.2, invert=True)
        levels, _ = sig.stream(frames, self.SR, 48000)
        got = self.midi(decode(levels, self.SR, midi_uart='idle low'))
        self.assertEqual(got, [(0x55, []), (0xAA, [])][:len(got)])
        self.assertEqual(len(got), 2)
        # The wrong polarity must not produce the same bytes.
        wrong = self.midi(decode(levels, self.SR, midi_uart='idle high'))
        self.assertNotEqual(wrong, got)

    def test_bad_stop_bit_is_a_framing_error(self):
        data = [0x55, 0x55, 0x55]
        frames = sig.midi_frames(data, 48000, 0.2, stop=0)
        levels, _ = sig.stream(frames, self.SR, 48000)
        puts = decode(levels, self.SR, midi_uart='idle high')
        errs = [t[0] for ss, es, t in anns(puts, ERROR)]
        self.assertEqual(self.midi(puts), [])
        self.assertTrue(errs)
        self.assertTrue(all(e == 'MIDI framing error' for e in errs), errs)
        # Each bad byte is one error, not one per low frame of its stop bit.
        self.assertEqual(len(errs), len(data))

    def test_damaged_frame_interrupts_the_byte_in_flight(self):
        data = [0x55, 0x55, 0x55]
        # 14 idle bits between bytes: more than the 10 needed to resync.
        frames = sig.midi_frames(data, 48000, 0.2, gap_bits=14)
        bits = []
        for i, (s, u) in enumerate(frames):
            fb = sig.frame_bits(s, u)
            if i == 12:     # inside the first byte (frames 6..21)
                k = next(j for j in range(17, 256) if fb[j] == 0 and
                         fb[j - 1] == 0)
                del fb[k]
            bits += fb
        levels, _ = sig.from_bits(bits, self.SR, 48000)
        puts = decode(levels, self.SR, midi_uart='idle high')
        errs = [t[0] for ss, es, t in anns(puts, ERROR)]
        self.assertTrue(any('MIDI byte interrupted' in e for e in errs), errs)
        # The rest of the interrupted byte must not decode as a byte.
        self.assertEqual(self.midi(puts), [(0x55, []), (0x55, [])])

    def test_resync_needs_ten_idle_bits_not_a_short_high_run(self):
        # 0x0F is start, four ones, four zeros, stop. Losing a frame inside
        # the ones leaves a three-bit high run followed by a falling edge
        # that is not a start bit.
        frames = sig.midi_frames([0x0F, 0x55, 0x55], 48000, 0.2, gap_bits=14)
        bits = []
        for i, (s, u) in enumerate(frames):
            fb = sig.frame_bits(s, u)
            if i == 10:
                del fb[next(j for j in range(17, 256) if fb[j] == 0 and
                            fb[j - 1] == 0)]
            bits += fb
        levels, _ = sig.from_bits(bits, self.SR, 48000)
        puts = decode(levels, self.SR, midi_uart='idle high')
        self.assertEqual(self.midi(puts), [(0x55, []), (0x55, [])])

    def test_no_resync_without_idle_after_a_lost_frame(self):
        # Back-to-back bytes (2 idle bits) never show ten high bits, so after
        # a lost frame nothing more is decoded rather than guessed.
        frames = sig.midi_frames([0x55] * 4, 48000, 0.2, gap_bits=0)
        bits = []
        for i, (s, u) in enumerate(frames):
            fb = sig.frame_bits(s, u)
            if i == 12:
                del fb[next(j for j in range(17, 256) if fb[j] == 0 and
                            fb[j - 1] == 0)]
            bits += fb
        levels, _ = sig.from_bits(bits, self.SR, 48000)
        puts = decode(levels, self.SR, midi_uart='idle high')
        self.assertEqual(self.midi(puts), [])

    def test_primary_guess_is_mostly_right_when_ambiguous(self):
        flagged = right = 0
        for seed in (5, 6, 7, 8):
            rng = random.Random(seed)
            for fs in (48000, 44100):
                for i in range(20):
                    data = [rng.randrange(256) for _ in range(6)]
                    for (byte, alts), want in zip(
                            self.midi(self.run_midi(data, fs, i / 20)), data):
                        if alts:
                            flagged += 1
                            right += byte == want
        # Measured: 32 of 51. A coin flip between two candidates would be
        # about half; this pins the range-middle choice.
        self.assertGreaterEqual(flagged, 40)
        self.assertGreaterEqual(right / flagged, 0.55)

    def test_byte_names(self):
        puts = self.run_midi([0x90, 0xFA, 0x7F, 0xF1], phase=0.0, gap_bits=4)
        names = {t[0] for ss, es, t in anns(puts, A_MIDIBYTE)}
        text = ' | '.join(sorted(names))
        self.assertIn('Note On ch1', text)
        self.assertIn('Start', text)
        self.assertIn('System', text)

    def test_annotation_spans_ten_uart_bits(self):
        puts = self.run_midi([0x55, 0x55], phase=0.4)
        ss, es, _ = anns(puts, A_MIDIBYTE)[0]
        self.assertAlmostEqual(es - ss, 10 * self.SR / 31250.0, delta=2000)


class Faults(unittest.TestCase):
    SR = 100_000_000

    def frames(self):
        # No all-zero nibbles anywhere near the places the tests damage, so
        # a damaged frame cannot grow a run long enough to look like a sync.
        rng = random.Random(11)
        out = []
        for _ in range(6):
            s = [rng.randint(0x111111, 0x7EEEEE) | 0x111111 for _ in range(8)]
            out.append((s, 0xA))
        return out

    def run_bits(self, bits, **kw):
        levels, _ = sig.from_bits(bits, self.SR, 48000)
        return decode(levels, self.SR, **kw)

    def frame_bits(self, frames):
        bits = []
        for s, u in frames:
            bits += sig.frame_bits(s, u)
        return bits

    def test_dropped_bit_flags_frame_and_recovers(self):
        frames = self.frames()
        bits = self.frame_bits(frames)
        pos = 256 * 2 + next(i for i in range(17, 256)
                             if bits[256 * 2 + i] == 0 and
                             bits[256 * 2 + i - 1] == 0)
        del bits[pos]   # one zero fewer in a run: frame is 255 cells long
        puts = self.run_bits(bits)
        self.assertEqual(decoded_frames(puts),
                         expect([frames[0], frames[1], frames[3], frames[4]]))
        errs = anns(puts, ERROR)
        self.assertEqual(len(errs), 1)
        self.assertIn('Frame 3 error', errs[0][2][0])
        self.assertIn('255 bits', errs[0][2][0])

    def test_inserted_bit_flags_frame_and_recovers(self):
        frames = self.frames()
        bits = self.frame_bits(frames)
        pos = 256 * 2 + next(i for i in range(17, 256)
                             if bits[256 * 2 + i] == 0)
        bits.insert(pos, 0)
        puts = self.run_bits(bits)
        self.assertEqual(decoded_frames(puts),
                         expect([frames[0], frames[1], frames[3], frames[4]]))
        self.assertIn('257 bits', anns(puts, ERROR)[0][2][0])

    def test_lost_marker_edge(self):
        frames = self.frames()
        bits = self.frame_bits(frames)
        m = 256 * 2 + 21     # a marker between two nonzero nibbles
        self.assertEqual(bits[m], 1)
        bits[m] = 0          # edge missing: the two runs merge, count stays
        ones = [i for i in range(256 * 2 + 11, 256 * 3) if bits[i]]
        self.assertLess(max(b - a for a, b in zip(ones, ones[1:])), 8)
        puts = self.run_bits(bits)
        self.assertEqual(decoded_frames(puts),
                         expect([frames[0], frames[1], frames[3], frames[4]]))
        errs = anns(puts, ERROR)
        self.assertEqual(len(errs), 1)
        self.assertIn('sync bit missing at bit 21', errs[0][2][0])

    def test_missing_edge_that_mimics_a_sync(self):
        # Dropping a marker between all-zero nibbles leaves a ten-cell run,
        # which is inside the sync window. The decoder cuts the frame there,
        # reports both halves as errors, and relocks on the real sync.
        s = [0x100000] + [0x0F0F0F] * 7
        frames = [(s, 0)] * 6
        bits = self.frame_bits(frames)
        base = 256 * 2
        # user nibble 0, first data nibble 0001: marker at 11 and 16 are
        # both followed by zero runs of five; remove the marker at 16.
        self.assertEqual((bits[base + 11], bits[base + 16]), (1, 1))
        bits[base + 16] = 0
        puts = self.run_bits(bits)
        got = decoded_frames(puts)
        self.assertEqual(got[:2], [s, s])
        self.assertEqual(got[-2:], [s, s])
        self.assertGreaterEqual(len(anns(puts, ERROR)), 1)

    def test_glitch_is_flagged(self):
        frames = self.frames()
        levels, _ = sig.stream(frames, self.SR, 48000)
        # A one-sample pulse inside frame 2 (frame = 2083 samples).
        i = 2 * 2083 + 700
        while len(set(levels[i - 4:i + 5])) != 1:
            i += 1
        levels[i] ^= 1
        puts = decode(levels, self.SR)
        errs = anns(puts, ERROR)
        self.assertEqual(len(errs), 1)
        self.assertIn('edge glitch', errs[0][2][0])
        self.assertEqual(decoded_frames(puts)[:2], expect(frames[:2]))
        self.assertEqual(decoded_frames(puts)[-1:], expect(frames[4:5]))

    def test_signal_loss_and_reacquisition(self):
        a = self.frames()[:3]
        b = self.frames()[3:]
        la, _ = sig.stream(a, self.SR, 48000)
        lb, _ = sig.stream(b, self.SR, 48000)
        # 240 samples is 30 cells: just past the sync window, so any longer
        # silence has to be reported rather than mistaken for a sync.
        for gap in (240, 20000):
            levels = la + [la[-1]] * gap + lb
            for kw in ({}, {'rate': '48 kHz'}):
                puts = decode(levels, self.SR, **kw)
                self.assertEqual(decoded_frames(puts),
                                 expect([a[0], a[1], b[0], b[1]]), (gap, kw))
                errs = anns(puts, ERROR)
                self.assertEqual(len(errs), 1, (gap, kw))
                self.assertIn('Signal lost', errs[0][2][0], (gap, kw))

    def test_rate_change_across_signal_loss(self):
        # 32 kHz then 48 kHz: 11 new cells measure as 7.3 old ones, below
        # the sync window, so the estimate must be dropped on loss.
        a = self.frames()[:3]
        b = self.frames()[3:]
        la, _ = sig.stream(a, self.SR, 32000)
        lb, _ = sig.stream(b, self.SR, 48000)
        levels = la + [la[-1]] * 20000 + lb
        puts = decode(levels, self.SR)
        self.assertEqual(decoded_frames(puts),
                         expect([a[0], a[1], b[0], b[1]]))

    def test_stream_without_sync_does_not_grow_unbounded(self):
        frames = self.frames()
        bits = self.frame_bits(frames)
        for f in (1, 2):    # replace two syncs with a pattern that cannot
            bits[256 * f:256 * f + 11] = [1, 0] * 5 + [1]   # look like one
        puts = self.run_bits(bits)
        self.assertEqual(decoded_frames(puts), expect(frames[3:5]))
        errs = anns(puts, ERROR)
        self.assertEqual(len(errs), 1)
        self.assertIn('Signal lost', errs[0][2][0])

    def test_sample_rate_too_low(self):
        levels, _ = sig.stream(self.frames(), 40_000_000, 48000)
        puts = decode(levels, 40_000_000)
        errs = anns(puts, ERROR)
        self.assertTrue(errs)
        self.assertIn('Sample rate too low', errs[0][2][0])

    def test_no_samplerate(self):
        d = Decoder()
        d.puts = []
        d.options = options()
        d.start()
        with self.assertRaises(Exception) as cm:
            d.decode()
        self.assertEqual(type(cm.exception).__name__, 'SamplerateError')


if __name__ == '__main__':
    unittest.main()
