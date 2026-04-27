"""Sideband progress channel for tools that need to push incremental updates.

Motivation
----------
Strands' tool-call pipeline doesn't reliably surface intermediate yields from
async-generator ``@tool`` functions as ``tool_stream_event``s in the agent's
event stream (and the shape has shifted between versions). When a long-running
tool like ``discover_s3tables_bucket`` wants to show real-time progress to the
user, relying on Strands' internal wrapping produces silent drops.

This module provides a minimal, framework-agnostic side channel:

1. The entrypoint creates an ``asyncio.Queue`` per invocation and binds it to
   :data:`_queue_ctx` (a :class:`~contextvars.ContextVar`) for the request.
2. Tools call :func:`emit_progress` from anywhere inside that task tree to push
   a payload into the queue.
3. The entrypoint drains the queue concurrently with ``agent.stream_async`` and
   emits each payload to the AgentCore runtime as a ``TextStreamEvent``.

ContextVars are copied into child tasks by ``asyncio.create_task``, so the
queue is automatically reachable from ``@tool`` functions invoked by the
Strands agent — no threading, no globals, no registry lookup.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Per-invocation queue. ``None`` when no invocation is active (e.g. tests).
_queue_ctx: contextvars.ContextVar[Optional[asyncio.Queue]] = contextvars.ContextVar(
    "discovery_progress_queue", default=None
)


def set_queue(queue: Optional[asyncio.Queue]) -> contextvars.Token:
    """Bind ``queue`` to the current context and return a reset token."""
    return _queue_ctx.set(queue)


def reset_queue(token: contextvars.Token) -> None:
    """Undo a prior :func:`set_queue` call."""
    _queue_ctx.reset(token)


def emit_progress(payload: Any) -> None:
    """Push a progress payload to the current invocation's queue.

    Safe to call from anywhere: if no queue is bound to the current context
    (e.g. unit tests, local `agentcore run`), the call is a silent no-op.

    ``payload`` is typically a ``dict`` matching the shapes documented in
    :mod:`tools.analyze` (``type: progress | phase_update | namespace_result``),
    but any JSON-serializable value is accepted.
    """
    queue = _queue_ctx.get()
    if queue is None:
        return
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        # Extremely unlikely for a bounded-then-unbounded queue; log and drop.
        logger.warning("progress queue is full; dropping payload %r", payload)


# Sentinel used by the entrypoint to signal "no more progress, stop draining".
DONE = object()
