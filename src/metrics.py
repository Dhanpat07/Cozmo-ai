"""
Prometheus metrics for the Voice AI system
All metrics labeled by call_id
"""
from prometheus_client import (
    Counter, Gauge, Histogram, CollectorRegistry, REGISTRY
)

# Use default registry
metrics_registry = REGISTRY

# ── Active Calls ──────────────────────────────────────────────
active_calls_gauge = Gauge(
    "voice_ai_active_calls",
    "Number of currently active calls"
)

# ── Call Setup ───────────────────────────────────────────────
call_setup_time_hist = Histogram(
    "voice_ai_call_setup_time_ms",
    "Time to set up a new call session (ms)",
    labelnames=["call_id"],
    buckets=[10, 25, 50, 100, 200, 500, 1000]
)

failed_call_setup_counter = Counter(
    "voice_ai_failed_call_setups_total",
    "Total number of failed call setups",
    labelnames=["reason"]
)

# ── STT Latency ──────────────────────────────────────────────
stt_latency_hist = Histogram(
    "voice_ai_stt_latency_ms",
    "Speech-to-text latency (ms)",
    labelnames=["call_id"],
    buckets=[50, 100, 150, 200, 300, 500, 1000, 2000]
)

# ── LLM Latency ──────────────────────────────────────────────
llm_first_token_hist = Histogram(
    "voice_ai_llm_first_token_latency_ms",
    "LLM first token latency (ms)",
    labelnames=["call_id"],
    buckets=[50, 100, 150, 200, 300, 500, 1000]
)

llm_total_latency_hist = Histogram(
    "voice_ai_llm_total_latency_ms",
    "LLM total response latency (ms)",
    labelnames=["call_id"],
    buckets=[100, 200, 500, 1000, 2000, 5000]
)

# ── TTS Latency ──────────────────────────────────────────────
tts_start_latency_hist = Histogram(
    "voice_ai_tts_start_latency_ms",
    "Time to first TTS audio chunk (ms)",
    labelnames=["call_id"],
    buckets=[50, 100, 150, 200, 300, 500]
)

# ── End-to-End Latency ────────────────────────────────────────
end_to_end_latency_hist = Histogram(
    "voice_ai_end_to_end_latency_ms",
    "Total round-trip latency from user speech end to TTS start (ms)",
    labelnames=["call_id"],
    buckets=[200, 300, 400, 500, 600, 700, 800, 1000, 2000]
)

# ── WebRTC / Network Quality ──────────────────────────────────
jitter_gauge = Gauge(
    "voice_ai_jitter_ms",
    "Audio jitter (ms)",
    labelnames=["call_id"]
)

packet_loss_gauge = Gauge(
    "voice_ai_packet_loss_percent",
    "Packet loss percentage",
    labelnames=["call_id"]
)

mos_gauge = Gauge(
    "voice_ai_mos_estimate",
    "Estimated MOS score (1.0-5.0)",
    labelnames=["call_id"]
)

# ── Barge-In ─────────────────────────────────────────────────
barge_in_counter = Counter(
    "voice_ai_barge_in_total",
    "Total number of barge-in events",
    labelnames=["call_id"]
)


def record_call_failure(reason: str):
    failed_call_setup_counter.labels(reason=reason).inc()


def update_network_metrics(call_id: str, jitter_ms: float, packet_loss: float):
    """Update network quality metrics for a call"""
    jitter_gauge.labels(call_id=call_id).set(jitter_ms)
    packet_loss_gauge.labels(call_id=call_id).set(packet_loss)

    # Estimate MOS from packet loss and jitter (simplified E-model)
    mos = _estimate_mos(jitter_ms, packet_loss)
    mos_gauge.labels(call_id=call_id).set(mos)


def _estimate_mos(jitter_ms: float, packet_loss_pct: float) -> float:
    """Simplified MOS estimation"""
    base_mos = 4.5
    jitter_penalty = min(jitter_ms / 100.0, 1.5)
    loss_penalty = min(packet_loss_pct / 5.0, 2.0)
    return max(1.0, base_mos - jitter_penalty - loss_penalty)
