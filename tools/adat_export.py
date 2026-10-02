#!/usr/bin/env python3
"""Export the audio in a DSLogic capture of an ADAT line.

DSView has no way to save what a protocol decoder produces except the
annotation table, so this runs the same decoder (adat/pd.py, unmodified) on a
saved capture and writes the decoded audio to files:

  wav        one 8-channel 24-bit WAV (WAVE_FORMAT_EXTENSIBLE)
  wav-split  eight mono 24-bit WAVs
  csv        frame, sample index, time, eight samples, user bits
  raw        interleaved signed 24-bit little endian, no header
  npy        numpy int32 array of shape (frames, 8)

Input is a DSView .dsl file (--channel picks the probe) or a file of packed
bits, one byte per eight samples, earliest sample in the least significant
bit (--raw, with --samplerate).

Frames the decoder rejects are not output. By default each gap is filled with
silence of the same duration so the audio stays aligned with the capture time
(--no-fill leaves the gaps out).
"""

import argparse
import configparser
import json
import os
import re
import struct
import sys
import types
import zipfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
CHANNELS = 8
BLOCK_BITS = 1 << 24          # samples unpacked at a time
FLUSH_FRAMES = 8192
NOMINAL_RATES = (32000, 44100, 48000)


# --- running the decoder on the host -----------------------------------------

def load_decoder():
    """Import adat.pd. Outside DSView there is no sigrokdecode module, so a
    bare stand-in is registered for the import; the host behaviour itself is
    supplied by run_decoder, which works with any stand-in."""
    if 'sigrokdecode' not in sys.modules:
        srd = types.ModuleType('sigrokdecode')
        srd.OUTPUT_ANN = 0
        srd.OUTPUT_PYTHON = 1
        srd.OUTPUT_BINARY = 2
        srd.SRD_CONF_SAMPLERATE = 10000
        srd.Decoder = type('Decoder', (), {})
        sys.modules['sigrokdecode'] = srd
    sys.path.insert(0, str(REPO))
    from adat import pd
    return pd


def run_decoder(pd, edges, samplerate, options, on_put):
    """Run the decoder over an iterator of edge sample numbers."""
    class Host(pd.Decoder):
        def register(self, out_type, **kwargs):
            return out_type

        def put(self, ss, es, out, data):
            on_put(ss, es, out, data)

        def wait(self, cond):
            try:
                self.samplenum = next(edges)
            except StopIteration:
                raise EOFError
            return (0,)

    dec = Host()
    dec.options = options
    dec.metadata(10000, samplerate)
    dec.start()
    try:
        dec.decode()
    except EOFError:
        pass


# --- input -------------------------------------------------------------------

def parse_rate(text):
    m = re.fullmatch(r'\s*([0-9.]+)\s*([kMG]?)\s*(?:Hz)?\s*', text)
    if not m:
        raise ValueError('cannot parse sample rate %r' % text)
    return int(round(float(m.group(1)) *
                     {'': 1, 'k': 1e3, 'M': 1e6, 'G': 1e9}[m.group(2)]))


class Dsl:
    """A DSView .dsl capture: a zip with an INI 'header' and one bit-packed
    chunk 'L-<probe>/<block>' per logic probe and block (header version 2,
    see StoreSession::MakeChunkName in DSView)."""

    def __init__(self, path):
        self.zf = zipfile.ZipFile(path)
        names = self.zf.namelist()
        if 'header' not in names:
            raise SystemExit('%s: not a DSView capture (no header)' % path)
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_string(self.zf.read('header').decode('utf-8', 'replace'))
        hdr = cp['header']
        version = int(cp['version'].get('version', '1'))
        if version < 2:
            raise SystemExit('%s: header version %d is not supported '
                             '(only version 2, one chunk per probe)' %
                             (path, version))
        self.samplerate = parse_rate(hdr['samplerate'])
        self.total = int(hdr['total samples'])
        self.probe_names = {}
        for key, value in hdr.items():
            m = re.fullmatch(r'probe(\d+)', key)
            if m:
                self.probe_names[int(m.group(1))] = value
        self.chunks = {}
        for n in names:
            m = re.fullmatch(r'L-(\d+)/(\d+)', n)
            if m:
                self.chunks.setdefault(int(m.group(1)), {})[int(m.group(2))] = n

    def probe(self, spec):
        if spec is None:
            if len(self.chunks) == 1:
                return next(iter(self.chunks))
            raise SystemExit('--channel is required, probes in this capture: '
                             + self.describe())
        if spec.isdigit() and int(spec) in self.chunks:
            return int(spec)
        for idx, name in self.probe_names.items():
            if name == spec and idx in self.chunks:
                return idx
        raise SystemExit('no probe %r with data, available: %s' %
                         (spec, self.describe()))

    def describe(self):
        return ', '.join('%d (%s)' % (i, self.probe_names.get(i, '?'))
                         for i in sorted(self.chunks))

    def blocks(self, probe):
        c = self.chunks[probe]
        for k in sorted(c):
            yield self.zf.read(c[k])


def raw_blocks(path):
    with open(path, 'rb') as f:
        while True:
            blk = f.read(BLOCK_BITS // 8)
            if not blk:
                return
            yield blk


def edge_stream(blocks, total):
    """Sample indices at which the line changes, from packed bits."""
    pos = 0
    prev = None
    for blk in blocks:
        bits = np.unpackbits(np.frombuffer(blk, np.uint8), bitorder='little')
        if total is not None and pos + len(bits) > total:
            bits = bits[:max(total - pos, 0)]
        if len(bits) == 0:
            return
        if prev is not None and bits[0] != prev:
            yield pos
        yield from (np.flatnonzero(bits[1:] != bits[:-1]) + 1 + pos).tolist()
        prev = int(bits[-1])
        pos += len(bits)


# --- output ------------------------------------------------------------------

def pack24(samples):
    """(n, channels) int array to interleaved signed 24-bit little endian."""
    a = np.ascontiguousarray(samples, dtype='<i4')
    return a.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()


class WavWriter:
    """Streaming WAV writer; sizes are patched in when closed."""
    PCM_GUID = bytes.fromhex('0100000000001000800000aa00389b71')

    def __init__(self, path, rate, channels):
        self.f = open(path, 'wb')
        self.channels = channels
        self.frames = 0
        align = channels * 3
        fmt = struct.pack('<HHIIHHHHI', 0xFFFE, channels, rate, rate * align,
                          align, 24, 22, 24, 0) + self.PCM_GUID
        self.f.write(b'RIFF\0\0\0\0WAVE')
        self.f.write(b'fmt ' + struct.pack('<I', len(fmt)) + fmt)
        self.f.write(b'data')
        self.size_pos = self.f.tell()
        self.f.write(b'\0\0\0\0')

    def write(self, samples):
        self.f.write(pack24(samples))
        self.frames += len(samples)

    def close(self):
        size = self.frames * self.channels * 3
        if size > 0xFFFFFFFF - 100:
            raise SystemExit('audio exceeds the 4 GiB WAV limit')
        end = self.f.tell()
        self.f.seek(4)
        self.f.write(struct.pack('<I', end - 8))
        self.f.seek(self.size_pos)
        self.f.write(struct.pack('<I', size))
        self.f.close()


class Sinks:
    def __init__(self, prefix, formats, rate):
        self.prefix = prefix
        self.formats = formats
        self.rate = rate
        self.paths = []
        self.wav = self.split = self.csv = self.raw = None
        self.npy = []
        if 'wav' in formats:
            self.wav = WavWriter(self.path('.wav'), rate, CHANNELS)
        if 'wav-split' in formats:
            self.split = [WavWriter(self.path('_ch%d.wav' % (c + 1)), rate, 1)
                          for c in range(CHANNELS)]
        if 'csv' in formats:
            self.csv = open(self.path('.csv'), 'w')
            self.csv.write('frame,sample,time_s,%s,user,timecode,midi,smux,'
                           'reserved,filled\n' %
                           ','.join('ch%d' % (c + 1) for c in range(CHANNELS)))
        if 'raw' in formats:
            self.raw = open(self.path('.s24le.raw'), 'wb')

    def path(self, suffix):
        p = self.prefix + suffix
        self.paths.append(p)
        return p

    def write(self, rows, samplerate):
        """rows: list of (frame no, sample index, user, flags, samples,
        filled)."""
        samples = np.array([r[4] for r in rows], dtype=np.int32)
        if self.wav:
            self.wav.write(samples)
        if self.split:
            for c in range(CHANNELS):
                self.split[c].write(samples[:, c:c + 1])
        if self.raw:
            self.raw.write(pack24(samples))
        if 'npy' in self.formats:
            self.npy.append(samples)
        if self.csv:
            for no, idx, user, flags, smp, filled in rows:
                self.csv.write('%d,%d,%.9f,%s,%d,%d,%d,%d,%d,%d\n' % (
                    no, idx, idx / samplerate, ','.join(map(str, smp)), user,
                    flags['timecode'], flags['midi'], flags['smux'],
                    flags['reserved'], int(filled)))

    def close(self):
        if self.wav:
            self.wav.close()
        for w in self.split or []:
            w.close()
        if self.csv:
            self.csv.close()
        if self.raw:
            self.raw.close()
        if 'npy' in self.formats:
            arr = np.concatenate(self.npy) if self.npy else \
                np.zeros((0, CHANNELS), np.int32)
            np.save(self.path('.npy'), arr)


class Pipeline:
    """Turns the decoder's Python output into rows for the sinks, filling
    gaps left by rejected frames with silence."""

    def __init__(self, pd, samplerate, sinks, fill, max_fill_s):
        self.pd = pd
        self.samplerate = samplerate
        self.sinks = sinks
        self.fill = fill
        self.max_fill = max_fill_s
        self.pending = None
        self.rows = []
        self.frame_no = 0
        self.last = None
        self.period = None
        self.deltas = []
        self.filled = 0
        self.gaps_unfilled = 0
        self.errors = []
        self.error_count = 0
        self.midi = []
        self.flag_counts = {'timecode': 0, 'midi': 0, 'smux': 0, 'reserved': 0}
        self.first = None

    def on_put(self, ss, es, out, data):
        if out == 0:
            if data[0] == self.pd.A_ERROR:
                self.error_count += 1
                if len(self.errors) < 20:
                    self.errors.append((ss, data[1][0]))
            return
        if out != 1:
            return
        kind = data[0]
        if kind == 'USER':
            self.pending = {'ss': ss, 'user': data[1], 'samples': [0] * 8}
        elif kind == 'FLAGS':
            self.pending['flags'] = data[1]
        elif kind == 'SAMPLE':
            ch, value = data[1]
            self.pending['samples'][ch - 1] = value
            if ch == CHANNELS:
                self.add_frame(self.pending)
        elif kind == 'MIDI':
            self.midi.append((ss, data[1], data[2]))

    def add_frame(self, f):
        ss = f['ss']
        if self.first is None:
            self.first = ss
        if self.last is not None:
            delta = ss - self.last
            if self.period is None:
                self.period = delta
            if delta > 1.5 * self.period:
                missing = int(round(delta / self.period)) - 1
                if self.fill and missing * self.period / self.samplerate \
                        <= self.max_fill:
                    for k in range(1, missing + 1):
                        self.emit(self.last + k * delta / (missing + 1),
                                  0, dict.fromkeys(self.flag_counts, 0),
                                  [0] * 8, True)
                    self.filled += missing
                else:
                    self.gaps_unfilled += 1
            else:
                self.period = 0.9 * self.period + 0.1 * delta
                self.deltas.append(delta)
        self.last = ss
        for k in self.flag_counts:
            self.flag_counts[k] += f['flags'][k]
        self.emit(ss, f['user'], f['flags'], f['samples'], False)

    def emit(self, idx, user, flags, samples, filled):
        self.rows.append((self.frame_no, int(round(idx)), user, flags,
                          samples, filled))
        self.frame_no += 1
        if len(self.rows) >= FLUSH_FRAMES:
            self.flush()

    def flush(self):
        if self.rows:
            self.sinks.write(self.rows, self.samplerate)
            self.rows = []


def nominal_rate(measured):
    for r in NOMINAL_RATES:
        if abs(measured / r - 1) < 0.02:
            return r
    return int(round(measured))


# --- command line --------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Export the audio in a DSLogic capture of an ADAT line.')
    ap.add_argument('capture', help='DSView .dsl file, or packed bits (--raw)')
    ap.add_argument('--raw', action='store_true',
                    help='input is packed bits, LSB = earliest sample')
    ap.add_argument('--samplerate', help='sample rate for --raw, e.g. 100M')
    ap.add_argument('--channel', help='probe index or name (.dsl input)')
    ap.add_argument('-o', '--output', help='output prefix (default: input '
                    'name without extension)')
    ap.add_argument('--format', default='wav',
                    help='comma list of: wav, wav-split, csv, raw, npy '
                         '(default wav)')
    ap.add_argument('--wav-rate', type=int,
                    help='sample rate written to the WAV (default: nearest of '
                         '32000/44100/48000 to the measured frame rate)')
    ap.add_argument('--rate', default='auto',
                    choices=('auto', '32 kHz', '44.1 kHz', '48 kHz'),
                    help='decoder start value for the frame rate')
    ap.add_argument('--user-order', default='first sent = bit 0',
                    choices=('first sent = bit 0', 'first sent = bit 3'))
    ap.add_argument('--midi', default='off',
                    choices=('off', 'idle high', 'idle low'),
                    help='experimental MIDI user-bit decoding, listed in the '
                         'report')
    ap.add_argument('--no-fill', action='store_true',
                    help='leave gaps out instead of filling with silence')
    ap.add_argument('--max-fill', type=float, default=1.0, metavar='SECONDS',
                    help='longest gap filled with silence (default 1.0)')
    ap.add_argument('--json', metavar='PATH', help='also write the report')
    args = ap.parse_args(argv)

    formats = [f.strip() for f in args.format.split(',') if f.strip()]
    bad = [f for f in formats if f not in
           ('wav', 'wav-split', 'csv', 'raw', 'npy')]
    if bad or not formats:
        ap.error('unknown format %s' % (bad or 'list'))

    if args.raw:
        if not args.samplerate:
            ap.error('--raw needs --samplerate')
        samplerate = parse_rate(args.samplerate)
        total = os.path.getsize(args.capture) * 8
    else:
        dsl = Dsl(args.capture)
        samplerate = dsl.samplerate
        total = dsl.total
        dsl.probe(args.channel)   # fail early on a bad --channel
    prefix = args.output or os.path.splitext(args.capture)[0]

    pd = load_decoder()
    options = {'rate': args.rate, 'format': 'hex', 'bits': 'no',
               'user_order': args.user_order, 'midi_uart': args.midi}

    # The WAV header needs a rate before the first sample is written, so the
    # first frames are decoded once to measure it.
    wav_rate = args.wav_rate or nominal_rate_from_capture(
        pd, args, samplerate, total, options)
    sinks = Sinks(prefix, formats, wav_rate)
    pipe = Pipeline(pd, samplerate, sinks, not args.no_fill, args.max_fill)
    run_decoder(pd, edge_stream(open_blocks(args), total), samplerate,
                options, pipe.on_put)
    pipe.flush()
    sinks.close()

    measured = samplerate / float(np.median(pipe.deltas)) \
        if pipe.deltas else None
    report = {
        'capture': args.capture,
        'capture_samplerate_hz': samplerate,
        'frames_decoded': pipe.frame_no - pipe.filled,
        'frames_filled_with_silence': pipe.filled,
        'gaps_not_filled': pipe.gaps_unfilled,
        'decoder_errors': pipe.error_count,
        'first_errors': [{'sample': s, 'message': m} for s, m in pipe.errors],
        'measured_frame_rate_hz': measured,
        'wav_rate_hz': wav_rate,
        'user_bit_frames': pipe.flag_counts,
        'midi_bytes': [{'sample': s, 'byte': b, 'alternatives': a}
                       for s, b, a in pipe.midi],
        'files': sinks.paths,
    }
    print_report(report)
    if args.json:
        with open(args.json, 'w') as f:
            json.dump(report, f, indent=2)
    return 0 if report['frames_decoded'] else 1


def open_blocks(args):
    """A fresh iterator over the packed bits of the selected probe."""
    if args.raw:
        return raw_blocks(args.capture)
    dsl = Dsl(args.capture)
    return dsl.blocks(dsl.probe(args.channel))


def nominal_rate_from_capture(pd, args, samplerate, total, options):
    """Decode the start of the capture to measure the frame rate."""
    class Probe:
        times = []

        def on_put(self, ss, es, out, data):
            if out == 1 and data[0] == 'USER':
                self.times.append(ss)
                if len(self.times) >= 200:
                    raise EOFError
    probe = Probe()
    probe.times = []
    run_decoder(pd, edge_stream(open_blocks(args), total), samplerate,
                options, probe.on_put)
    if len(probe.times) < 3:
        return 48000
    return nominal_rate(samplerate / float(np.median(np.diff(probe.times))))


def print_report(r):
    print('capture          %s' % r['capture'])
    print('sample rate      %.3f MS/s' % (r['capture_samplerate_hz'] / 1e6))
    print('frames decoded   %d' % r['frames_decoded'])
    if r['measured_frame_rate_hz']:
        print('frame rate       %.1f Hz (WAV written as %d Hz)' %
              (r['measured_frame_rate_hz'], r['wav_rate_hz']))
    if r['frames_filled_with_silence'] or r['gaps_not_filled']:
        print('gaps             %d frames filled with silence, %d gaps left '
              'open' % (r['frames_filled_with_silence'], r['gaps_not_filled']))
    print('decoder errors   %d' % r['decoder_errors'])
    for e in r['first_errors'][:5]:
        print('  sample %-12d %s' % (e['sample'], e['message']))
    print('user bit frames  ' + ', '.join('%s %d' % kv for kv in
                                          r['user_bit_frames'].items()))
    if r['midi_bytes']:
        print('MIDI bytes       ' + ' '.join(
            '%02X%s' % (m['byte'], '?' if m['alternatives'] else '')
            for m in r['midi_bytes'][:40]))
    for p in r['files']:
        print('wrote            %s' % p)


if __name__ == '__main__':
    sys.exit(main())
