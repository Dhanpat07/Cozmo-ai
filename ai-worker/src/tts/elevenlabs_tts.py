import logging
import os
from typing import AsyncIterator
import aiohttp

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel - fast, clear voice
URL = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"


class ElevenLabsTTS:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "xi-api-key": ELEVENLABS_API_KEY,
                    "Content-Type": "application/json"
                }
            )
        return self._session

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        session = await self._get_session()
        payload = {
            "text": text,
            "model_id": "eleven_flash_v2_5",  # Fastest model - 75ms
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": 1.1
            },
            "output_format": "pcm_16000"  # Raw PCM, no decode needed
        }
        params = {"output_format": "pcm_16000", "optimize_streaming_latency": 4}
        try:
            async with session.post(URL, json=payload, params=params,
                timeout=aiohttp.ClientTimeout(total=10, connect=1)) as resp:
                if resp.status == 200:
                    async for chunk in resp.content.iter_chunked(4096):
                        if chunk:
                            yield chunk
                else:
                    error = await resp.text()
                    logger.error("[%s] ElevenLabs error %d: %s", self.call_id, resp.status, error[:100])
        except Exception as e:
            logger.error("[%s] ElevenLabs TTS failed: %s", self.call_id, e)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
