"""Pipecat voice pipeline:
browser mic -> VAD -> ElevenLabs STT -> Gemini (function calling) -> ElevenLabs TTS -> browser speaker
"""
import json
import os
from datetime import date

from fastapi import WebSocket
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.elevenlabs.stt import CommitStrategy, ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import default_user_turn_start_strategies, UserTurnStrategies

from serializer import SAMPLE_RATE, BrowserPCMSerializer
from tools import register_tools, tools_schema

# Preload Silero VAD model once at module load instead of on every incoming call
_SHARED_VAD_ANALYZER = SileroVADAnalyzer()

SYSTEM_PROMPT = """You are Ziora, a friendly phone receptionist for a clinic. You help callers check a doctor's
availability, book an appointment, or cancel one. Today is {today} ({weekday}). This is a VOICE call.

Speaking style:
- Short, natural sentences. No markdown, bullets, emojis or lists.
- Say times in 12-hour form ("ten thirty in the morning"). Say dates naturally ("tomorrow", "Friday the 28th").
- Offer at most 3 or 4 slots at a time.

Identification (strict):
- The caller is identified ONLY by the phone number they say out loud. Never ask for anything else.
- Ask for the phone number first. Then read it back digit by digit and ask "Is that correct?".
- Do NOT call book_appointment or cancel_appointment until the caller says yes. If they say no, ask again.
  Only then pass phone_confirmed=true.

Flow:
- Booking: ask which doctor or speciality (General Physician, Cardiologist, Dermatologist, Orthopedic Surgeon, Pediatrician, Neurologist, ENT Specialist, Psychiatrist, Gynecologist, Ophthalmologist) and the
  day (default tomorrow). Call check_doctor_availability, offer the real slots, let the caller choose, repeat
  doctor + day + time once for confirmation, then call book_appointment.
- Cancelling: after the number is confirmed, call cancel_appointment. If the caller has several appointments,
  ask which one. Confirm clearly once it is cancelled.
- Only offer slots returned by the tool. Never invent doctors, slots or confirmations. If a tool returns an
  error, explain simply and offer another option.
- After finishing, ask if there is anything else, then say goodbye.
"""


class TranscriptForwarder(FrameProcessor):
    """Sends live transcript lines to the dashboard over the same websocket, then passes frames on."""

    def __init__(self, ws: WebSocket, role: str):
        super().__init__()
        self._ws, self._role, self._buf = ws, role, ""

    async def _send(self, text: str):
        if not text.strip():
            return
        try:
            await self._ws.send_text(json.dumps({"type": "transcript", "role": self._role, "text": text.strip()}))
        except Exception:
            pass

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame):
            try:
                await self._ws.send_text(json.dumps({"type": "interrupt"}))
            except Exception:
                pass
        elif self._role == "user" and isinstance(frame, TranscriptionFrame):
            await self._send(frame.text)
        elif self._role == "agent":
            if isinstance(frame, LLMFullResponseStartFrame):
                self._buf = ""
            elif isinstance(frame, LLMTextFrame):
                self._buf += frame.text
            elif isinstance(frame, LLMFullResponseEndFrame):
                await self._send(self._buf)
                self._buf = ""
        await self.push_frame(frame, direction)


async def run_pipeline(websocket: WebSocket, user_id: str | None = None) -> None:
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=BrowserPCMSerializer(),
        ),
    )

    stt = ElevenLabsRealtimeSTTService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        commit_strategy=CommitStrategy.VAD,
        settings=ElevenLabsRealtimeSTTService.Settings(
            vad_silence_threshold_secs=0.35,
            vad_threshold=0.3,
            min_silence_duration_ms=100,
        ),
        audio_passthrough=True,
    )
    tts = ElevenLabsTTSService(
        api_key=os.environ["ELEVENLABS_API_KEY"],
        settings=ElevenLabsTTSService.Settings(
            voice=os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL"),
            model="eleven_flash_v2_5",
        ),
    )
    llm = GoogleLLMService(
        api_key=os.environ["GOOGLE_API_KEY"],
        settings=GoogleLLMService.Settings(
            model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            thinking=GoogleLLMService.ThinkingConfig(thinking_level="minimal"),
            max_tokens=256,
        ),
    )
    register_tools(llm, user_id=user_id)

    today = date.today()
    context = LLMContext(
        messages=[{"role": "system", "content": SYSTEM_PROMPT.format(today=today.isoformat(), weekday=today.strftime("%A"))}],
        tools=tools_schema,
    )
    user_turn_strategies = UserTurnStrategies(
        start=default_user_turn_start_strategies(),
        stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.35)],
    )
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=_SHARED_VAD_ANALYZER,
            user_turn_strategies=user_turn_strategies,
            user_turn_stop_timeout=0.8,
        ),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        TranscriptForwarder(websocket, "user"),
        user_agg,
        llm,
        TranscriptForwarder(websocket, "agent"),
        tts,
        transport.output(),
        assistant_agg,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE),
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        logger.info(f"Caller connected (user_id={user_id})")
        context.add_message({"role": "user", "content": "A caller just connected. Greet them and ask for their phone number."})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        logger.info("Caller disconnected")
        await task.cancel()

    await PipelineRunner(handle_sigint=False).run(task)
