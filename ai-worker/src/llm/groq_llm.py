import asyncio
import json
import logging
import os
from typing import AsyncIterator

import aiohttp

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
LLM_MODEL = "llama-3.1-8b-instant"  # Fastest Groq model


class GroqLLM:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                }
            )
        return self._session

    async def stream(self, messages: list) -> AsyncIterator[str]:
        session = await self._get_session()
        payload = {
            "model": LLM_MODEL,
            "messages": messages,
            "stream": True,
            "max_tokens": 30,
            "temperature": 0.7
        }
        async with session.post(
            GROQ_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10, connect=1)
        ) as resp:
            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    content = chunk["choices"][0].get("delta", {}).get("content")
                    if content:
                        yield content
                except Exception:
                    continue

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
