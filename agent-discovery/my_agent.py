"""
Discovery Agent — AgentCore Runtime entry point.

Architecture:
- Inspection tools (inspect_rds_schema, inspect_api_spec, inspect_mcp_server) — Phase 1
- Discovery helpers (get_canonical_concepts) — Phase 2-3
- Registration tools (register_system) — Phase 4
- Guardrails — content filtering (shadow mode)
- Memory — conversation persistence via AgentCore Memory
"""

import json
import os
import re
import time

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands import Agent
from strands.models import BedrockModel
from strands.agent.conversation_manager import SummarizingConversationManager
from strands.hooks import AgentInitializedEvent, HookProvider, HookRegistry, MessageAddedEvent

from config import DiscoveryConfig
from guardrails import NotifyOnlyGuardrailsHook
from logger import setup_logger
from telemetry import is_telemetry_initialized, setup_telemetry
from tools.discovery_helpers import get_canonical_concepts
from tools.inspect import inspect_api_spec, inspect_mcp_server, inspect_rds_schema, inspect_athena_source, list_s3tables_namespaces
from tools.register import register_system_metadata, register_fields, register_equivalences, log_discovery_session
from tools.state import save_phase_results, load_phase_results, load_table_schema
from tools.analyze import analyze_schema, correlate_fields, register_all, discover_s3tables_bucket

# Gateway MCP client for shared registry tools
from strands.tools.mcp import MCPClient
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

DISCOVERY_MODEL = "us.anthropic.claude-sonnet-4-6"

DISCOVERY_PROMPT = """\
You are the Data Discovery Agent for a manufacturing universal knowledge graph platform. Your job is to \
inspect new data sources, catalog their schema, infer semantic mappings, discover \
cross-system equivalences, and register everything in the System Registry.

**CRITICAL — STATE MANAGEMENT**: After each phase, call `save_phase_results` to persist \
your results. At the start of each phase (except Phase 1), call `load_phase_results` to \
retrieve prior phase data. This prevents data loss when the conversation context is compressed.

Follow this strict 5-phase workflow for every discovery request:

## Phase 1: INSPECT

Extract the raw schema from the target data source using the appropriate inspection tool:
- For RDS/PostgreSQL databases: use `inspect_rds_schema` with the cluster ARN, secret ARN, database, and schema name.
- For REST APIs with OpenAPI specs: use `inspect_api_spec` with the spec URL.
- For MCP servers: use `inspect_mcp_server` with the server URL.
- For S3 Tables, Iceberg, or any Glue-cataloged data: use `inspect_athena_source` with the Glue database name. \
For S3 Tables specifically, set catalog to "s3tablescatalog/<bucket-name>" \
(e.g. "s3tablescatalog/mfg-ukg-manufacturing-123456789012-us-east-2"). \
**IMPORTANT for S3 Tables**: Use `discover_s3tables_bucket` with the bucket name. \
This tool handles the entire multi-namespace pipeline automatically — it lists all \
namespaces, then processes each through all 5 phases (inspect → analyze → correlate → \
register → log) with isolated contexts. You do NOT need to call the individual phase \
tools for S3 Tables. \
\
The tool yields incremental results as it works: \
1. First, a "progress" yield with the namespace list and count. \
2. After each namespace completes (or fails), a "namespace_result" yield with status, \
tables, fields, concepts_mapped, equivalences, and duration. \
3. Finally, a "summary" yield with consolidated counts. \
\
**Relay progress to the user as each namespace result arrives** — for example: \
"✓ Discovered erp — 8 tables, 65 fields, 12 concepts mapped" or \
"✗ Failed cmms — Inspect failed: timeout". This gives the user real-time visibility. \
\
Use the final "summary" yield to produce the detailed report. If only partial results \
arrive (some namespaces failed or the tool was interrupted), still report what was \
discovered using whatever namespace_result yields you received.

Collect all tables, columns, data types, primary keys, foreign keys, and row counts.

The inspect tools automatically save full results to DynamoDB and return only a compact \
summary to keep the conversation lean. You do NOT need to call `save_phase_results` after \
Phase 1 — it's already saved.

## Phase 2: UNDERSTAND

Call `analyze_schema` — this is a sub-agent tool that:
1. Loads the full inspect data from DDB (in its own context window)
2. Maps every field to canonical manufacturing concepts using LLM inference
3. Saves the full analysis to DDB as phase="understand"
4. Returns a compact summary (system_id, table_count, field_count, concepts_mapped)

You do NOT need to manually load data, call get_canonical_concepts, or save results. \
The sub-agent handles everything. Just call `analyze_schema` and review the summary.

## Phase 3: CORRELATE

Call `correlate_fields` — this sub-agent tool:
1. Loads the understand results from DDB
2. Queries the registry for existing concept mappings
3. Uses LLM inference to identify cross-system equivalences
4. Saves equivalences to DDB as phase="correlate"
5. Returns a compact summary (equivalence_count, concepts_checked)

## Phase 4: REGISTER

Call `register_all` — this sub-agent tool:
1. Loads understand + correlate results from DDB
2. Writes system metadata, schemas, fields, and equivalences to the registry
3. Validates equivalence endpoints exist before writing
4. Returns a summary (schemas_registered, fields_registered, equivalences_registered)

## Phase 5: LOG

Call `log_discovery_session` with the counts from Phase 4's result. \
Set action to "registered" for new systems or "re-registered" if already in the registry.

## Important Rules

- Always complete all 5 phases in order. Do not skip phases.
- **S3 Tables exception**: For S3 Tables buckets, call `discover_s3tables_bucket` instead of \
running the 5 phases manually. It handles all phases internally for every namespace and \
yields incremental progress per namespace. Relay each namespace result to the user as it \
arrives. After the final summary yield, produce the detailed report. If only partial \
results arrive, still report what was discovered.
- Each phase is a single tool call — the sub-agents handle everything internally.
- Never expose raw connection strings or credentials in your responses.
- If inspection fails, report the error clearly and do not proceed.
- **FINAL REPORT** — After Phase 5, output a detailed summary including:
  - System name and ID
  - Each table/endpoint group with field count
  - Number of concepts mapped
  - Each cross-system equivalence (source → target, concept, confidence, transform)
  - Any rejected equivalences with reasons
  - Discovery ID for reference
- **FORMATTING** — Always put a blank line before markdown headings and bullet lists.
"""

app = BedrockAgentCoreApp()

_config = None
_logger = None
_memory_client = None


def get_config():
    """Lazy-initialize configuration, logger, memory client, and telemetry."""
    global _config, _logger, _memory_client
    if _config is None:
        _config = DiscoveryConfig.from_env()
        _logger = setup_logger(__name__, _config.log_level)
        _memory_client = MemoryClient(region_name=_config.aws_region)
        if not is_telemetry_initialized():
            setup_telemetry(
                enabled=_config.otel_enabled,
                otlp_endpoint=_config.otel_endpoint,
                console_export=_config.otel_console_export,
                service_name="discovery-agent",
            )
    return _config, _logger, _memory_client


class MemoryHook(HookProvider):
    """Load/save conversation history via AgentCore Memory."""

    def on_agent_initialized(self, event):
        """Load previous conversation history into the agent's system prompt."""
        config, log, mem_client = get_config()
        if not config.memory_id:
            return
        session_id = event.agent.state.get("session_id") or "default"
        user_id = event.agent.state.get("user_id") or "anonymous"
        try:
            events = mem_client.list_events(
                memory_id=config.memory_id,
                actor_id=user_id,
                session_id=session_id,
                max_results=20,
            )
            if events:
                lines = []
                for ev in events:
                    payload = ev.get("payload", {})
                    msg = payload.get("message", {})
                    role = msg.get("role", "")
                    content_blocks = msg.get("content", [])
                    text = " ".join(b.get("text", "") for b in content_blocks if "text" in b)
                    if text.strip():
                        lines.append(f"{role}: {text[:500]}")
                if lines:
                    context = "\n".join(lines[-10:])
                    event.agent.system_prompt += f"\n\nPrevious conversation history:\n{context}"
        except Exception as e:
            log.error(f"Memory load error: {e}", exc_info=True)

    def on_message_added(self, event):
        """Save new messages to AgentCore Memory."""
        try:
            config, log, mem_client = get_config()
            if not config.memory_id:
                return
            session_id = event.agent.state.get("session_id") or "default"
            user_id = event.agent.state.get("user_id") or "anonymous"
            msg = event.message
            if not msg or not isinstance(msg, dict):
                return
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                return
            content = msg.get("content", [])
            has_tool = any("toolUse" in b or "toolResult" in b for b in content if isinstance(b, dict))
            if has_tool:
                return
            text = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and "text" in b)
            text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()
            if not text:
                return
            mem_client.create_event(
                memory_id=config.memory_id,
                actor_id=user_id,
                session_id=session_id,
                message=msg,
            )
        except Exception as e:
            get_config()[1].error(f"Memory save error: {e}", exc_info=True)

    def register_hooks(self, registry: HookRegistry):
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        registry.add_callback(MessageAddedEvent, self.on_message_added)


@app.entrypoint
async def invoke(payload, context):
    """Discovery Agent — 4-phase schema discovery and registration."""
    start_time = time.time()
    config, log, _ = get_config()

    session_id = getattr(context, "session_id", None) or "default"
    user_id = payload.get("userId", "anonymous")
    model_id = payload.get("modelId", DISCOVERY_MODEL)

    guardrail_id = payload.get("guardrailId") or config.guardrail_id
    guardrail_version = payload.get("guardrailVersion") or config.guardrail_version
    guardrail_enabled = payload.get("guardrailEnabled", config.guardrail_enabled)
    if isinstance(guardrail_enabled, str):
        guardrail_enabled = guardrail_enabled.lower() in ("true", "1", "yes")

    guardrails_hook = NotifyOnlyGuardrailsHook(
        guardrail_id=guardrail_id,
        guardrail_version=guardrail_version,
        region=config.aws_region,
        enabled=guardrail_enabled,
    )
    hooks = [MemoryHook(), guardrails_hook]

    tools = [
        # Phase 1: INSPECT
        list_s3tables_namespaces,
        inspect_rds_schema,
        inspect_api_spec,
        inspect_mcp_server,
        inspect_athena_source,
        # S3 Tables multi-namespace orchestrator
        discover_s3tables_bucket,
        # Phase 2: UNDERSTAND (sub-agent)
        analyze_schema,
        # Phase 3: CORRELATE (sub-agent)
        correlate_fields,
        # Phase 4: REGISTER (sub-agent)
        register_all,
        # Phase 5: LOG
        log_discovery_session,
        # Utilities (used by sub-agents internally, also available to orchestrator)
        get_canonical_concepts,
        save_phase_results,
        load_phase_results,
        load_table_schema,
    ]

    # Add Gateway MCP tools if configured
    gateway_name = os.getenv("REGISTRY_GATEWAY_NAME")
    gateway_url = os.getenv("REGISTRY_GATEWAY_URL")
    gateway_mcp = None

    # Resolve gateway URL at runtime if we have the name but not the URL
    if gateway_name and not gateway_url:
        try:
            import boto3 as _boto3
            _agentcore = _boto3.client('bedrock-agentcore-control', region_name=os.getenv("AWS_REGION", "us-east-1"))
            _gateways = _agentcore.list_gateways()
            for _gw in _gateways.get('items', []):
                if _gw['name'] == gateway_name:
                    _gw_detail = _agentcore.get_gateway(gatewayIdentifier=_gw['gatewayId'])
                    gateway_url = _gw_detail.get('gatewayUrl')
                    log.info(f"Resolved gateway URL from name '{gateway_name}': {gateway_url}")
                    break
        except Exception as e:
            log.warning(f"Could not resolve gateway URL from name: {e}")

    if gateway_url:
        gateway_mcp = MCPClient(
            lambda: aws_iam_streamablehttp_client(
                endpoint=gateway_url,
                aws_region=os.getenv("AWS_REGION", "us-east-1"),
                aws_service="bedrock-agentcore",
            )
        )
        tools.append(gateway_mcp)
        log.info(f"Gateway MCP tools enabled: {gateway_url}")

    bedrock_model = BedrockModel(
        model_id=model_id,
        max_tokens=64000,
    )

    # SummarizingConversationManager compresses older messages into summaries
    # instead of dropping them entirely. Combined with save/load_phase_results
    # tools that persist intermediate data to DDB, this ensures no data is lost
    # even when the conversation context is compressed.
    conversation_manager = SummarizingConversationManager(
        summary_ratio=0.3,
        preserve_recent_messages=10,
    )

    agent = Agent(
        model=bedrock_model,
        system_prompt=DISCOVERY_PROMPT,
        conversation_manager=conversation_manager,
        hooks=hooks,
        tools=tools,
        state={"session_id": session_id, "user_id": user_id},
        trace_attributes={
            "session.id": session_id,
            "user.id": user_id,
            "deployment.environment": os.getenv("DEPLOYMENT_ENV", "production"),
        },
    )

    user_message = payload.get("prompt", "Hello! How can I help you today?")
    log.info(f"Processing (session={session_id}, user={user_id}): {user_message[:80]}...")

    try:
        agent_stream = agent.stream_async(user_message)
        seen_tool_uses: set[str] = set()

        async for event in agent_stream:
            if isinstance(event, dict) and "messages" in event:
                for message in event.get("messages", []):
                    if message.get("role") == "assistant":
                        for block in message.get("content", []):
                            if "toolUse" in block:
                                tool_use = block["toolUse"]
                                tool_id = tool_use.get("toolUseId")
                                if tool_id and tool_id not in seen_tool_uses:
                                    seen_tool_uses.add(tool_id)
                                    yield {
                                        "type": "tool_use",
                                        "tool_name": tool_use.get("name", "unknown"),
                                        "tool_input": tool_use.get("input", {}),
                                        "tool_use_id": tool_id,
                                    }
                    elif message.get("role") == "user":
                        for block in message.get("content", []):
                            if "toolResult" in block:
                                tool_result = block["toolResult"]
                                tool_id = tool_result.get("toolUseId")
                                if tool_id:
                                    result_text = ""
                                    for rc in tool_result.get("content", []):
                                        if "text" in rc:
                                            result_text = rc["text"]
                                            break
                                    yield {
                                        "type": "tool_result",
                                        "tool_name": tool_id,
                                        "tool_result": result_text,
                                        "tool_use_id": tool_id,
                                    }

            # Intercept tool_stream_event from discover_s3tables_bucket to
            # emit real-time progress messages to the chat UI. Without this,
            # the user sees nothing for the entire multi-minute tool execution.
            if isinstance(event, dict):
                tse = event.get("tool_stream_event", {})
                tse_data = tse.get("data")
                if isinstance(tse_data, str):
                    try:
                        streamed = json.loads(tse_data)
                        stype = streamed.get("type", "")
                        if stype == "progress":
                            ns_list = ", ".join(streamed.get("namespaces", []))
                            yield {
                                "type": "TextStreamEvent",
                                "text": f"\n\n🔍 Found **{streamed.get('namespace_count', 0)} namespaces**: {ns_list}. Starting discovery…\n\n",
                            }
                        elif stype == "phase_update":
                            yield {
                                "type": "TextStreamEvent",
                                "text": f"  ⏳ {streamed.get('message', '')}\n",
                            }
                        elif stype == "namespace_result":
                            ns = streamed.get("namespace", "")
                            status = streamed.get("status", "")
                            if status == "completed":
                                yield {
                                    "type": "TextStreamEvent",
                                    "text": (
                                        f"✓ **{streamed.get('progress', '')}** Discovered **{ns}** — "
                                        f"{streamed.get('tables', 0)} tables, "
                                        f"{streamed.get('fields', 0)} fields, "
                                        f"{streamed.get('concepts_mapped', 0)} concepts mapped, "
                                        f"{streamed.get('equivalences', 0)} equivalences "
                                        f"({streamed.get('duration_seconds', 0)}s)\n\n"
                                    ),
                                }
                            else:
                                yield {
                                    "type": "TextStreamEvent",
                                    "text": (
                                        f"✗ **{streamed.get('progress', '')}** Failed **{ns}** — "
                                        f"{streamed.get('error', 'unknown error')} "
                                        f"({streamed.get('duration_seconds', 0)}s)\n\n"
                                    ),
                                }
                    except (json.JSONDecodeError, TypeError):
                        pass

            yield event

        for violation in guardrails_hook.get_and_clear_violations():
            yield violation

        log.info(f"Done in {time.time() - start_time:.2f}s (session={session_id})")

    except Exception as e:
        log.error(f"Agent error: {e}", exc_info=True)
        yield {"error": True, "message": str(e)}
        raise


if __name__ == "__main__":
    app.run()
