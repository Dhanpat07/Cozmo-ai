"""
Voice AI Agent - Main Worker Entry Point
Handles up to 8 concurrent calls per worker instance
"""
import asyncio
import logging
import os
import signal
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from .call_manager import CallManager
from .metrics import metrics_registry
from .livekit_client import LiveKitWorker
from .simulation import router as simulation_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

MAX_CALLS_PER_WORKER = int(os.getenv("MAX_CALLS_PER_WORKER", "8"))

call_manager = CallManager(max_calls=MAX_CALLS_PER_WORKER)
livekit_worker = LiveKitWorker(call_manager=call_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle"""
    logger.info("Starting Voice AI Worker (max %d calls)", MAX_CALLS_PER_WORKER)
    
    # Initialize knowledge base
    from .kb.knowledge_base import KnowledgeBase
    kb = KnowledgeBase()
    await kb.initialize()
    call_manager.knowledge_base = kb
    
    # Start LiveKit worker
    worker_task = asyncio.create_task(livekit_worker.start())
    
    logger.info("Worker ready")
    yield
    
    # Graceful shutdown
    logger.info("Shutting down worker...")
    worker_task.cancel()
    await call_manager.cleanup_all()
    logger.info("Worker stopped")


app = FastAPI(title="Voice AI Worker", lifespan=lifespan)

# Mount simulation/test endpoints
app.include_router(simulation_router)

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app(registry=metrics_registry)
app.mount("/metrics", metrics_app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "active_calls": call_manager.active_call_count,
        "capacity": MAX_CALLS_PER_WORKER,
        "available": MAX_CALLS_PER_WORKER - call_manager.active_call_count
    }


@app.get("/ready")
async def ready():
    """Kubernetes readiness probe"""
    if call_manager.active_call_count >= MAX_CALLS_PER_WORKER:
        return {"ready": False, "reason": "at_capacity"}, 503
    return {"ready": True}


def main():
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        loop="uvloop",
        log_level="info"
    )


if __name__ == "__main__":
    main()
