"""
Discovery Agent — Intermediate State Tools

Tools for persisting and retrieving phase results to/from DynamoDB.
This allows the agent to offload large intermediate data (schemas, concept
mappings) from the conversation context and reload only what's needed
for subsequent phases.

The state table reuses the existing System Registry table with a special
PK prefix: DISCOVERY_STATE#{session_id}
"""

import json
import logging
import os

import boto3
from strands import tool

logger = logging.getLogger(__name__)

_dynamodb_client = None


def _get_client():
    global _dynamodb_client
    if _dynamodb_client is None:
        _dynamodb_client = boto3.client(
            "dynamodb",
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )
    return _dynamodb_client


def _get_table_name() -> str:
    table_name = os.getenv("REGISTRY_TABLE_NAME")
    if not table_name:
        raise ValueError("REGISTRY_TABLE_NAME environment variable is required.")
    return table_name


def save_state(phase: str, data_json: str, namespace: str = None) -> bool:
    """Internal helper — save raw JSON string to DDB. Returns True on success.

    Args:
        phase: Phase identifier (e.g. "inspect", "understand", "correlate").
        data_json: The JSON string to persist.
        namespace: Optional namespace scope. When provided, the sort key becomes
            ``PHASE#{phase}#{namespace}`` so that each namespace's data is stored
            independently. When ``None``, the unscoped key ``PHASE#{phase}`` is used.
    """
    try:
        table_name = _get_table_name()
        client = _get_client()
        pk = "DISCOVERY_STATE#current"
        sk = f"PHASE#{phase}#{namespace}" if namespace else f"PHASE#{phase}"
        client.put_item(
            TableName=table_name,
            Item={
                "PK": {"S": pk},
                "SK": {"S": sk},
                "phase": {"S": phase},
                "data": {"S": data_json},
            },
        )
        size_kb = len(data_json) / 1024
        logger.info("save_state(%s, namespace=%s): saved %.1f KB", phase, namespace, size_kb)
        return True
    except Exception as e:
        logger.error("save_state(%s, namespace=%s) failed: %s", phase, namespace, e)
        return False


@tool
def save_phase_results(phase: str, data: dict) -> str:
    """Save intermediate phase results to DynamoDB for later retrieval.

    Call this at the END of each phase to persist the results. This allows
    subsequent phases to load only what they need without relying on
    conversation history (which may be summarized or truncated).

    Args:
        phase: Phase identifier — one of "inspect", "understand", "correlate".
        data: The phase results as a JSON-serializable dict.

    Returns:
        JSON string confirming the save.
    """
    data_json = json.dumps(data, default=str)
    ok = save_state(phase, data_json)
    return json.dumps({
        "success": ok,
        "phase": phase,
        "size_kb": round(len(data_json) / 1024, 1),
    })


@tool
def load_phase_results(phase: str, namespace: str = None) -> str:
    """Load previously saved phase results from DynamoDB.

    Call this at the START of a phase to retrieve data from a prior phase.
    For the 'inspect' phase, this returns a SUMMARY (table/endpoint names and
    column counts) — use load_table_schema to get full details for individual tables.

    Args:
        phase: Phase identifier to load — one of "inspect", "understand", "correlate".
        namespace: Optional namespace scope. When provided, loads from
            ``PHASE#{phase}#{namespace}`` instead of ``PHASE#{phase}``.

    Returns:
        JSON string with the saved phase data (summary for inspect, full for others).
    """
    try:
        table_name = _get_table_name()
        client = _get_client()
        pk = "DISCOVERY_STATE#current"
        sk = f"PHASE#{phase}#{namespace}" if namespace else f"PHASE#{phase}"
        response = client.get_item(
            TableName=table_name,
            Key={"PK": {"S": pk}, "SK": {"S": sk}},
        )
        item = response.get("Item")
        if not item:
            return json.dumps({"success": False, "error": f"No saved results for phase '{phase}'"})
        data_json = item.get("data", {}).get("S", "{}")
        logger.info("load_phase_results(%s, namespace=%s): loaded %.1f KB", phase, namespace, len(data_json) / 1024)

        # For inspect phase, return a summary instead of the full payload
        if phase == "inspect":
            data = json.loads(data_json)
            tables = data.get("tables", [])
            endpoints = data.get("endpoints", [])
            if endpoints:
                summary = {
                    "success": True,
                    "source_type": "api",
                    "info": data.get("info", {}),
                    "endpoint_count": len(endpoints),
                    "endpoints": [{"path": e.get("path", ""), "method": e.get("method", ""), "summary": e.get("summary", "")} for e in endpoints],
                }
                return json.dumps(summary)
            elif tables:
                summary = {
                    "success": True,
                    "source_type": "rds" if data.get("schema_name") else "s3tables",
                    "table_count": len(tables),
                    "table_names": [t.get("table_name", "") for t in tables],
                    "tables": [{"table_name": t.get("table_name", ""), "column_count": len(t.get("columns", [])), "row_count": t.get("row_count")} for t in tables],
                }
                return json.dumps(summary)
            else:
                return data_json

        return data_json
    except Exception as e:
        logger.error("load_phase_results(%s) failed: %s", phase, e)
        return json.dumps({"success": False, "error": str(e)})


@tool
def load_table_schema(table_name_or_index: str, namespace: str = None) -> str:
    """Load the full schema for a single table/endpoint group from the saved inspect results.

    Use this during Phase 2 to process tables one at a time instead of loading
    the entire schema into the conversation at once.

    Args:
        table_name_or_index: The table name to retrieve, or "all" to get all tables
            (only use "all" for small schemas with < 5 tables).
        namespace: Optional namespace scope. When provided, loads inspect data from
            ``PHASE#inspect#{namespace}`` instead of ``PHASE#inspect``.

    Returns:
        JSON string with the full column details for the requested table.
    """
    try:
        table_name = _get_table_name()
        client = _get_client()
        pk = "DISCOVERY_STATE#current"
        sk = f"PHASE#inspect#{namespace}" if namespace else "PHASE#inspect"
        response = client.get_item(
            TableName=table_name,
            Key={"PK": {"S": pk}, "SK": {"S": sk}},
        )
        item = response.get("Item")
        if not item:
            return json.dumps({"success": False, "error": "No inspect results saved"})

        data = json.loads(item.get("data", {}).get("S", "{}"))

        # Handle API specs — return endpoints grouped by tag or path prefix
        endpoints = data.get("endpoints", [])
        if endpoints:
            if table_name_or_index == "all":
                return json.dumps({"success": True, "endpoints": endpoints, "count": len(endpoints)}, default=str)
            # Filter endpoints by path prefix match
            matched = [e for e in endpoints if table_name_or_index.lower() in e.get("path", "").lower()]
            if not matched:
                # Try matching by tag
                matched = [e for e in endpoints if table_name_or_index.lower() in str(e.get("tags", [])).lower()]
            return json.dumps({"success": True, "endpoints": matched, "count": len(matched)}, default=str)

        # Handle table schemas
        tables = data.get("tables", [])
        if table_name_or_index == "all":
            return json.dumps({"success": True, "tables": tables, "count": len(tables)}, default=str)

        for t in tables:
            if t.get("table_name", "").lower() == table_name_or_index.lower():
                return json.dumps({"success": True, "table": t}, default=str)

        return json.dumps({"success": False, "error": f"Table '{table_name_or_index}' not found"})

    except Exception as e:
        logger.error("load_table_schema(%s) failed: %s", table_name_or_index, e)
        return json.dumps({"success": False, "error": str(e)})
