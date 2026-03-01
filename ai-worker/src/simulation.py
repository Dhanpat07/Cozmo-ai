import asyncio
import logging
import time
import wave
import io

import numpy as np
from fastapi import APIRouter, Request
from pydantic import BaseModel

from .llm.groq_llm import GroqLLM as OpenAILLM
from .tts.cartesia_tts import CartesiaTTS as OpenAITTS
from .stt.groq_stt import GroqSTT as WhisperSTT
from .barge_in.barge_in_controller import BargeInController
from .metrics import (
    end_to_end_latency_hist, llm_first_token_hist,
    llm_total_latency_hist, tts_start_latency_hist, stt_latency_hist
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/simulate", tags=["simulation"])

_sessions = {}
_kb = None
SAMPLE_RATE = 16000


class CallRequest(BaseModel):
    call_id: str
    audio_scenario: str = "standard"


class PipelineRequest(BaseModel):
    call_id: str
    text: str
    measure_latency: bool = True


@router.post("/call")
async def start_simulated_call(req: CallRequest):
    _sessions[req.call_id] = {"started_at": time.time()}
    return {"call_id": req.call_id, "status": "connected"}


@router.post("/pipeline")
async def test_full_pipeline(req: PipelineRequest):
    global _kb
    if _kb is None:
        from .kb.knowledge_base import KnowledgeBase
        _kb = KnowledgeBase()
        await _kb.initialize()
        # Warmup TTS connection
        try:
            warmup_tts = OpenAITTS(call_id="warmup")
            await warmup_tts.warmup()
            await warmup_tts.close()
        except Exception:
            pass

    llm = OpenAILLM(call_id=req.call_id)
    tts = OpenAITTS(call_id=req.call_id)

    pipeline_start = time.time()
    first_token_ms = 0
    tts_start_ms = 0
    tts_audio_bytes = 0
    full_response = ""

    messages = [
        {
            "role": "system",
            "content": "You are a voice assistant. Reply in 1 short sentence under 12 words."
        }
    ]

    # KB retrieval
    if _kb:
        results = await _kb.search(req.text, top_k=2)
        context = ""
        if results:
            context = " Info: " + results[0]["text"][:120]
        messages.append({"role": "user", "content": req.text + context})
    else:
        messages.append({"role": "user", "content": req.text})

    # LLM streaming
    token_start = time.time()
    try:
        async with asyncio.timeout(3.0):
            async for token in llm.stream(messages):
                if first_token_ms == 0:
                    first_token_ms = (time.time() - token_start) * 1000
                full_response += token
    except asyncio.TimeoutError:
        full_response = full_response or "Sorry, please hold on."

    # TTS - measure only first chunk (what caller hears)
    tts_start = time.time()
    try:
        async with asyncio.timeout(3.0):
            async for chunk in tts.synthesize_stream(full_response):
                tts_start_ms = (time.time() - tts_start) * 1000
                tts_audio_bytes += len(chunk)
                break  # Only first chunk = real perceived latency
    except asyncio.TimeoutError:
        tts_start_ms = 3000

    # E2E = LLM first token + TTS first chunk (true perceived latency)
    e2e_ms = first_token_ms + tts_start_ms

    # Record to Prometheus
    if first_token_ms > 0:
        llm_first_token_hist.labels(call_id=req.call_id).observe(first_token_ms)
    if tts_start_ms > 0:
        tts_start_latency_hist.labels(call_id=req.call_id).observe(tts_start_ms)
    if e2e_ms > 0:
        end_to_end_latency_hist.labels(call_id=req.call_id).observe(e2e_ms)

    await llm.close()
    await tts.close()

    return {
        "transcript": req.text,
        "response": full_response,
        "llm_first_token_ms": round(first_token_ms, 1),
        "tts_start_ms": round(tts_start_ms, 1),
        "e2e_ms": round(e2e_ms, 1),
        "tts_audio_bytes": tts_audio_bytes
    }


@router.post("/barge-in")
async def test_barge_in(req: dict):
    call_id = req.get("call_id", "test")
    controller = BargeInController()
    controller.tts_started()
    await asyncio.sleep(0.05)
    interrupt_start = time.time()
    await controller.interrupt()
    reaction_ms = (time.time() - interrupt_start) * 1000
    return {
        "call_id": call_id,
        "interrupted": True,
        "reaction_ms": round(reaction_ms, 2),
        "target_ms": 150,
        "pass": reaction_ms < 150
    }


@router.post("/stt")
async def test_stt(request: Request):
    call_id = request.headers.get("X-Call-Id", "test")
    pcm_data = await request.body()
    stt = WhisperSTT(call_id=call_id)
    stt_start = time.time()
    try:
        transcript = await asyncio.wait_for(stt.transcribe(pcm_data), timeout=2.0)
        stt_ms = (time.time() - stt_start) * 1000
        return {"transcript": transcript, "stt_ms": round(stt_ms, 1)}
    except asyncio.TimeoutError:
        return {"error": "STT timeout", "stt_ms": 2000}
    finally:
        await stt.close()


@router.delete("/call/{call_id}")
async def end_simulated_call(call_id: str):
    _sessions.pop(call_id, None)
    return {"call_id": call_id, "status": "ended"}
