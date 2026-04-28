"""Institutional-memory writer for the Discovery Agent.

After a successful discovery run, the agent calls ``remember_discovery`` with a
structured summary of what it learned. The summary is written as markdown to
the Bedrock KB source bucket under a stable per-system key and a DynamoDB
dirty flag is flipped. A separate EventBridge-scheduled Lambda
(``{app}-kb-ingestion-tick``) picks up the flag every 5 minutes and debounces
``bedrock-agent.StartIngestionJob`` so bursts of writes coalesce into at most
one ingestion job per tick.

Storage layout::

    s3://{KB_SOURCE_BUCKET}/documents/learned/discovery/{system_id}.md

Writing with a stable key means re-discovery overwrites the previous summary —
we always have exactly one current doc per system, so the KB never accumulates
near-duplicate chunks.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Optional

import boto3
from strands import tool

logger = logging.getLogger(__name__)

_s3 = boto3.client("s3")
_ddb = boto3.resource("dynamodb")

DIRTY_PK = "kb_dirty"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _render_markdown(
    *,
    system_id: str,
    system_name: str,
    system_type: str,
    protocol: str,
    plant: Optional[str],
    summary_markdown: str,
    tags: Optional[list[str]],
) -> str:
    """Build the full markdown file with YAML front-matter.

    The front-matter is preserved by Bedrock KB as chunk attributes, which
    lets future retrieval filter by category/system/tags if needed.
    """
    tag_list = list(tags or [])
    front_matter = [
        "---",
        "category: discovery",
        f"system_id: {system_id}",
        f"system_name: {system_name}",
        f"system_type: {system_type}",
        f"protocol: {protocol}",
    ]
    if plant:
        front_matter.append(f"plant: {plant}")
    if tag_list:
        front_matter.append(f"tags: [{', '.join(tag_list)}]")
    front_matter.append(f"discovered_at: {_now_iso()}")
    front_matter.append("agent: discovery")
    front_matter.append("---")

    body = summary_markdown.strip()
    if not body.startswith("#"):
        body = f"# {system_name} ({system_id}) — Discovery Summary\n\n{body}"

    return "\n".join(front_matter) + "\n\n" + body + "\n"


def _mark_kb_dirty(table_name: str) -> None:
    """Flip the single-item ``kb_dirty`` flag in the sync-state table.

    Failures are logged but not raised — missing the dirty flag only delays
    ingestion until the next write, and the S3 object is already in place.
    """
    try:
        _ddb.Table(table_name).update_item(
            Key={"pk": DIRTY_PK},
            UpdateExpression="SET dirty = :true, marked_at = :now",
            ExpressionAttributeValues={":true": True, ":now": _now_iso()},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to mark KB dirty flag in %s: %s", table_name, e)


@tool
def remember_discovery(
    system_id: str,
    system_name: str,
    system_type: str,
    protocol: str,
    summary_markdown: str,
    plant: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> str:
    """Persist what was learned about a system to institutional memory.

    Call this **once** at the very end of a successful discovery run, after
    ``register_all`` and ``log_discovery_session`` have succeeded. The markdown
    you provide will be retrievable by the Explorer agent on future questions
    about this system.

    Args:
        system_id: Canonical system identifier used in the registry.
        system_name: Human-readable system name.
        system_type: e.g. ``erp``, ``mes``, ``cmms``, ``plm``, ``iot``.
        protocol: e.g. ``postgres``, ``athena``, ``openapi``, ``mcp``.
        summary_markdown: A concise markdown summary covering: tables
            discovered, concept mappings, cross-system equivalences
            registered, observed enum values, known quirks or gotchas,
            and anything else the Explorer agent should know next time
            someone asks a question about this system. Keep it factual
            and grounded in what Phase 1–4 actually produced.
        plant: Optional plant/site where the system runs.
        tags: Optional list of short tags for filterability (e.g.
            ``["erp", "orders", "status-enum"]``).

    Returns:
        A string of the form ``"stored:{key}"`` on success, or an error
        message if the write failed.
    """
    bucket = os.getenv("KB_SOURCE_BUCKET")
    sync_table = os.getenv("KB_SYNC_STATE_TABLE")
    if not bucket:
        return "error: KB_SOURCE_BUCKET not configured"
    if not sync_table:
        return "error: KB_SYNC_STATE_TABLE not configured"

    key = f"documents/learned/discovery/{system_id}.md"
    body = _render_markdown(
        system_id=system_id,
        system_name=system_name,
        system_type=system_type,
        protocol=protocol,
        plant=plant,
        summary_markdown=summary_markdown,
        tags=tags,
    )

    try:
        _s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
    except Exception as e:  # noqa: BLE001
        logger.error("remember_discovery s3.put_object failed: %s", e, exc_info=True)
        return f"error: failed to write to s3://{bucket}/{key}: {e}"

    _mark_kb_dirty(sync_table)
    logger.info("Stored discovery memory at s3://%s/%s", bucket, key)
    return f"stored:{key}"
