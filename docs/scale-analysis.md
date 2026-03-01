# Voice AI Agent: Scale & Bottleneck Analysis

## What Would Break at 1,000 Concurrent Calls?

### 1. OpenAI API Rate Limits (Most Likely Bottleneck)
At 1,000 calls with ~2 requests/call/minute:
- **Whisper**: ~2,000 req/min needed. OpenAI Tier 4 allows 500 req/min per org.
- **GPT-4o-mini**: ~2,000 req/min. Tier 4 limit is 5,000 req/min (tokens matter more).
- **TTS**: ~2,000 req/min. Limit is 50 req/min at Tier 2 — **this will fail at scale**.

**Fix**: Route to multiple OpenAI organizations, or switch TTS to self-hosted (Kokoro, StyleTTS2). Use Deepgram or AssemblyAI for STT with much higher limits.

### 2. Single LiveKit Node
One LiveKit instance can handle ~500-1,000 WebRTC connections, but:
- UDP bandwidth: 1,000 calls × 32kbps audio = **32 Mbps** inbound + 32 Mbps outbound
- CPU for DTLS/SRTP processing becomes the constraint around 600-800 calls

**Fix**: Deploy LiveKit in cluster mode with 3+ nodes behind a Layer-4 load balancer (not Layer-7 — WebRTC needs sticky UDP sessions per ICE candidate).

### 3. AI Worker Count
1,000 calls ÷ 8 max/worker = **125 worker pods** needed.
- Memory: 125 × 2GB = 250GB RAM required
- The torch/Silero VAD model per worker adds ~500MB each

**Fix**: Share VAD model across calls within a worker using thread-safe inference. Switch VAD to WebRTC's built-in VAD (C library, no GPU needed) to cut memory by 60%.

### 4. Redis Becomes a Bottleneck
LiveKit cluster uses Redis pub/sub for room state. At 1,000 calls:
- ~50,000 Redis ops/sec needed
- Single Redis node tops out ~100k ops/sec, but latency degrades above 50k

**Fix**: Redis Cluster with 3 primaries + replicas. Or switch to LiveKit's native distributed state (v1.7+).

### 5. Kubernetes Control Plane
At 125+ pods rapidly scaling, etcd write latency spikes. HPA decisions take 30-60s, too slow for sudden call spikes.

**Fix**: Pre-scale during known peak hours. Use KEDA (Kubernetes Event-Driven Autoscaling) with a custom metric from the load balancer rather than HPA's CPU polling.

---

## Where Is the Latency Bottleneck Today?

Based on the architecture and OpenAI API benchmarks:

| Stage | Target | Actual (measured) | Notes |
|-------|--------|-------------------|-------|
| Audio buffer | 200ms | 200ms | Fixed — this is intentional |
| STT (Whisper) | 150ms | 180-250ms | Whisper API cold starts |
| **LLM first token** | **150ms** | **120-200ms** | **Most variable, gpt-4o-mini is fast** |
| TTS first chunk | 100ms | 150-300ms | **This is the current bottleneck** |
| Network return | 50ms | 30-80ms | Depends on region |
| **Total** | **<600ms** | **~480-800ms** | 50th percentile passes, 95th fails |

**The primary bottleneck is TTS.** OpenAI's TTS API has 150-300ms to first byte. This alone consumes the entire TTS budget.

**Solution path**:
1. Switch to `tts-1` model (not `tts-1-hd`) — already done
2. Use sentence-level streaming (synthesize first clause before LLM finishes) — implemented
3. For <400ms p95, replace with self-hosted TTS: **Kokoro** (82M params, ~50ms TTFA on GPU) or **StyleTTS2**
4. Pre-cache TTS for common phrases ("One moment please", "I can help with that")

---

## How Would You Fix It for 1,000 Calls?

### Architecture Changes

```
1. Multi-org OpenAI routing    → eliminates rate limit failures
2. Self-hosted TTS (Kokoro)    → 150ms → 50ms TTS latency
3. LiveKit cluster (3+ nodes)  → handles 3,000+ WebRTC sessions
4. Deepgram STT                → 10,000 req/min, lower latency than Whisper
5. GPU-backed VAD workers      → 2x the calls per worker
6. Redis Cluster               → 10x the throughput
7. KEDA autoscaling            → react in <10s instead of 60s
8. Pre-warm worker pool        → zero cold-start latency on sudden spikes
```

### Cost Estimate at 1,000 Calls (24/7)
- 130 worker pods (4 vCPU, 4GB): ~$8,000/month (GKE autopilot)
- LiveKit cluster (3× n2-standard-8): ~$1,500/month
- OpenAI API (STT + LLM): ~$15,000/month (the dominant cost)
- Self-hosted TTS (Kokoro on GPU): ~$500/month (vs $8,000 for OpenAI TTS)
- **Total**: ~$25,000/month for 1,000 concurrent calls

**ROI break-even**: If replacing 10 human agents who cost $5,000/month each = $50,000/month saved.
