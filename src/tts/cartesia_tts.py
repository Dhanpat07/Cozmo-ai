import logging
import os
from typing import AsyncIterator
import aiohttp

logger = logging.getLogger(__name__)

CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
URL = "https://api.cartesia.ai/tts/bytes"
VOICE_ID = "f786b574-daa5-4673-aa0c-cbe3e8534c02"  # Katie - optimized for voice agents


class CartesiaTTS:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "X-API-Key": CARTESIA_API_KEY,
                "Cartesia-Version": "2025-04-16",
                "Content-Type": "application/json"
            })
        return self._session

    async def warmup(self):
        """Pre-warm connection to reduce first-request latency"""
        try:
            async for _ in self.synthesize_stream("Hello"):
                break
        except Exception:
            pass

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        # Use only first sentence to keep TTS fast
        text = text.split(".")[0].strip()
        if not text:
            return
        session = await self._get_session()
        payload = {
            "model_id": "sonic-turbo",
            "transcript": text,
            "voice": {"mode": "id", "id": VOICE_ID},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 16000
            },
            "language": "en"
        }
        try:
            async with session.post(URL, json=payload,
                timeout=aiohttp.ClientTimeout(total=5, connect=1)) as resp:
                if resp.status == 200:
                    async for chunk in resp.content.iter_chunked(4096):
                        if chunk:
                            yield chunk
                elif resp.status == 422:
                    # sonic-turbo not available, fallback to sonic-3
                    payload["model_id"] = "sonic-3"
                    async with session.post(URL, json=payload,
                        timeout=aiohttp.ClientTimeout(total=5, connect=1)) as resp2:
                        async for chunk in resp2.content.iter_chunked(4096):
                            if chunk:
                                yield chunk
                else:
                    error = await resp.text()
                    logger.error("[%s] Cartesia error %d: %s", self.call_id, resp.status, error[:200])
        except Exception as e:
            logger.error("[%s] Cartesia TTS failed: %s", self.call_id, e)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
