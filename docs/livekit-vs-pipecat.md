# LiveKit vs Pipecat: Trade-Off Analysis

## TL;DR

| | LiveKit | Pipecat |
|---|---|---|
| **Best for** | Production, scale, SIP/PSTN | Rapid prototyping, experimentation |
| **Learning curve** | Medium | Low |
| **Operational overhead** | High (infra to manage) | Low (runs in-process) |
| **Scalability ceiling** | ~10,000+ concurrent | ~50-100 concurrent |
| **SIP/PSTN support** | Native, first-class | Via LiveKit or Twilio plugin |
| **Barge-in support** | Requires custom impl | Built-in pipeline control |
| **Vendor lock-in** | Low (self-hostable) | Low (framework, bring your own APIs) |

---

## LiveKit

### What It Is
LiveKit is a real-time communication infrastructure platform (WebRTC SFU) that has added an AI agents layer. The core value is the battle-tested WebRTC stack, not the AI pipeline.

### Pros
- **Production-grade WebRTC**: DTLS, SRTP, ICE, TURN — all handled. This is extremely hard to build.
- **SIP/PSTN native**: First-class SIP trunk support with Twilio, Vonage, Bandwidth. Essential for PSTN calls.
- **Horizontal scaling**: Cluster mode with Redis. Proven to 10,000+ concurrent WebRTC sessions.
- **Self-hostable**: You own the infra. No per-minute pricing surprises.
- **Ecosystem**: Active community, good SDKs (Python, Go, Node, iOS, Android).
- **Observability built-in**: WebRTC stats (jitter, packet loss, MOS) exposed out of the box.

### Cons
- **Operational complexity**: Running a LiveKit cluster requires Kubernetes expertise, proper UDP firewall rules, TURN servers, certificates. This is not trivial.
- **AI pipeline is young**: The `livekit-agents` SDK (for AI workers) is relatively new (2024). Less documented, more breaking changes.
- **Barge-in is DIY**: No built-in support. Must implement VAD monitoring + track interruption yourself.
- **Infrastructure cost**: Self-hosted cluster adds ~$1,500-3,000/month vs a SaaS option.
- **Cold start latency**: Worker discovery + room join can add 200-500ms to initial call setup.

### When to Use LiveKit
- Building for >50 concurrent calls
- Need PSTN/SIP integration
- Require full control over infrastructure (compliance, data residency)
- Team has WebRTC/infrastructure experience
- Building the product for the long term

---

## Pipecat

### What It Is
Pipecat (by Daily.co) is an open-source framework for building voice AI pipelines. It provides a frame-based processing pipeline where each stage (STT, LLM, TTS) is a composable processor.

### Pros
- **Developer experience**: Fastest path from idea to working voice AI. 50 lines of code for a basic agent.
- **Built-in pipeline primitives**: Barge-in, turn detection, and interruption are first-class features.
- **Composable**: Swap STT providers, LLM providers, TTS providers with one-line changes.
- **Excellent for prototyping**: Can demo a working voice AI in hours, not days.
- **Active development**: Daily.co is heavily invested, frequent releases.
- **WebSocket/WebRTC transport**: Works with Daily.co rooms natively; can connect to LiveKit as transport.

### Cons
- **Single-process model**: Pipecat pipelines run in a single Python process. No built-in clustering.
- **Scaling requires custom work**: To handle 100 calls, you need to orchestrate 100 Pipecat processes yourself — there's no native cluster mode.
- **No native SIP**: Must use Twilio Media Streams or plug into LiveKit for PSTN. This adds latency.
- **Daily.co dependency risk**: While open-source, the ecosystem is strongly tied to Daily.co's cloud.
- **Latency overhead**: The Python frame pipeline adds ~10-30ms overhead vs a custom async pipeline.
- **Memory per process**: Each call needs its own process or async task with the full pipeline loaded.

### When to Use Pipecat
- Prototyping or building an MVP
- <50 concurrent calls
- Team is Python-native and wants fast iteration
- Building a custom voice AI feature in an existing product
- Experimenting with different STT/LLM/TTS combinations

---

## The Choice for This Project

**We chose LiveKit** because:
1. The requirement is 100 concurrent PSTN calls — LiveKit's SIP support is necessary
2. Production-grade WebRTC scaling is non-negotiable for PSTN reliability
3. Self-hosting gives us observability and control over the data plane

**What we borrowed from Pipecat's design**:
- Frame-based per-call pipeline (our `CallPipeline` class)
- Sentence-boundary TTS streaming (inspired by Pipecat's aggregators)
- Clean separation of VAD → STT → LLM → TTS concerns

**If the requirement were <20 calls or a rapid prototype**: Use Pipecat with Daily.co transport. Ship in a day, optimize later.
