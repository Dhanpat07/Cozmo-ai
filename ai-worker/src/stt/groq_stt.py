import asyncio
import io
import logging
import os
import wave
import aiohttp

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
SAMPLE_RATE = 16000


class GroqSTT:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                timeout=aiohttp.ClientTimeout(total=2.0)
            )
        return self._session

    def _pcm_to_wav(self, pcm_bytes: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm_bytes)
        return buf.getvalue()

    async def transcribe(self, pcm_audio: bytes) -> str:
        wav_data = self._pcm_to_wav(pcm_audio)
        session = await self._get_session()
        form = aiohttp.FormData()
        form.add_field("file", wav_data, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", "whisper-large-v3-turbo")
        form.add_field("language", "en")
        form.add_field("response_format", "text")
        async with session.post(GROQ_STT_URL, data=form) as resp:
            if resp.status == 200:
                return (await resp.text()).strip()
            return ""

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
