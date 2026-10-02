##
## This file is part of the libsigrokdecode project.
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, see <http://www.gnu.org/licenses/>.
##

import sigrokdecode as srd

'''
OUTPUT_PYTHON format:

Packet:
[<ptype>, <pdata>]

<ptype>, <pdata>:
 - 'SAMPLE', [<channel>, <value>]    channel 1..8, value signed 24-bit int
 - 'USER', <value>                   4 user bits of the frame, 0..15
'''

# Frame layout, 256 bit cells per frame (bit rate = 256 * frame rate):
#
#   bits 0..10   sync: a '1' followed by ten '0'
#   bit  11      marker '1', bits 12..15 user nibble
#   bit  16+5j   marker '1', bits 17+5j .. 20+5j data nibble j (j = 0..47)
#
# Data nibble j belongs to channel j // 6 and is nibble j % 6 of its 24-bit
# sample (MSB first). Every nibble is preceded by a '1' marker, so a data run
# of zeros is at most four bits long and the ten-zero sync cannot occur
# inside the payload. The line code is NRZI: a '1' is a transition.
FRAME_BITS = 256
SYNC_BITS = 11
USER_BIT = 12
DATA_BIT = 17
NIBBLES = 49
MARKERS = tuple(SYNC_BITS + 5 * m for m in range(NIBBLES))
CHANNELS = 8
SAMPLE_SPAN = 29  # first data bit of a sample .. the marker after its last bit

# Edge-to-edge interval, in bit cells, that identifies the sync. The nominal
# value is 11; payload intervals never exceed 5.
SYNC_MIN = 8.0
SYNC_MAX = 14.0
# Longest payload frame is far shorter than this; more bits means lost sync.
MAX_BITS = FRAME_BITS + 2 * SYNC_BITS

# With no frame rate given, the first sync is found by absolute duration.
# 11 cells span 0.80 us at 13.8 Mbit/s (48 kHz + 12.5 % varispeed) up to
# 1.35 us at 8.19 Mbit/s (32 kHz); the longest payload interval, five cells
# at 32 kHz, is 0.61 us. The windows do not overlap.
BOOT_MIN_US = 0.70
BOOT_MAX_US = 1.60

# Fewer samples per bit cell than this cannot be rounded to cells reliably.
MIN_SAMPLES_PER_BIT = 4.0

RATES = {'auto': None, '32 kHz': 32000, '44.1 kHz': 44100, '48 kHz': 48000}

class SamplerateError(Exception):
    pass

class Decoder(srd.Decoder):
    api_version = 3
    id = 'adat'
    name = 'ADAT'
    longname = 'ADAT Lightpipe'
    desc = '8-channel 24-bit digital audio over a single optical NRZI line.'
    license = 'gplv2+'
    inputs = ['logic']
    outputs = ['adat']
    tags = ['Audio', 'PC']
    channels = (
        {'id': 'data', 'name': 'Data', 'desc': 'ADAT line (receiver output)',
            'idn': 'dec_adat_chan_data'},
    )
    options = (
        {'id': 'rate', 'desc': 'Frame rate (start value)', 'default': 'auto',
            'values': tuple(RATES), 'idn': 'dec_adat_opt_rate'},
        {'id': 'format', 'desc': 'Sample format', 'default': 'hex+signed',
            'values': ('hex', 'signed', 'hex+signed'),
            'idn': 'dec_adat_opt_format'},
        {'id': 'bits', 'desc': 'Show individual bits', 'default': 'no',
            'values': ('no', 'yes'), 'idn': 'dec_adat_opt_bits'},
    )
    annotations = (
        ('sync', 'Frame sync'),
        ('user', 'User bits'),
        ('sample', 'Sample'),
        ('frame', 'Frame'),
        ('error', 'Error'),
        ('bit', 'Bit'),
        ('marker', 'Sync bit'),
    )
    annotation_rows = (
        ('frames', 'Frames', (3,)),
        ('control', 'Sync / User', (0, 1)),
        ('samples', 'Samples', (2,)),
        ('bits', 'Bits', (5, 6)),
        ('errors', 'Errors', (4,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.samplerate = None
        self.seed_period = None   # bit cell in samples from the rate option
        self.period = None        # current bit cell estimate, in samples
        self.prev_edge = None
        self.starts = None        # start sample of every bit of the open frame
        self.vals = None          # NRZI-decoded value of every bit
        self.glitch = False
        self.frame_no = 0
        self.rate_checked = False

    def start(self):
        self.out_python = self.register(srd.OUTPUT_PYTHON)
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def metadata(self, key, value):
        if key == srd.SRD_CONF_SAMPLERATE:
            self.samplerate = value

    def putx(self, ss, es, data):
        ss = int(round(ss))
        es = max(int(round(es)), ss + 1)
        self.put(ss, es, self.out_ann, data)

    def putp(self, ss, es, data):
        ss = int(round(ss))
        es = max(int(round(es)), ss + 1)
        self.put(ss, es, self.out_python, data)

    # --- bit stream recovery -------------------------------------------------

    def check_samplerate(self, at):
        if self.rate_checked:
            return
        self.rate_checked = True
        if self.period < MIN_SAMPLES_PER_BIT:
            self.putx(at, at + self.period, [4, [
                'Sample rate too low: %.1f samples per bit, use at least '
                '100 MS/s' % self.period, 'Sample rate too low', 'Rate']])

    def begin_frame(self, sync_edge, marker_edge):
        # The sync is a '1' at sync_edge, ten '0', and the first marker '1'
        # at marker_edge. Spread the ten zeros evenly across the interval.
        step = (marker_edge - sync_edge) / SYNC_BITS
        self.starts = [sync_edge + k * step for k in range(SYNC_BITS)]
        self.starts.append(marker_edge)
        self.vals = [1] + [0] * (SYNC_BITS - 1) + [1]
        self.glitch = False
        self.check_samplerate(marker_edge)

    def add_run(self, prev_edge, edge, cells):
        n = int(cells + 0.5)
        if n < 1:
            n = 1
            self.glitch = True
        step = (edge - prev_edge) / n
        for j in range(1, n):
            self.starts.append(prev_edge + j * step)
            self.vals.append(0)
        self.starts.append(edge)
        self.vals.append(1)

    def lose_lock(self, at):
        if self.starts is not None:
            self.putx(self.starts[0], at, [4, [
                'Signal lost, no frame sync', 'Signal lost', 'Lost']])
        self.starts = None
        self.vals = None
        self.period = self.seed_period

    def on_edge(self, prev_edge, edge):
        dt = edge - prev_edge
        if self.period is None:
            # No rate known yet: the first interval of sync length gives it.
            us = dt * 1e6 / self.samplerate
            if BOOT_MIN_US <= us <= BOOT_MAX_US:
                self.period = dt / SYNC_BITS
                self.begin_frame(prev_edge, edge)
            return

        cells = dt / self.period
        if SYNC_MIN <= cells < SYNC_MAX:
            if not self.end_frame(prev_edge):
                # No clean frame to time against (first frame, or the last
                # one was damaged): the sync itself is 11 cells, good to a
                # percent or two, which is enough to round runs correctly.
                self.period = dt / SYNC_BITS
            self.begin_frame(prev_edge, edge)
        elif cells >= SYNC_MAX:
            self.lose_lock(prev_edge)
        elif self.starts is not None:
            self.add_run(prev_edge, edge, cells)
            if len(self.vals) > MAX_BITS:
                self.lose_lock(edge)

    # --- frame output ----------------------------------------------------------

    def fmt_sample(self, ch, raw, signed):
        h = '%06X' % raw
        if self.options['format'] == 'hex':
            return ['Ch%d: 0x%s' % (ch, h), '%d: %s' % (ch, h), h]
        if self.options['format'] == 'signed':
            return ['Ch%d: %d' % (ch, signed), '%d: %d' % (ch, signed),
                    '%d' % signed]
        return ['Ch%d: 0x%s (%d)' % (ch, h, signed),
                'Ch%d: 0x%s' % (ch, h), '%d: %s' % (ch, h), h]

    def end_frame(self, sync_edge):
        """Output the open frame. Returns True if the frame was clean and
        its length was used to refine the bit cell estimate."""
        # The last bit collected is the '1' of the next frame's sync, which
        # only now turned out to be one. Everything before it is this frame.
        if self.starts is None:
            return False
        starts = self.starts[:-1]
        vals = self.vals[:-1]
        glitch = self.glitch
        self.frame_no += 1

        errors = []
        if len(vals) != FRAME_BITS:
            errors.append('%d bits, expected %d' % (len(vals), FRAME_BITS))
        else:
            bad = [p for p in MARKERS if vals[p] != 1]
            if bad:
                errors.append('sync bit missing at bit %d' % bad[0])
        if glitch:
            errors.append('edge glitch')

        if errors:
            self.putx(starts[0], sync_edge, [4, [
                'Frame %d error: %s' % (self.frame_no, ', '.join(errors)),
                'Frame error', 'Err']])
            return False

        # A clean frame is exactly 256 cells long, known to a sample or two
        # over thousands: far more precise than any single interval.
        frame_len = sync_edge - starts[0]
        self.period = frame_len / FRAME_BITS
        fs = self.samplerate / frame_len

        def st(i):
            return sync_edge if i >= FRAME_BITS else starts[i]

        self.putx(starts[0], sync_edge, [3, [
            'Frame %d: %.3f kHz' % (self.frame_no, fs / 1000.0),
            'Frame %d' % self.frame_no, 'F%d' % self.frame_no]])
        self.putx(st(0), st(SYNC_BITS), [0, ['Sync', 'S']])

        user = 0
        for i in range(USER_BIT, USER_BIT + 4):
            user = (user << 1) | vals[i]
        self.putx(st(USER_BIT), st(USER_BIT + 4), [1, [
            'User: 0x%X' % user, 'U: %X' % user, '%X' % user]])
        self.putp(st(USER_BIT), st(USER_BIT + 4), ['USER', user])

        for c in range(CHANNELS):
            first = DATA_BIT + 30 * c
            raw = 0
            for t in range(6):
                for b in range(4):
                    raw = (raw << 1) | vals[first + 5 * t + b]
            signed = raw - (1 << 24) if raw & 0x800000 else raw
            ss, es = st(first), st(first + SAMPLE_SPAN)
            self.putx(ss, es, [2, self.fmt_sample(c + 1, raw, signed)])
            self.putp(ss, es, ['SAMPLE', [c + 1, signed]])

        if self.options['bits'] == 'yes':
            markers = set((0,) + MARKERS)
            for i in range(FRAME_BITS):
                self.putx(st(i), st(i + 1),
                          [6 if i in markers else 5, ['%d' % vals[i]]])
        return True

    # --- main loop -------------------------------------------------------------

    def decode(self):
        if not self.samplerate:
            raise SamplerateError('Cannot decode without samplerate.')

        rate = RATES[self.options['rate']]
        if rate:
            self.seed_period = self.samplerate / (rate * FRAME_BITS)
        self.period = self.seed_period

        # The first edge has no known predecessor.
        self.wait({0: 'e'})
        self.prev_edge = self.samplenum
        while True:
            self.wait({0: 'e'})
            self.on_edge(self.prev_edge, self.samplenum)
            self.prev_edge = self.samplenum
