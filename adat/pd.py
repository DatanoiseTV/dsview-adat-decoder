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

import math

import sigrokdecode as srd

'''
OUTPUT_PYTHON format:

Packet:
[<ptype>, <pdata>]

<ptype>, <pdata>:
 - 'SAMPLE', [<channel>, <value>]    channel 1..8, value signed 24-bit int
 - 'USER', <value>                   4 user bits of the frame, 0..15
 - 'FLAGS', {'timecode': 0|1, 'midi': 0|1, 'smux': 0|1, 'reserved': 0|1}
 - 'MIDI', <byte>, [<alternatives>]  experimental, see the midi_uart option;
                                       alternatives is non-empty if the byte was ambiguous
 - 'MIDI_EVENT', {'bytes': [..], 'text': str, 'ambiguous': bool}
                                       one assembled MIDI message
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

# The four user bits of a frame: bit 0 timecode transport, bit 1 MIDI
# transport, bit 2 S/MUX indication, bit 3 reserved = 0 (Wikipedia, ADAT
# Lightpipe). The nibble is a value sent MSB first like every other nibble,
# so bit 3 goes out first. Evidence, all from source code, none measured on
# hardware: lib_adat (XMOS) transmits S/MUX 2 with user bits 0100, the second
# bit sent, which is bit 2; amaranth-farm/adat-core builds the header word
# 0b100000000001uuuu, sent MSB first, so u[3] leads. The 'user_order' option
# keeps the opposite reading available.
FLAG_NAMES = ('timecode', 'midi', 'smux', 'reserved')
FLAG_LABELS = ('Timecode', 'MIDI', 'S/MUX', 'Reserved')
FLAG_SHORT = ('TC', 'MIDI', 'SMUX', 'Rsvd')

# Annotation classes. Samples get one class per channel so DSView colours
# neighbouring channels differently.
(A_SYNC, A_USER, A_FRAME, A_ERROR, A_BIT, A_MARKER,
 A_TIMECODE, A_MIDI, A_SMUX, A_RESERVED) = range(10)
A_CH0 = 10
A_FLAGS = (A_TIMECODE, A_MIDI, A_SMUX, A_RESERVED)
A_MIDIBYTE = A_CH0 + 8
A_MIDIEVENT = A_MIDIBYTE + 1

# EXPERIMENTAL MIDI-over-ADAT. Neither encoding below is documented for ADAT.
#  - 'frame bit': one MIDI bit per frame, the line inverted (idle 0, start 1,
#    stop 0, data bits inverted), LSB first, 10 frames per byte, bytes spaced
#    to average 31250 baud. Modelled on RME's MIDI over MADI as described in a
#    2015 RME forum exchange; exact, no timing ambiguity.
#  - 'idle high' / 'idle low': the user bit read once per frame is the line of
#    a 31250 baud 8N1 UART. At 48 kHz a frame is 20.8 us and a UART bit 32 us,
#    so each bit is seen only 1-2 times and the start edge is known to one
#    frame.
MIDI_BAUD = 31250
MIDI_STATUS = {0x80: 'Note Off', 0x90: 'Note On', 0xA0: 'Poly Aftertouch',
               0xB0: 'Control Change', 0xC0: 'Program Change',
               0xD0: 'Channel Aftertouch', 0xE0: 'Pitch Bend'}
NOTE_NAMES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')
# Data bytes that follow a status byte; system common 0xF1.. use their own.
MIDI_DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1,
                 0xE0: 2, 0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF4: 0, 0xF5: 0,
                 0xF6: 0}
MIDI_CC = {0: 'Bank Select', 1: 'Mod Wheel', 2: 'Breath', 4: 'Foot',
           5: 'Portamento Time', 6: 'Data Entry', 7: 'Volume', 8: 'Balance',
           10: 'Pan', 11: 'Expression', 32: 'Bank Select LSB',
           38: 'Data Entry LSB', 64: 'Sustain', 65: 'Portamento',
           66: 'Sostenuto', 67: 'Soft Pedal', 71: 'Resonance', 72: 'Release',
           73: 'Attack', 74: 'Cutoff', 91: 'Reverb', 93: 'Chorus',
           98: 'NRPN LSB', 99: 'NRPN MSB', 100: 'RPN LSB', 101: 'RPN MSB',
           120: 'All Sound Off', 121: 'Reset Controllers', 122: 'Local Control',
           123: 'All Notes Off', 124: 'Omni Off', 125: 'Omni On',
           126: 'Mono On', 127: 'Poly On'}
MIDI_MANUFACTURER = {0x7D: 'Educational', 0x7E: 'Universal Non-Realtime',
                     0x7F: 'Universal Realtime', 0x41: 'Roland', 0x42: 'Korg',
                     0x43: 'Yamaha', 0x47: 'Akai', 0x00: 'extended id'}
MIDI_SYSTEM = {0xF0: 'SysEx', 0xF6: 'Tune Request', 0xF7: 'SysEx End',
               0xF8: 'Clock', 0xFA: 'Start', 0xFB: 'Continue', 0xFC: 'Stop',
               0xFE: 'Active Sensing', 0xFF: 'System Reset'}

RATES = {'auto': None, '32 kHz': 32000, '44.1 kHz': 44100, '48 kHz': 48000}

def midi_byte_text(byte, alts):
    """Annotation texts (long to short) of one decoded MIDI byte; alts are
    the other bytes the samples were consistent with."""
    if byte in MIDI_SYSTEM:
        name = MIDI_SYSTEM[byte]
    elif byte >= 0xF0:
        name = 'System'
    elif byte & 0x80:
        name = '%s ch%d' % (MIDI_STATUS[byte & 0xF0], (byte & 0xF) + 1)
    else:
        name = 'data %d' % byte
    if alts:
        other = ' or '.join('0x%02X' % b for b in alts)
        return ['MIDI 0x%02X? or %s: %s' % (byte, other, name),
                'MIDI 0x%02X? or %s' % (byte, other),
                'MIDI 0x%02X?' % byte, '%02X?' % byte]
    return ['MIDI 0x%02X: %s' % (byte, name),
            'MIDI 0x%02X' % byte, '%02X' % byte]


def midi_data_len(status):
    """Data bytes that follow a channel or system common status byte."""
    return MIDI_DATA_LEN[status & 0xF0 if status < 0xF0 else status]


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
        {'id': 'user_order', 'desc': 'User bit order',
            'default': 'first sent = bit 3',
            'values': ('first sent = bit 3', 'first sent = bit 0'),
            'idn': 'dec_adat_opt_user_order'},
        {'id': 'midi_uart', 'desc': 'MIDI bit decoding (experimental)',
            'default': 'off',
            'values': ('off', 'frame bit', 'idle high', 'idle low'),
            'idn': 'dec_adat_opt_midi_uart'},
        {'id': 'bits', 'desc': 'Show individual bits', 'default': 'no',
            'values': ('no', 'yes'), 'idn': 'dec_adat_opt_bits'},
    )
    annotations = (
        ('sync', 'Frame sync'),
        ('user', 'User nibble'),
        ('frame', 'Frame'),
        ('error', 'Error'),
        ('bit', 'Bit'),
        ('marker', 'Sync bit'),
        ('timecode', 'Timecode bit'),
        ('midi', 'MIDI bit'),
        ('smux', 'S/MUX flag'),
        ('reserved', 'Reserved bit'),
        ('ch1', 'Channel 1'),
        ('ch2', 'Channel 2'),
        ('ch3', 'Channel 3'),
        ('ch4', 'Channel 4'),
        ('ch5', 'Channel 5'),
        ('ch6', 'Channel 6'),
        ('ch7', 'Channel 7'),
        ('ch8', 'Channel 8'),
        ('midibyte', 'MIDI byte (experimental)'),
        ('midievent', 'MIDI event (experimental)'),
    )
    annotation_rows = (
        ('frames', 'Frames', (A_FRAME,)),
        ('control', 'Sync / User', (A_SYNC, A_USER)),
        ('flags', 'User bits', A_FLAGS),
        ('samples', 'Samples', tuple(range(A_CH0, A_CH0 + CHANNELS))),
        ('midi_events', 'MIDI events', (A_MIDIEVENT,)),
        ('midi', 'MIDI bytes', (A_MIDIBYTE,)),
        ('bits', 'Bits', (A_BIT, A_MARKER)),
        ('errors', 'Errors', (A_ERROR,)),
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
        self.midi_reset()

    def midi_reset(self, need_idle=False):
        # After a lost frame the byte boundaries are unknown. The longest
        # high run inside a valid byte is 9 bits (8 data + stop), so ten
        # high bits in a row means the line is idle and a start bit is safe.
        self.midi_idle_ok = not need_idle
        self.midi_high_since = None
        self.midi_parser_reset()
        self.midi_frames = []    # (instant, level) of recent frames
        self.midi_start = None   # (last high, first low) frame of a byte in flight
        self.midi_resume = 0     # no new start bit before this sample
        self.midi_last = None
        self.fb_reset()

    def fb_reset(self):
        # 'frame bit' decoder: the line state before the first frame is
        # unknown, so a start edge needs a 0 seen first.
        self.fb_prev = None
        # Byte boundaries are known at the start and after a good byte. After
        # a lost frame or a bad stop bit they are not: the data frames left
        # over can hold a 0 -> 1 edge that is no start bit. The longest run of
        # 0 inside a byte is 9 (eight inverted data 1s and the stop), so ten
        # 0 frames in a row prove the line idle.
        self.fb_sync = True
        self.fb_zeros = 0
        self.fb_n = 0            # frames of the byte in flight, 0 = hunting
        self.fb_ss = None        # first frame of the byte in flight
        self.fb_bits = []        # the 9 frames after the start frame
        self.fb_last = None

    def midi_parser_reset(self):
        # A lost byte makes running status unreliable and the message in
        # progress unfinishable, so both are dropped.
        self.midi_status = None     # running status
        self.midi_cur = None        # status of the message in progress
        self.midi_msg = []          # its data bytes so far
        self.midi_msg_start = None
        self.midi_msg_amb = False
        self.midi_sysex = None      # [start, end, nbytes, amb, first bytes]

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
            self.putx(at, at + self.period, [A_ERROR, [
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
            self.putx(self.starts[0], at, [A_ERROR, [
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
        if signed == 0:
            level = '-inf dBFS'
        else:
            level = '%.1f dBFS' % (20 * math.log10(abs(signed) / 8388608.0))
        if self.options['format'] == 'hex':
            return ['Ch%d: 0x%s  %s' % (ch, h, level),
                    'Ch%d: 0x%s' % (ch, h), '%d: %s' % (ch, h), h]
        if self.options['format'] == 'signed':
            return ['Ch%d: %d  %s' % (ch, signed, level),
                    'Ch%d: %d' % (ch, signed), '%d: %d' % (ch, signed),
                    '%d' % signed]
        return ['Ch%d: 0x%s (%d)  %s' % (ch, h, signed, level),
                'Ch%d: 0x%s (%d)' % (ch, h, signed),
                'Ch%d: 0x%s' % (ch, h), '%d: %s' % (ch, h), h]

    def user_flags(self, nibble_bits):
        """nibble_bits: the four user bits in the order they are sent.
        Returns the flag values (timecode, midi, smux, reserved)."""
        if self.options['user_order'] == 'first sent = bit 0':
            return tuple(nibble_bits)
        return tuple(reversed(nibble_bits))

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
            self.putx(starts[0], sync_edge, [A_ERROR, [
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

        sent = [vals[USER_BIT + k] for k in range(4)]
        flags = self.user_flags(sent)
        smux = flags[2]
        self.putx(starts[0], sync_edge, [A_FRAME, [
            'Frame %d: %.3f kHz, %.3f Mbit/s%s' % (
                self.frame_no, fs / 1000.0, fs * FRAME_BITS / 1e6,
                ', S/MUX' if smux else ''),
            'Frame %d: %.3f kHz' % (self.frame_no, fs / 1000.0),
            'Frame %d' % self.frame_no, 'F%d' % self.frame_no]])
        self.putx(st(0), st(SYNC_BITS), [A_SYNC, ['Sync', 'S']])

        user = 0
        for v in sent:
            user = (user << 1) | v
        names = [FLAG_SHORT[k] for k in range(3) if flags[k]]
        self.putx(st(USER_BIT), st(USER_BIT + 4), [A_USER, [
            'User 0x%X: %s' % (user, ' '.join(names) or 'none'),
            'User 0x%X' % user, '%X' % user]])
        self.putp(st(USER_BIT), st(USER_BIT + 4), ['USER', user])
        for k in range(4):
            # Flag k sits at the bit position it is sent in.
            pos = USER_BIT + (k if self.options['user_order'] ==
                              'first sent = bit 0' else 3 - k)
            text = ['%s: %d' % (FLAG_LABELS[k], flags[k]),
                    '%s %d' % (FLAG_SHORT[k], flags[k]), '%d' % flags[k]]
            if k == 3 and flags[k]:
                text[0] = 'Reserved: 1 (expected 0)'
            self.putx(st(pos), st(pos + 1), [A_FLAGS[k], text])
        self.putp(st(USER_BIT), st(USER_BIT + 4), [
            'FLAGS', dict(zip(FLAG_NAMES, flags))])

        for c in range(CHANNELS):
            first = DATA_BIT + 30 * c
            raw = 0
            for t in range(6):
                for b in range(4):
                    raw = (raw << 1) | vals[first + 5 * t + b]
            signed = raw - (1 << 24) if raw & 0x800000 else raw
            ss, es = st(first), st(first + SAMPLE_SPAN)
            self.putx(ss, es, [A_CH0 + c, self.fmt_sample(c + 1, raw, signed)])
            self.putp(ss, es, ['SAMPLE', [c + 1, signed]])

        if self.options['midi_uart'] == 'frame bit':
            self.fb_feed(starts[0], frame_len, flags[1])
        elif self.options['midi_uart'] != 'off':
            level = flags[1] if self.options['midi_uart'] == 'idle high' \
                else 1 - flags[1]
            self.midi_feed(starts[0], frame_len, level)

        if self.options['bits'] == 'yes':
            markers = set((0,) + MARKERS)
            for i in range(FRAME_BITS):
                self.putx(st(i), st(i + 1),
                          [A_MARKER if i in markers else A_BIT, ['%d' % vals[i]]])
        return True

    # --- experimental MIDI user bit ----------------------------------------------

    def midi_feed(self, instant, frame_len, level):
        # Gap in the frame sequence (a damaged frame): the byte in flight is
        # lost, and so is the timing reference.
        if self.midi_last is not None and instant - self.midi_last > 1.5 * frame_len:
            if self.midi_start is not None:
                self.putx(self.midi_start[1], instant, [A_ERROR, [
                    'MIDI byte interrupted by a damaged frame', 'MIDI lost']])
            self.midi_reset(need_idle=True)
        self.midi_last = instant
        self.midi_frames.append((instant, level))
        bit = self.samplerate / float(MIDI_BAUD)
        if level == 1:
            if self.midi_high_since is None:
                self.midi_high_since = instant
            elif instant - self.midi_high_since >= 10 * bit:
                self.midi_idle_ok = True
        else:
            self.midi_high_since = None

        while True:
            if self.midi_start is None:
                # A start bit: first low sample after a high one. The edge
                # fell somewhere between the two.
                f = self.midi_frames
                for i in range(1, len(f)):
                    if f[i][1] == 0 and f[i - 1][1] == 1 and \
                            f[i][0] >= self.midi_resume and self.midi_idle_ok:
                        self.midi_start = (f[i - 1][0], f[i][0])
                        break
                else:
                    self.midi_frames = f[-2:]
                    return
            lo, hi = self.midi_start
            if instant < hi + 10 * bit:
                return
            self.midi_finish(lo, hi, bit)

    def fb_feed(self, instant, frame_len, level):
        """'frame bit' scheme: one inverted 8N1 UART bit per frame. A byte is
        the start frame (1), eight data frames (the inverse of the data bit,
        LSB first) and the stop frame (0). Exact: nothing is ambiguous. After
        a good byte the next frame may start another one; a bad stop bit or a
        lost frame is an error after which ten 0 frames must pass before a
        start is accepted again. A glitch
        of one frame on an idle line is a valid 0xFF, as on any UART."""
        if self.fb_last is not None and instant - self.fb_last > 1.5 * frame_len:
            if self.fb_n:
                self.putx(self.fb_ss, instant, [A_ERROR, [
                    'MIDI byte interrupted by a damaged frame', 'MIDI lost']])
                self.midi_parser_reset()
            self.fb_prev = None
            self.fb_n = 0
            self.fb_sync = False
            self.fb_zeros = 0
        self.fb_last = instant
        if self.fb_n == 0:
            self.fb_zeros = self.fb_zeros + 1 if level == 0 else 0
            if self.fb_zeros >= 10:
                self.fb_sync = True
            if self.fb_sync and self.fb_prev == 0 and level == 1:
                self.fb_ss = instant
                self.fb_bits = []
                self.fb_n = 1
            self.fb_prev = level
            return
        self.fb_bits.append(level)
        self.fb_n += 1
        if self.fb_n < 10:
            return
        end = instant + frame_len
        self.fb_n = 0
        self.fb_prev = level
        self.fb_zeros = 1 if level == 0 else 0
        if self.fb_bits[8] != 0:
            self.putx(self.fb_ss, end, [A_ERROR, [
                'MIDI framing error', 'MIDI framing', 'MIDI err']])
            self.midi_parser_reset()
            self.fb_sync = False
            return
        byte = 0
        for k in range(8):
            byte |= (1 - self.fb_bits[k]) << k
        self.putx(self.fb_ss, end, [A_MIDIBYTE, midi_byte_text(byte, [])])
        self.putp(self.fb_ss, end, ['MIDI', byte, []])
        self.midi_event_feed(self.fb_ss, end, byte, False)

    def midi_event_feed(self, ss, es, byte, amb):
        """Assemble UART bytes into MIDI messages: running status, realtime
        bytes between the bytes of another message, SysEx."""
        if byte >= 0xF8:    # realtime: may sit anywhere, changes no state
            self.midi_emit(ss, es, [byte], amb)
            return
        sx = self.midi_sysex
        if byte & 0x80:
            if sx is not None:
                self.midi_sysex = None
                if byte == 0xF7:
                    self.midi_emit_sysex(sx, es, amb, True)
                    return
                self.midi_emit_sysex(sx, ss, amb, False)   # cut short
            self.midi_cur = None
            self.midi_msg = []
            if byte == 0xF7:    # end of SysEx with none open
                self.midi_emit(ss, es, [byte], amb)
                return
            if byte == 0xF0:
                self.midi_status = None
                self.midi_sysex = [ss, es, 0, amb, []]
                return
            # System common cancels running status, channel messages set it.
            self.midi_status = byte if byte < 0xF0 else None
            if midi_data_len(byte) == 0:
                self.midi_emit(ss, es, [byte], amb)
            else:
                self.midi_cur = byte
                self.midi_msg_start = ss
                self.midi_msg_amb = amb
            return

        # data byte
        if sx is not None:
            sx[1] = es
            sx[2] += 1
            sx[3] = sx[3] or amb
            if len(sx[4]) < 3:
                sx[4].append(byte)
            return
        if self.midi_cur is None:
            if self.midi_status is None:
                self.putx(ss, es, [A_MIDIEVENT, [
                    'Data 0x%02X without status' % byte,
                    'stray 0x%02X' % byte, '%02X' % byte]])
                return
            # Running status: this data byte starts a new message.
            self.midi_cur = self.midi_status
            self.midi_msg = []
            self.midi_msg_start = ss
            self.midi_msg_amb = False
        self.midi_msg.append(byte)
        self.midi_msg_amb = self.midi_msg_amb or amb
        status = self.midi_cur
        if len(self.midi_msg) == midi_data_len(status):
            self.midi_emit(self.midi_msg_start, es, [status] + self.midi_msg,
                           self.midi_msg_amb)
            self.midi_cur = None
            self.midi_msg = []

    def midi_emit_sysex(self, sx, end, amb, complete):
        start, last, n, sx_amb, head = sx
        amb = amb or sx_amb
        who = MIDI_MANUFACTURER.get(head[0]) if head else None
        label = 'SysEx%s (%d bytes)' % (' ' + who if who else '', n)
        if not complete:
            label += ' cut short'
        self.midi_put_event(start, end, [0xF0] + head, label, label,
                            'SysEx', amb)

    def midi_put_event(self, ss, es, raw, long_text, short_text, tiny, amb):
        q = ' ?' if amb else ''
        self.putx(ss, es, [A_MIDIEVENT, [long_text + q, short_text + q,
                                         tiny + q]])
        self.putp(ss, es, ['MIDI_EVENT', {
            'bytes': raw, 'text': long_text, 'ambiguous': amb}])

    def midi_emit(self, ss, es, raw, amb):
        st = raw[0]
        d = raw[1:]
        ch = (st & 0xF) + 1
        kind = st & 0xF0

        def note(n):
            return '%s%d' % (NOTE_NAMES[n % 12], n // 12 - 1)

        if kind == 0x80 or (kind == 0x90 and d[1] == 0):
            tail = ' (note on, vel 0)' if kind == 0x90 else ''
            long_t = 'Note Off ch%d %s (%d) vel %d%s' % (ch, note(d[0]), d[0],
                                                         d[1], tail)
            short_t, tiny = 'Off ch%d %s' % (ch, note(d[0])), note(d[0])
        elif kind == 0x90:
            long_t = 'Note On ch%d %s (%d) vel %d' % (ch, note(d[0]), d[0], d[1])
            short_t, tiny = 'On ch%d %s v%d' % (ch, note(d[0]), d[1]), note(d[0])
        elif kind == 0xA0:
            long_t = 'Poly Pressure ch%d %s (%d) = %d' % (ch, note(d[0]), d[0],
                                                          d[1])
            short_t, tiny = 'Poly ch%d %s %d' % (ch, note(d[0]), d[1]), 'AT'
        elif kind == 0xB0:
            name = MIDI_CC.get(d[0])
            cc = 'CC%d %s' % (d[0], name) if name else 'CC%d' % d[0]
            long_t = '%s ch%d = %d' % (cc, ch, d[1])
            short_t, tiny = 'CC%d ch%d = %d' % (d[0], ch, d[1]), 'CC%d' % d[0]
        elif kind == 0xC0:
            long_t = 'Program Change ch%d -> %d' % (ch, d[0])
            short_t, tiny = 'PC ch%d %d' % (ch, d[0]), 'PC'
        elif kind == 0xD0:
            long_t = 'Channel Pressure ch%d = %d' % (ch, d[0])
            short_t, tiny = 'Press ch%d %d' % (ch, d[0]), 'AT'
        elif kind == 0xE0:
            v = ((d[1] << 7) | d[0]) - 8192
            long_t = 'Pitch Bend ch%d %+d' % (ch, v)
            short_t, tiny = 'Bend ch%d %+d' % (ch, v), 'PB'
        elif st == 0xF1:
            long_t = 'MTC Quarter Frame type %d value %d' % (d[0] >> 4,
                                                             d[0] & 0xF)
            short_t, tiny = 'MTC', 'MTC'
        elif st == 0xF2:
            long_t = 'Song Position %d' % ((d[1] << 7) | d[0])
            short_t, tiny = 'SPP', 'SPP'
        elif st == 0xF3:
            long_t = 'Song Select %d' % d[0]
            short_t, tiny = 'Song %d' % d[0], 'Song'
        elif st in MIDI_SYSTEM:
            long_t = MIDI_SYSTEM[st]
            short_t, tiny = long_t, long_t[:3]
        else:
            long_t = 'System 0x%02X' % st
            short_t, tiny = long_t, '%02X' % st
        self.midi_put_event(ss, es, raw, long_t, short_t, tiny, amb)

    def midi_finish(self, lo, hi, bit):
        # Each UART bit (32 us) spans at least one frame (<= 31 us), so for
        # a candidate edge position every bit holds one or more samples.
        # A position is possible if the samples of each bit agree and the
        # start/stop bits are 0/1. Several positions can give different
        # bytes: one sample per frame is only 1.5 samples per baud, so e.g.
        # 0x3C and 0x1E, or 0x00 and 0x80, can be indistinguishable. Say so
        # whenever there was a choice.
        steps = 64
        found = []
        for j in range(steps):
            e = lo + (j + 0.5) * (hi - lo) / steps
            bits = [None] * 10
            ok = True
            for t, v in self.midi_frames:
                k = int(math.floor((t - e) / bit))
                if 0 <= k < 10:
                    if bits[k] is None:
                        bits[k] = v
                    elif bits[k] != v:
                        ok = False
                        break
            if ok and bits[0] == 0 and bits[9] == 1 and None not in bits:
                found.append((e, bits))
        self.midi_start = None
        if not found:
            self.putx(hi, hi + 10 * bit, [A_ERROR, [
                'MIDI framing error', 'MIDI framing', 'MIDI err']])
            self.midi_parser_reset()
            # The low stop bit looks like the start of the next byte, so
            # wait for the line to go idle again (as after a lost frame).
            self.midi_idle_ok = False
            self.midi_high_since = None
            self.midi_resume = hi + 9 * bit
        else:
            decoded = []
            for e, bits in found:
                byte = 0
                for k in range(8):
                    byte |= bits[1 + k] << k
                decoded.append(byte)
            # Middle of the possible range. Majority voting over the range
            # was tried and gave the same primary byte on every case tested.
            byte = decoded[len(decoded) // 2]
            alts = sorted(set(decoded) - set([byte]))
            e = found[decoded.index(byte)][0]
            self.putx(e, e + 10 * bit, [A_MIDIBYTE, midi_byte_text(byte, alts)])
            self.putp(e, e + 10 * bit, ['MIDI', byte, alts])
            self.midi_event_feed(e, e + 10 * bit, byte, bool(alts))
            self.midi_resume = e + 9 * bit
        self.midi_frames = [f for f in self.midi_frames
                            if f[0] >= self.midi_resume - 1e-9 - bit]

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
