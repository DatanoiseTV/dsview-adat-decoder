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
                     A_RESERVED, A_CH0, A_MIDIBYTE, A_MIDIEVENT)
import adat_signal as sig

ANN, PY = srd.OUTPUT_ANN, srd.OUTPUT_PYTHON


def options(**kw):
    # Defaults come from the decoder's own option table, i.e. what DSView
    # uses, so a changed default cannot go unnoticed.
    o = {opt['id']: opt['default'] for opt in Decoder.options}
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

    def test_declared_defaults(self):
        self.assertEqual(options(), {
            'rate': 'auto', 'user_order': 'first sent = bit 3',
            'format': 'hex+signed', 'midi_uart': 'off', 'bits': 'no'})
        for opt in Decoder.options:
            self.assertIn(opt['default'], opt['values'], opt['id'])

    def test_flags_are_the_bits_of_the_user_value(self):
        # The nibble is a value sent MSB first: bit 0 timecode, bit 1 MIDI,
        # bit 2 S/MUX, bit 3 reserved.
        users = [0b0001, 0b0010, 0b0100, 0b1000, 0b0000, 0b1111, 0b0110]
        got = self.flags(self.run_user(users))
        names = ('timecode', 'midi', 'smux', 'reserved')
        want = [dict(zip(names, [u & 1, (u >> 1) & 1, (u >> 2) & 1,
                                 (u >> 3) & 1])) for u in users[:-1]]
        self.assertEqual(got, want)

    def test_xmos_smux_header_is_smux(self):
        # lib_adat adat_tx_port.xc: the S/MUX 2 header carries user bits
        # 0100 (second bit sent), no S/MUX 0000. Under the other reading the
        # 0100 would be MIDI-then-nothing: bit 2 sent first is bit 1 = MIDI.
        got = self.flags(self.run_user([0b0100] * 3))[0]
        self.assertEqual((got['smux'], got['midi'], got['timecode'],
                          got['reserved']), (1, 0, 0, 0))
        puts = self.run_user([0b0100] * 3)
        self.assertTrue(anns(puts, FRAME)[0][2][0].endswith(', S/MUX'))
        got = self.flags(self.run_user([0b0000] * 3))[0]
        self.assertEqual(sum(got.values()), 0)

    def test_header_matches_the_amaranth_word(self):
        # adat-core transmitter: header = 0b100000000001uuuu, MSB first.
        for u in (0b0000, 0b0100, 0b1010):
            word = (1 << 15) | (1 << 4) | u
            self.assertEqual(sig.frame_bits([0] * 8, u)[:16],
                             [(word >> (15 - k)) & 1 for k in range(16)])

    def test_flags_opposite_order_option(self):
        users = [0b1000, 0b0100, 0b0010, 0b0001, 0]
        got = self.flags(self.run_user(users, user_order='first sent = bit 0'))
        self.assertEqual([[g[n] for n in ('timecode', 'midi', 'smux',
                                          'reserved')] for g in got],
                         [[1, 0, 0, 0], [0, 1, 0, 0],
                          [0, 0, 1, 0], [0, 0, 0, 1]])

    def test_flag_annotations_sit_on_their_bit_cells(self):
        # 0b1010: reserved=1 and MIDI=1; sent order is bit 3, 2, 1, 0.
        levels, bt = sig.stream([([0] * 8, 0b1010)] * 3, self.SR, 48000)
        puts = decode(levels, self.SR)
        for cls, pos, text in ((A_RESERVED, 12, 'Reserved: 1 (expected 0)'),
                               (A_SMUX, 13, 'S/MUX: 0'),
                               (A_MIDI, 14, 'MIDI: 1'),
                               (A_TIMECODE, 15, 'Timecode: 0')):
            ss, es, t = anns(puts, cls)[0]
            self.assertEqual(t[0], text)
            self.assertAlmostEqual(ss, bt(pos), delta=2.5)
            self.assertAlmostEqual(es, bt(pos + 1), delta=2.5)

    def test_flag_cells_follow_the_order_option(self):
        # 'first sent = bit 0': timecode is the first of the four cells.
        levels, bt = sig.stream([([0] * 8, 0b1000)] * 3, self.SR, 48000)
        puts = decode(levels, self.SR, user_order='first sent = bit 0')
        for cls, pos, text in ((A_TIMECODE, 12, 'Timecode: 1'),
                               (A_MIDI, 13, 'MIDI: 0'),
                               (A_SMUX, 14, 'S/MUX: 0'),
                               (A_RESERVED, 15, 'Reserved: 0')):
            ss, es, t = anns(puts, cls)[0]
            self.assertEqual(t[0], text)
            self.assertAlmostEqual(ss, bt(pos), delta=2.5)

    def test_reserved_bit_set_is_called_out(self):
        puts = self.run_user([0b1000] * 3)
        self.assertEqual(anns(puts, A_RESERVED)[0][2][0],
                         'Reserved: 1 (expected 0)')

    def test_smux_shown_in_frame_and_user_summary(self):
        puts = self.run_user([0b0100] * 3)
        self.assertTrue(anns(puts, FRAME)[0][2][0].endswith(', S/MUX'))
        self.assertEqual(anns(puts, USER)[0][2][0], 'User 0x4: SMUX')
        puts = self.run_user([0b0011] * 3)
        self.assertNotIn('S/MUX', anns(puts, FRAME)[0][2][0])
        self.assertEqual(anns(puts, USER)[0][2][0], 'User 0x3: TC MIDI')
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
        # 14 idle bits: after a framing error the decoder waits for 10.
        frames = sig.midi_frames(data, 48000, 0.2, stop=0, gap_bits=14)
        levels, _ = sig.stream(frames, self.SR, 48000)
        puts = decode(levels, self.SR, midi_uart='idle high')
        errs = [t[0] for ss, es, t in anns(puts, ERROR)]
        self.assertEqual(self.midi(puts), [])
        self.assertTrue(errs)
        self.assertTrue(all(e == 'MIDI framing error' for e in errs), errs)
        # Each bad byte is one error, not one per low frame of its stop bit.
        self.assertEqual(len(errs), len(data))

    def test_capture_starting_mid_byte_waits_for_a_falling_edge(self):
        # The line is already low in the first frames. A start bit is a
        # high-to-low edge, not just a low sample.
        frames = sig.midi_frames([0x55], 48000, 0.2, gap_bits=14, lead=14)
        frames = [(s, 0) for s, _ in frames[:4]] + frames[4:]
        levels, _ = sig.stream(frames, self.SR, 48000)
        puts = decode(levels, self.SR, midi_uart='idle high')
        self.assertEqual(self.midi(puts), [(0x55, [])])
        self.assertEqual(anns(puts, ERROR), [])

    def test_framing_error_waits_for_idle_before_the_next_byte(self):
        # With bad stop bits and no idle between bytes the low stop bit looks
        # like a start bit; decoding on would invent a 0xFF. One error, then
        # silence, rather than a phantom byte.
        frames = sig.midi_frames([0x55, 0x55, 0x55], 48000, 0.2, stop=0,
                                 gap_bits=0)
        levels, _ = sig.stream(frames, self.SR, 48000)
        puts = decode(levels, self.SR, midi_uart='idle high')
        self.assertEqual(self.midi(puts), [])
        self.assertEqual([t[0] for ss, es, t in anns(puts, ERROR)],
                         ['MIDI framing error'])

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


class MidiFrameBit(unittest.TestCase):
    """The 'frame bit' scheme: one inverted 8N1 bit per frame."""
    SR = 100_000_000

    def run_fb(self, levels, fs=48000, mode='frame bit', **kw):
        levels, _ = sig.stream(sig.user_frames(levels), self.SR, fs)
        return decode(levels, self.SR, midi_uart=mode, **kw)

    def midi(self, puts):
        return [(d[1], d[2]) for ss, es, out, d in puts
                if out == PY and d[0] == 'MIDI']

    def bytes_of(self, puts):
        return [b for b, alts in self.midi(puts)]

    def test_option_is_declared_with_a_default_of_off(self):
        opt = [o for o in Decoder.options if o['id'] == 'midi_uart'][0]
        self.assertIn('frame bit', opt['values'])
        self.assertEqual(opt['default'], 'off')

    def test_generator_matches_the_hand_derived_waveforms(self):
        # 0x00: start 1, eight data 0 sent as 1, stop 0. 0xFF: start 1, eight
        # data 1 sent as 0, stop 0. 0x01: LSB first, so the first data frame
        # is 0 (a 1 inverted) and the rest are 1.
        g = sig.framebit_levels
        self.assertEqual(g([0x00], 48000, lead=0, tail=0),
                         [1, 1, 1, 1, 1, 1, 1, 1, 1, 0])
        self.assertEqual(g([0xFF], 48000, lead=0, tail=0),
                         [1, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(g([0x01], 48000, lead=0, tail=0),
                         [1, 0, 1, 1, 1, 1, 1, 1, 1, 0])
        self.assertEqual(g([0x80], 48000, lead=0, tail=0),
                         [1, 1, 1, 1, 1, 1, 1, 1, 0, 0])
        self.assertEqual(g([0x55], 48000, lead=0, tail=0),
                         [1, 0, 1, 0, 1, 0, 1, 0, 1, 0])

    def test_pacing_helper_gives_15_or_16_frames_per_byte_at_48k(self):
        gaps = sig.paced_gaps(3125, 48000)
        spacing = set(10 + g for g in gaps)
        self.assertEqual(spacing, {15, 16})
        # 3125 bytes take exactly 3125 * 15.36 frames = one second at 48 kHz
        self.assertEqual(sum(10 + g for g in gaps), 48000)
        gaps = sig.paced_gaps(3125, 44100)
        self.assertEqual(set(10 + g for g in gaps), {14, 15})
        self.assertEqual(sum(10 + g for g in gaps), 44100)

    def test_every_byte_value_decodes_exactly_at_both_rates(self):
        data = list(range(256))
        for fs in (48000, 44100):
            levels = sig.framebit_levels(data, fs, gaps=sig.paced_gaps(256, fs))
            puts = self.run_fb(levels, fs)
            self.assertEqual(self.midi(puts), [(b, []) for b in data], fs)
            self.assertEqual(anns(puts, ERROR), [], fs)

    def test_back_to_back_bytes(self):
        data = [0x90, 0x3C, 0x64, 0x80, 0x3C, 0x00, 0xFF, 0x00]
        puts = self.run_fb(sig.framebit_levels(data, 48000, gaps=0))
        self.assertEqual(self.bytes_of(puts), data)
        self.assertEqual(anns(puts, ERROR), [])

    def test_random_gaps(self):
        rng = random.Random(7)
        data = [rng.randint(0, 255) for _ in range(120)]
        gaps = [rng.randint(0, 9) for _ in data]
        puts = self.run_fb(sig.framebit_levels(data, 48000, gaps=gaps))
        self.assertEqual(self.bytes_of(puts), data)

    def test_byte_annotation_spans_ten_frames(self):
        puts = self.run_fb(sig.framebit_levels([0x90], 48000, lead=8))
        (ss, es, t), = anns(puts, A_MIDIBYTE)
        self.assertEqual(t[0], 'MIDI 0x90: Note On ch1')
        frame = self.SR / 48000.0
        self.assertAlmostEqual((es - ss) / frame, 10, delta=0.2)
        # starts at the first frame with the line high: frame 8
        self.assertAlmostEqual((ss - 40) / frame, 8, delta=0.2)

    def test_events_are_assembled_from_the_bytes(self):
        data = [0x90, 0x3C, 0x64, 0xC0, 0x05]
        puts = self.run_fb(sig.framebit_levels(data, 48000, gaps=6))
        texts = [t[0] for ss, es, t in anns(puts, A_MIDIEVENT)]
        self.assertEqual(texts, ['Note On ch1 C4 (60) vel 100',
                                 'Program Change ch1 -> 5'])

    def test_wrong_polarity_modes_do_not_read_it(self):
        levels = sig.framebit_levels([0x55, 0xAA, 0x12], 48000, gaps=6)
        for mode in ('idle high', 'idle low'):
            got = self.bytes_of(self.run_fb(levels, mode=mode))
            self.assertNotEqual(got, [0x55, 0xAA, 0x12], mode)

    def test_idle_line_alone_produces_nothing(self):
        puts = self.run_fb([0] * 200)
        self.assertEqual(self.midi(puts), [])
        self.assertEqual(anns(puts, ERROR), [])
        # a line stuck at 1 has no 0 before its start edge: no byte and no
        # framing error either (there is no start bit to be wrong about)
        puts = self.run_fb([1] * 200)
        self.assertEqual(self.midi(puts), [])
        self.assertEqual(anns(puts, ERROR), [])

    def test_bad_stop_bit_is_one_framing_error_and_decoding_recovers(self):
        # 14 idle frames after each byte: ten are needed to resync.
        levels = sig.framebit_levels([0x55, 0x66, 0x77], 48000, gaps=14,
                                     stop=[0, 1, 0])
        puts = self.run_fb(levels)
        self.assertEqual(self.bytes_of(puts), [0x55, 0x77])
        errs = [t[0] for ss, es, t in anns(puts, ERROR)]
        self.assertEqual(errs, ['MIDI framing error'])

    def test_ten_idle_frames_are_exactly_what_a_resync_needs(self):
        # Byte 1 has a bad stop bit (a 1) and is never delivered. The zeros
        # that follow count towards the ten: with 9 byte 2 is dropped, with
        # 10 it is decoded.
        for gap, want in ((9, []), (10, [0x77]), (11, [0x77])):
            levels = sig.framebit_levels([0x55, 0x77], 48000,
                                         gaps=[gap, 0], stop=[1, 0])
            self.assertEqual(self.bytes_of(self.run_fb(levels)), want, gap)

    def test_after_a_framing_error_short_gaps_give_no_phantom_bytes(self):
        # With only 4 idle frames the line is not proven idle again, so the
        # bytes after the bad one are dropped, never invented.
        levels = sig.framebit_levels([0x55, 0x66, 0x77], 48000, gaps=4,
                                     stop=[0, 1, 0])
        puts = self.run_fb(levels)
        self.assertEqual(self.bytes_of(puts), [0x55])
        self.assertEqual(len(anns(puts, ERROR)), 1)

    def test_a_one_frame_glitch_on_an_idle_line_is_0xff(self):
        # The same as on any UART: a short low pulse on an idle high line is
        # 0xFF. Documented, not a defect.
        levels = [0] * 10 + [1] + [0] * 30
        self.assertEqual(self.bytes_of(self.run_fb(levels)), [0xFF])

    def damaged(self, data, gaps, hit):
        """Wave of `data` with frame `hit` made undecodable (a bit cell is
        removed, so the frame keeps its place in time but is rejected)."""
        frames = sig.user_frames(sig.framebit_levels(data, 48000, gaps=gaps))
        bits = []
        for i, (s, u) in enumerate(frames):
            fb = sig.frame_bits(s, u)
            if i == hit:
                del fb[next(j for j in range(17, 256) if fb[j] == 0 and
                            fb[j - 1] == 0)]
            bits += fb
        levels, _ = sig.from_bits(bits, self.SR, 48000)
        return decode(levels, self.SR, midi_uart='frame bit')

    def test_damaged_frame_loses_the_byte_in_flight_and_resyncs(self):
        # Byte 1 occupies frames 6..15 (lead 6, 14 idle after), byte 2
        # 30..39; frame 34 is inside it.
        puts = self.damaged([0x55, 0x66, 0x77], 14, 34)
        self.assertEqual(self.bytes_of(puts), [0x55, 0x77])
        # the frame itself is reported by the frame decoder, once
        errs = [t[0] for ss, es, t in anns(puts, ERROR)]
        self.assertEqual([e for e in errs if e.startswith('MIDI')],
                         ['MIDI byte interrupted by a damaged frame'])
        self.assertEqual(len([e for e in errs if e.startswith('Frame')]), 1)

    def test_damaged_frame_leaves_no_phantom_byte_from_the_remains(self):
        # 0x66 has data frames 1,0,0,1,1,0,0,1 inverted: after the loss the
        # rest of the byte holds 0 -> 1 edges. With short gaps the line is
        # never proven idle again; nothing may be decoded from the remains.
        puts = self.damaged([0x55, 0x66, 0x77], 4, 6 + 10 + 4 + 4)
        got = self.bytes_of(puts)
        self.assertEqual(got[:1], [0x55])
        for b in got:
            self.assertIn(b, [0x55, 0x66, 0x77])
        self.assertNotIn(0x66, got)

    def test_capture_starting_inside_a_byte_never_invents_a_wrong_byte(self):
        # The state before the first frame is unknown. Starting at any frame
        # of a stream of known bytes, everything delivered must be a byte
        # that was sent, in order, once the stream has been seen to go idle.
        data = [0x12, 0x34, 0x56, 0x78]
        full = sig.framebit_levels(data, 48000, gaps=14)
        for cut in range(0, 40):
            puts = self.run_fb(full[cut:])
            got = self.bytes_of(puts)
            self.assertEqual(got[-2:], data[-2:], cut)

    def test_44100_hz_stream_decodes(self):
        data = [0x90, 0x3C, 0x64, 0xE0, 0x00, 0x40]
        puts = self.run_fb(sig.framebit_levels(data, 44100, gaps=5), 44100)
        self.assertEqual(self.bytes_of(puts), data)

    def test_python_output_has_no_alternatives(self):
        puts = self.run_fb(sig.framebit_levels([0x42], 48000, gaps=6))
        self.assertEqual(self.midi(puts), [(0x42, [])])

    def test_off_means_off(self):
        puts = self.run_fb(sig.framebit_levels([0x42], 48000), mode='off')
        self.assertEqual(self.midi(puts), [])


class MidiEvents(unittest.TestCase):
    """The message assembler, fed bytes directly so ambiguity and timing of
    the UART layer play no part."""
    BIT = 3200      # one UART bit at 100 MS/s

    def feed(self, stream):
        """stream: bytes, or (byte, ambiguous) pairs. Returns the event
        annotations as (start, end, long text) and the Python packets."""
        d = Decoder()
        d.puts = []
        d.options = options(midi_uart='idle high')
        d.metadata(srd.SRD_CONF_SAMPLERATE, 100_000_000)
        d.start()
        for i, b in enumerate(stream):
            byte, amb = b if isinstance(b, tuple) else (b, False)
            d.midi_event_feed(i * 10 * self.BIT, (i + 1) * 10 * self.BIT - 1,
                              byte, amb)
        ev = [(ss, es, t[0]) for ss, es, t in anns(d.puts, A_MIDIEVENT)]
        pk = [x[3][1] for x in d.puts if x[2] == PY and x[3][0] == 'MIDI_EVENT']
        return ev, pk

    def texts(self, stream):
        return [t for ss, es, t in self.feed(stream)[0]]

    def test_the_lane_exists(self):
        rows = {r[0]: r[2] for r in Decoder.annotation_rows}
        self.assertEqual(rows['midi_events'], (A_MIDIEVENT,))
        self.assertEqual(rows['midi'], (A_MIDIBYTE,))
        self.assertNotEqual(A_MIDIEVENT, A_MIDIBYTE)

    def test_note_on_off_with_names(self):
        self.assertEqual(self.texts([0x90, 60, 100]),
                         ['Note On ch1 C4 (60) vel 100'])
        self.assertEqual(self.texts([0x8F, 69, 0]),
                         ['Note Off ch16 A4 (69) vel 0'])
        self.assertEqual(self.texts([0x93, 61, 0]),
                         ['Note Off ch4 C#4 (61) vel 0 (note on, vel 0)'])

    def test_event_spans_the_whole_message(self):
        ev, pk = self.feed([0x90, 60, 100])
        self.assertEqual(ev, [(0, 3 * 10 * self.BIT - 1,
                               'Note On ch1 C4 (60) vel 100')])
        self.assertEqual(pk, [{'bytes': [0x90, 60, 100], 'ambiguous': False,
                               'text': 'Note On ch1 C4 (60) vel 100'}])

    def test_running_status_starts_at_the_first_data_byte(self):
        ev, _ = self.feed([0x90, 60, 100, 62, 90, 64, 80])
        self.assertEqual([t for _, _, t in ev],
                         ['Note On ch1 C4 (60) vel 100',
                          'Note On ch1 D4 (62) vel 90',
                          'Note On ch1 E4 (64) vel 80'])
        self.assertEqual([s for s, _, _ in ev],
                         [0, 3 * 10 * self.BIT, 5 * 10 * self.BIT])

    def test_control_change_names_and_unnamed(self):
        self.assertEqual(self.texts([0xB2, 7, 100]),
                         ['CC7 Volume ch3 = 100'])
        self.assertEqual(self.texts([0xB0, 64, 127]),
                         ['CC64 Sustain ch1 = 127'])
        self.assertEqual(self.texts([0xB0, 3, 5]), ['CC3 ch1 = 5'])
        self.assertEqual(self.texts([0xB0, 123, 0]),
                         ['CC123 All Notes Off ch1 = 0'])

    def test_one_data_byte_messages(self):
        self.assertEqual(self.texts([0xC5, 12]),
                         ['Program Change ch6 -> 12'])
        self.assertEqual(self.texts([0xD1, 40]),
                         ['Channel Pressure ch2 = 40'])
        self.assertEqual(self.texts([0xC0, 1, 2, 3]),
                         ['Program Change ch1 -> 1', 'Program Change ch1 -> 2',
                          'Program Change ch1 -> 3'])

    def test_pitch_bend_is_fourteen_bit_centred(self):
        self.assertEqual(self.texts([0xE0, 0x00, 0x40]), ['Pitch Bend ch1 +0'])
        self.assertEqual(self.texts([0xE0, 0x7F, 0x7F]),
                         ['Pitch Bend ch1 +8191'])
        self.assertEqual(self.texts([0xE0, 0x00, 0x00]),
                         ['Pitch Bend ch1 -8192'])
        self.assertEqual(self.texts([0xE0, 0x01, 0x41]),
                         ['Pitch Bend ch1 +129'])

    def test_poly_pressure(self):
        self.assertEqual(self.texts([0xA0, 60, 33]),
                         ['Poly Pressure ch1 C4 (60) = 33'])

    def test_realtime_bytes_pass_through_a_message(self):
        ev, _ = self.feed([0x90, 0xF8, 60, 0xF8, 100, 0xFA])
        self.assertEqual([t for _, _, t in ev],
                         ['Clock', 'Clock', 'Note On ch1 C4 (60) vel 100',
                          'Start'])
        # The note spans from its status byte to its last data byte.
        self.assertEqual((ev[2][0], ev[2][1]), (0, 5 * 10 * self.BIT - 1))

    def test_realtime_names(self):
        self.assertEqual(self.texts([0xF8, 0xFA, 0xFB, 0xFC, 0xFE, 0xFF]),
                         ['Clock', 'Start', 'Continue', 'Stop',
                          'Active Sensing', 'System Reset'])

    def test_system_common(self):
        self.assertEqual(self.texts([0xF2, 0x01, 0x02]),
                         ['Song Position 257'])
        self.assertEqual(self.texts([0xF3, 5]), ['Song Select 5'])
        self.assertEqual(self.texts([0xF6]), ['Tune Request'])
        self.assertEqual(self.texts([0xF1, 0x34]),
                         ['MTC Quarter Frame type 3 value 4'])

    def test_system_common_cancels_running_status(self):
        self.assertEqual(self.texts([0x90, 60, 100, 0xF3, 5, 60, 100]),
                         ['Note On ch1 C4 (60) vel 100', 'Song Select 5',
                          'Data 0x3C without status', 'Data 0x64 without status'])

    def test_sysex(self):
        self.assertEqual(self.texts([0xF0, 0x41, 0x10, 0x42, 0x12, 0xF7]),
                         ['SysEx Roland (4 bytes)'])
        ev, pk = self.feed([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])
        self.assertEqual(ev[0][2], 'SysEx Universal Non-Realtime (4 bytes)')
        self.assertEqual((ev[0][0], ev[0][1]), (0, 6 * 10 * self.BIT - 1))
        self.assertEqual(pk[0]['bytes'], [0xF0, 0x7E, 0x7F, 0x06])
        self.assertEqual(self.texts([0xF0, 0xF7]), ['SysEx (0 bytes)'])

    def test_sysex_cut_short_by_a_status_byte(self):
        ev = self.texts([0xF0, 0x41, 0x10, 0x90, 60, 100])
        self.assertEqual(ev, ['SysEx Roland (2 bytes) cut short',
                              'Note On ch1 C4 (60) vel 100'])

    def test_realtime_inside_sysex_does_not_end_it(self):
        self.assertEqual(self.texts([0xF0, 0x43, 0xF8, 0x10, 0xF7]),
                         ['Clock', 'SysEx Yamaha (2 bytes)'])

    def test_stray_data_and_stray_sysex_end(self):
        self.assertEqual(self.texts([0x40]), ['Data 0x40 without status'])
        self.assertEqual(self.texts([0xF7]), ['SysEx End'])

    def test_a_new_status_drops_an_unfinished_message(self):
        self.assertEqual(self.texts([0x90, 60, 0x80, 60, 0]),
                         ['Note Off ch1 C4 (60) vel 0'])

    def test_ambiguous_byte_marks_the_event(self):
        ev, pk = self.feed([0x90, (60, True), 100])
        self.assertEqual(ev[0][2], 'Note On ch1 C4 (60) vel 100 ?')
        self.assertTrue(pk[0]['ambiguous'])
        ev, _ = self.feed([0x90, 60, 100, (62, True), 90, 64, 80])
        self.assertEqual([t.endswith(' ?') for _, _, t in ev],
                         [False, True, False])

    def test_parser_reset_forgets_running_status(self):
        d = Decoder()
        d.puts = []
        d.options = options(midi_uart='idle high')
        d.metadata(srd.SRD_CONF_SAMPLERATE, 100_000_000)
        d.start()
        for i, b in enumerate([0x90, 60, 100]):
            d.midi_event_feed(i * 3200, i * 3200 + 3199, b, False)
        d.midi_parser_reset()           # what a lost byte does
        d.midi_event_feed(10000, 13199, 62, False)
        self.assertEqual([t[0] for _, _, t in anns(d.puts, A_MIDIEVENT)],
                         ['Note On ch1 C4 (60) vel 100',
                          'Data 0x3E without status'])

    def test_end_to_end_from_the_wire(self):
        # Alternating-bit bytes decode exactly at every start phase.
        msgs = ([0x95, 0x55, 0x55], 'Note On ch6 C#6 (85) vel 85'), \
               ([0xD5, 0x55], 'Channel Pressure ch6 = 85'), \
               ([0xB5, 0x55, 0x55], 'CC85 ch6 = 85')
        for data, want in msgs:
            for i in range(20):
                frames = sig.midi_frames(data, 48000, i / 20, gap_bits=14)
                levels, _ = sig.stream(frames, 100_000_000, 48000)
                puts = decode(levels, 100_000_000, midi_uart='idle high')
                got = [t[0] for ss, es, t in anns(puts, A_MIDIEVENT)]
                self.assertEqual(got, [want], (data, i))
                ev = anns(puts, A_MIDIEVENT)[0]
                byts = anns(puts, A_MIDIBYTE)
                self.assertEqual(ev[0], byts[0][0])
                self.assertEqual(ev[1], byts[-1][1])

    def test_a_lost_byte_forgets_running_status(self):
        # Fourth byte has a bad stop bit. The two data bytes after it must
        # not be taken as a continuation of the earlier Note On.
        data = [0x95, 0x55, 0x55, 0x55, 0x55, 0x55]
        frames = sig.midi_frames(data, 48000, 0.2, gap_bits=14,
                                 stop=[1, 1, 1, 0, 1, 1])
        levels, _ = sig.stream(frames, 100_000_000, 48000)
        puts = decode(levels, 100_000_000, midi_uart='idle high')
        self.assertEqual([t[0] for ss, es, t in anns(puts, A_MIDIEVENT)],
                         ['Note On ch6 C#6 (85) vel 85',
                          'Data 0x55 without status',
                          'Data 0x55 without status'])

    def test_events_off_when_midi_is_off(self):
        frames = sig.midi_frames([0x95, 0x55, 0x55], 48000, 0.2, gap_bits=14)
        levels, _ = sig.stream(frames, 100_000_000, 48000)
        puts = decode(levels, 100_000_000, midi_uart='off')
        self.assertEqual(anns(puts, A_MIDIEVENT), [])

    def test_framing_error_drops_the_message_in_progress(self):
        # Note On, then a byte with a bad stop bit, then a data byte that
        # would be running status.
        frames = sig.midi_frames([0x95, 0x55, 0x55], 48000, 0.2, gap_bits=14,
                                 stop=0)
        levels, _ = sig.stream(frames, 100_000_000, 48000)
        puts = decode(levels, 100_000_000, midi_uart='idle high')
        self.assertEqual(anns(puts, A_MIDIEVENT), [])


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
