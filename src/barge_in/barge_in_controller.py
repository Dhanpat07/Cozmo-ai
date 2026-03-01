"""
Barge-In Controller
Monitors for user speech while TTS is playing and enables interruption.
Target reaction time: <150ms
"""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class BargeInController:
    """
    Manages the barge-in state machine.
    
    States:
    - IDLE: No TTS playing
    - TTS_PLAYING: TTS is currently outputting audio
    - INTERRUPTED: User spoke while TTS was playing
    """

    def __init__(self):
        self._tts_playing = False
        self._interrupt_event = asyncio.Event()
        self._tts_started_at: float = 0
        self._text_queue: asyncio.Queue = asyncio.Queue()

    @property
    def is_tts_playing(self) -> bool:
        return self._tts_playing and not self._interrupt_event.is_set()

    def tts_started(self):
        """Signal that TTS output has begun"""
        self._tts_playing = True
        self._interrupt_event.clear()
        self._tts_started_at = time.time()
        logger.debug("TTS started")

    def tts_finished(self):
        """Signal that TTS output completed naturally"""
        self._tts_playing = False
        self._interrupt_event.clear()
        logger.debug("TTS finished naturally")

    async def interrupt(self):
        """
        Interrupt TTS immediately.
        Sets event so any in-progress audio generation stops yielding.
        """
        if self._tts_playing:
            reaction_ms = (time.time() - self._tts_started_at) * 1000
            logger.info("Barge-in: TTS interrupted after %.0fms of playback", reaction_ms)

        self._tts_playing = False
        self._interrupt_event.set()

        # Drain text queue
        while not self._text_queue.empty():
            try:
                self._text_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def feed_tts_text(self, text_chunk: str):
        """Feed a text token into the TTS stream"""
        if not self._interrupt_event.is_set():
            await self._text_queue.put(text_chunk)

    async def wait_for_interrupt(self) -> bool:
        """
        Wait until TTS is interrupted.
        Returns True if interrupted, False if finished naturally.
        """
        try:
            await asyncio.wait_for(self._interrupt_event.wait(), timeout=30)
            return True
        except asyncio.TimeoutError:
            return False
