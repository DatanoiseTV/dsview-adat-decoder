"""Minimal host stand-in for the sigrokdecode module that DSView embeds.

Only the surface the ADAT decoder uses: a base class with register/put/wait,
and the output type constants. wait() supports the single-channel edge
condition {0: 'e'} and raises EOFError at the end of the capture, which is how
the real framework ends a decode run.
"""

OUTPUT_ANN = 0
OUTPUT_PYTHON = 1
OUTPUT_BINARY = 2
SRD_CONF_SAMPLERATE = 10000


class Decoder:
    def register(self, out_type, **kwargs):
        return out_type

    def put(self, ss, es, out, data):
        self.puts.append((ss, es, out, data))

    def wait(self, cond):
        assert cond == {0: 'e'}, cond
        if self._next >= len(self._edges):
            raise EOFError
        self.samplenum = self._edges[self._next]
        self._next += 1
        return (self._levels[self.samplenum],)

    def run(self, levels, samplerate, options):
        """Feed a list of 0/1 samples through decode(); return the puts."""
        self.puts = []
        self.options = options
        self.metadata(SRD_CONF_SAMPLERATE, samplerate)
        self.start()
        self._levels = levels
        self._edges = [i for i in range(1, len(levels))
                       if levels[i] != levels[i - 1]]
        self._next = 0
        self.samplenum = 0
        try:
            self.decode()
        except EOFError:
            pass
        return self.puts
