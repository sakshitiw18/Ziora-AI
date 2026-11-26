"""Wire format between the browser and Pipecat (keeps the plain-HTML frontend simple).

Browser -> server : binary frames = raw PCM16 mono @16 kHz (mic audio)
Server  -> browser: binary frames = raw PCM16 mono @16 kHz (agent voice)
                    text frames   = JSON ({"type":"transcript"|"interrupt", ...})
"""
import json

from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.serializers.base_serializer import FrameSerializer

try:  # name differs between Pipecat versions
    from pipecat.frames.frames import InterruptionFrame
except ImportError:
    from pipecat.frames.frames import StartInterruptionFrame as InterruptionFrame

try:  # older versions require declaring a serializer type
    from pipecat.serializers.base_serializer import FrameSerializerType
    _HAS_TYPE = True
except ImportError:
    _HAS_TYPE = False

SAMPLE_RATE = 16000


class BrowserPCMSerializer(FrameSerializer):
    if _HAS_TYPE:
        @property
        def type(self):
            return FrameSerializerType.BINARY

    async def serialize(self, frame: Frame):
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio
        if isinstance(frame, InterruptionFrame):  # user barged in -> browser must drop queued audio
            return json.dumps({"type": "interrupt"})
        return None

    async def deserialize(self, data):
        if isinstance(data, (bytes, bytearray)):
            return InputAudioRawFrame(audio=bytes(data), sample_rate=SAMPLE_RATE, num_channels=1)
        return None