"""Small PCM gain with a peak ceiling; adds no buffering to the audio bridge."""
import array
import math
import sys


class SpeechGain:
    def __init__(self, gain_db=6.0):
        self.maximum_gain = 10 ** (gain_db / 20)
        self.gain = self.maximum_gain
        self.ceiling = int(32767 * .95)
        self.limited_blocks = 0

    def process(self, raw):
        values = array.array('h')
        values.frombytes(raw)
        if sys.byteorder != 'little':
            values.byteswap()
        peak = max(map(abs, values), default=0)
        target = min(self.maximum_gain, self.ceiling / max(peak, 1))
        # Immediate reduction uses the whole existing 20 ms block as lookahead;
        # restore gain slowly after a peak to avoid audible pumping.
        self.gain = min(target, self.gain + (self.maximum_gain - self.gain) * .08)
        if target < self.maximum_gain:
            self.limited_blocks += 1
        output = array.array('h', (max(-self.ceiling, min(self.ceiling, round(v * self.gain)))
                                  for v in values))
        if sys.byteorder != 'little':
            output.byteswap()
        return output.tobytes()

    @property
    def gain_db(self):
        return round(20 * math.log10(self.gain), 2)
