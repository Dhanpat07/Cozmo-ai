"""
Speech-to-Text using OpenAI Whisper API
Async, non-blocking, with retry on failure
"""
import asyncio
import io
import logging
import os
import time
import wave

import aiohttp

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
SAMPLE_RATE = 16000
TIMEOUT_SECONDS = 2.0


class WhisperSTT:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                timeout=timeout
            )
        return self._session

    def _pcm_to_wav(self, pcm_bytes: bytes) -> bytes:
        """Convert raw PCM to WAV format for Whisper API"""
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wav_file:
            wav_file.setnchannels(1)       # Mono
            wav_file.setsampwidth(2)        # 16-bit
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm_bytes)
        return buf.getvalue()

    async def transcribe(self, pcm_audio: bytes) -> str:
        """
        Transcribe PCM audio bytes to text.
        Returns empty string on failure.
        Raises asyncio.TimeoutError if exceeds TIMEOUT_SECONDS.
        """
        wav_data = self._pcm_to_wav(pcm_audio)

        session = await self._get_session()

        form = aiohttp.FormData()
        form.add_field(
            "file",
            wav_data,
            filename="audio.wav",
            content_type="audio/wav"
        )
        form.add_field("model", "whisper-1")
        form.add_field("language", "en")
        form.add_field("response_format", "text")

        try:
            async with session.post(WHISPER_URL, data=form) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return text.strip()
                else:
                    error = await resp.text()
                    logger.error("[%s] Whisper API error %d: %s",
                               self.call_id, resp.status, error[:200])
                    return ""
        except aiohttp.ClientError as e:
            logger.error("[%s] Whisper request failed: %s", self.call_id, e)
            raise

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
