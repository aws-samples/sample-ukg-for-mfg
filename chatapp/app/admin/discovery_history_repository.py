"""Repository for querying discovery history records from DynamoDB."""

import asyncio
import logging
import os
from datetime import datetime
from typing import List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.models.discovery_history import DiscoveryHistoryRecord

logger = logging.getLogger(__name__)


class DiscoveryHistoryRepository:
    """Repository for discovery history DynamoDB operations."""

    def __init__(self, table_name: Optional[str] = None, region: Optional[str] = None):
        self.table_name = table_name or os.environ.get(
            "DISCOVERY_HISTORY_TABLE_NAME", "mfg-ukg-discovery-history"
        )
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        boto_config = Config(
            region_name=self.region,
            retries={"max_attempts": 3, "mode": "adaptive"},
        )
        self._client = boto3.client("dynamodb", config=boto_config)

    def _scan_by_time_range(self, start_iso: str, end_iso: str) -> List[dict]:
        """Scan for records within a time range."""
        items = []
        paginator = self._client.get_paginator("scan")
        for page in paginator.paginate(
            TableName=self.table_name,
            FilterExpression="#ts BETWEEN :start AND :end_time",
            ExpressionAttributeNames={"#ts": "timestamp"},
            ExpressionAttributeValues={
                ":start": {"S": start_iso},
                ":end_time": {"S": end_iso},
            },
        ):
            items.extend(page.get("Items", []))
        return items

    async def get_all_records(
        self, start_time: datetime, end_time: datetime
    ) -> List[DiscoveryHistoryRecord]:
        """Get all discovery history records within a time range."""
        try:
            loop = asyncio.get_event_loop()
            items = await loop.run_in_executor(
                None,
                self._scan_by_time_range,
                start_time.isoformat(),
                end_time.isoformat(),
            )
            records = [DiscoveryHistoryRecord.from_dynamodb_item(item) for item in items]
            records.sort(key=lambda r: r.timestamp, reverse=True)
            return records
        except ClientError as e:
            logger.error("Failed to scan discovery history: %s", e)
            return []

    def _get_record(self, discovery_id: str) -> Optional[dict]:
        """Get a single record by discovery_id (scan since we need to find the SK)."""
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="discovery_id = :did",
                ExpressionAttributeValues={":did": {"S": discovery_id}},
                Limit=1,
            )
            items = response.get("Items", [])
            return items[0] if items else None
        except ClientError as e:
            logger.error("Failed to get discovery record %s: %s", discovery_id, e)
            return None

    async def get_record(self, discovery_id: str) -> Optional[DiscoveryHistoryRecord]:
        """Get a single discovery history record by ID."""
        loop = asyncio.get_event_loop()
        item = await loop.run_in_executor(None, self._get_record, discovery_id)
        if item:
            return DiscoveryHistoryRecord.from_dynamodb_item(item)
        return None

    def _get_records_for_system(self, system_id: str) -> List[dict]:
        """Query GSI for all records for a system."""
        items = []
        try:
            paginator = self._client.get_paginator("query")
            for page in paginator.paginate(
                TableName=self.table_name,
                IndexName="system-index",
                KeyConditionExpression="system_id = :sid",
                ExpressionAttributeValues={":sid": {"S": system_id}},
                ScanIndexForward=False,
            ):
                items.extend(page.get("Items", []))
        except ClientError as e:
            logger.error("Failed to query system history for %s: %s", system_id, e)
        return items

    async def get_records_for_system(
        self, system_id: str
    ) -> List[DiscoveryHistoryRecord]:
        """Get all discovery records for a specific system."""
        loop = asyncio.get_event_loop()
        items = await loop.run_in_executor(None, self._get_records_for_system, system_id)
        return [DiscoveryHistoryRecord.from_dynamodb_item(item) for item in items]

    def write_record(self, record: DiscoveryHistoryRecord) -> bool:
        """Write a discovery history record (sync, called from agent tools)."""
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=record.to_dynamodb_item(),
            )
            return True
        except ClientError as e:
            logger.error("Failed to write discovery history: %s", e)
            return False
