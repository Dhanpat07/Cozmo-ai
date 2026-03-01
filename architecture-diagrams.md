# Voice AI Agent - System Architecture Diagrams

---

## Diagram 1: High-Level System Architecture

```mermaid
graph TB
    subgraph PSTN["PSTN / Telephony Layer"]
        CALLER[Caller Phone]
        TWILIO[Twilio SIP Trunk]
    end

    subgraph MEDIA["Real-Time Media Layer"]
        LK[LiveKit Server\nSIP to WebRTC SFU]
        REDIS[(Redis\nCluster State)]
        LK --> REDIS
        REDIS --> LK
    end

    subgraph WORKERS["AI Worker Pool - Stateless, Scalable"]
        LB[Load Balancer]
        W1[AI Worker 1\n8 calls max]
        W2[AI Worker 2\n8 calls max]
        WN[AI Worker N\n8 calls max]
        LB --> W1
        LB --> W2
        LB --> WN
    end

    subgraph PIPELINE["Per-Call AI Pipeline"]
        VAD[VAD Silero\n20ms frames]
        STT[STT Groq Whisper\n110ms avg]
        KB[FAISS KB Search\ntop-3 results]
        LLM[LLM Groq llama-3.1-8b\n128ms avg]
        TTS[TTS Cartesia Sonic-Turbo\n166ms avg]
        BI[Barge-In Monitor\n0.2ms reaction]
        VAD --> STT
        STT --> KB
        KB --> LLM
        LLM --> TTS
        TTS --> BI
        BI --> VAD
    end

    subgraph OBS["Observability"]
        PROM[Prometheus]
        GRAF[Grafana Dashboard]
        PROM --> GRAF
    end

    CALLER --> TWILIO
    TWILIO --> CALLER
    TWILIO --> LK
    LK --> TWILIO
    LK --> WORKERS
    WORKERS --> LK
    W1 --> PIPELINE
    W2 --> PIPELINE
    WN --> PIPELINE
    WORKERS --> PROM
```

---

## Diagram 2: Call Flow - Media and Control Plane

```mermaid
sequenceDiagram
    participant C as Caller
    participant TW as Twilio SIP
    participant LK as LiveKit SFU
    participant W as AI Worker
    participant G as Groq API
    participant CA as Cartesia TTS

    Note over C,CA: Call Setup
    C->>TW: PSTN Call
    TW->>LK: SIP INVITE TLS
    LK->>W: Assign call
    W-->>LK: Join room as AI participant
    W-->>C: Hello, how can I help?

    Note over C,CA: User Speaks
    C->>LK: PCM Audio RTP
    LK->>W: WebRTC Audio Track
    W->>W: VAD detects speech end
    W->>G: Whisper STT - 110ms avg
    G-->>W: Transcript
    W->>W: FAISS KB search
    W->>G: llama-3.1-8b streaming
    G-->>W: First token at 128ms
    W->>CA: Cartesia Sonic-Turbo
    CA-->>W: First audio chunk at 166ms
    W->>LK: Audio frames
    LK->>C: Voice response
    Note over W: Total E2E 173ms avg

    Note over C,CA: Barge-In
    C->>LK: Speech while TTS playing
    LK->>W: Audio frame
    W->>W: VAD detects speech
    W->>W: INTERRUPT TTS in 0.2ms
    W->>W: Cancel LLM stream
    W->>G: New STT request

    Note over C,CA: Call End
    C->>TW: Hang up
    TW->>LK: BYE
    LK->>W: Participant disconnected
    W->>W: Cleanup and emit metrics
```

---

## Diagram 3: Scaling Plan

```mermaid
graph LR
    subgraph TIER1["Tier 1 - 1 to 8 calls - Dev"]
        S1[1 AI Worker\n1 LiveKit\n1 Redis]
    end

    subgraph TIER2["Tier 2 - 8 to 100 calls - Docker Scale"]
        S2[15 AI Workers\n120 call capacity\n1 LiveKit Node]
    end

    subgraph TIER3["Tier 3 - 100 to 1000 calls - Kubernetes"]
        S3A[HPA 15 to 130 Workers]
        S3B[LiveKit Cluster 3 nodes]
        S3C[Redis Cluster 3 nodes]
        S3D[Global Load Balancer]
    end

    TIER1 -->|scale workers| TIER2
    TIER2 -->|add Kubernetes and HPA| TIER3

    style TIER1 fill:#e8f4e8
    style TIER2 fill:#f4f0e8
    style TIER3 fill:#f4e8e8
```

---

## Scaling Numbers

| Tier | Workers | Calls/Worker | Total Capacity | Infrastructure |
|------|---------|--------------|----------------|----------------|
| Dev | 1 | 8 | 8 | Docker Compose |
| Staging | 2 | 8 | 16 | Docker Compose |
| Production 100 calls | 15 | 8 | 120 | Docker Compose or K8s |
| Production 1000 calls | 130 | 8 | 1040 | Kubernetes + HPA |

---

## Latency Budget (Measured)

| Stage | Target | Measured avg | Implementation |
|-------|--------|-------------|----------------|
| VAD / Audio buffer | 200ms | 200ms | Silero VAD, 250ms silence threshold |
| STT | 150ms | 110ms | Groq Whisper Large v3 Turbo |
| LLM first token | 150ms | 128ms | Groq llama-3.1-8b-instant streaming |
| TTS first chunk | 150ms | 166ms | Cartesia Sonic-Turbo streaming |
| Network overhead | 50ms | 50ms | Docker bridge network |
| Total E2E | 600ms | 173ms | 3.5x under target |