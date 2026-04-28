"""Scheduled tick Lambda that debounces Bedrock KB ingestion jobs.

EventBridge fires every 5 minutes. We read a single-item "kb_dirty" flag from
DynamoDB; if it's true and no ingestion job is currently in progress, we call
``bedrock-agent.start_ingestion_job`` and clear the flag.

The Discovery agent (or any writer) sets ``dirty=true`` after every S3 write
into the KB source bucket. This way a burst of writes collapses into at most
one ingestion job per tick instead of one-per-write.

Env:
    KB_ID:                 Bedrock Knowledge Base ID
    KB_DATA_SOURCE_ID:     Data source ID within the KB
    KB_SYNC_STATE_TABLE:   DynamoDB table holding the single dirty-flag item
"""

import datetime as _dt
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_bedrock_agent = boto3.client("bedrock-agent")
_ddb = boto3.resource("dynamodb")

KB_ID = os.environ["KB_ID"]
DATA_SOURCE_ID = os.environ["KB_DATA_SOURCE_ID"]
STATE_TABLE = os.environ["KB_SYNC_STATE_TABLE"]

DIRTY_PK = "kb_dirty"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _recent_job_still_running(job_id: str | None) -> bool:
    """Return True if ``job_id`` is still in flight according to Bedrock."""
    if not job_id:
        return False
    try:
        resp = _bedrock_agent.get_ingestion_job(
            knowledgeBaseId=KB_ID,
            dataSourceId=DATA_SOURCE_ID,
            ingestionJobId=job_id,
        )
        status = resp["ingestionJob"]["status"]
        # Any non-terminal status means the previous job is still doing work
        return status in ("STARTING", "IN_PROGRESS")
    except _bedrock_agent.exceptions.ResourceNotFoundException:
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("get_ingestion_job failed for %s: %s", job_id, e)
        # Fail-safe: assume not running so we don't get wedged forever
        return False


def handler(event, context):  # noqa: ARG001 — Lambda signature
    table = _ddb.Table(STATE_TABLE)
    item = table.get_item(Key={"pk": DIRTY_PK}).get("Item") or {}

    if not item.get("dirty"):
        logger.info("No pending KB changes. Skipping.")
        return {"skipped": True, "reason": "not_dirty"}

    if _recent_job_still_running(item.get("last_ingestion_job_id")):
        logger.info("Previous ingestion job still in progress. Skipping.")
        return {"skipped": True, "reason": "job_in_progress"}

    resp = _bedrock_agent.start_ingestion_job(
        knowledgeBaseId=KB_ID,
        dataSourceId=DATA_SOURCE_ID,
        description=f"scheduled tick {_now_iso()}",
    )
    job_id = resp["ingestionJob"]["ingestionJobId"]

    # Atomically clear the dirty flag and record the new job. We use
    # ConditionExpression so concurrent writes that flipped dirty back to
    # true (race between our GetItem and this update) are not clobbered.
    table.update_item(
        Key={"pk": DIRTY_PK},
        UpdateExpression=(
            "SET dirty = :false, "
            "last_ingestion_started_at = :now, "
            "last_ingestion_job_id = :job"
        ),
        ExpressionAttributeValues={
            ":false": False,
            ":now": _now_iso(),
            ":job": job_id,
        },
    )

    logger.info("Started ingestion job %s", job_id)
    return {"started": job_id}
