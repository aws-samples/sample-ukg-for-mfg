"""
Orchestrator V2 Agent - AgentCore Runtime entry point.

Architecture:
- Registry tools (DynamoDB reads) — dynamic system discovery
- query_system (config-driven) — generic data query across any registered system
- Knowledge Base (Bedrock KB) — entity resolution via semantic search
- Memory hooks — conversation persistence via AgentCore Memory
- Guardrails — content filtering
- Starter tools — web search, URL fetcher, calculator, current_time
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
from strands_tools import calculator, current_time

from config import OrchestratorConfig
from guardrails import NotifyOnlyGuardrailsHook
from logger import setup_logger
from orchestrator import ORCHESTRATOR_MODEL, ORCHESTRATOR_PROMPT
from telemetry import is_telemetry_initialized, setup_telemetry
from tools.query_system import query_system
from tools.knowledge_base import search_knowledge_base
from tools.web_search import ddg_web_search
from tools.url_fetcher import fetch_url_content

# Gateway MCP client for shared registry tools
from strands.tools.mcp import MCPClient
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

app = BedrockAgentCoreApp()

_config = None
_logger = None
_memory_client = None


def get_config():
    """Lazy initialization of config, logger, and memory client.

    Avoids crashing the container at import time if environment
    variables are not yet available.

    Returns:
        Tuple of (OrchestratorConfig, Logger, MemoryClient)
    """
    global _config, _logger, _memory_client
    if _config is None:
        _config = OrchestratorConfig.from_env()
        _logger = setup_logger(__name__, _config.log_level)
        _memory_client = MemoryClient(region_name=_config.aws_region)
        if not is_telemetry_initialized():
            setup_telemetry(
                enabled=_config.otel_enabled,
                otlp_endpoint=_config.otel_endpoint,
                console_export=_config.otel_console_export,
                service_name="orchestrator-v2-agent",
            )
    return _config, _logger, _memory_client


class MemoryHook(HookProvider):
    """Load/save conversation history via AgentCore Memory."""

    def on_agent_initialized(self, event):
        """Load previous conversation history into the agent's system prompt.

        Retrieves recent memory events for the current session and appends
        them to the system prompt so the agent has conversational context.

        Args:
            event: AgentInitializedEvent with agent instance
        """
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
                max_results=50,
                include_payload=True,
            )
            if events:
                messages = []
                for evt in reversed(events):
                    for payload_item in evt.get("payload", []):
                        if "conversational" in payload_item:
                            conv = payload_item["conversational"]
                            role = conv.get("role", "")
                            text = conv.get("content", {}).get("text", "")
                            if text:
                                messages.append(f"{role}: {text}")
                if messages:
                    context = "\n".join(messages[:30])
                    event.agent.system_prompt += f"\n\nPrevious conversation history:\n{context}"
        except Exception as e:
            log.error(f"Memory load error: {e}", exc_info=True)

    def on_message_added(self, event):
        """Save new messages to AgentCore Memory for persistence.

        Filters out tool use/result messages and strips thinking tags
        before saving to memory.

        Args:
            event: MessageAddedEvent with agent instance
        """
        try:
            config, log, mem_client = get_config()
            if not config.memory_id:
                return
            session_id = event.agent.state.get("session_id") or "default"
            user_id = event.agent.state.get("user_id") or "anonymous"
            msg = event.agent.messages[-1]
            content = msg.get("content", "")
            role = msg.get("role", "user")
            if isinstance(content, list):
                if any("toolResult" in b or "toolUse" in b for b in content if isinstance(b, dict)):
                    return
                text_content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
            else:
                text_content = str(content)
            text_content = re.sub(r"<thinking>[\s\S]*?</thinking>\s*", "", text_content).strip()
            if not text_content:
                return
            mem_client.create_event(
                memory_id=config.memory_id,
                actor_id=user_id,
                session_id=session_id,
                messages=[(text_content, role)],
            )
        except Exception as e:
            get_config()[1].error(f"Memory save error: {e}", exc_info=True)

    def register_hooks(self, registry: HookRegistry):
        """Register memory hooks with the agent hook registry.

        Args:
            registry: HookRegistry to register callbacks with
        """
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        registry.add_callback(MessageAddedEvent, self.on_message_added)


@app.entrypoint
async def invoke(payload, context):
    """Orchestrator V2 agent — dynamic registry-driven multi-system queries."""
    start_time = time.time()
    config, log, _ = get_config()

    session_id = getattr(context, "session_id", None) or "default"
    user_id = payload.get("userId", "anonymous")
    model_id = payload.get("modelId", ORCHESTRATOR_MODEL)

    guardrail_id = payload.get("guardrailId") or config.guardrail_id
    guardrail_version = payload.get("guardrailVersion") or config.guardrail_version
    guardrail_enabled = payload.get("guardrailEnabled", config.guardrail_enabled)
    if isinstance(guardrail_enabled, str):
        guardrail_enabled = guardrail_enabled.lower() in ("true", "1", "yes")

    hooks = [MemoryHook()]
    guardrails_hook = NotifyOnlyGuardrailsHook(
        guardrail_id=guardrail_id,
        guardrail_version=guardrail_version,
        region=config.aws_region,
        enabled=guardrail_enabled,
    )
    hooks.append(guardrails_hook)

    tools = [
        # Generic data query — config-driven, routes to any registered system
        query_system,
        # Entity resolution — Bedrock Knowledge Base semantic search
        search_knowledge_base,
        # Starter tools
        ddg_web_search,
        fetch_url_content,
        calculator,
        current_time,
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
                aws_region=os.getenv("AWS_REGION", "us-east-2"),
                aws_service="bedrock-agentcore",
            )
        )
        tools.append(gateway_mcp)
        log.info(f"Gateway MCP tools enabled: {gateway_url}")

    agent = Agent(
        model=BedrockModel(model_id=model_id, max_tokens=64000),
        system_prompt=ORCHESTRATOR_PROMPT,
        conversation_manager=SummarizingConversationManager(
            summary_ratio=0.3,
            preserve_recent_messages=10,
        ),
        hooks=hooks,
        tools=tools,
        state={"session_id": session_id, "user_id": user_id},
        trace_attributes={
            "session.id": session_id,
            "user.id": user_id,
            "deployment.environment": os.getenv("DEPLOYMENT_ENV", "production"),
            "memory.id": config.memory_id,
        },
    )

    user_message = payload.get("prompt", "Hello! How can I help you today?")
    log.info(f"Processing (session={session_id}, user={user_id}): {user_message[:80]}...")

    try:
        agent_stream = agent.stream_async(user_message)
        seen_tool_uses: set[str] = set()
        deferred_metrics: list = []

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

            # Emit progress messages from tool_stream_event so the user
            # sees real-time feedback during long-running tool executions.
            if isinstance(event, dict):
                tse = event.get("tool_stream_event", {})
                tse_data = tse.get("data")
                if isinstance(tse_data, str):
                    try:
                        streamed = json.loads(tse_data)
                        stype = streamed.get("type", "")
                        if stype == "progress":
                            yield {
                                "type": "TextStreamEvent",
                                "text": f"\n\n🔍 {streamed.get('message', 'Processing...')}\n\n",
                            }
                        elif stype == "phase_update":
                            yield {
                                "type": "TextStreamEvent",
                                "text": f"  ⏳ {streamed.get('message', '')}\n",
                            }
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Defer metrics/usage events until after the stream completes.
            # Yielding them mid-stream can cause the AgentCore runtime to
            # close the HTTP response before all text content is flushed.
            if isinstance(event, dict) and ("usage" in event or "metrics" in event):
                deferred_metrics.append(event)
                continue

            yield event

        # Now yield deferred metrics after all content has been streamed
        for m in deferred_metrics:
            yield m

        for violation in guardrails_hook.get_and_clear_violations():
            yield violation

        log.info(f"Done in {time.time() - start_time:.2f}s (session={session_id})")

    except Exception as e:
        log.error(f"Agent error: {e}", exc_info=True)
        yield {"error": True, "message": str(e)}
        raise


if __name__ == "__main__":
    app.run()
