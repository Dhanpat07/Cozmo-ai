 LiveKit vs Pipecat: Trade-Off Analysis

## TL;DR

| | LiveKit | Pipecat |
|---|---|---|
| **Best for** | Production, scale, SIP/PSTN | Rapid prototyping, experimentation |
| **Learning curve** | Medium | Low |
| **Operational overhead** | High (infra to manage) | Low (runs in-process) |
| **Scalability ceiling** | 10,000+ concurrent | 50-100 concurrent |
| **SIP/PSTN support** | Native, first-class | Via LiveKit or Twilio plugin |
| **Barge-in support** | Requires custom impl | Built-in pipeline control |
| **Vendor lock-in** | Low (self-hostable) | Low (framework, bring your own APIs) |

---

## LiveKit

### What It Is
LiveKit is a real-time communication infrastructure platform (WebRTC SFU) that has added an AI agents layer. The core value is the battle-tested WebRTC stack, not the AI pipeline.

### Pros
- **Production-grade WebRTC**: DTLS, SRTP, ICE, TURN all handled. This is extremely hard to build correctly.
- **SIP/PSTN native**: First-class SIP trunk support with Twilio, Vonage, Bandwidth. Essential for PSTN calls.
- **Horizontal scaling**: Cluster mode with Redis. Proven to 10,000+ concurrent WebRTC sessions.
- **Self-hostable**: You own the infra. No per-minute pricing surprises.
- **Observability built-in**: WebRTC stats (jitter, packet loss, MOS) exposed out of the box.

### Cons
- **Operational complexity**: Running a LiveKit cluster requires Kubernetes expertise, proper UDP firewall rules, TURN servers, and certificates.
- **AI pipeline is young**: The livekit-agents SDK is relatively new (2024). Less documented, more breaking changes.
- **Barge-in is DIY**: No built-in support. Must implement VAD monitoring and track interruption yourself.
- **Infrastructure cost**: Self-hosted cluster adds approximately $1,500-3,000/month vs a SaaS option.
- **Cold start latency**: Worker discovery and room join can add 200-500ms to initial call setup.

### When to Use LiveKit
- Building for more than 50 concurrent calls
- Need PSTN/SIP integration
- Require full control over infrastructure (compliance, data residency)
- Team has WebRTC/infrastructure experience
- Building for the long term

---

## Pipecat

### What It Is
Pipecat (by Daily.co) is an open-source framework for building voice AI pipelines. It provides a frame-based processing pipeline where each stage (STT, LLM, TTS) is a composable processor.

### Pros
- **Developer experience**: Fastest path from idea to working voice AI. 50 lines of code for a basic agent.
- **Built-in pipeline primitives**: Barge-in, turn detection, and interruption are first-class features.
- **Composable**: Swap STT, LLM, TTS providers with one-line changes.
- **Excellent for prototyping**: Can demo a working voice AI in hours, not days.
- **Active development**: Daily.co is heavily invested with frequent releases.

### Cons
- **Single-process model**: Pipecat pipelines run in a single Python process. No built-in clustering.
- **Scaling requires custom work**: To handle 100 calls, you need to orchestrate 100 Pipecat processes yourself.
- **No native SIP**: Must use Twilio Media Streams or plug into LiveKit for PSTN. This adds latency.
- **Daily.co dependency risk**: While open-source, the ecosystem is strongly tied to Daily.co's cloud.
- **Latency overhead**: The Python frame pipeline adds 10-30ms overhead vs a custom async pipeline.

### When to Use Pipecat
- Prototyping or building an MVP
- Fewer than 50 concurrent calls
- Team is Python-native and wants fast iteration
- Building a custom voice AI feature in an existing product
- Experimenting with different STT/LLM/TTS combinations

---

## The Choice for This Project

**We chose LiveKit** because:
1. The requirement is 100 concurrent PSTN calls — LiveKit's SIP support is necessary
2. Production-grade WebRTC scaling is non-negotiable for PSTN reliability
3. Self-hosting gives full observability and control over the data plane

**What we borrowed from Pipecat's design:**
- Frame-based per-call pipeline (our CallPipeline class)
- Sentence-boundary TTS streaming
- Clean separation of VAD → STT → LLM → TTS concerns

**If the requirement were fewer than 20 calls or a rapid prototype**: Use Pipecat with Daily.co transport. Ship in a day, optimize later.

---
---

# 1-Pager: Scaling to 1,000 Calls

## What Would Break at 1,000 Calls?

### 1. Single LiveKit Node
Our current setup runs one LiveKit server. At 1,000 concurrent calls, a single node would saturate CPU and memory handling WebRTC negotiation, audio forwarding, and SIP signaling simultaneously.

**Fix:** Deploy 3-5 LiveKit nodes in cluster mode, all sharing the same Redis instance. Use DNS round-robin or a Layer 4 load balancer (AWS NLB) in front. LiveKit's cluster mode handles room distribution automatically.

### 2. AI Worker Count
Each worker handles 8 calls. 1,000 calls requires 125 workers. Docker Compose can technically scale this far but becomes unmanageable — no health-based routing, no auto-recovery, no rolling deploys.

**Fix:** Move to Kubernetes with HPA (Horizontal Pod Autoscaler) configured to scale on active call count or CPU utilization. Set min=5, max=130 replicas with a target of 60% CPU utilization per pod.

### 3. External API Rate Limits
At 1,000 concurrent calls, Groq and Cartesia API rate limits become the hard ceiling. Groq's free tier allows roughly 30 requests/minute. Even production tiers have limits that 1,000 simultaneous STT+LLM calls would breach.

**Fix:** Upgrade to Groq and Cartesia enterprise API tiers. Implement exponential backoff with jitter. Add a self-hosted Whisper.cpp instance as STT fallback for burst traffic.

### 4. Redis Single Node
Redis holds LiveKit cluster state and our call session data. A single Redis node becomes both a single point of failure and a throughput bottleneck under 1,000 concurrent writes.

**Fix:** Redis Cluster with 3 primary + 3 replica nodes. Use Redis Sentinel for automatic failover. Shard session data by call_id prefix.

### 5. Prometheus Cardinality Explosion
Our current metrics use call_id as a label. At 1,000 calls/minute, this creates millions of unique time series and will OOM the Prometheus server within hours.

**Fix:** Remove call_id from all Prometheus labels. Aggregate metrics at the worker level only (instance label). Use Thanos or VictoriaMetrics for long-term storage and downsampling.

---

## Where Is the Latency Bottleneck Today?

Based on measured results from the load test (100 calls, 10 concurrent):

| Stage | Measured avg | Measured P95 | Share of E2E |
|-------|-------------|-------------|--------------|
| STT Groq Whisper | 110ms | 212ms | 32% |
| LLM first token | 128ms | 154ms | 37% |
| TTS Cartesia | 166ms | 179ms | 48% |
| Network and overhead | ~50ms | ~50ms | 14% |
| **Total E2E** | **173ms** | **297ms** | - |

**TTS is the biggest bottleneck** at 166ms avg and 48% of E2E time. This is the delay from LLM first token to first audio chunk reaching the caller. Cartesia Sonic-Turbo is already one of the fastest TTS providers globally, but sentence-boundary buffering adds unavoidable latency.

**STT at P95 spikes to 212ms** — this is network variance to Groq's API. From India, round-trip variance to US-based API endpoints causes occasional 200-300ms STT responses even when average is 110ms.

### How to Reduce Latency Further

**TTS (biggest win):**
- Stream TTS at word level instead of sentence level — reduces first-chunk latency by ~40ms
- Pre-generate and cache common responses (greetings, hold messages, FAQs) as audio files
- Use Cartesia's WebSocket streaming API directly instead of HTTP for lower connection overhead

**STT (biggest P95 improvement):**
- Deploy self-hosted Whisper.cpp on the same machine as the AI workers — eliminates network hop entirely, gives consistent 50-80ms STT with zero variance
- Use Groq's streaming transcription endpoint for real-time partial results

**LLM:**
- Already at 128ms with Groq's LPU hardware — this is near the floor for a cloud API
- For further reduction: deploy a quantized llama-3.1-8b locally on GPU (A10G or better)

---

## Summary Table

| Problem at 1,000 calls | Severity | Fix |
|------------------------|----------|-----|
| Single LiveKit node saturation | Critical | LiveKit cluster 3-5 nodes |
| 125 workers unmanageable | Critical | Kubernetes + HPA |
| API rate limits | Critical | Enterprise tiers + self-hosted fallback |
| Redis single point of failure | High | Redis Cluster + Sentinel |
| Prometheus cardinality explosion | High | Remove call_id labels, use Thanos |
| TTS latency bottleneck | Medium | Word-level streaming + audio caching |
| STT P95 spikes | Medium | Self-hosted Whisper.cpp |