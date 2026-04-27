"""
Discovery Agent — Schema Analysis Sub-Agent Tool

Implements Phase 2 (UNDERSTAND) as a sub-agent tool. The sub-agent gets its
own fresh context window, loads the full inspect data from DDB, performs
concept mapping with LLM inference, saves results to DDB, and returns only
a compact summary to the parent agent.

This prevents the large schema data from ever entering the parent agent's
conversation context.
"""

import json
import logging
import os
from typing import AsyncIterator

from strands import Agent, tool
from strands.models import BedrockModel

from concepts import CANONICAL_CONCEPTS, get_all_concepts_serializable
from tools.inspect import inspect_athena_source, list_s3tables_namespaces
from tools.register import log_discovery_session
from tools.state import save_state, _get_client, _get_table_name

logger = logging.getLogger(__name__)

# Compact system prompt for the analysis sub-agent
_ANALYZE_PROMPT = """\
You are a schema analysis specialist. You will receive raw schema data (tables with columns, \
or API endpoints) and a list of canonical manufacturing concepts organized by ISA-95 domain.

Each concept has: id, domain, qualified_id (domain.id), description, and aliases \
(common field names seen in real systems).

Your job:
1. Classify the system type (ERP, MES, CMMS, PLM, IoT, or Weather/Other) and ISA-95 level.
2. For each table/endpoint group, generate a brief description (max 15 words).
3. Map each field/parameter to the most appropriate canonical concept. Use the aliases as \
   hints — if a field name closely matches an alias, prefer that concept. When the same \
   concept ID exists in multiple domains (e.g. "work-order" in production vs maintenance), \
   use the system type context to pick the correct domain. Return the domain-qualified \
   concept_id (e.g. "production.work-order" or "maintenance.work-order"). \
   Assign a confidence score (0.0-1.0). Fields with no good match get concept_id="" and confidence=0.0.

CRITICAL: Only return fields that are EXPLICITLY present in the input schema data. \
Do NOT invent, infer, or add fields that do not appear in the provided schema. \
The fields_by_table entries must be a 1-to-1 mapping of the input columns/parameters — \
no more, no less.

Return your analysis as a JSON object with this exact structure:
{
  "system_id": "suggested-system-id",
  "system_name": "Human Readable Name",
  "system_type": "ERP|MES|CMMS|PLM|IoT|Other",
  "isa95_level": 3,
  "source_type": "api|rds|s3tables|mcp",
  "schemas": [{"table_name": "...", "description": "...", "primary_key": ["..."]}],
  "fields_by_table": {
    "TableName": [
      {"field_name": "...", "data_type": "...", "is_key": false, "nullable": true, "concept_id": "domain.concept", "concept_confidence": 0.8}
    ]
  }
}

Return ONLY the JSON object, no markdown formatting, no explanation.
"""


def _load_inspect_data(namespace: str = None) -> dict:
    """Load the full inspect results from DDB.

    Args:
        namespace: Optional namespace scope. When provided, loads from
            ``PHASE#inspect#{namespace}`` instead of ``PHASE#inspect``.
    """
    client = _get_client()
    table_name = _get_table_name()
    sk = f"PHASE#inspect#{namespace}" if namespace else "PHASE#inspect"
    response = client.get_item(
        TableName=table_name,
        Key={"PK": {"S": "DISCOVERY_STATE#current"}, "SK": {"S": sk}},
    )
    item = response.get("Item")
    if not item:
        return {}
    return json.loads(item.get("data", {}).get("S", "{}"))


@tool
async def analyze_schema(namespace: str = None) -> AsyncIterator:
    """Analyze the inspected schema using a sub-agent with its own context window.

    This tool loads the full inspect data from DDB, sends it to a specialized
    sub-agent for concept mapping, saves the results to DDB, and returns a
    compact summary. The full schema data never enters the parent agent's context.

    Call this during Phase 2 (UNDERSTAND) instead of manually loading and
    analyzing the schema.

    Args:
        namespace: Optional namespace scope. When provided, loads inspect data
            from ``PHASE#inspect#{namespace}`` and saves understand results to
            ``PHASE#understand#{namespace}``. When None, uses the original
            unscoped keys (backward compatible).

    Returns:
        Compact summary of the analysis (system_id, table_count, field_count,
        concept_count). Full results are saved to DDB as phase="understand".
    """
    # Load full inspect data from DDB
    inspect_data = _load_inspect_data(namespace=namespace)
    if not inspect_data:
        yield json.dumps({"success": False, "error": "No inspect data found in DDB"})
        return

    # Build the analysis prompt with the full data + concepts
    analysis_input = json.dumps({
        "schema_data": inspect_data,
        "canonical_concepts": get_all_concepts_serializable(),
    }, default=str)

    # Create a sub-agent with its own clean context
    sub_model = BedrockModel(
        model_id=os.getenv("ANALYSIS_MODEL_ID", "global.anthropic.claude-sonnet-4-6"),
        max_tokens=32000,
    )

    sub_agent = Agent(
        model=sub_model,
        system_prompt=_ANALYZE_PROMPT,
        callback_handler=None,  # No streaming to parent
    )

    logger.info("analyze_schema: invoking sub-agent with %.1f KB of schema data",
                len(analysis_input) / 1024)

    try:
        # Invoke the sub-agent — it gets its own context window
        result = sub_agent(f"Analyze this schema and map concepts:\n\n{analysis_input}")

        # Extract the JSON from the sub-agent's response
        response_text = str(result)

        # Try to parse the JSON from the response
        # The sub-agent might wrap it in markdown code blocks
        json_text = response_text
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0].strip()
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0].strip()

        analysis = json.loads(json_text)

        # --- Deterministic field validation ---
        # Build ground-truth field sets from the inspect data
        inspect_tables = inspect_data.get("tables", [])
        inspect_endpoints = inspect_data.get("endpoints", [])
        if inspect_tables:
            # Build lookup: lowercase table name → set of lowercase column names
            ground_truth = {}
            for t in inspect_tables:
                tname = t.get("table_name", "").lower()
                ground_truth[tname] = {
                    c.get("column_name", "").lower() for c in t.get("columns", [])
                }
            # Strip hallucinated fields from the analysis
            fields_by_table = analysis.get("fields_by_table", {})
            for tname, fields in list(fields_by_table.items()):
                expected = ground_truth.get(tname.lower(), set())
                if not expected:
                    # No matching table in inspect data — skip validation
                    logger.info(
                        "analyze_schema: no ground-truth for table '%s', skipping validation",
                        tname,
                    )
                    continue
                original_count = len(fields)
                fields_by_table[tname] = [
                    f for f in fields
                    if f.get("field_name", "").lower() in expected
                ]
                kept = len(fields_by_table[tname])
                removed = original_count - kept
                if removed:
                    logger.warning(
                        "analyze_schema: stripped %d hallucinated field(s) from %s "
                        "(kept %d of %d)",
                        removed, tname, kept, original_count,
                    )

        # Save the full analysis to DDB
        analysis_json = json.dumps(analysis, default=str)
        save_state("understand", analysis_json, namespace=namespace)

        # Count fields and concepts
        fields_by_table = analysis.get("fields_by_table", {})
        total_fields = sum(len(fields) for fields in fields_by_table.values())
        concepts_mapped = sum(
            1 for fields in fields_by_table.values()
            for f in fields
            if f.get("concept_id") and f.get("concept_confidence", 0) >= 0.5
        )

        # Return compact summary to parent agent
        summary = {
            "success": True,
            "system_id": analysis.get("system_id", ""),
            "system_name": analysis.get("system_name", ""),
            "system_type": analysis.get("system_type", ""),
            "isa95_level": analysis.get("isa95_level", 0),
            "source_type": analysis.get("source_type", ""),
            "table_count": len(analysis.get("schemas", [])),
            "field_count": total_fields,
            "concepts_mapped": concepts_mapped,
            "tables": [s.get("table_name", "") for s in analysis.get("schemas", [])],
            "_saved_to_ddb": True,
        }

        yield json.dumps(summary)

    except json.JSONDecodeError as e:
        logger.error("analyze_schema: failed to parse sub-agent response: %s", e)
        yield json.dumps({
            "success": False,
            "error": f"Sub-agent returned invalid JSON: {e}",
            "raw_response_preview": response_text[:500],
        })
    except Exception as e:
        logger.error("analyze_schema failed: %s — %s", type(e).__name__, e, exc_info=True)
        yield json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})


# ========================================================================
# Phase 3: CORRELATE — Sub-agent tool
# ========================================================================

_CORRELATE_PROMPT = """\
You are a cross-system correlation specialist. You will receive:
1. A list of fields with their concept mappings from a newly discovered system
2. Registry query results showing existing systems with the same concepts

Your job is to identify cross-system field equivalences — fields in different systems \
that represent the same real-world concept and could be joined or translated.

For each potential equivalence, assess:
- Are the fields truly equivalent? (Consider names, data types, context)
- What is the confidence? (0.0-1.0, only create equivalences >= 0.7)
- What transform is needed? (direct, prefix_strip, suffix_strip, lookup_required, unit_conversion)

**CRITICAL**: Use the EXACT system_id and table_name values from the registry query results. \
Do NOT rename, abbreviate, or translate them.

Return a JSON object:
{
  "equivalences": [
    {
      "source_system": "...", "source_table": "...", "source_field": "...",
      "target_system": "...", "target_table": "...", "target_field": "...",
      "concept_id": "...", "confidence": 0.85, "transform": "direct"
    }
  ]
}

Return ONLY the JSON object, no markdown, no explanation.
"""


@tool
async def correlate_fields(namespace: str = None) -> AsyncIterator:
    """Find cross-system field equivalences using a sub-agent with its own context.

    Loads the understand results from DDB, queries the registry for existing
    concept mappings, and uses LLM inference to identify equivalences.
    Saves results to DDB and returns a compact summary.

    Call this during Phase 3 (CORRELATE).

    Args:
        namespace: Optional namespace scope. When provided, loads understand data
            from ``PHASE#understand#{namespace}`` and saves correlate results to
            ``PHASE#correlate#{namespace}``. When None, uses the original
            unscoped keys (backward compatible).

    Returns:
        Compact summary with equivalence count. Full results saved to DDB.
    """
    import boto3
    from boto3.dynamodb.conditions import Key

    try:
        # Load understand results
        client = _get_client()
        table_name = _get_table_name()
        understand_sk = f"PHASE#understand#{namespace}" if namespace else "PHASE#understand"
        response = client.get_item(
            TableName=table_name,
            Key={"PK": {"S": "DISCOVERY_STATE#current"}, "SK": {"S": understand_sk}},
        )
        item = response.get("Item")
        if not item:
            yield json.dumps({"success": False, "error": "No understand results found"})
            return

        understand_data = json.loads(item.get("data", {}).get("S", "{}"))
        fields_by_table = understand_data.get("fields_by_table", {})

        # Collect all unique concept_ids with confidence >= 0.5
        concept_ids = set()
        for fields in fields_by_table.values():
            for f in fields:
                cid = f.get("concept_id", "")
                if cid and f.get("concept_confidence", 0) >= 0.5:
                    concept_ids.add(cid)

        if not concept_ids:
            save_state("correlate", json.dumps({"equivalences": []}), namespace=namespace)
            yield json.dumps({"success": True, "equivalence_count": 0, "message": "No concepts to correlate"})
            return

        # Query registry for existing mappings (counts first)
        dynamodb = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "us-east-1"))
        reg_table = dynamodb.Table(table_name)

        # Find which concepts have existing mappings
        concepts_with_mappings = {}
        for cid in concept_ids:
            resp = reg_table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key("GSI1PK").eq(f"CONCEPT#{cid}"),
                Select="COUNT",
            )
            count = resp.get("Count", 0)
            if count > 0:
                concepts_with_mappings[cid] = count

        if not concepts_with_mappings:
            save_state("correlate", json.dumps({"equivalences": []}), namespace=namespace)
            yield json.dumps({"success": True, "equivalence_count": 0, "message": "No existing mappings found"})
            return

        # Get detailed mappings for concepts that have matches
        registry_mappings = {}
        for cid in concepts_with_mappings:
            resp = reg_table.query(
                IndexName="GSI1",
                KeyConditionExpression=Key("GSI1PK").eq(f"CONCEPT#{cid}"),
            )
            items = resp.get("Items", [])
            registry_mappings[cid] = [
                {"system_id": i.get("system_id", ""), "table_name": i.get("table_name", ""),
                 "field_name": i.get("field_name", ""), "data_type": i.get("data_type", "")}
                for i in items
            ]

        # Build input for the correlation sub-agent
        system_id = understand_data.get("system_id", "")
        correlation_input = json.dumps({
            "new_system_id": system_id,
            "new_system_fields": fields_by_table,
            "existing_registry_mappings": registry_mappings,
        }, default=str)

        # Invoke sub-agent
        sub_model = BedrockModel(
            model_id=os.getenv("ANALYSIS_MODEL_ID", "global.anthropic.claude-sonnet-4-6"),
            max_tokens=16000,
        )
        sub_agent = Agent(model=sub_model, system_prompt=_CORRELATE_PROMPT, callback_handler=None)

        logger.info("correlate_fields: %d concepts with mappings, invoking sub-agent", len(concepts_with_mappings))
        result = sub_agent(f"Find equivalences:\n\n{correlation_input}")

        response_text = str(result)
        json_text = response_text
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0].strip()
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0].strip()

        correlation = json.loads(json_text)
        equivalences = correlation.get("equivalences", [])

        save_state("correlate", json.dumps(correlation, default=str), namespace=namespace)

        yield json.dumps({
            "success": True,
            "equivalence_count": len(equivalences),
            "concepts_checked": len(concepts_with_mappings),
            "_saved_to_ddb": True,
        })

    except json.JSONDecodeError as e:
        logger.error("correlate_fields: invalid JSON from sub-agent: %s", e)
        save_state("correlate", json.dumps({"equivalences": []}), namespace=namespace)
        yield json.dumps({"success": False, "error": f"Sub-agent JSON error: {e}"})
    except Exception as e:
        logger.error("correlate_fields failed: %s", e, exc_info=True)
        yield json.dumps({"success": False, "error": str(e)})


# ========================================================================
# Phase 4: REGISTER — Sub-agent tool
# ========================================================================

@tool
async def register_all(namespace: str = None) -> AsyncIterator:
    """Register all discovery results in the System Registry using a sub-agent.

    Loads understand + correlate results from DDB, then writes system metadata,
    fields, and equivalences to the registry. Returns a compact summary.

    Call this during Phase 4 (REGISTER).

    Args:
        namespace: Optional namespace scope. When provided, loads understand data
            from ``PHASE#understand#{namespace}`` and correlate data from
            ``PHASE#correlate#{namespace}``. When None, uses the original
            unscoped keys (backward compatible).

    Returns:
        Summary with counts of registered items.
    """
    from tools.register import (
        _get_dynamodb_client, _get_table_name as _get_reg_table,
        _build_metadata_item, _build_schema_item, _build_field_item,
        _build_equivalence_item, _batch_write_items,
        _get_registered_system_tables,
    )

    try:
        # Load understand results
        client = _get_client()
        table_name = _get_table_name()

        understand_sk = f"PHASE#understand#{namespace}" if namespace else "PHASE#understand"
        resp = client.get_item(
            TableName=table_name,
            Key={"PK": {"S": "DISCOVERY_STATE#current"}, "SK": {"S": understand_sk}},
        )
        understand_item = resp.get("Item")
        if not understand_item:
            yield json.dumps({"success": False, "error": "No understand results"})
            return
        understand = json.loads(understand_item.get("data", {}).get("S", "{}"))

        # Load correlate results
        correlate_sk = f"PHASE#correlate#{namespace}" if namespace else "PHASE#correlate"
        resp = client.get_item(
            TableName=table_name,
            Key={"PK": {"S": "DISCOVERY_STATE#current"}, "SK": {"S": correlate_sk}},
        )
        correlate_item = resp.get("Item")
        equivalences = []
        if correlate_item:
            correlate = json.loads(correlate_item.get("data", {}).get("S", "{}"))
            equivalences = correlate.get("equivalences", [])

        system_id = understand.get("system_id", "")
        schemas = understand.get("schemas", [])
        fields_by_table = understand.get("fields_by_table", {})

        reg_table = _get_reg_table()
        reg_client = _get_dynamodb_client()

        # Step 1: Register metadata + schemas
        source_type = understand.get("source_type", "")

        # Map source_type → protocol for the query router
        _SOURCE_TO_PROTOCOL = {
            "s3tables": "s3tables",
            "rds": "rds-data-api",
            "api": "openapi",
            "mcp": "mcp",
        }
        protocol = _SOURCE_TO_PROTOCOL.get(source_type, source_type)

        # Build connection_config so the query router can execute queries
        connection_config = {}
        if protocol == "s3tables":
            # Load inspect data to get catalog and database
            inspect_sk = f"PHASE#inspect#{namespace}" if namespace else "PHASE#inspect"
            inspect_resp = client.get_item(
                TableName=table_name,
                Key={"PK": {"S": "DISCOVERY_STATE#current"}, "SK": {"S": inspect_sk}},
            )
            inspect_item = inspect_resp.get("Item")
            if inspect_item:
                inspect_data = json.loads(inspect_item.get("data", {}).get("S", "{}"))
                catalog = inspect_data.get("catalog", "")
                database = inspect_data.get("database", namespace or "")
                # Default output location matches inspect_athena_source logic
                _region = os.getenv("AWS_REGION", "us-east-1")
                _account_id = ""
                try:
                    import boto3 as _boto3
                    _account_id = _boto3.client("sts", region_name=_region).get_caller_identity()["Account"]
                except Exception:
                    pass
                _app_name = os.getenv("APP_NAME", "mfg-ukg")
                connection_config = {
                    "catalog": catalog,
                    "database": database,
                    "workgroup": os.getenv("ATHENA_WORKGROUP", "primary"),
                    "output_location": f"s3://athena-{_account_id}-{_region}/results/",
                }

        from datetime import datetime, timezone

        system_metadata = {
            "system_id": system_id,
            "name": understand.get("system_name", system_id),
            "system_type": understand.get("system_type", ""),
            "isa95_level": understand.get("isa95_level", 0),
            "source_type": source_type,
            "protocol": protocol,
            "connection_config": connection_config,
            "status": "active",
            "table_count": len(schemas),
            "field_count": sum(len(f) for f in fields_by_table.values()),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

        metadata_items = [_build_metadata_item(system_metadata)]
        for schema in schemas:
            metadata_items.append(_build_schema_item(system_id, schema))

        succeeded_meta, failed_meta = _batch_write_items(reg_client, reg_table, metadata_items)
        logger.info("register_all: metadata + %d schemas written", len(schemas))

        # Step 2: Register fields — accumulate across all tables into one
        # batched write. _batch_write_items handles 25-item chunking + retry
        # internally, so a single call is optimal regardless of field count.
        all_field_items = []
        for tbl_name, fields in fields_by_table.items():
            for field in fields:
                field["table_name"] = tbl_name
                all_field_items.append(_build_field_item(system_id, field))

        total_fields_written = 0
        total_fields_failed = 0
        if all_field_items:
            succeeded_f, failed_f = _batch_write_items(reg_client, reg_table, all_field_items)
            total_fields_written = len(succeeded_f)
            total_fields_failed = len(failed_f)

        logger.info("register_all: %d fields written, %d failed", total_fields_written, total_fields_failed)

        # Step 3: Register equivalences (with validation)
        equiv_written = 0
        equiv_rejected = 0
        if equivalences:
            valid_endpoints = _get_registered_system_tables(reg_client, reg_table)
            accepted = []
            for eq in equivalences:
                src = f"{eq.get('source_system', '')}.{eq.get('source_table', '')}"
                tgt = f"{eq.get('target_system', '')}.{eq.get('target_table', '')}"
                if src in valid_endpoints and tgt in valid_endpoints:
                    accepted.append(eq)
                else:
                    equiv_rejected += 1

            if accepted:
                equiv_items = [_build_equivalence_item(eq) for eq in accepted]
                s, f = _batch_write_items(reg_client, reg_table, equiv_items)
                equiv_written = len(s)

        logger.info("register_all: %d equivalences written, %d rejected", equiv_written, equiv_rejected)

        yield json.dumps({
            "success": total_fields_failed == 0,
            "system_id": system_id,
            "schemas_registered": len(schemas),
            "fields_registered": total_fields_written,
            "fields_failed": total_fields_failed,
            "equivalences_registered": equiv_written,
            "equivalences_rejected": equiv_rejected,
        })

    except Exception as e:
        logger.error("register_all failed: %s", e, exc_info=True)
        yield json.dumps({"success": False, "error": str(e)})


# ========================================================================
# S3 Tables Bucket Discovery — Multi-Namespace Orchestrator
# ========================================================================

@tool
async def discover_s3tables_bucket(
    bucket_name: str,
    workgroup: str = "primary",
    output_location: str = "",
) -> AsyncIterator:
    """Discover all namespaces in an S3 Tables bucket through the full 5-phase pipeline.

    Lists all namespaces in the bucket, then processes each namespace sequentially
    through all 5 phases (inspect → analyze → correlate → register → log) using
    namespace-scoped state keys. Each phase's sub-agent gets its own context window,
    preventing context overflow in the parent agent.

    Args:
        bucket_name: S3 Tables bucket name
            (e.g. "mfg-ukg-manufacturing-136380264626-us-east-2").
        workgroup: Athena workgroup (default "primary").
        output_location: S3 path for Athena query results. If empty, auto-generated.

    Returns:
        Consolidated JSON summary with per-namespace results including system_id,
        tables, fields, concepts_mapped, and equivalences for each namespace.
    """
    import time as _time

    def _emit(payload: dict) -> str:
        """Serialize a progress payload for Strands to stream back to the
        orchestrator as a ``tool_stream_event``. The orchestrator's invoke
        loop parses each of these and re-emits them as ``TextStreamEvent``
        markdown for the chat UI."""
        return json.dumps(payload)

    overall_start = _time.time()
    catalog = f"s3tablescatalog/{bucket_name}"

    # Step 1: List all namespaces in the bucket
    logger.info("discover_s3tables_bucket: listing namespaces for bucket %s", bucket_name)
    try:
        ns_result_json = list_s3tables_namespaces(bucket_name=bucket_name)
        ns_result = json.loads(ns_result_json)
        if not ns_result.get("success"):
            yield json.dumps({
                "success": False,
                "error": f"Failed to list namespaces: {ns_result.get('error', 'unknown')}",
            })
            return
        namespaces = ns_result.get("namespaces", [])
    except Exception as e:
        logger.error("discover_s3tables_bucket: failed to list namespaces: %s", e)
        yield json.dumps({"success": False, "error": f"Failed to list namespaces: {e}"})
        return

    if not namespaces:
        yield json.dumps({
            "success": True,
            "bucket_name": bucket_name,
            "namespace_count": 0,
            "namespaces_processed": 0,
            "results": [],
        })
        return

    logger.info("discover_s3tables_bucket: found %d namespaces: %s", len(namespaces), namespaces)

    # Yield initial progress message so the agent can relay namespace list to the user
    logger.info("discover_s3tables_bucket: yielding 'progress' event (namespace_count=%d)", len(namespaces))
    yield _emit({
        "type": "progress",
        "message": f"Found {len(namespaces)} namespaces: {', '.join(namespaces)}. Starting discovery...",
        "namespace_count": len(namespaces),
        "namespaces": namespaces,
    })

    # Step 2: Process each namespace through all 5 phases sequentially
    results = []
    try:
        for ns in namespaces:
            ns_start = _time.time()
            ns_result_entry = {
                "namespace": ns,
                "system_id": "",
                "tables": 0,
                "fields": 0,
                "concepts_mapped": 0,
                "equivalences": 0,
                "status": "failed",
                "error": "",
            }

            try:
                # Phase 1: INSPECT — sync @tool function, call directly
                logger.info("discover_s3tables_bucket: [%s] Phase 1 — INSPECT", ns)
                yield _emit({
                    "type": "phase_update",
                    "namespace": ns,
                    "namespace_progress": f"{len(results) + 1}/{len(namespaces)}",
                    "phase": "inspect",
                    "phase_number": 1,
                    "message": f"[{ns}] Phase 1/5 — Inspecting schema…",
                })
                inspect_result_json = inspect_athena_source(
                    database=ns,
                    catalog=catalog,
                    workgroup=workgroup,
                    output_location=output_location,
                )
                inspect_result = json.loads(inspect_result_json)
                if not inspect_result.get("success"):
                    ns_result_entry["error"] = f"Inspect failed: {inspect_result.get('error', 'unknown')}"
                    results.append(ns_result_entry)
                    yield _emit({
                        "type": "namespace_result",
                        "progress": f"{len(results)}/{len(namespaces)}",
                        "namespace": ns,
                        "status": ns_result_entry["status"],
                        "system_id": ns_result_entry.get("system_id", ""),
                        "tables": ns_result_entry.get("tables", 0),
                        "fields": ns_result_entry.get("fields", 0),
                        "concepts_mapped": ns_result_entry.get("concepts_mapped", 0),
                        "equivalences": ns_result_entry.get("equivalences", 0),
                        "error": ns_result_entry.get("error", ""),
                        "duration_seconds": round(_time.time() - ns_start, 1),
                    })
                    continue

                ns_result_entry["tables"] = inspect_result.get("table_count", 0)

                # Phase 2: UNDERSTAND — async generator, consume to get result
                logger.info("discover_s3tables_bucket: [%s] Phase 2 — UNDERSTAND", ns)
                yield _emit({
                    "type": "phase_update",
                    "namespace": ns,
                    "namespace_progress": f"{len(results) + 1}/{len(namespaces)}",
                    "phase": "understand",
                    "phase_number": 2,
                    "message": f"[{ns}] Phase 2/5 — Analyzing {ns_result_entry['tables']} tables…",
                })
                analyze_json = ""
                async for chunk in analyze_schema(namespace=ns):
                    analyze_json += str(chunk)
                analyze_result = json.loads(analyze_json)
                if not analyze_result.get("success"):
                    ns_result_entry["error"] = f"Analyze failed: {analyze_result.get('error', 'unknown')}"
                    results.append(ns_result_entry)
                    yield _emit({
                        "type": "namespace_result",
                        "progress": f"{len(results)}/{len(namespaces)}",
                        "namespace": ns,
                        "status": ns_result_entry["status"],
                        "system_id": ns_result_entry.get("system_id", ""),
                        "tables": ns_result_entry.get("tables", 0),
                        "fields": ns_result_entry.get("fields", 0),
                        "concepts_mapped": ns_result_entry.get("concepts_mapped", 0),
                        "equivalences": ns_result_entry.get("equivalences", 0),
                        "error": ns_result_entry.get("error", ""),
                        "duration_seconds": round(_time.time() - ns_start, 1),
                    })
                    continue

                ns_result_entry["system_id"] = analyze_result.get("system_id", "")
                ns_result_entry["fields"] = analyze_result.get("field_count", 0)
                ns_result_entry["concepts_mapped"] = analyze_result.get("concepts_mapped", 0)

                # Phase 3: CORRELATE — async generator, consume to get result
                logger.info("discover_s3tables_bucket: [%s] Phase 3 — CORRELATE", ns)
                yield _emit({
                    "type": "phase_update",
                    "namespace": ns,
                    "namespace_progress": f"{len(results) + 1}/{len(namespaces)}",
                    "phase": "correlate",
                    "phase_number": 3,
                    "message": f"[{ns}] Phase 3/5 — Correlating {ns_result_entry['concepts_mapped']} concepts…",
                })
                correlate_json = ""
                async for chunk in correlate_fields(namespace=ns):
                    correlate_json += str(chunk)
                correlate_result = json.loads(correlate_json)
                equiv_count = correlate_result.get("equivalence_count", 0)

                # Phase 4: REGISTER — async generator, consume to get result
                logger.info("discover_s3tables_bucket: [%s] Phase 4 — REGISTER", ns)
                yield _emit({
                    "type": "phase_update",
                    "namespace": ns,
                    "namespace_progress": f"{len(results) + 1}/{len(namespaces)}",
                    "phase": "register",
                    "phase_number": 4,
                    "message": f"[{ns}] Phase 4/5 — Registering in system registry…",
                })
                register_json = ""
                async for chunk in register_all(namespace=ns):
                    register_json += str(chunk)
                register_result = json.loads(register_json)
                if not register_result.get("success"):
                    ns_result_entry["error"] = f"Register failed: {register_result.get('error', 'unknown')}"
                    results.append(ns_result_entry)
                    yield _emit({
                        "type": "namespace_result",
                        "progress": f"{len(results)}/{len(namespaces)}",
                        "namespace": ns,
                        "status": ns_result_entry["status"],
                        "system_id": ns_result_entry.get("system_id", ""),
                        "tables": ns_result_entry.get("tables", 0),
                        "fields": ns_result_entry.get("fields", 0),
                        "concepts_mapped": ns_result_entry.get("concepts_mapped", 0),
                        "equivalences": ns_result_entry.get("equivalences", 0),
                        "error": ns_result_entry.get("error", ""),
                        "duration_seconds": round(_time.time() - ns_start, 1),
                    })
                    continue

                ns_result_entry["equivalences"] = register_result.get("equivalences_registered", 0)

                # Phase 5: LOG — sync @tool function, call directly
                logger.info("discover_s3tables_bucket: [%s] Phase 5 — LOG", ns)
                yield _emit({
                    "type": "phase_update",
                    "namespace": ns,
                    "namespace_progress": f"{len(results) + 1}/{len(namespaces)}",
                    "phase": "log",
                    "phase_number": 5,
                    "message": f"[{ns}] Phase 5/5 — Logging discovery session…",
                })
                ns_duration = _time.time() - ns_start
                log_discovery_session(
                    system_id=ns_result_entry["system_id"],
                    system_name=analyze_result.get("system_name", ns),
                    action="registered",
                    system_type=analyze_result.get("system_type", "Other"),
                    source_type="s3tables",
                    status="completed",
                    table_count=ns_result_entry["tables"],
                    field_count=ns_result_entry["fields"],
                    correlation_count=ns_result_entry["concepts_mapped"],
                    equivalence_count=ns_result_entry["equivalences"],
                    rejected_equivalence_count=register_result.get("equivalences_rejected", 0),
                    duration_seconds=ns_duration,
                )

                ns_result_entry["status"] = "completed"
                logger.info(
                    "discover_s3tables_bucket: [%s] completed — %d tables, %d fields, %d concepts, %d equivalences (%.1fs)",
                    ns, ns_result_entry["tables"], ns_result_entry["fields"],
                    ns_result_entry["concepts_mapped"], ns_result_entry["equivalences"], ns_duration,
                )

            except Exception as e:
                logger.error("discover_s3tables_bucket: [%s] failed: %s — %s", ns, type(e).__name__, e, exc_info=True)
                ns_result_entry["error"] = f"{type(e).__name__}: {e}"

            results.append(ns_result_entry)
            logger.info(
                "discover_s3tables_bucket: yielding 'namespace_result' ns=%s status=%s (%d/%d)",
                ns,
                ns_result_entry["status"],
                len(results),
                len(namespaces),
            )
            yield _emit({
                "type": "namespace_result",
                "progress": f"{len(results)}/{len(namespaces)}",
                "namespace": ns,
                "status": ns_result_entry["status"],
                "system_id": ns_result_entry.get("system_id", ""),
                "tables": ns_result_entry.get("tables", 0),
                "fields": ns_result_entry.get("fields", 0),
                "concepts_mapped": ns_result_entry.get("concepts_mapped", 0),
                "equivalences": ns_result_entry.get("equivalences", 0),
                "error": ns_result_entry.get("error", ""),
                "duration_seconds": round(_time.time() - ns_start, 1),
            })
    except Exception as e:
        logger.error("discover_s3tables_bucket: loop failed: %s", e, exc_info=True)

    # Step 3: Build consolidated summary (always executes, even after loop failure)
    namespaces_processed = sum(1 for r in results if r["status"] == "completed")
    overall_duration = _time.time() - overall_start

    # Strip error field from successful results for cleaner output
    clean_results = []
    for r in results:
        entry = {
            "namespace": r["namespace"],
            "system_id": r["system_id"],
            "tables": r["tables"],
            "fields": r["fields"],
            "concepts_mapped": r["concepts_mapped"],
            "equivalences": r["equivalences"],
        }
        if r["status"] != "completed":
            entry["status"] = r["status"]
            entry["error"] = r["error"]
        clean_results.append(entry)

    summary = {
        "type": "summary",
        "success": namespaces_processed == len(namespaces),
        "bucket_name": bucket_name,
        "namespace_count": len(namespaces),
        "namespaces_processed": namespaces_processed,
        "namespaces_failed": len(namespaces) - namespaces_processed,
        "duration_seconds": round(overall_duration, 1),
        "results": clean_results,
    }

    logger.info(
        "discover_s3tables_bucket: done — %d/%d namespaces processed in %.1fs",
        namespaces_processed, len(namespaces), overall_duration,
    )

    yield json.dumps(summary)
