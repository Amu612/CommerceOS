"""Pooled Redis clients (sync + async). No-ops gracefully when REDIS_ENABLED is false."""

from __future__ import annotations

from functools import lru_cache

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger("redis")


@lru_cache
def get_redis():  # -> Optional[redis.Redis]
    if not settings.REDIS_ENABLED:
        return None
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("redis_unavailable", error=str(exc))
        return None


@lru_cache
def get_async_redis():  # -> Optional[redis.asyncio.Redis]
    if not settings.REDIS_ENABLED:
        return None
    try:
        import redis.asyncio as aioredis

        return aioredis.from_url(settings.REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    except Exception as exc:
        logger.warning("async_redis_unavailable", error=str(exc))
        return None


EVENTS_CHANNEL = "commerceos:events"
