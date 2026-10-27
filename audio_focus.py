"""A silent FilePlayer activity holds the stock DIALOG audio focus.

PCM playback still uses the bridge's bounded ALSA worker. The native agent
waits for FOCUS_CHANGED confirmation and has an independent local guardian;
this is not an external AudioClient registration or a replacement PCM transport.
"""
import io
import wave

LEASE_SECONDS = 12
GUARD_TIMEOUT_SECONDS = 10
FOCUS_FILES = ('focus-0.wav',)
MAIND_SHA256 = '7d431541e2737a94f8499953204dbb1306877e0cf7d36170c4b1a9e19974ba14'


def silent_lease():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(16000 * 2 * LEASE_SECONDS))
    return output.getvalue()
