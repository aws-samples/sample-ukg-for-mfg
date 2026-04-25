"""
Discovery Agent — Registration Tools

Phase 4 tools for the Discovery Agent workflow. These tools write discovery
results to the DynamoDB System Registry in small incremental calls to avoid
oversized tool payloads that crash the AgentCore Runtime stream.

Tools:
  - register_system_metadata: Write system metadata + schema entries
  - register_fields: Write field items for a single table
  - register_equivalences: Write cross-system field equivalences
"""

import json
import logging
import os
import time

import boto3
from strands import tool

logger = logging.getLogger(__name__)

# Maximum items per DynamoDB BatchWriteItem call
_BATCH_WRITE_MAX = 25

# Retry configuration for unprocessed items
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5  # seconds


def _get_dynamodb_client():
    """Get a boto3 DynamoDB client."""
    return boto3.client(
        "dynamodb",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


def _get_table_name() -> str:
    """Get the registry table name from environment."""
    table_name = os.getenv("REGISTRY_TABLE_NAME")
    if not table_name:
        raise ValueError(
            "REGISTRY_TABLE_NAME environment variable is required. "
            "Set it in your .env file or deploy the Foundation stack via CDK."
        )
    return table_name


def _to_dynamodb_item(item: dict) -> dict:
    """Convert a plain dict to DynamoDB AttributeValue format."""
    ddb_item = {}
    for key, value in item.items():
        ddb_item[key] = _to_attribute_value(value)
    return ddb_item


def _to_attribute_value(value):
    """Convert a Python value to a DynamoDB AttributeValue."""
    if value is None:
        return {"NULL": True}
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, int):
        return {"N": str(value)}
    if isinstance(value, float):
        return {"N": str(value)}
    if isinstance(value, list):
        return {"L": [_to_attribute_value(v) for v in value]}
    if isinstance(value, dict):
        return {"M": {k: _to_attribute_value(v) for k, v in value.items()}}
    return {"S": str(value)}


def _build_metadata_item(system_metadata: dict) -> dict:
    """Build a DynamoDB item for System_Metadata. PK = SYSTEM#{system_id}, SK = METADATA."""
    system_id = system_metadata["system_id"]
    item = {"PK": f"SYSTEM#{system_id}", "SK": "METADATA"}
    for key, value in system_metadata.items():
        if key not in ("PK", "SK"):
            item[key] = value
    return item


def _build_schema_item(system_id: str, schema: dict) -> dict:
    """Build a DynamoDB item for Schema_Entry. PK = SYSTEM#{system_id}, SK = SCHEMA#{table_name}."""
    table_name = schema["table_name"]
    item = {"PK": f"SYSTEM#{system_id}", "SK": f"SCHEMA#{table_name}", "system_id": system_id}
    for key, value in schema.items():
        if key not in ("PK", "SK"):
            item[key] = value
    return item


def _build_field_item(system_id: str, field: dict) -> dict:
    """Build a DynamoDB item for Field_Item with GSI1 keys when concept_confidence >= 0.5."""
    table_name = field["table_name"]
    field_name = field["field_name"]
    item = {"PK": f"SYSTEM#{system_id}", "SK": f"FIELD#{table_name}#{field_name}", "system_id": system_id}
    for key, value in field.items():
        if key not in ("PK", "SK"):
            item[key] = value
    concept_id = field.get("concept_id")
    concept_confidence = field.get("concept_confidence", 0.0)
    if concept_id and concept_confidence >= 0.5:
        item["GSI1PK"] = f"CONCEPT#{concept_id}"
        item["GSI1SK"] = f"SYSTEM#{system_id}#{table_name}#{field_name}"
    return item


def _build_equivalence_item(equivalence: dict) -> dict:
    """Build a DynamoDB item for Field_Equivalence with GSI2 keys."""
    source_system = equivalence["source_system"]
    source_table = equivalence["source_table"]
    source_field = equivalence["source_field"]
    target_system = equivalence["target_system"]
    target_table = equivalence["target_table"]
    target_field = equivalence["target_field"]
    concept_id = equivalence.get("concept_id", "unknown")
    item = {
        "PK": f"SYSTEM#{source_system}",
        "SK": f"EQUIV#{source_table}#{source_field}\u2192{target_system}#{target_table}#{target_field}",
    }
    for key, value in equivalence.items():
        if key not in ("PK", "SK"):
            item[key] = value
    item["GSI2PK"] = f"EQUIV#{concept_id}"
    item["GSI2SK"] = f"{source_system}#{source_table}#{source_field}"
    return item


def _batch_write_items(client, table_name: str, items: list[dict]) -> tuple[list, list]:
    """Write items to DynamoDB using BatchWriteItem with retry for unprocessed items."""
    succeeded = []
    failed = []
    batches = [items[i:i + _BATCH_WRITE_MAX] for i in range(0, len(items), _BATCH_WRITE_MAX)]

    for batch in batches:
        put_requests = [{"PutRequest": {"Item": _to_dynamodb_item(item)}} for item in batch]
        unprocessed = put_requests
        retries = 0

        while unprocessed and retries <= _MAX_RETRIES:
            if retries > 0:
                backoff = _BACKOFF_BASE * (2 ** (retries - 1))
                logger.warning("Retrying %d unprocessed items (attempt %d/%d)", len(unprocessed), retries, _MAX_RETRIES)
                time.sleep(backoff)

            response = client.batch_write_item(RequestItems={table_name: unprocessed})
            unprocessed_items = response.get("UnprocessedItems", {}).get(table_name, [])

            processed_count = len(unprocessed) - len(unprocessed_items)
            if processed_count > 0:
                unprocessed_pks = set()
                for req in unprocessed_items:
                    item_data = req.get("PutRequest", {}).get("Item", {})
                    unprocessed_pks.add((item_data.get("PK", {}).get("S", ""), item_data.get("SK", {}).get("S", "")))
                for req in unprocessed:
                    item_data = req.get("PutRequest", {}).get("Item", {})
                    pk_sk = (item_data.get("PK", {}).get("S", ""), item_data.get("SK", {}).get("S", ""))
                    if pk_sk not in unprocessed_pks:
                        succeeded.append({"PK": pk_sk[0], "SK": pk_sk[1]})

            unprocessed = unprocessed_items
            retries += 1

        for req in unprocessed:
            item_data = req.get("PutRequest", {}).get("Item", {})
            failed.append({"PK": item_data.get("PK", {}).get("S", ""), "SK": item_data.get("SK", {}).get("S", "")})

    return succeeded, failed


@tool
def register_system_metadata(
    system_metadata: dict,
    schemas: list,
) -> str:
    """Register system metadata and schema entries in the System Registry.

    Call this FIRST in Phase 4. Writes the METADATA item and all SCHEMA items
    for a single system. Follow up with register_fields for each table, then
    register_equivalences.

    Args:
        system_metadata: Dict with system_id, name, plant, system_type, vendor,
            protocol, isa95_level, status, discovered_at, discovered_by,
            table_count, field_count.
        schemas: List of schema dicts, each with table_name, schema_name,
            primary_key. Keep lean — omit columns, sample_query, description.

    Returns:
        JSON string with write results.
    """
    try:
        system_id = system_metadata.get("system_id")
        if not system_id:
            return json.dumps({"success": False, "error": "system_metadata must contain 'system_id'"})

        table_name = _get_table_name()
        client = _get_dynamodb_client()

        all_items = [_build_metadata_item(system_metadata)]
        for schema in schemas:
            all_items.append(_build_schema_item(system_id, schema))

        logger.info("register_system_metadata(%s): writing 1 metadata + %d schemas", system_id, len(schemas))
        succeeded, failed = _batch_write_items(client, table_name, all_items)

        return json.dumps({
            "success": len(failed) == 0,
            "system_id": system_id,
            "total_items": len(all_items),
            "succeeded_count": len(succeeded),
            "failed_count": len(failed),
        }, default=str)

    except Exception as e:
        logger.error("register_system_metadata failed: %s — %s", type(e).__name__, e, exc_info=True)
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})


@tool
def register_fields(
    system_id: str,
    table_name: str,
    fields: list,
) -> str:
    """Register field items for a single table in the System Registry.

    Call this once per table after register_system_metadata. Each call writes
    only the fields for one table, keeping the tool payload small.

    Args:
        system_id: The system identifier (e.g. "cmms-api").
        table_name: The table these fields belong to (e.g. "Asset").
        fields: List of field dicts, each with field_name, data_type, is_key,
            nullable, concept_id, concept_confidence.

    Returns:
        JSON string with write results.
    """
    try:
        registry_table = _get_table_name()
        client = _get_dynamodb_client()

        all_items = []
        for field in fields:
            field["table_name"] = table_name
            all_items.append(_build_field_item(system_id, field))

        logger.info("register_fields(%s, %s): writing %d fields", system_id, table_name, len(all_items))
        succeeded, failed = _batch_write_items(client, registry_table, all_items)

        return json.dumps({
            "success": len(failed) == 0,
            "system_id": system_id,
            "table_name": table_name,
            "total_items": len(all_items),
            "succeeded_count": len(succeeded),
            "failed_count": len(failed),
        }, default=str)

    except Exception as e:
        logger.error("register_fields(%s, %s) failed: %s — %s", system_id, table_name, type(e).__name__, e, exc_info=True)
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})


def _get_registered_system_tables(client, table_name: str) -> set[str]:
    """Query the registry for all registered system_id.table_name pairs.

    Scans for SCHEMA# items and returns a set of "system_id.table_name" strings
    that represent valid equivalence endpoints.
    """
    valid = set()
    paginator = client.get_paginator("scan")
    for page in paginator.paginate(
        TableName=table_name,
        FilterExpression="begins_with(SK, :prefix)",
        ExpressionAttributeValues={":prefix": {"S": "SCHEMA#"}},
        ProjectionExpression="PK, SK",
    ):
        for item in page.get("Items", []):
            system_id = item["PK"]["S"].replace("SYSTEM#", "")
            tbl = item["SK"]["S"].replace("SCHEMA#", "")
            valid.add(f"{system_id}.{tbl}")
    return valid


@tool
def register_equivalences(
    equivalences: list,
) -> str:
    """Register cross-system field equivalences in the System Registry.

    Call this after all register_fields calls are complete. Validates that
    both source and target system_id + table_name exist in the registry.
    Equivalences referencing non-existent systems or tables are rejected.

    Args:
        equivalences: List of equivalence dicts, each with source_system,
            source_table, source_field, target_system, target_table,
            target_field, concept_id, confidence, transform.

    Returns:
        JSON string with write results including any rejected equivalences.
    """
    try:
        if not equivalences:
            return json.dumps({"success": True, "total_items": 0, "succeeded_count": 0, "failed_count": 0, "rejected_count": 0})

        table_name = _get_table_name()
        client = _get_dynamodb_client()

        # Validate: only allow equivalences where both endpoints exist
        valid_endpoints = _get_registered_system_tables(client, table_name)
        logger.info("register_equivalences: %d valid endpoints in registry", len(valid_endpoints))

        accepted = []
        rejected = []
        for eq in equivalences:
            src = f"{eq.get('source_system', '')}.{eq.get('source_table', '')}"
            tgt = f"{eq.get('target_system', '')}.{eq.get('target_table', '')}"
            missing = []
            if src not in valid_endpoints:
                missing.append(f"source {src}")
            if tgt not in valid_endpoints:
                missing.append(f"target {tgt}")
            if missing:
                rejected.append({"equivalence": eq, "reason": f"Not registered: {', '.join(missing)}"})
                logger.warning("register_equivalences: rejected %s ↔ %s — %s", src, tgt, missing)
            else:
                accepted.append(eq)

        if rejected:
            logger.warning("register_equivalences: %d/%d equivalences rejected (phantom targets)", len(rejected), len(equivalences))

        if not accepted:
            # Only include first few rejected items to keep response compact
            sample_rejected = [
                {"src": f"{r['equivalence'].get('source_system')}.{r['equivalence'].get('source_table')}",
                 "tgt": f"{r['equivalence'].get('target_system')}.{r['equivalence'].get('target_table')}",
                 "reason": r["reason"]}
                for r in rejected[:5]
            ]
            return json.dumps({
                "success": True,
                "total_items": len(equivalences),
                "succeeded_count": 0,
                "failed_count": 0,
                "rejected_count": len(rejected),
                "rejected_sample": sample_rejected,
                "message": "All equivalences rejected — use exact system_id and table_name from find_by_concept results.",
            }, default=str)

        all_items = [_build_equivalence_item(eq) for eq in accepted]

        logger.info("register_equivalences: writing %d equivalences (%d rejected)", len(all_items), len(rejected))
        succeeded, failed = _batch_write_items(client, table_name, all_items)

        result = {
            "success": len(failed) == 0,
            "total_items": len(equivalences),
            "succeeded_count": len(succeeded),
            "failed_count": len(failed),
            "rejected_count": len(rejected),
        }
        if rejected:
            # Only include first few rejected items to keep response compact
            result["rejected_sample"] = [
                {"src": f"{r['equivalence'].get('source_system')}.{r['equivalence'].get('source_table')}",
                 "tgt": f"{r['equivalence'].get('target_system')}.{r['equivalence'].get('target_table')}",
                 "reason": r["reason"]}
                for r in rejected[:5]
            ]
            result["message"] = "Some equivalences rejected — use exact system_id and table_name from find_by_concept results."

        return json.dumps(result, default=str)

    except Exception as e:
        logger.error("register_equivalences failed: %s — %s", type(e).__name__, e, exc_info=True)
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})


@tool
def log_discovery_session(
    system_id: str,
    system_name: str,
    action: str,
    system_type: str,
    source_type: str,
    status: str,
    table_count: int,
    field_count: int,
    correlation_count: int,
    equivalence_count: int,
    rejected_equivalence_count: int,
    duration_seconds: float,
    error_message: str = "",
) -> str:
    """Log a discovery session to the Discovery History table.

    Call this AFTER all Phase 4 registration is complete. Records the
    discovery session for audit and analytics purposes. Automatically
    loads detailed phase data (schemas, fields, equivalences) from DDB
    and stores it in the history record for the admin detail page.

    Args:
        system_id: The system identifier (e.g. "cmms-api").
        system_name: Human-readable system name (e.g. "CMMS REST API").
        action: One of "registered", "re-registered", "removed".
        system_type: ERP, MES, CMMS, PLM, or IoT.
        source_type: rds, api, mcp, or s3tables.
        status: "completed", "failed", or "partial".
        table_count: Number of tables discovered.
        field_count: Number of fields registered.
        correlation_count: Number of concepts mapped.
        equivalence_count: Number of cross-system equivalences created.
        rejected_equivalence_count: Number of equivalences rejected.
        duration_seconds: Total discovery duration in seconds.
        error_message: Error details if status is "failed" or "partial".

    Returns:
        JSON string with the discovery_id of the logged session.
    """
    import uuid
    from datetime import datetime, timezone

    try:
        history_table = os.getenv("DISCOVERY_HISTORY_TABLE_NAME")
        if not history_table:
            return json.dumps({
                "success": False,
                "error": "DISCOVERY_HISTORY_TABLE_NAME not configured",
            })

        registry_table = os.getenv("REGISTRY_TABLE_NAME", "")
        client = _get_dynamodb_client()
        discovery_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Load detailed phase data from DDB state for the history record
        detail_data = {}
        if registry_table:
            for phase in ("understand", "correlate"):
                try:
                    resp = client.get_item(
                        TableName=registry_table,
                        Key={"PK": {"S": "DISCOVERY_STATE#current"}, "SK": {"S": f"PHASE#{phase}"}},
                    )
                    phase_item = resp.get("Item")
                    if phase_item:
                        detail_data[phase] = phase_item.get("data", {}).get("S", "{}")
                except Exception as e:
                    logger.warning("log_discovery_session: failed to load %s data: %s", phase, e)

        item = {
            "discovery_id": {"S": discovery_id},
            "timestamp": {"S": timestamp},
            "system_id": {"S": system_id},
            "system_name": {"S": system_name},
            "action": {"S": action},
            "user_id": {"S": "discovery-agent"},
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
        if detail_data.get("understand"):
            item["understand_data"] = {"S": detail_data["understand"]}
        if detail_data.get("correlate"):
            item["correlate_data"] = {"S": detail_data["correlate"]}

        client.put_item(TableName=history_table, Item=item)

        logger.info("log_discovery_session: %s %s (%s)", action, system_id, discovery_id)

        return json.dumps({
            "success": True,
            "discovery_id": discovery_id,
            "timestamp": timestamp,
        })

    except Exception as e:
        logger.error("log_discovery_session failed: %s — %s", type(e).__name__, e)
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})
