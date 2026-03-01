"""
LiveKit Worker
Connects to LiveKit server, handles room events,
routes audio to/from CallPipeline
"""
import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "ws://livekit:7880")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "devsecret")


class LiveKitWorker:
    """
    Connects to LiveKit as an AI participant worker.
    
    Uses livekit-agents SDK for production.
    This implementation shows the integration pattern.
    """

    def __init__(self, call_manager):
        self.call_manager = call_manager
        self._running = False

    async def start(self):
        """
        Start the LiveKit worker.
        Listens for new rooms (incoming calls) and spawns pipelines.
        """
        self._running = True
        logger.info("LiveKit worker starting, connecting to %s", LIVEKIT_URL)

        try:
            from livekit import agents, rtc
            from livekit.agents import JobContext, WorkerOptions, cli

            async def entrypoint(ctx: JobContext):
                """Called for each new room (call)"""
                room = ctx.room
                call_id = await self.call_manager.handle_new_call(
                    room_name=room.name,
                    participant_id="incoming"
                )

                if not call_id:
                    logger.warning("Rejected call for room %s (at capacity)", room.name)
                    await ctx.disconnect()
                    return

                session = self.call_manager._sessions.get(call_id)
                if not session:
                    return

                # Set up audio output back to room
                audio_source = rtc.AudioSource(sample_rate=16000, num_channels=1)
                local_track = rtc.LocalAudioTrack.create_audio_track("ai-voice", audio_source)

                async def send_audio(pcm_bytes: bytes):
                    """Send TTS audio back through LiveKit"""
                    import numpy as np
                    audio_frame = rtc.AudioFrame(
                        data=pcm_bytes,
                        sample_rate=16000,
                        num_channels=1,
                        samples_per_channel=len(pcm_bytes) // 2
                    )
                    await audio_source.capture_frame(audio_frame)

                session.pipeline.set_audio_output_callback(send_audio)

                # Publish AI audio track
                await room.local_participant.publish_track(
                    local_track,
                    rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
                )

                # Subscribe to incoming audio from the caller
                @room.on("track_subscribed")
                def on_track(track: rtc.Track, publication, participant):
                    if track.kind == rtc.TrackKind.KIND_AUDIO:
                        asyncio.create_task(
                            self._handle_audio_track(call_id, track)
                        )

                # Handle participant disconnect
                @room.on("participant_disconnected")
                def on_disconnect(participant):
                    asyncio.create_task(
                        self.call_manager.end_call(call_id)
                    )

                logger.info("LiveKit room handler ready for call %s", call_id)

            # Run the LiveKit worker
            worker = agents.Worker(
                WorkerOptions(
                    entrypoint_fnc=entrypoint,
                    ws_url=LIVEKIT_URL,
                    api_key=LIVEKIT_API_KEY,
                    api_secret=LIVEKIT_API_SECRET,
                )
            )
            await worker.run()

        except ImportError:
            logger.warning("livekit-agents not installed, running in simulation mode")
            await self._simulation_mode()
        except Exception as e:
            logger.error("LiveKit worker error: %s", e, exc_info=True)

    async def _handle_audio_track(self, call_id: str, audio_track) -> None:
        """Read audio frames from track and push to pipeline"""
        session = self.call_manager._sessions.get(call_id)
        if not session or not session.pipeline:
            return

        from livekit import rtc
        async for event in rtc.AudioStream(audio_track, sample_rate=16000, num_channels=1):
            if call_id not in self.call_manager._sessions:
                break
            # Push 20ms PCM frame to pipeline
            await session.pipeline.push_audio_frame(bytes(event.frame.data))

    async def _simulation_mode(self):
        """
        Simulation mode for testing without LiveKit.
        Generates synthetic calls to test the pipeline.
        """
        logger.info("Running in simulation mode")
        while self._running:
            await asyncio.sleep(10)
