"""
CallManager - Orchestrates the full per-call AI pipeline
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .metrics import (
    active_calls_gauge, call_setup_time_hist,
    end_to_end_latency_hist, record_call_failure
)
from .pipeline import CallPipeline

logger = logging.getLogger(__name__)


@dataclass
class CallSession:
    call_id: str
    room_name: str
    participant_id: str
    pipeline: Optional["CallPipeline"] = None
    created_at: float = field(default_factory=time.time)
    task: Optional[asyncio.Task] = None


class CallManager:
    def __init__(self, max_calls: int = 8):
        self.max_calls = max_calls
        self._sessions: dict[str, CallSession] = {}
        self._lock = asyncio.Lock()
        self.knowledge_base = None
        logger.info("CallManager initialized (max_calls=%d)", max_calls)

    @property
    def active_call_count(self) -> int:
        return len(self._sessions)

    async def handle_new_call(self, room_name: str, participant_id: str) -> Optional[str]:
        """Called when a new participant joins a LiveKit room"""
        async with self._lock:
            if len(self._sessions) >= self.max_calls:
                logger.warning("At capacity (%d calls), rejecting new call", self.max_calls)
                record_call_failure("at_capacity")
                return None

            call_id = str(uuid.uuid4())
            setup_start = time.time()

            pipeline = CallPipeline(
                call_id=call_id,
                room_name=room_name,
                knowledge_base=self.knowledge_base
            )

            session = CallSession(
                call_id=call_id,
                room_name=room_name,
                participant_id=participant_id,
                pipeline=pipeline
            )
            self._sessions[call_id] = session
            active_calls_gauge.inc()

            # Record setup time
            setup_ms = (time.time() - setup_start) * 1000
            call_setup_time_hist.labels(call_id=call_id).observe(setup_ms)

            logger.info("New call started: call_id=%s room=%s setup_ms=%.1f",
                       call_id, room_name, setup_ms)
            return call_id

    async def start_pipeline(self, call_id: str, audio_track) -> None:
        """Start the processing pipeline for a call"""
        session = self._sessions.get(call_id)
        if not session:
            return

        async def run_pipeline():
            try:
                await session.pipeline.run(audio_track)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("Pipeline error for call %s: %s", call_id, e, exc_info=True)
            finally:
                await self.end_call(call_id)

        session.task = asyncio.create_task(run_pipeline())

    async def end_call(self, call_id: str) -> None:
        """Clean up a call session"""
        async with self._lock:
            session = self._sessions.pop(call_id, None)
            if session:
                if session.task and not session.task.done():
                    session.task.cancel()
                if session.pipeline:
                    await session.pipeline.cleanup()
                active_calls_gauge.dec()
                duration = time.time() - session.created_at
                logger.info("Call ended: call_id=%s duration=%.1fs", call_id, duration)

    async def cleanup_all(self) -> None:
        """Graceful shutdown - end all active calls"""
        call_ids = list(self._sessions.keys())
        await asyncio.gather(*[self.end_call(cid) for cid in call_ids])
