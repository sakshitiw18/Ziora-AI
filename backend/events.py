"""Tiny pub/sub so the dashboard updates live when the agent books/cancels."""
from fastapi import WebSocket

subscribers: set[WebSocket] = set()


async def broadcast(message: dict) -> None:
    for ws in list(subscribers):
        try:
            await ws.send_json(message)
        except Exception:
            subscribers.discard(ws)