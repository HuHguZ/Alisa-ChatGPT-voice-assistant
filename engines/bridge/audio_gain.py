"""Small PCM gain with a peak ceiling; adds no buffering to the audio bridge."""
import array
import math
import sys


class SpeechGain:
    def __init__(self, gain_db=6.0, sample_rate=48000, channels=2):
        self.maximum_gain = 10 ** (gain_db / 20)
        self.gain = self.maximum_gain
        self.ceiling = int(32767 * .95)
        self.limited_blocks = 0
        self.silent_samples = 0
        self.silence_reset_samples = round(sample_rate * channels * .12)
        self.silence_resets = 0

    def process(self, raw):
        values = array.array('h')
        values.frombytes(raw)
        if sys.byteorder != 'little':
            values.byteswap()
        peak = max(map(abs, values), default=0)
        # A previous phrase's peak must not fade in the next one. Reset only
        # inside 120 ms of digital silence, never on an active speech block.
        if peak <= 4:
            self.silent_samples += len(values)
            if self.silent_samples >= self.silence_reset_samples and self.gain < self.maximum_gain:
                self.gain = self.maximum_gain
                self.silence_resets += 1
        else:
            self.silent_samples = 0
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
