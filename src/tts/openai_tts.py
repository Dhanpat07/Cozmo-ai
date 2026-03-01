"""
Text-to-Speech using OpenAI TTS API
Streaming synthesis, interruptible
Target: <150ms to first audio chunk
"""
import asyncio
import logging
import os
from typing import AsyncIterator

import aiohttp

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
TTS_MODEL = "tts-1"          # Low latency model (vs tts-1-hd)
TTS_VOICE = os.getenv("TTS_VOICE", "alloy")
TTS_SPEED = float(os.getenv("TTS_SPEED", "1.0"))
CHUNK_SIZE = 4096            # Stream in 4KB chunks


class OpenAITTS:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                }
            )
        return self._session

    async def synthesize_stream(self, text: str) -> AsyncIterator[bytes]:
        """
        Stream TTS audio chunks.
        Yields raw PCM/MP3 audio chunks as they arrive from the API.
        Caller should stop consuming if barge-in detected.
        """
        if not text or not text.strip():
            return

        session = await self._get_session()

        payload = {
            "model": TTS_MODEL,
            "input": text,
            "voice": TTS_VOICE,
            "response_format": "pcm",  # Raw PCM for lowest latency
            "speed": TTS_SPEED
        }

        try:
            async with session.post(
                OPENAI_TTS_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30, connect=1)
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error("[%s] TTS API error %d: %s",
                               self.call_id, resp.status, error[:200])
                    return

                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    if chunk:
                        yield chunk

        except aiohttp.ClientError as e:
            logger.error("[%s] TTS request failed: %s", self.call_id, e)

    async def synthesize_text_stream(self, text_stream: AsyncIterator[str]) -> AsyncIterator[bytes]:
        """
        Consume streaming text tokens and produce audio chunks.
        Batches tokens into sentences before synthesizing.
        """
        sentence_buffer = ""
        sentence_enders = {'.', '!', '?', ','}  # Commas also trigger synthesis for low latency

        async for token in text_stream:
            sentence_buffer += token

            # Check if we have a complete phrase to synthesize
            if any(sentence_buffer.rstrip().endswith(c) for c in sentence_enders):
                if len(sentence_buffer.strip()) > 3:
                    async for chunk in self.synthesize_stream(sentence_buffer):
                        yield chunk
                    sentence_buffer = ""

        # Synthesize any remaining text
        if sentence_buffer.strip():
            async for chunk in self.synthesize_stream(sentence_buffer):
                yield chunk

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
