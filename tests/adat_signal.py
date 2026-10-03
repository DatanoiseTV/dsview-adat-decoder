"""ADAT waveform generator for the decoder tests.

Written from the frame definition, independently of the decoder: each frame
is 256 bit cells, sync = '1' + ten '0', then 49 nibbles each led by a '1'
marker (user nibble first, then eight 24-bit samples MSB first), NRZI coded.
"""

import math
import random


def frame_bits(samples, user=0):
    """samples: eight signed 24-bit ints. Returns the 256 bit values."""
    assert len(samples) == 8
    nibbles = [user & 0xF]
    for s in samples:
        raw = s & 0xFFFFFF
        nibbles += [(raw >> shift) & 0xF for shift in (20, 16, 12, 8, 4, 0)]
    bits = [1] + [0] * 10
    for nib in nibbles:
        bits.append(1)
        bits += [(nib >> k) & 1 for k in (3, 2, 1, 0)]
    assert len(bits) == 256
    return bits


def render(edges, total, jitter=0, rng=None):
    """Sample a transition list onto the integer grid. jitter adds a uniform
    integer error of +-jitter samples to every transition on top of the
    quantisation that sampling itself imposes."""
    rng = rng or random.Random(0)
    idx = []
    last = -1
    for t in edges:
        i = int(math.ceil(t))
        if jitter:
            i += rng.randint(-jitter, jitter)
        i = max(i, last + 1)
        idx.append(i)
        last = i
    levels = [0] * total
    level = 0
    pos = 0
    for i in idx:
        if i >= total:
            break
        for k in range(pos, i):
            levels[k] = level
        level ^= 1
        pos = i
    for k in range(pos, total):
        levels[k] = level
    return levels, idx


def from_bits(bits, samplerate, fs, jitter=0, seed=0, pad=40):
    """Raw bit list to sampled levels. fs is a frame rate in Hz, or a function
    of the frame index to model varispeed. Returns (levels, bit_time) where
    bit_time(k) is the nominal start sample of bit k (k == len(bits) is the
    end of the last bit)."""
    times = []
    t = float(pad)
    for k in range(len(bits) + 1):
        times.append(t)
        f = fs(k // 256) if callable(fs) else fs
        t += samplerate / (f * 256.0)
    edges = [times[k] for k, b in enumerate(bits) if b]
    total = int(times[-1]) + 2 * pad
    levels, _ = render(edges, total, jitter, random.Random(seed))
    return levels, lambda k: times[k]


def stream(frames, samplerate, fs, jitter=0, seed=0, pad=40):
    """frames: list of (samples8, user). Returns (levels, bit_time)."""
    bits = []
    for samples, user in frames:
        bits += frame_bits(samples, user)
    return from_bits(bits, samplerate, fs, jitter, seed, pad)


def midi_frames(data, fs, phase, gap_bits=2, lead=6, seed=1, invert=False,
                stop=1):
    """Frames whose MIDI user bit carries `data` as a 31250 baud 8N1 UART,
    sampled once per frame. phase (0..1) is where the first start edge falls
    inside a frame cell. The MIDI flag is bit 1 of the user nibble value,
    which is the third bit sent (the nibble goes out MSB first)."""
    baud = 31250.0
    start = (lead + phase) / fs
    segs = []
    t = start
    for b in data:
        segs.append((t, [0] + [(b >> k) & 1 for k in range(8)] + [stop]))
        t += (10 + gap_bits) / baud

    def level(tt):
        for st, bits in segs:
            if 0 <= tt - st < 10 / baud:
                return bits[int((tt - st) * baud)]
        return 1

    rng = random.Random(seed)
    frames = []
    for i in range(int(t * fs) + 8):
        m = level(i / fs)
        if invert:
            m = 1 - m
        frames.append(([rng.randint(-2 ** 23, 2 ** 23 - 1) for _ in range(8)],
                       m << 1))
    return frames


def write_dsl(path, probes, samplerate_text, block_bytes=4096, version=2):
    """Write a DSView capture the way StoreSession does for version 2:
    a zip with an INI 'header' and chunks 'L-<probe>/<block>' of bit-packed
    samples, earliest sample in the least significant bit.
    probes: {index: (name, levels)}."""
    import zipfile
    import numpy as np
    total = max(len(lv) for _, lv in probes.values())
    nblocks = 0
    chunks = {}
    for idx, (name, levels) in probes.items():
        bits = np.zeros(total, np.uint8)
        bits[:len(levels)] = levels
        packed = np.packbits(bits, bitorder='little').tobytes()
        parts = [packed[i:i + block_bytes]
                 for i in range(0, len(packed), block_bytes)]
        nblocks = len(parts)
        for k, part in enumerate(parts):
            chunks['L-%d/%d' % (idx, k)] = part
    header = ['[version]', 'version = %d' % version, '[header]',
              'driver = DSLogic U3Pro16', 'device mode = 0',
              'capturefile = data', 'total samples = %d' % total,
              'total probes = %d' % len(probes), 'total blocks = %d' % nblocks,
              'samplerate = %s' % samplerate_text, 'trigger pos = 0']
    header += ['probe%d = %s' % (i, n) for i, (n, _) in sorted(probes.items())]
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('header', '\n'.join(header) + '\n')
        # Reverse order: readers must sort blocks numerically.
        for name, data in reversed(list(chunks.items())):
            zf.writestr(name, data)
