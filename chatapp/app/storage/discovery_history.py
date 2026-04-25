"""Storage helper for writing discovery history records.

Called from the discovery agent's registration tools to log each
discovery session to the history table.
"""

import logging
import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def write_discovery_history(
    system_id: str,
    system_name: str,
    action: str,
    user_id: str,
    system_type: str = "",
    source_type: str = "",
    status: str = "completed",
    table_count: int = 0,
    field_count: int = 0,
    correlation_count: int = 0,
    equivalence_count: int = 0,
    rejected_equivalence_count: int = 0,
    duration_seconds: float = 0.0,
    error_message: str = None,
) -> str:
    """Write a discovery history record to DynamoDB.

    Returns the discovery_id on success, empty string on failure.
    """
    table_name = os.environ.get("DISCOVERY_HISTORY_TABLE_NAME", "")
    if not table_name:
        logger.warning("DISCOVERY_HISTORY_TABLE_NAME not set, skipping history write")
        return ""

    region = os.environ.get("AWS_REGION", "us-east-1")
    discovery_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    item = {
        "discovery_id": {"S": discovery_id},
        "timestamp": {"S": timestamp},
        "system_id": {"S": system_id},
        "system_name": {"S": system_name},
        "action": {"S": action},
        "user_id": {"S": user_id},
        "system_type": {"S": system_type},
        "source_type": {"S": source_type},
        "status": {"S": status},
        "table_count": {"N": str(table_count)},
        "field_count": {"N": str(field_count)},
        "correlation_count": {"N": str(correlation_count)},
        "equivalence_count": {"N": str(equivalence_count)},
        "rejected_equivalence_count": {"N": str(rejected_equivalence_count)},
        "duration_seconds": {"N": str(round(duration_seconds, 2))},
    }
    if error_message:
        item["error_message"] = {"S": error_message}

    try:
        client = boto3.client(
            "dynamodb",
            config=Config(region_name=region, retries={"max_attempts": 2}),
        )
        client.put_item(TableName=table_name, Item=item)
        logger.info("Wrote discovery history %s for system %s", discovery_id, system_id)
        return discovery_id
    except ClientError as e:
        logger.error("Failed to write discovery history: %s", e)
        return ""
