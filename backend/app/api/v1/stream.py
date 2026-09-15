"""
WebSocket stream: `GET /api/v1/stream/ws?token=<jwt>`

Fans out platform events (agent runs, notifications, ingestion ticks, automation
decisions) from the Redis `commerceos:events` channel to connected clients. When
Redis is off it falls back to an in-process subscription so a single-instance
deployment still gets live updates.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger
from app.core.redis import EVENTS_CHANNEL, get_async_redis
from app.core.security import decode_token
from app.core.settings import settings
from app.services.event_bus import subscribe_platform_local

logger = get_logger("ws")
router = APIRouter(tags=["Stream"])

_local_clients: set[asyncio.Queue] = set()


def _fanout_local(envelope: dict[str, Any]) -> None:
    for q in list(_local_clients):
        with contextlib.suppress(Exception):
            q.put_nowait(envelope)


subscribe_platform_local(_fanout_local)


@router.websocket("/api/v1/stream/ws")
async def stream_ws(ws: WebSocket, token: str | None = Query(None)):
    # Auth (only enforced when configured).
    if settings.AUTH_ENFORCED:
        payload = decode_token(token or "")
        if not payload or payload.get("type") != "access":
            await ws.close(code=4401)
            return

    await ws.accept()
    await ws.send_json({"channel": "system", "data": {"type": "connected"}})

    redis = get_async_redis()
    stop = asyncio.Event()

    async def _heartbeat():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=25)
            except TimeoutError:
                try:
                    await ws.send_json({"channel": "system", "data": {"type": "ping"}})
                except Exception:
                    stop.set()

    async def _pump_redis():
        pubsub = redis.pubsub()
        await pubsub.subscribe(EVENTS_CHANNEL)
        try:
            async for msg in pubsub.listen():
                if stop.is_set():
                    break
                if msg.get("type") != "message":
                    continue
                try:
                    await ws.send_text(
                        msg["data"] if isinstance(msg["data"], str) else json.dumps(msg["data"])
                    )
                except Exception:
                    stop.set()
                    break
        finally:
            await pubsub.unsubscribe(EVENTS_CHANNEL)
            await pubsub.close()

    async def _pump_local():
        q: asyncio.Queue = asyncio.Queue()
        _local_clients.add(q)
        try:
            while not stop.is_set():
                env = await q.get()
                try:
                    await ws.send_json(env)
                except Exception:
                    stop.set()
                    break
        finally:
            _local_clients.discard(q)

    async def _recv():
        try:
            while not stop.is_set():
                await ws.receive_text()  # ignore client messages; just detect disconnect
        except WebSocketDisconnect:
            stop.set()

    tasks = [asyncio.create_task(_heartbeat()), asyncio.create_task(_recv())]
    tasks.append(asyncio.create_task(_pump_redis() if redis is not None else _pump_local()))
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
