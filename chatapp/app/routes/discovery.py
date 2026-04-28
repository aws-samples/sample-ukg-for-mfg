"""Discovery Agent API route for admin users.

This module provides an SSE streaming endpoint that invokes the Discovery Agent
runtime, restricted to admin users only. It reuses the same SSE streaming
pattern as the regular chat endpoint.
"""

import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agentcore.client import AgentCoreClient
from app.config import get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["discovery"])


class DiscoveryRequest(BaseModel):
    """Request body for discovery endpoint.

    Attributes:
        prompt: Admin instruction for the Discovery Agent
        session_id: Session ID for conversation context
    """

    prompt: str = Field(..., min_length=1, description="Discovery instruction")
    session_id: str = Field(..., min_length=1, description="Session ID")


def _require_admin(request: Request) -> None:
    """Verify the current user has admin privileges.

    Args:
        request: Incoming request with auth state set by middleware

    Raises:
        HTTPException: 403 if user is not an admin
    """
    is_admin = getattr(request.state, "is_admin", False)
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin access required to invoke the Discovery Agent.",
        )


async def _stream_discovery_response(
    prompt: str,
    session_id: str,
    user_id: str,
):
    """Generate SSE stream from the Discovery Agent runtime.

    Uses the same streaming pattern as the chat endpoint but routes
    to the Discovery Agent runtime ARN.

    Args:
        prompt: Admin instruction for the Discovery Agent
        session_id: Session ID for conversation context
        user_id: User ID for the invoking admin

    Yields:
        SSE formatted event strings
    """
    config = get_config()
    client = AgentCoreClient(runtime_arn=config.discovery_runtime_arn)

    async for event in client.invoke_stream(
        prompt=prompt,
        session_id=session_id,
        user_id=user_id,
    ):
        yield event.to_sse_format()


@router.post("/discovery")
async def discovery(request: Request, body: DiscoveryRequest):
    """SSE streaming endpoint for the Discovery Agent (admin only).

    Accepts a discovery request, validates admin access, and streams
    the Discovery Agent response back via SSE.

    Args:
        request: Incoming request with session cookie
        body: Discovery request with prompt and session_id

    Returns:
        SSE stream response

    Raises:
        HTTPException: 403 if not admin, 503 if discovery runtime not configured
    """
    _require_admin(request)

    config = get_config()
    if not config.discovery_runtime_arn:
        raise HTTPException(
            status_code=503,
            detail="Discovery Agent runtime is not configured. "
            "Set the DISCOVERY_RUNTIME_ARN environment variable.",
        )

    # Extract user info from request state (set by auth middleware)
    user = getattr(request.state, "user", None)
    if not user or not hasattr(user, "user_id"):
        raise HTTPException(status_code=401, detail="No authenticated user found")

    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    if not body.session_id.strip():
        raise HTTPException(status_code=400, detail="Session ID cannot be empty")

    return StreamingResponse(
        _stream_discovery_response(
            prompt=body.prompt,
            session_id=body.session_id,
            user_id=user.user_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
