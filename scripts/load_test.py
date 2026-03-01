#!/usr/bin/env python3
"""
Load Test Script - Simulates 100 concurrent voice calls
Measures end-to-end latency and reports statistics

Usage:
    python scripts/load_test.py --calls 100 --duration 60
"""
import asyncio
import argparse
import statistics
import time
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class CallResult:
    call_id: str
    start_time: float
    setup_time_ms: float = 0
    stt_latency_ms: float = 0
    llm_first_token_ms: float = 0
    tts_start_ms: float = 0
    e2e_latency_ms: float = 0
    error: str = ""
    barge_in_tested: bool = False
    barge_in_reaction_ms: float = 0


async def simulate_single_call(call_id: str, worker_url: str) -> CallResult:
    """
    Simulate a single PSTN call through the full pipeline.
    Tests: setup, STT, LLM, TTS, and barge-in.
    """
    import aiohttp

    result = CallResult(call_id=call_id, start_time=time.time())

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Initiate call (simulate LiveKit room join)
            setup_start = time.time()

            async with session.post(
                f"{worker_url}/simulate/call",
                json={"call_id": call_id, "audio_scenario": "standard"},
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    result.error = f"Call setup failed: {resp.status}"
                    return result

                data = await resp.json()
                result.setup_time_ms = (time.time() - setup_start) * 1000

            # 2. Send audio (simulated STT request)
            stt_start = time.time()

            # Generate synthetic 300ms of speech (4800 samples @ 16kHz)
            synthetic_audio = np.random.randint(-1000, 1000, 4800, dtype=np.int16).tobytes()

            async with session.post(
                f"{worker_url}/simulate/stt",
                data=synthetic_audio,
                headers={"Content-Type": "audio/pcm", "X-Call-Id": call_id},
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result.stt_latency_ms = data.get("stt_ms", (time.time() - stt_start) * 1000)

            # 3. Full pipeline (STT → LLM → TTS)
            e2e_start = time.time()

            async with session.post(
                f"{worker_url}/simulate/pipeline",
                json={
                    "call_id": call_id,
                    "text": "What is your refund policy?",
                    "measure_latency": True
                },
                timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result.llm_first_token_ms = data.get("llm_first_token_ms", 0)
                    result.tts_start_ms = data.get("tts_start_ms", 0)
                    result.e2e_latency_ms = data.get("e2e_ms", (time.time() - e2e_start) * 1000)

            # 4. Test barge-in
            barge_in_start = time.time()
            async with session.post(
                f"{worker_url}/simulate/barge-in",
                json={"call_id": call_id},
                timeout=aiohttp.ClientTimeout(total=2)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result.barge_in_tested = True
                    result.barge_in_reaction_ms = data.get(
                        "reaction_ms",
                        (time.time() - barge_in_start) * 1000
                    )

            # End call
            await session.delete(
                f"{worker_url}/simulate/call/{call_id}",
                timeout=aiohttp.ClientTimeout(total=2)
            )

    except asyncio.TimeoutError:
        result.error = "timeout"
    except Exception as e:
        result.error = str(e)

    return result


def print_stats(results: List[CallResult]):
    """Print latency statistics"""
    successful = [r for r in results if not r.error]
    failed = [r for r in results if r.error]

    print("\n" + "="*60)
    print(f"LOAD TEST RESULTS - {len(results)} calls")
    print("="*60)
    print(f"✅ Successful: {len(successful)}")
    print(f"❌ Failed: {len(failed)}")
    if failed:
        error_types = {}
        for r in failed:
            error_types[r.error] = error_types.get(r.error, 0) + 1
        for err, count in error_types.items():
            print(f"   - {err}: {count}")

    if not successful:
        print("No successful calls to analyze.")
        return

    def stats(values, label, unit="ms"):
        if not values:
            return
        p50 = statistics.median(values)
        p95 = np.percentile(values, 95)
        p99 = np.percentile(values, 99)
        avg = statistics.mean(values)
        print(f"\n{label}:")
        print(f"  avg={avg:.0f}{unit}  p50={p50:.0f}{unit}  p95={p95:.0f}{unit}  p99={p99:.0f}{unit}")

    print("\n── Latency Breakdown ────────────────────────────────")
    stats([r.setup_time_ms for r in successful], "📞 Call Setup")
    stats([r.stt_latency_ms for r in successful if r.stt_latency_ms > 0], "🎤 STT Latency")
    stats([r.llm_first_token_ms for r in successful if r.llm_first_token_ms > 0], "🧠 LLM First Token")
    stats([r.tts_start_ms for r in successful if r.tts_start_ms > 0], "🔊 TTS Start")
    stats([r.e2e_latency_ms for r in successful if r.e2e_latency_ms > 0], "⚡ END-TO-END TOTAL")

    e2e_values = [r.e2e_latency_ms for r in successful if r.e2e_latency_ms > 0]
    if e2e_values:
        avg_e2e = statistics.mean(e2e_values)
        p95_e2e = np.percentile(e2e_values, 95)
        target = 600
        status = "✅ PASS" if avg_e2e < target else "❌ FAIL"
        print(f"\n── Target: <{target}ms avg e2e ───────────────────────")
        print(f"Result: avg={avg_e2e:.0f}ms  p95={p95_e2e:.0f}ms  {status}")

    barge_in_results = [r for r in successful if r.barge_in_tested]
    if barge_in_results:
        bi_values = [r.barge_in_reaction_ms for r in barge_in_results]
        print(f"\n── Barge-In Reaction ({len(barge_in_results)} tests) ─────────────")
        print(f"  avg={statistics.mean(bi_values):.0f}ms  "
              f"max={max(bi_values):.0f}ms  "
              f"target=<150ms")

    print("\n" + "="*60)


async def main(num_calls: int, worker_url: str, concurrency: int, ramp_up_secs: int):
    """Run load test with configurable concurrency"""
    logger.info("Starting load test: %d calls, %d concurrent, ramp=%ds",
               num_calls, concurrency, ramp_up_secs)

    semaphore = asyncio.Semaphore(concurrency)

    async def rate_limited_call(call_id: str):
        async with semaphore:
            return await simulate_single_call(call_id, worker_url)

    # Generate calls with ramp-up
    tasks = []
    for i in range(num_calls):
        call_id = f"load-test-{i:04d}"
        task = asyncio.create_task(rate_limited_call(call_id))
        tasks.append(task)

        # Ramp up - add slight delay between call starts
        if ramp_up_secs > 0:
            await asyncio.sleep(ramp_up_secs / num_calls)

    # Wait for all calls
    results = await asyncio.gather(*tasks, return_exceptions=False)
    print_stats(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Voice AI Load Test")
    parser.add_argument("--calls", type=int, default=100, help="Number of calls to simulate")
    parser.add_argument("--url", default="http://localhost:8080", help="Worker URL")
    parser.add_argument("--concurrency", type=int, default=20, help="Max concurrent calls")
    parser.add_argument("--ramp", type=int, default=10, help="Ramp-up time in seconds")
    args = parser.parse_args()

    asyncio.run(main(args.calls, args.url, args.concurrency, args.ramp))
