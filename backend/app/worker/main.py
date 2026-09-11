"""
Worker entrypoint.

M1: keeps the container alive and logs a heartbeat so the compose/ECS topology
is exercised. M4/R3 replaces the heartbeat loop with:
  - the single-writer replay engine loop (Redis-locked)
  - scheduled per-agent analyses
  - the orchestrator cron
  - housekeeping (prune old agent_runs / notifications)
"""
from __future__ import annotations

import asyncio
import signal

from app.core.logging import configure_logging, get_logger
from app.core.settings import settings

configure_logging()
logger = get_logger("worker")

_stop = asyncio.Event()


def _handle_signal(*_: object) -> None:
    _stop.set()


async def _run() -> None:
    logger.info("worker_start", environment=settings.ENVIRONMENT)
    tick = 0
    while not _stop.is_set():
        tick += 1
        logger.info("worker_heartbeat", tick=tick)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
    logger.info("worker_stop")


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:  # Windows
            signal.signal(sig, _handle_signal)
    loop.run_until_complete(_run())


if __name__ == "__main__":
    main()
