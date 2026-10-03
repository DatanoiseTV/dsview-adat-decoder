import contextlib
import io
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))

import adat_export as ex
import adat_signal as sig

SR = 100_000_000


def random_frames(n, seed):
    rng = random.Random(seed)
    return [([rng.randint(-2 ** 23, 2 ** 23 - 1) for _ in range(8)],
             rng.randint(0, 15)) for _ in range(n)]


class Export(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='adat_export_')
        self.addCleanup(shutil.rmtree, self.dir)

    @staticmethod
    def read(path, mode='r'):
        with open(path, mode) as f:
            return f.read()

    def p(self, name):
        return os.path.join(self.dir, name)

    def capture(self, frames, name='cap.dsl', fs=48000, extra_probe=True,
                block_bytes=4096, bits=None):
        if bits is None:
            levels, _ = sig.stream(frames, SR, fs)
        else:
            levels, _ = sig.from_bits(bits, SR, fs)
        probes = {2: ('ADAT', levels)}
        if extra_probe:
            probes[0] = ('Other', [i // 7 & 1 for i in range(len(levels))])
            probes[1] = ('Idle', [0] * len(levels))
        sig.write_dsl(self.p(name), probes, '100 MHz', block_bytes)
        return self.p(name)

    def run_export(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = ex.main(list(args))
        self.last_output = out.getvalue()
        return rc

    def expected(self, frames):
        # The last frame needs the next sync to be output.
        return np.array([f[0] for f in frames[:-1]], dtype=np.int32)

    def ffmpeg_samples(self, wav, channels):
        out = subprocess.run(
            ['ffmpeg', '-v', 'error', '-i', wav, '-f', 's32le', '-'],
            capture_output=True, check=True).stdout
        a = np.frombuffer(out, dtype='<i4').reshape(-1, channels)
        self.assertTrue(np.all(a & 0xFF == 0))   # 24 bit, left justified
        return a >> 8

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),
                         'ffmpeg not installed')
    def test_wav_read_back_by_ffmpeg(self):
        frames = random_frames(40, 1)
        cap = self.capture(frames)
        self.assertEqual(self.run_export(cap, '--channel', '2', '-o',
                                         self.p('out')), 0)
        probe = json.loads(subprocess.run(
            ['ffprobe', '-v', 'error', '-show_streams', '-of', 'json',
             self.p('out.wav')], capture_output=True, check=True).stdout)
        st = probe['streams'][0]
        self.assertEqual((st['codec_name'], st['channels'],
                          int(st['sample_rate'])),
                         ('pcm_s24le', 8, 48000))
        np.testing.assert_array_equal(
            self.ffmpeg_samples(self.p('out.wav'), 8), self.expected(frames))

    def test_wav_read_back_by_python_wave(self):
        frames = random_frames(12, 2)
        cap = self.capture(frames)
        self.run_export(cap, '--channel', 'ADAT', '-o', self.p('out'))
        with wave.open(self.p('out.wav')) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(),
                              w.getframerate(), w.getnframes()),
                             (8, 3, 48000, 11))

    def test_wav_header_is_exact(self):
        # Readers are lenient about sizes; players and editors are not.
        frames = random_frames(12, 11)
        cap = self.capture(frames)
        self.run_export(cap, '--channel', '2', '-o', self.p('out'))
        d = self.read(self.p('out.wav'), 'rb')
        n = 11
        self.assertEqual(d[0:4], b'RIFF')
        self.assertEqual(struct.unpack('<I', d[4:8])[0], len(d) - 8)
        self.assertEqual(d[8:16], b'WAVEfmt ')
        self.assertEqual(struct.unpack('<I', d[16:20])[0], 40)
        (tag, ch, rate, byte_rate, align, bits, cb, valid, mask) = \
            struct.unpack('<HHIIHHHHI', d[20:44])
        self.assertEqual((tag, ch, rate, byte_rate, align, bits, cb, valid),
                         (0xFFFE, 8, 48000, 48000 * 24, 24, 24, 22, 24))
        self.assertEqual(d[44:60], bytes.fromhex('0100000000001000800000aa00389b71'))
        self.assertEqual(d[60:64], b'data')
        self.assertEqual(struct.unpack('<I', d[64:68])[0], n * 8 * 3)
        self.assertEqual(len(d), 68 + n * 8 * 3)

    def test_user_order_option_reaches_the_decoder(self):
        frames = [([0] * 8, 0b1000)] * 5
        cap = self.capture(frames)
        flags = {}
        for order in ('first sent = bit 3', 'first sent = bit 0'):
            self.run_export(cap, '--channel', '2', '--format', 'csv',
                            '--user-order', order, '-o', self.p('u'))
            row = self.read(self.p('u.csv')).splitlines()[1].split(',')
            flags[order] = [int(x) for x in row[12:16]]
        self.assertEqual(flags['first sent = bit 3'], [0, 0, 0, 1])
        self.assertEqual(flags['first sent = bit 0'], [1, 0, 0, 0])
        # No option given: the same as the decoder's default.
        self.run_export(cap, '--channel', '2', '--format', 'csv', '-o',
                        self.p('u'))
        row = self.read(self.p('u.csv')).splitlines()[1].split(',')
        self.assertEqual([int(x) for x in row[12:16]],
                         flags['first sent = bit 3'])

    def test_split_wavs_match_channels(self):
        frames = random_frames(10, 3)
        cap = self.capture(frames)
        self.run_export(cap, '--channel', '2', '--format', 'wav-split', '-o',
                        self.p('out'))
        want = self.expected(frames)
        for c in range(8):
            with wave.open(self.p('out_ch%d.wav' % (c + 1))) as w:
                self.assertEqual((w.getnchannels(), w.getnframes()), (1, 9))
                raw = w.readframes(9)
            got = np.frombuffer(raw, np.uint8).reshape(-1, 3)
            val = (got[:, 0].astype(np.int32) | got[:, 1].astype(np.int32) << 8
                   | got[:, 2].astype(np.int32) << 16)
            val = np.where(val & 0x800000, val - (1 << 24), val)
            np.testing.assert_array_equal(val, want[:, c])

    def test_csv_raw_npy(self):
        frames = random_frames(10, 4)
        cap = self.capture(frames)
        self.run_export(cap, '--channel', '2', '--format', 'csv,raw,npy', '-o',
                        self.p('out'))
        want = self.expected(frames)
        np.testing.assert_array_equal(np.load(self.p('out.npy')), want)
        raw = np.frombuffer(self.read(self.p('out.s24le.raw'), 'rb'), np.uint8)
        self.assertEqual(len(raw), 9 * 8 * 3)
        rows = self.read(self.p('out.csv')).splitlines()
        self.assertEqual(rows[0].split(',')[:4], ['frame', 'sample', 'time_s',
                                                   'ch1'])
        self.assertEqual(len(rows), 10)
        first = rows[1].split(',')
        self.assertEqual([int(x) for x in first[3:11]], list(want[0]))
        user = int(first[11])
        self.assertEqual(user, frames[0][1])
        # flags are the bits of the user value: timecode, midi, smux, reserved
        self.assertEqual([int(x) for x in first[12:16]],
                         [user & 1, (user >> 1) & 1, (user >> 2) & 1,
                          (user >> 3) & 1])
        times = [float(r.split(',')[2]) for r in rows[1:]]
        self.assertTrue(all(b > a for a, b in zip(times, times[1:])))
        self.assertAlmostEqual(times[1] - times[0], 1 / 48000, delta=2e-7)

    def test_block_boundaries_do_not_matter(self):
        frames = random_frames(20, 5)
        outs = []
        for bb in (61, 4096, 1 << 20):
            cap = self.capture(frames, 'c%d.dsl' % bb, block_bytes=bb)
            self.run_export(cap, '--channel', '2', '--format', 'npy', '-o',
                            self.p('o%d' % bb))
            outs.append(np.load(self.p('o%d.npy' % bb)))
        np.testing.assert_array_equal(outs[0], self.expected(frames))
        np.testing.assert_array_equal(outs[0], outs[1])
        np.testing.assert_array_equal(outs[1], outs[2])

    def test_gap_is_filled_with_silence_by_default(self):
        frames = random_frames(12, 6)
        bits = []
        for i, (s, u) in enumerate(frames):
            fb = sig.frame_bits(s, u)
            if i == 5:
                del fb[next(j for j in range(17, 256) if fb[j] == 0 and
                            fb[j - 1] == 0)]
            bits += fb
        cap = self.capture(None, bits=bits)
        self.run_export(cap, '--channel', '2', '--format', 'npy,csv', '-o',
                        self.p('fill'), '--json', self.p('r.json'))
        arr = np.load(self.p('fill.npy'))
        self.assertEqual(arr.shape, (11, 8))             # 12 - last frame
        np.testing.assert_array_equal(arr[5], np.zeros(8))
        np.testing.assert_array_equal(arr[4], frames[4][0])
        np.testing.assert_array_equal(arr[6], frames[6][0])
        rep = json.loads(self.read(self.p('r.json')))
        self.assertEqual((rep['frames_decoded'], rep['decoder_errors'],
                          rep['frames_filled_with_silence']), (10, 1, 1))
        filled = [r.split(',')[-1] for r in
                  self.read(self.p('fill.csv')).splitlines()[1:]]
        self.assertEqual(filled, ['0'] * 5 + ['1'] + ['0'] * 5)
        self.run_export(cap, '--channel', '2', '--format', 'npy', '-o',
                        self.p('nofill'), '--no-fill')
        self.assertEqual(np.load(self.p('nofill.npy')).shape, (10, 8))

    def test_gap_longer_than_max_fill_is_left_open(self):
        frames = random_frames(12, 7)
        bits = []
        for i, (s, u) in enumerate(frames):
            fb = sig.frame_bits(s, u)
            if i in (5, 6, 7):      # three damaged frames in a row
                del fb[next(j for j in range(17, 256) if fb[j] == 0 and
                            fb[j - 1] == 0)]
            bits += fb
        cap = self.capture(None, bits=bits)
        self.run_export(cap, '--channel', '2', '--format', 'npy', '-o',
                        self.p('a'), '--max-fill', '0.00005')
        self.assertEqual(np.load(self.p('a.npy')).shape[0], 8)
        self.run_export(cap, '--channel', '2', '--format', 'npy', '-o',
                        self.p('b'))
        self.assertEqual(np.load(self.p('b.npy')).shape[0], 11)

    def test_raw_packed_input(self):
        frames = random_frames(10, 8)
        levels, _ = sig.stream(frames, SR, 48000)
        packed = np.packbits(np.array(levels, np.uint8), bitorder='little')
        with open(self.p('bits.bin'), 'wb') as f:
            f.write(packed.tobytes())
        self.run_export(self.p('bits.bin'), '--raw', '--samplerate', '100M',
                        '--format', 'npy', '-o', self.p('r'))
        np.testing.assert_array_equal(np.load(self.p('r.npy')),
                                      self.expected(frames))

    def test_wav_rate_follows_the_stream(self):
        self.assertEqual(ex.nominal_rate(47980.0), 48000)
        self.assertEqual(ex.nominal_rate(44123.0), 44100)
        self.assertEqual(ex.nominal_rate(32010.0), 32000)
        self.assertEqual(ex.nominal_rate(51000.0), 51000)
        frames = random_frames(10, 9)
        cap = self.capture(frames, fs=44100)
        self.run_export(cap, '--channel', '2', '-o', self.p('o'))
        with wave.open(self.p('o.wav')) as w:
            self.assertEqual(w.getframerate(), 44100)
        self.run_export(cap, '--channel', '2', '-o', self.p('p'),
                        '--wav-rate', '96000')
        with wave.open(self.p('p.wav')) as w:
            self.assertEqual(w.getframerate(), 96000)

    def test_midi_and_user_flags_are_reported(self):
        # Note On ch6 C#6 vel 85, then Channel Pressure ch6 = 85.
        data = [0x95, 0x55, 0x55, 0xD5, 0x55]
        frames = sig.midi_frames(data, 48000, 0.2, gap_bits=4)
        cap = self.capture(frames)
        self.run_export(cap, '--channel', '2', '--midi', 'idle high', '-o',
                        self.p('m'), '--json', self.p('m.json'))
        rep = json.loads(self.read(self.p('m.json')))
        self.assertEqual([m['byte'] for m in rep['midi_bytes']], data)
        self.assertEqual([e['text'] for e in rep['midi_events']],
                         ['Note On ch6 C#6 (85) vel 85',
                          'Channel Pressure ch6 = 85'])
        self.assertEqual([e['bytes'] for e in rep['midi_events']],
                         [[0x95, 0x55, 0x55], [0xD5, 0x55]])
        self.assertFalse(any(e['ambiguous'] for e in rep['midi_events']))
        t = [e['time_s'] for e in rep['midi_events']]
        # Seconds from the start of the capture: the first byte starts about
        # 6 frames in (125 us), the whole burst is a few milliseconds.
        self.assertTrue(0 < t[0] < 0.002 and t[0] < t[1] < 0.01, t)
        self.assertIn('Note On ch6 C#6 (85) vel 85', self.last_output)
        self.assertGreater(rep['user_bit_frames']['midi'], 0)
        self.assertEqual(rep['user_bit_frames']['smux'], 0)

    def test_rejections(self):
        cap = self.capture(random_frames(5, 10))
        with self.assertRaises(SystemExit) as cm:
            self.run_export(cap, '--channel', '7', '-o', self.p('x'))
        self.assertIn('no probe', str(cm.exception))
        with self.assertRaises(SystemExit) as cm:
            self.run_export(cap, '-o', self.p('x'))
        self.assertIn('--channel is required', str(cm.exception))
        old = self.p('old.dsl')
        sig.write_dsl(old, {0: ('A', [0, 1] * 100)}, '100 MHz', version=1)
        with self.assertRaises(SystemExit) as cm:
            self.run_export(old, '--channel', '0', '-o', self.p('x'))
        self.assertIn('not supported', str(cm.exception))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.run_export(self.p('bits.bin'), '--raw', '-o', self.p('x'))

    def test_no_frames_is_a_failure_exit(self):
        levels = [i // 9 & 1 for i in range(50000)]
        sig.write_dsl(self.p('n.dsl'), {0: ('A', levels)}, '100 MHz')
        self.assertEqual(self.run_export(self.p('n.dsl'), '--channel', '0',
                                         '-o', self.p('n')), 1)


if __name__ == '__main__':
    unittest.main()
