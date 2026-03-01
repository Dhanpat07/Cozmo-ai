"""
CallPipeline - Per-call processing pipeline
Coordinates: VAD → STT → LLM → TTS → Output
with barge-in support throughout
"""
import asyncio
import logging
import time
from typing import Optional, AsyncIterator
import numpy as np

from .vad.silero_vad import SileroVAD
from .stt.groq_stt import GroqSTT
from .llm.groq_llm import GroqLLM
from .tts.cartesia_tts import CartesiaTTS
from .barge_in.barge_in_controller import BargeInController
from .metrics import (
    end_to_end_latency_hist, stt_latency_hist,
    llm_first_token_hist, llm_total_latency_hist, tts_start_latency_hist
)

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SIZE_MS = 20
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_SIZE_MS / 1000)  # 320 samples
BUFFER_MS = 250  # Buffer voiced audio for 250ms before sending to STT


class ConversationState:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self.messages = [
            {
                "role": "system",
                "content": (
                    "You are a helpful voice assistant for Acme Corp. "
                    "Be concise - your responses will be converted to speech. "
                    "Keep answers under 3 sentences unless asked for detail. "
                    "You handle customer inquiries including refunds, pricing, and product questions."
                )
            }
        ]
        self.turn_count = 0

    def add_user_message(self, text: str):
        self.messages.append({"role": "user", "content": text})
        self.turn_count += 1

    def add_assistant_message(self, text: str):
        self.messages.append({"role": "assistant", "content": text})


class CallPipeline:
    def __init__(self, call_id: str, room_name: str, knowledge_base=None):
        self.call_id = call_id
        self.room_name = room_name
        self.knowledge_base = knowledge_base

        self.vad = SileroVAD(sample_rate=SAMPLE_RATE)
        self.stt = GroqSTT(call_id=call_id)
        self.llm = GroqLLM(call_id=call_id)
        self.tts = CartesiaTTS(call_id=call_id)
        self.barge_in = BargeInController()
        self.state = ConversationState(call_id=call_id)

        self._audio_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._output_audio_callback = None
        self._running = True

        logger.info("Pipeline initialized for call_id=%s", call_id)

    def set_audio_output_callback(self, callback):
        """Set callback for sending audio back to LiveKit"""
        self._output_audio_callback = callback

    async def push_audio_frame(self, pcm_frame: bytes) -> None:
        """Non-blocking push of 20ms PCM frame from LiveKit"""
        try:
            self._audio_queue.put_nowait(pcm_frame)
        except asyncio.QueueFull:
            pass  # Drop frames if overloaded

    async def run(self, audio_track) -> None:
        """Main pipeline loop"""
        logger.info("[%s] Pipeline started", self.call_id)

        # Send greeting
        await self._respond("Hello! Welcome to Acme Corp. How can I help you today?")

        voice_buffer = bytearray()
        is_speaking = False
        silence_frames = 0
        SILENCE_THRESHOLD = 15  # ~300ms of silence to trigger STT

        async for pcm_frame in self._audio_from_track(audio_track):
            if not self._running:
                break

            # Convert bytes to numpy for VAD
            audio_np = np.frombuffer(pcm_frame, dtype=np.int16).astype(np.float32) / 32768.0

            # Run VAD
            speech_prob = self.vad.is_speech(audio_np)
            is_speech = speech_prob > 0.5

            # --- BARGE-IN CHECK ---
            if is_speech and self.barge_in.is_tts_playing:
                logger.info("[%s] Barge-in detected! Interrupting TTS", self.call_id)
                await self.barge_in.interrupt()
                voice_buffer = bytearray()
                is_speaking = False

            # Buffer voiced audio
            if is_speech:
                if not is_speaking:
                    is_speaking = True
                    silence_frames = 0
                    logger.debug("[%s] Speech started", self.call_id)
                voice_buffer.extend(pcm_frame)
                silence_frames = 0
            elif is_speaking:
                silence_frames += 1
                voice_buffer.extend(pcm_frame)  # Include trailing silence

                # End of utterance detected
                if silence_frames >= SILENCE_THRESHOLD:
                    is_speaking = False
                    audio_chunk = bytes(voice_buffer)
                    voice_buffer = bytearray()

                    # Process the utterance
                    if len(audio_chunk) > SAMPLE_RATE * 0.3 * 2:  # >300ms of audio
                        asyncio.create_task(self._process_utterance(audio_chunk))

    async def _audio_from_track(self, audio_track) -> AsyncIterator[bytes]:
        """Async generator yielding 20ms PCM frames from LiveKit track"""
        while self._running:
            try:
                frame = await asyncio.wait_for(
                    self._audio_queue.get(), timeout=1.0
                )
                yield frame
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("[%s] Audio track error: %s", self.call_id, e)
                break

    async def _process_utterance(self, audio_chunk: bytes) -> None:
        """Full pipeline: audio → STT → KB → LLM → TTS"""
        utterance_start = time.time()

        # --- STEP 1: STT ---
        stt_start = time.time()
        try:
            transcript = await asyncio.wait_for(
                self.stt.transcribe(audio_chunk),
                timeout=2.0
            )
        except asyncio.TimeoutError:
            logger.warning("[%s] STT timeout, retrying once", self.call_id)
            try:
                transcript = await asyncio.wait_for(
                    self.stt.transcribe(audio_chunk),
                    timeout=2.0
                )
            except Exception:
                logger.error("[%s] STT failed after retry", self.call_id)
                await self._respond("Sorry, I didn't catch that. Could you repeat?")
                return

        stt_ms = (time.time() - stt_start) * 1000
        stt_latency_hist.labels(call_id=self.call_id).observe(stt_ms)
        logger.info("[%s] STT [%.0fms]: %s", self.call_id, stt_ms, transcript)

        if not transcript or len(transcript.strip()) < 2:
            return

        # --- STEP 2: Knowledge Base Retrieval ---
        kb_context = ""
        if self.knowledge_base:
            results = await self.knowledge_base.search(transcript, top_k=3)
            if results:
                kb_context = "\n\nRelevant information:\n" + "\n".join(
                    f"- {r['text']}" for r in results
                )

        # --- STEP 3: LLM ---
        self.state.add_user_message(transcript)
        messages = self.state.messages.copy()

        # Inject KB context into last user message
        if kb_context:
            messages[-1] = {
                "role": "user",
                "content": transcript + kb_context
            }

        # Stream LLM response
        response_text = await self._stream_llm_to_tts(messages, utterance_start)

        if response_text:
            self.state.add_assistant_message(response_text)

        total_ms = (time.time() - utterance_start) * 1000
        end_to_end_latency_hist.labels(call_id=self.call_id).observe(total_ms)
        logger.info("[%s] End-to-end latency: %.0fms", self.call_id, total_ms)

    async def _stream_llm_to_tts(self, messages: list, utterance_start: float) -> str:
        """Stream LLM tokens → feed into TTS as they arrive"""
        llm_start = time.time()
        first_token_recorded = False
        full_response = ""

        # LLM timeout guard
        try:
            async with asyncio.timeout(2.0):
                async for token in self.llm.stream(messages):
                    if not first_token_recorded:
                        first_token_ms = (time.time() - llm_start) * 1000
                        llm_first_token_hist.labels(call_id=self.call_id).observe(first_token_ms)
                        # Start TTS immediately on first meaningful token
                        await self._start_tts_streaming()
                        first_token_recorded = True

                    full_response += token
                    await self.barge_in.feed_tts_text(token)

                    # Check if interrupted
                    if not self.barge_in.is_tts_playing and first_token_recorded:
                        logger.info("[%s] LLM stream cancelled due to barge-in", self.call_id)
                        break

        except asyncio.TimeoutError:
            logger.warning("[%s] LLM timeout, using fallback", self.call_id)
            fallback = "I'm having trouble processing that. Please hold on a moment."
            await self._respond(fallback)
            return fallback

        llm_total_ms = (time.time() - llm_start) * 1000
        llm_total_latency_hist.labels(call_id=self.call_id).observe(llm_total_ms)

        return full_response

    async def _respond(self, text: str) -> None:
        """Synthesize and play a response"""
        await self._start_tts_streaming()
        tts_start = time.time()

        async for audio_chunk in self.tts.synthesize_stream(text):
            tts_elapsed = (time.time() - tts_start) * 1000
            if tts_elapsed < 150:  # Record first chunk latency
                tts_start_latency_hist.labels(call_id=self.call_id).observe(tts_elapsed)

            if not self.barge_in.is_tts_playing:
                break  # Interrupted

            if self._output_audio_callback:
                await self._output_audio_callback(audio_chunk)

    async def _start_tts_streaming(self) -> None:
        """Signal TTS is starting"""
        self.barge_in.tts_started()

    async def cleanup(self) -> None:
        """Clean up resources"""
        self._running = False
        await self.barge_in.interrupt()
        logger.info("[%s] Pipeline cleaned up", self.call_id)
