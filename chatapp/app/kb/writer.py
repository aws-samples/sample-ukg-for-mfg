"""Institutional-memory writer for chatapp-originated learnings.

Mirrors the pattern used by ``agent-discovery/tools/remember.py``: write a
markdown file under ``documents/learned/...`` in the Bedrock KB source bucket
and flip the DynamoDB dirty flag so the scheduled ingestion tick picks up
the change within ~5 minutes.

Currently exposes just one writer — ``write_gotcha_from_feedback`` — invoked
from the feedback route when a user marks their negative feedback as a
correction. Future writers (validated answers, etc.) can share the helpers
here.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
from typing import List, Optional

import boto3

logger = logging.getLogger(__name__)

_s3 = boto3.client("s3")
_ddb = boto3.resource("dynamodb")

DIRTY_PK = "kb_dirty"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _mark_kb_dirty(table_name: str) -> None:
    """Flip the single-item ``kb_dirty`` flag so the tick Lambda ingests.

    Failures are logged but not raised — the S3 object is already durable;
    worst case, the dirty flag lands on the next successful write and the
    tick picks both changes up together.
    """
    try:
        _ddb.Table(table_name).update_item(
            Key={"pk": DIRTY_PK},
            UpdateExpression="SET dirty = :true, marked_at = :now",
            ExpressionAttributeValues={":true": True, ":now": _now_iso()},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to mark KB dirty flag in %s: %s", table_name, e)


def _slugify_excerpt(text: str, max_len: int = 80) -> str:
    """Produce a short, human-legible preview of a message for tags/context."""
    text = " ".join((text or "").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _fingerprint(*parts: str) -> str:
    """Compute a stable 16-char hex fingerprint for a group of strings."""
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x1f")  # unit separator
    return h.hexdigest()[:16]


def _render_gotcha_markdown(
    *,
    user_correction: str,
    user_question: str,
    agent_answer: str,
    session_id: str,
    user_id: str,
    tags: Optional[List[str]],
    created_at: str,
    feedback_id: str,
) -> str:
    """Build the markdown body with YAML front-matter.

    The agent answer and user question are included for retrieval context
    so that when a future user asks something similar, vector search surfaces
    this gotcha with enough surrounding detail to be actionable.
    """
    tag_list = list(tags or [])
    tag_list.append("gotcha")
    front = [
        "---",
        "category: gotcha",
        f"feedback_id: {feedback_id}",
        f"session_id: {session_id}",
        f"user_id: {user_id}",
        f"created_at: {created_at}",
        f"tags: [{', '.join(sorted(set(tag_list)))}]",
        "---",
    ]
    body = [
        "# User Correction",
        "",
        "## Correction",
        user_correction.strip(),
        "",
        "## Original question",
        user_question.strip(),
        "",
        "## Agent answer being corrected",
        agent_answer.strip(),
        "",
    ]
    return "\n".join(front) + "\n\n" + "\n".join(body) + "\n"


def _extract_system_id_tags(text: str) -> List[str]:
    """Pull out obvious system/plant identifiers to use as filter tags.

    Best-effort — matches tokens that look like ``sys-...`` or ``SITE-###``.
    Keeps the tag set small so we don't bloat front-matter.
    """
    import re

    tags: set[str] = set()
    for match in re.findall(r"\bsys-[a-z0-9-]+\b", text or "", re.IGNORECASE):
        tags.add(match.lower())
    for match in re.findall(r"\bSITE-\d+\b", text or ""):
        tags.add(match)
    return sorted(tags)[:5]


def write_gotcha_from_feedback(
    *,
    feedback_id: str,
    user_id: str,
    session_id: str,
    user_question: str,
    agent_answer: str,
    user_correction: str,
) -> Optional[str]:
    """Persist a user correction as a gotcha in the KB.

    Returns the S3 key on success, or ``None`` if the KB is not configured
    (e.g. the admin hasn't deployed the Bedrock stack, or the env vars are
    missing). Never raises — feedback submission should not fail because
    the KB write failed.

    Key shape: ``documents/learned/gotchas/{feedback_id}.md``. Using the
    feedback id (which is unique per thumbs-down) means each correction gets
    its own file; duplicates would only arise from retries of the same
    feedback submission, which is fine because the key is stable and the
    write is idempotent.
    """
    bucket = os.getenv("KB_SOURCE_BUCKET")
    sync_table = os.getenv("KB_SYNC_STATE_TABLE_NAME")
    if not bucket or not sync_table:
        logger.debug(
            "KB writer skipped: KB_SOURCE_BUCKET=%r KB_SYNC_STATE_TABLE_NAME=%r",
            bool(bucket),
            bool(sync_table),
        )
        return None

    tags = _extract_system_id_tags(agent_answer) + _extract_system_id_tags(user_correction)
    key = f"documents/learned/gotchas/{feedback_id}.md"
    body = _render_gotcha_markdown(
        user_correction=user_correction,
        user_question=user_question,
        agent_answer=agent_answer,
        session_id=session_id,
        user_id=user_id,
        tags=tags,
        created_at=_now_iso(),
        feedback_id=feedback_id,
    )

    try:
        _s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
    except Exception as e:  # noqa: BLE001
        logger.error("gotcha KB write failed: %s", e, exc_info=True)
        return None

    _mark_kb_dirty(sync_table)
    logger.info("Stored gotcha memory at s3://%s/%s", bucket, key)
    return key
