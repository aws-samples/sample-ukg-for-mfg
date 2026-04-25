"""
AgentCore Gateway Lambda Target — System Registry Tools

Implements 4 registry read tools as a single Lambda function target
for AgentCore Gateway. Both the Orchestrator and Discovery agents access
these tools via the Gateway MCP protocol instead of bundling them directly.

Tools:
  - list_systems: List registered systems with optional filters
  - get_system_schema: Get tables and fields for a system
  - find_by_concept: Find fields mapped to a concept (compact or full)
  - find_equivalences: Find cross-system field mappings for a concept

Tool routing uses the bedrockAgentCoreToolName from the Lambda context,
stripping the target prefix per AWS docs.
"""

import json
import logging
import os
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key, Attr

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

DELIMITER = "___"


def _get_table():
    """Get the DynamoDB Table resource for the System Registry."""
    table_name = os.getenv("REGISTRY_TABLE_NAME")
    if not table_name:
        raise ValueError("REGISTRY_TABLE_NAME environment variable is required.")
    dynamodb = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "us-east-1"))
    return dynamodb.Table(table_name)


def _strip_prefix(tool_name: str) -> str:
    """Strip the target name prefix from the Gateway tool name."""
    if DELIMITER in tool_name:
        return tool_name[tool_name.index(DELIMITER) + len(DELIMITER):]
    return tool_name


# ---------------------------------------------------------------------------
# Bare-to-qualified concept ID resolution
# ---------------------------------------------------------------------------

# Cache resolved concept IDs for the Lambda invocation lifetime
_concept_cache: dict[str, list[str]] = {}


def _resolve_concept_id(table, concept_id: str, index: str, pk_prefix: str) -> list[str]:
    """Resolve a bare concept ID to domain-qualified variant(s).

    If concept_id already contains a dot (e.g. "production.work-order"), returns it as-is.
    Otherwise scans the index for all keys matching *.{bare_id} pattern.
    Results are cached for the Lambda invocation lifetime.
    """
    if "." in concept_id:
        return [concept_id]

    cache_key = f"{pk_prefix}:{concept_id}"
    if cache_key in _concept_cache:
        return _concept_cache[cache_key]

    # Scan the index for all partition keys, filter for ones ending with .{concept_id}
    suffix = f".{concept_id}"
    pk_attr = "GSI1PK" if index == "GSI1" else "GSI2PK"
    qualified = set()

    scan_kwargs = {
        "IndexName": index,
        "FilterExpression": Attr(pk_attr).begins_with(pk_prefix),
        "ProjectionExpression": pk_attr,
    }
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            pk_val = item.get(pk_attr, "")
            # e.g. "CONCEPT#production.work-order" → check if it ends with ".work-order"
            raw = pk_val.replace(pk_prefix, "", 1)
            if raw.endswith(suffix):
                qualified.add(raw)
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    result = list(qualified) if qualified else [concept_id]
    _concept_cache[cache_key] = result
    logger.info("_resolve_concept_id(%s, %s): %s", concept_id, index, result)
    return result


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def list_systems(event: dict) -> dict:
    """List all registered systems, optionally filtered by plant, type, or status."""
    plant = event.get("plant")
    system_type = event.get("system_type")
    status = event.get("status", "active")

    try:
        table = _get_table()
        filter_expr = Attr("SK").eq("METADATA")

        if status is not None:
            filter_expr = filter_expr & Attr("status").eq(status)
        if plant is not None:
            filter_expr = filter_expr & Attr("plant").eq(plant)
        if system_type is not None:
            filter_expr = filter_expr & Attr("system_type").eq(system_type)

        items = []
        scan_kwargs = {"FilterExpression": filter_expr}
        while True:
            response = table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

        logger.info("list_systems: %d systems (plant=%s, type=%s, status=%s)",
                     len(items), plant, system_type, status)

        # Strip DynamoDB internal keys
        systems = []
        for item in items:
            systems.append({
                "system_id": item.get("system_id", ""),
                "name": item.get("name", ""),
                "system_type": item.get("system_type", ""),
                "plant": item.get("plant", ""),
                "status": item.get("status", ""),
                "protocol": item.get("protocol", ""),
                "table_count": item.get("table_count", 0),
                "field_count": item.get("field_count", 0),
            })

        return {"success": True, "count": len(systems), "systems": systems}

    except Exception as e:
        logger.error("list_systems error: %s", e)
        return {"success": False, "error": str(e), "systems": []}


def get_system_schema(event: dict) -> dict:
    """Get all schema entries and field metadata for a specific system."""
    system_id = event.get("system_id", "").strip()
    if not system_id:
        return {"success": False, "error": "system_id is required", "schemas": [], "fields": []}

    try:
        table = _get_table()
        pk = f"SYSTEM#{system_id}"

        schema_resp = table.query(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with("SCHEMA#"),
        )
        raw_schemas = schema_resp.get("Items", [])

        field_resp = table.query(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with("FIELD#"),
        )
        raw_fields = field_resp.get("Items", [])

        # Strip DynamoDB internal keys — the agent only needs the domain data
        schemas = []
        for s in raw_schemas:
            schemas.append({
                "table_name": s.get("table_name", ""),
                "primary_key": s.get("primary_key", []),
                "description": s.get("description", ""),
            })

        fields = []
        for f in raw_fields:
            entry = {
                "table_name": f.get("table_name", ""),
                "field_name": f.get("field_name", ""),
                "data_type": f.get("data_type", ""),
                "is_key": f.get("is_key", False),
            }
            # Only include concept mapping if it exists
            concept_id = f.get("concept_id", "")
            if concept_id:
                entry["concept_id"] = concept_id
            fields.append(entry)

        logger.info("get_system_schema(%s): %d schemas, %d fields", system_id, len(schemas), len(fields))
        return {
            "success": True,
            "system_id": system_id,
            "schema_count": len(schemas),
            "field_count": len(fields),
            "schemas": schemas,
            "fields": fields,
        }

    except Exception as e:
        logger.error("get_system_schema(%s) error: %s", system_id, e)
        return {"success": False, "error": str(e), "system_id": system_id, "schemas": [], "fields": []}


def find_by_concept(event: dict) -> dict:
    """Find all systems and fields mapped to a manufacturing concept.

    Handles bare-ID resolution (e.g. "work-order" → ["production.work-order",
    "maintenance.work-order"]). When compact=true (default), returns only
    system_id, table_name, field_name, and data_type per mapping. When
    compact=false, returns full DynamoDB items.
    """
    concept_id = event.get("concept_id", "").strip()
    if not concept_id:
        return {"success": False, "error": "concept_id is required", "mappings": []}

    compact = event.get("compact", True)

    try:
        table = _get_table()
        qualified_ids = _resolve_concept_id(table, concept_id, "GSI1", "CONCEPT#")

        items = []
        for qid in qualified_ids:
            response = table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key("GSI1PK").eq(f"CONCEPT#{qid}"),
            )
            items.extend(response.get("Items", []))

        if compact:
            mappings = [
                {
                    "system_id": item.get("system_id", ""),
                    "table_name": item.get("table_name", ""),
                    "field_name": item.get("field_name", ""),
                    "data_type": item.get("data_type", ""),
                }
                for item in items
            ]
        else:
            mappings = items

        logger.info("find_by_concept(%s → %s, compact=%s): %d mappings",
                     concept_id, qualified_ids, compact, len(mappings))
        return {
            "success": True,
            "concept_id": concept_id,
            "resolved_ids": qualified_ids,
            "count": len(mappings),
            "mappings": mappings,
        }

    except Exception as e:
        logger.error("find_by_concept(%s) error: %s", concept_id, e)
        return {"success": False, "error": str(e), "concept_id": concept_id, "mappings": []}


def find_equivalences(event: dict) -> dict:
    """Find cross-system field mappings for a concept."""
    concept_id = event.get("concept_id", "").strip()
    if not concept_id:
        return {"success": False, "error": "concept_id is required", "equivalences": []}

    source_system = event.get("source_system")

    try:
        table = _get_table()
        qualified_ids = _resolve_concept_id(table, concept_id, "GSI2", "EQUIV#")

        items = []
        for qid in qualified_ids:
            gsi2pk = f"EQUIV#{qid}"
            query_kwargs = {
                "IndexName": "GSI2",
                "KeyConditionExpression": Key("GSI2PK").eq(gsi2pk),
            }
            if source_system:
                query_kwargs["KeyConditionExpression"] = (
                    Key("GSI2PK").eq(gsi2pk) & Key("GSI2SK").begins_with(f"{source_system}#")
                )
            response = table.query(**query_kwargs)
            items.extend(response.get("Items", []))

        logger.info("find_equivalences(%s → %s, source=%s): %d",
                     concept_id, qualified_ids, source_system, len(items))

        # Strip DynamoDB internal keys — agent only needs the equivalence data
        equivalences = []
        for item in items:
            equivalences.append({
                "concept_id": item.get("concept_id", ""),
                "source_system": item.get("source_system", ""),
                "source_table": item.get("source_table", ""),
                "source_field": item.get("source_field", ""),
                "target_system": item.get("target_system", ""),
                "target_table": item.get("target_table", ""),
                "target_field": item.get("target_field", ""),
                "confidence": item.get("confidence", ""),
            })

        return {
            "success": True,
            "concept_id": concept_id,
            "source_system": source_system,
            "count": len(equivalences),
            "equivalences": equivalences,
        }

    except Exception as e:
        logger.error("find_equivalences(%s) error: %s", concept_id, e)
        return {"success": False, "error": str(e), "concept_id": concept_id, "equivalences": []}


# ---------------------------------------------------------------------------
# Tool router
# ---------------------------------------------------------------------------

TOOL_MAP = {
    "list_systems": list_systems,
    "get_system_schema": get_system_schema,
    "find_by_concept": find_by_concept,
    "find_equivalences": find_equivalences,
}


def lambda_handler(event, context):
    """
    AgentCore Gateway Lambda handler.

    The Gateway passes:
    - event: dict of input properties from the tool's inputSchema
    - context.client_context.custom: Gateway metadata including bedrockAgentCoreToolName
    """
    logger.info("Event: %s", json.dumps(event, default=str))

    # Extract tool name from Gateway context
    try:
        original_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
        tool_name = _strip_prefix(original_tool_name)
    except (AttributeError, KeyError, TypeError):
        # Fallback: check if tool_name is passed directly (for local testing)
        tool_name = event.pop("tool_name", None)
        if not tool_name:
            return {"success": False, "error": "Could not determine tool name from context"}

    logger.info("Routing to tool: %s", tool_name)

    handler_fn = TOOL_MAP.get(tool_name)
    if not handler_fn:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}

    result = handler_fn(event)

    # DynamoDB Decimal types need serialization
    return json.loads(json.dumps(result, default=str))
