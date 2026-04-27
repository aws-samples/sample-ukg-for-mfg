"""
Discovery Agent — AgentCore Runtime entry point.

Architecture:
- Inspection tools (inspect_rds_schema, inspect_api_spec, inspect_mcp_server) — Phase 1
- Discovery helpers (get_canonical_concepts) — Phase 2-3
- Registration tools (register_system) — Phase 4
- Guardrails — content filtering (shadow mode)
- Memory — conversation persistence via AgentCore Memory
"""

import asyncio
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
import progress as progress_channel
from tools.discovery_helpers import get_canonical_concepts
from tools.inspect import inspect_api_spec, inspect_mcp_server, inspect_rds_schema, inspect_athena_source, list_s3tables_namespaces
from tools.register import register_system_metadata, register_fields, register_equivalences, log_discovery_session
from tools.state import save_phase_results, load_phase_results, load_table_schema
from tools.analyze import analyze_schema, correlate_fields, register_all, discover_s3tables_bucket
from tools.remember import remember_discovery

# Gateway MCP client for shared registry tools
from strands.tools.mcp import MCPClient
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

DISCOVERY_MODEL = "global.anthropic.claude-sonnet-4-6"


def _render_progress(payload: dict) -> str | None:
    """Convert a sideband progress payload to chat-friendly markdown text.

    Returns ``None`` if the payload type isn't one we know how to render, so
    callers can choose to log and drop.
    """
    stype = payload.get("type", "")
    if stype == "progress":
        ns_list = ", ".join(payload.get("namespaces", []))
        return (
            f"\n\n🔍 Found **{payload.get('namespace_count', 0)} namespaces**: "
            f"{ns_list}. Starting discovery…\n\n"
        )
    if stype == "phase_update":
        return f"  ⏳ {payload.get('message', '')}\n"
    if stype == "namespace_result":
        ns = payload.get("namespace", "")
        status = payload.get("status", "")
        progress_marker = payload.get("progress", "")
        duration = payload.get("duration_seconds", 0)
        if status == "completed":
            return (
                f"✓ **{progress_marker}** Discovered **{ns}** — "
                f"{payload.get('tables', 0)} tables, "
                f"{payload.get('fields', 0)} fields, "
                f"{payload.get('concepts_mapped', 0)} concepts mapped, "
                f"{payload.get('equivalences', 0)} equivalences "
                f"({duration}s)\n\n"
            )
        return (
            f"✗ **{progress_marker}** Failed **{ns}** — "
            f"{payload.get('error', 'unknown error')} ({duration}s)\n\n"
        )
    return None

DISCOVERY_PROMPT = """\
You are the Data Discovery Agent for a manufacturing universal knowledge graph platform. Your job is to \
inspect new data sources, catalog their schema, infer semantic mappings, discover \
cross-system equivalences, and register everything in the System Registry.

**CRITICAL — STATE MANAGEMENT**: After each phase, call `save_phase_results` to persist \
your results. At the start of each phase (except Phase 1), call `load_phase_results` to \
retrieve prior phase data. This prevents data loss when the conversation context is compressed.

Follow this strict 6-phase workflow for every discovery request:

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

## Phase 6: REMEMBER

Call `remember_discovery` with a concise markdown summary of what you learned. \
This is your institutional memory — treat it as a note-to-future-self that the \
Explorer agent will read next time someone asks about this system.

Include:
- Tables or endpoints discovered (with row counts if available)
- Concept mappings that matter for query construction (field → canonical concept)
- Cross-system equivalences registered in Phase 3
- Observed enum values and what they mean (e.g. `status='closed'` means completed, not cancelled)
- Gotchas: null semantics, timezone handling, composite keys, rate limits, anything non-obvious

Keep the summary factual and grounded in what Phase 1–4 actually produced. Do not speculate. \
The summary overwrites any prior summary for the same `system_id`, so re-discovery always \
results in exactly one current memory per system.

## Important Rules

- Always complete all 6 phases in order. Do not skip phases.
- **S3 Tables exception**: For S3 Tables buckets, call `discover_s3tables_bucket` instead of \
running the 6 phases manually. It handles all phases internally for every namespace and \
yields incremental progress per namespace. Relay each namespace result to the user as it \
arrives. After the final summary yield, produce the detailed report. If only partial \
results arrive, still report what was discovered.
- Each phase is a single tool call — the sub-agents handle everything internally.
- Never expose raw connection strings or credentials in your responses.
- If inspection fails, report the error clearly and do not proceed.
- **FINAL REPORT** — After Phase 6, output a detailed summary including:
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
        # Use a plain getLogger here — setup_logger() sets propagate=False,
        # which prevents OpenTelemetry log instrumentation from capturing
        # our log lines and forwarding them to CloudWatch. Plain getLogger
        # lets the OTEL root handler see everything. (tools/*.py do the same.)
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        _logger.setLevel(getattr(_logging, _config.log_level.upper(), _logging.INFO))
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
        # Phase 6: REMEMBER — write learnings to Bedrock KB institutional memory
        remember_discovery,
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

    # Sideband progress channel: tools call emit_progress(payload), we drain
    # here and forward each payload to the UI as a TextStreamEvent. This
    # bypasses Strands' tool-stream-event wrapping, which has proven
    # unreliable across versions for async-generator tools.
    progress_queue: asyncio.Queue = asyncio.Queue()
    progress_token = progress_channel.set_queue(progress_queue)

    try:
        agent_stream = agent.stream_async(user_message)
        seen_tool_uses: set[str] = set()

        # Run the agent loop as a task so we can race it against queue.get().
        # When the agent finishes, we flip a flag and drain any residual
        # progress payloads before returning.
        async def _next_event():
            try:
                return await agent_stream.__anext__()
            except StopAsyncIteration:
                return progress_channel.DONE

        agent_task = asyncio.create_task(_next_event())
        progress_task = asyncio.create_task(progress_queue.get())
        agent_done = False

        while True:
            pending = {agent_task, progress_task} - {None}
            if not pending:
                break
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

            if progress_task in done:
                try:
                    payload = progress_task.result()
                except Exception as e:
                    log.warning("progress queue drain error: %s", e)
                    payload = None
                if payload is progress_channel.DONE:
                    progress_task = None  # stop draining
                elif isinstance(payload, dict):
                    text = _render_progress(payload)
                    if text:
                        log.info(
                            "emitting TextStreamEvent from sideband: type=%s ns=%s len=%d",
                            payload.get("type"),
                            payload.get("namespace", ""),
                            len(text),
                        )
                        yield {"type": "TextStreamEvent", "text": text}
                    else:
                        log.debug("sideband payload with unknown type dropped: %r", payload)
                    # Re-arm next queue.get()
                    if not agent_done:
                        progress_task = asyncio.create_task(progress_queue.get())
                    else:
                        # Agent is done; drain anything else that's already
                        # in the queue without blocking.
                        if not progress_queue.empty():
                            progress_task = asyncio.create_task(progress_queue.get())
                        else:
                            progress_task = None

            if agent_task in done:
                try:
                    event = agent_task.result()
                except Exception as e:
                    log.error("agent stream error: %s", e, exc_info=True)
                    raise

                if event is progress_channel.DONE:
                    agent_done = True
                    agent_task = None
                    # Agent finished. If a progress_task is still blocking on
                    # an empty queue, cancel it so we don't hang forever.
                    if progress_task is not None and progress_queue.empty():
                        progress_task.cancel()
                        try:
                            await progress_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        progress_task = None
                    continue

                # Existing event handling — unchanged logic, just lives inside
                # the new multiplexed loop.
                if log.isEnabledFor(10) and isinstance(event, dict):
                    log.debug("agent stream event keys=%s", list(event.keys()))

                if isinstance(event, dict) and "messages" in event:
                    for message in event.get("messages", []):
                        if message.get("role") == "assistant":
                            for block in message.get("content", []):
                                if "toolUse" in block:
                                    tool_use = block["toolUse"]
                                    tool_id = tool_use.get("toolUseId")
                                    tool_name = tool_use.get("name", "unknown")
                                    if tool_name == "discover_s3tables_bucket":
                                        log.info(
                                            "discover_s3tables_bucket invoked (tool_use_id=%s). "
                                            "Progress will arrive via sideband channel.",
                                            tool_id,
                                        )
                                    if tool_id and tool_id not in seen_tool_uses:
                                        seen_tool_uses.add(tool_id)
                                        yield {
                                            "type": "tool_use",
                                            "tool_name": tool_name,
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

                yield event

                # Re-arm next agent stream pull
                agent_task = asyncio.create_task(_next_event())

        for violation in guardrails_hook.get_and_clear_violations():
            yield violation

        log.info(f"Done in {time.time() - start_time:.2f}s (session={session_id})")

    except Exception as e:
        log.error(f"Agent error: {e}", exc_info=True)
        yield {"error": True, "message": str(e)}
        raise
    finally:
        progress_channel.reset_queue(progress_token)


if __name__ == "__main__":
    app.run()
