"""Emitting structured BioFlow chunks into the agent stream.

Primary path: LangGraph ``get_stream_writer()`` inside a tool coroutine —
harness-agent streams with ``custom`` mode enabled, so these become
``{"type": "custom", "data": {"bio": ...}}`` frames on the dashboard WS.

Fallback (internal turns whose stream is consumed without a WS listener):
the tool returns a sentinel-prefixed JSON string; the gateway processor
peels it and emits the custom frame itself.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

_SENTINEL = "__bio_chunk__"


def emit_bio_chunk(payload: dict[str, Any]) -> bool:
    """Write a ``{"bio": payload}`` custom chunk; False when no writer is active."""
    try:
        from langgraph.config import get_stream_writer  # noqa: PLC0415

        writer = get_stream_writer()
    except Exception:
        return False
    try:
        writer({"bio": payload})
    except Exception:
        return False
    return True


def ack_or_sentinel(ack: str, payload: dict[str, Any], *, emitted: bool) -> str:
    """Tool return value: short ack when streamed, sentinel JSON otherwise."""
    if emitted:
        return ack
    return _SENTINEL + json.dumps({"ack": ack, "bio": payload}, ensure_ascii=False)


def _message_text(msg: Any) -> str | None:
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    return content if isinstance(content, str) else None


def _set_message_text(msg: Any, text: str) -> None:
    if isinstance(msg, dict):
        msg["content"] = text
    else:
        with contextlib.suppress(Exception):
            msg.content = text


def extract_bio_custom(chunk: dict[str, Any]) -> dict[str, Any] | None:
    """Peel sentinel payloads out of a ``tool_result`` chunk (mutates in place).

    Returns the ``{"bio": ...}`` payload to emit as a custom frame, or None.
    """
    if chunk.get("type") != "tool_result":
        return None
    messages = chunk.get("messages")
    if not isinstance(messages, list):
        return None
    payload: dict[str, Any] | None = None
    for msg in messages:
        text = _message_text(msg)
        if text is None or not text.startswith(_SENTINEL):
            continue
        try:
            data = json.loads(text[len(_SENTINEL) :])
        except json.JSONDecodeError:
            continue
        ack = data.get("ack")
        if isinstance(ack, str):
            _set_message_text(msg, ack)
        bio = data.get("bio")
        if isinstance(bio, dict):
            payload = {"bio": bio}
    return payload
