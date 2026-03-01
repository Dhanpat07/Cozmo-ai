"""
LLM using OpenAI Chat Completions API with streaming
First-token latency target: <150ms
"""
import asyncio
import json
import logging
import os
from typing import AsyncIterator

import aiohttp

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")  # Fast + cheap for voice


class OpenAILLM:
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

    async def stream(self, messages: list) -> AsyncIterator[str]:
        """
        Stream completion tokens.
        Yields individual tokens as they arrive.
        Raises asyncio.TimeoutError after 2 seconds total.
        """
        session = await self._get_session()

        payload = {
            "model": LLM_MODEL,
            "messages": messages,
            "stream": True,
            "max_tokens": 150,       # Keep responses short for voice
            "temperature": 0.7,
            "stream_options": {"include_usage": False}
        }

        try:
            async with session.post(
                OPENAI_CHAT_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10, connect=1)
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error("[%s] LLM API error %d: %s",
                               self.call_id, resp.status, error[:200])
                    return

                async for line in resp.content:
                    line = line.decode("utf-8").strip()
                    if not line or not line.startswith("data: "):
                        continue

                    data = line[6:]  # Remove "data: " prefix
                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

        except aiohttp.ClientError as e:
            logger.error("[%s] LLM request failed: %s", self.call_id, e)
            raise

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
