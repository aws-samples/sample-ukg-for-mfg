"""Read-only access to the Bedrock KB ingestion sync-state.

The sync-state table is a single-item DynamoDB table owned by the Bedrock
stack. The Discovery agent flips ``dirty=true`` after writing a learned
memory to the KB source bucket, and a 5-minute EventBridge tick debounces
ingestion jobs. This repository exposes the current state plus the status
of the most recent ingestion job for the admin dashboard.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

DIRTY_PK = "kb_dirty"


@dataclass
class KbSyncStatus:
    """Snapshot of the KB ingestion pipeline for display on the dashboard."""

    configured: bool
    dirty: bool
    marked_at: Optional[str]
    last_ingestion_started_at: Optional[str]
    last_ingestion_job_id: Optional[str]
    last_ingestion_status: Optional[str]
    """One of ``STARTING`` / ``IN_PROGRESS`` / ``COMPLETE`` / ``FAILED`` /
    ``None`` if no job has ever run."""

    @property
    def human_state(self) -> str:
        """Short label suitable for the dashboard tile."""
        if not self.configured:
            return "Not configured"
        if self.last_ingestion_status in ("STARTING", "IN_PROGRESS"):
            return "Ingesting…"
        if self.dirty:
            return "Pending"
        if self.last_ingestion_status == "FAILED":
            return "Last job failed"
        if self.last_ingestion_status == "COMPLETE":
            return "Up to date"
        return "Idle"


class KbSyncRepository:
    """Reader for the KB ingestion sync-state.

    Reads are best-effort: any error degrades gracefully to a
    ``configured=False`` status so the dashboard never crashes on this tile.
    """

    def __init__(
        self,
        table_name: Optional[str] = None,
        kb_id: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        self.table_name = table_name or os.environ.get("KB_SYNC_STATE_TABLE_NAME", "")
        self.kb_id = kb_id or os.environ.get("KB_ID", "")
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")

    def _is_configured(self) -> bool:
        return bool(self.table_name and self.kb_id)

    async def get_status(self) -> KbSyncStatus:
        """Return the current sync state or a ``Not configured`` placeholder."""
        if not self._is_configured():
            return KbSyncStatus(
                configured=False,
                dirty=False,
                marked_at=None,
                last_ingestion_started_at=None,
                last_ingestion_job_id=None,
                last_ingestion_status=None,
            )

        try:
            ddb = boto3.resource("dynamodb", region_name=self.region)
            item = (
                ddb.Table(self.table_name).get_item(Key={"pk": DIRTY_PK}).get("Item")
                or {}
            )
        except ClientError as e:
            logger.warning("KB sync-state read failed: %s", e)
            return KbSyncStatus(
                configured=True,
                dirty=False,
                marked_at=None,
                last_ingestion_started_at=None,
                last_ingestion_job_id=None,
                last_ingestion_status=None,
            )

        last_job_id = item.get("last_ingestion_job_id")
        last_status = (
            await self._get_job_status(last_job_id) if last_job_id else None
        )

        return KbSyncStatus(
            configured=True,
            dirty=bool(item.get("dirty")),
            marked_at=item.get("marked_at"),
            last_ingestion_started_at=item.get("last_ingestion_started_at"),
            last_ingestion_job_id=last_job_id,
            last_ingestion_status=last_status,
        )

    async def _get_job_status(self, job_id: str) -> Optional[str]:
        """Fetch the Bedrock ingestion job status; return ``None`` on failure.

        We need the data source ID for the Bedrock API; we look it up once
        and cache for the instance lifetime.
        """
        try:
            client = boto3.client("bedrock-agent", region_name=self.region)
            data_source_id = await self._get_data_source_id(client)
            if not data_source_id:
                return None
            resp = client.get_ingestion_job(
                knowledgeBaseId=self.kb_id,
                dataSourceId=data_source_id,
                ingestionJobId=job_id,
            )
            return resp.get("ingestionJob", {}).get("status")
        except ClientError as e:
            logger.debug("get_ingestion_job failed for %s: %s", job_id, e)
            return None

    _cached_data_source_id: Optional[str] = None

    async def _get_data_source_id(self, client) -> Optional[str]:
        if self._cached_data_source_id:
            return self._cached_data_source_id
        try:
            resp = client.list_data_sources(knowledgeBaseId=self.kb_id)
            summaries = resp.get("dataSourceSummaries", [])
            if not summaries:
                return None
            self._cached_data_source_id = summaries[0].get("dataSourceId")
            return self._cached_data_source_id
        except ClientError as e:
            logger.debug("list_data_sources failed for %s: %s", self.kb_id, e)
            return None


def format_timestamp(iso_ts: Optional[str]) -> str:
    """Render an ISO timestamp as a compact humanized string."""
    if not iso_ts:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return iso_ts
