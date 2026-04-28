"""Workflow Executor Lambda — invoked by EventBridge Scheduler.

Reads a workflow definition from DynamoDB, invokes AgentCore Runtime
with the workflow prompt, infers priority from the response, and saves
the result back to DynamoDB.

Environment variables:
    WORKFLOWS_TABLE_NAME: DynamoDB table for workflows and results
    EXPLORER_RUNTIME_ARN: AgentCore Runtime ARN to invoke
    AWS_REGION: AWS region
"""

import codecs
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

WORKFLOWS_TABLE = os.environ.get("WORKFLOWS_TABLE_NAME", "")
RESULTS_TABLE = os.environ.get("WORKFLOW_RESULTS_TABLE_NAME", "")
RUNTIME_ARN = os.environ.get("EXPLORER_RUNTIME_ARN", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

ddb = boto3.client("dynamodb", config=Config(region_name=REGION, retries={"max_attempts": 3, "mode": "adaptive"}))
agentcore = boto3.client("bedrock-agentcore", config=Config(region_name=REGION, read_timeout=900, connect_timeout=30, tcp_keepalive=True))


# ── Priority inference via AgentCore ──────────────────────────────────────

PRIORITY_PROMPT = (
    "You are a manufacturing operations priority classifier. "
    "Given the following agent response from a scheduled workflow, classify its priority as exactly one of: urgent, normal, or low.\n\n"
    "Rules:\n"
    "- urgent: safety issues, equipment failures, downtime, out-of-spec conditions, critical alerts, or errors\n"
    "- low: everything normal, no issues found, all within spec, healthy operations\n"
    "- normal: informational findings, minor observations, or mixed results\n\n"
    "Respond with ONLY the single word: urgent, normal, or low. Nothing else.\n\n"
    "--- AGENT RESPONSE ---\n{response}\n--- END ---"
)

VALID_PRIORITIES = {"urgent", "normal", "low"}


def _infer_priority(response_text: str, status: str, session_id: str) -> str:
    """Infer result priority by asking AgentCore to classify the response."""
    if status == "error":
        return "urgent"
    if not response_text.strip():
        return "normal"

    # Truncate to avoid excessive token usage on the classification call
    truncated = response_text[:4000]
    classify_prompt = PRIORITY_PROMPT.format(response=truncated)

    try:
        result = _invoke_agent(classify_prompt, f"{session_id}-priority")
        answer = result["response_text"].strip().lower()
        # Extract the first valid priority word from the response
        for word in answer.split():
            cleaned = word.strip(".,;:!?\"'")
            if cleaned in VALID_PRIORITIES:
                return cleaned
        logger.warning("Priority classification returned unexpected value: %s, defaulting to normal", answer)
        return "normal"
    except Exception as e:
        logger.warning("Priority classification failed, defaulting to normal: %s", e)
        return "normal"


# ── AgentCore invocation ─────────────────────────────────────────────────

def _invoke_agent(prompt: str, session_id: str) -> dict:
    """Invoke AgentCore Runtime and collect the full response.

    Returns dict with keys: response_text, input_tokens, output_tokens
    """
    payload = json.dumps({
        "prompt": prompt,
        "userId": "workflow-scheduler",
        "sessionId": session_id,
    }).encode("utf-8")

    resp = agentcore.invoke_agent_runtime(
        runtimeSessionId=session_id,
        agentRuntimeArn=RUNTIME_ARN,
        payload=BytesIO(payload),
    )

    stream = resp.get("response")
    if not stream:
        return {"response_text": "", "input_tokens": 0, "output_tokens": 0}

    utf8_decoder = codecs.getincrementaldecoder("utf-8")("replace")
    buffer = ""
    text_chunks = []
    input_tokens = 0
    output_tokens = 0

    def _safe_get(obj, *keys, default=None):
        """Safely traverse nested dicts without AttributeError on non-dicts."""
        for k in keys:
            if not isinstance(obj, dict):
                return default
            obj = obj.get(k)
            if obj is None:
                return default
        return obj

    def _parse_line(stripped):
        """Parse a single NDJSON line, return (text, in_tok, out_tok) or None."""
        nonlocal input_tokens, output_tokens
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(data, dict):
            return

        # Text from contentBlockDelta
        delta = _safe_get(data, "event", "contentBlockDelta", "delta", "text")
        if delta:
            text_chunks.append(delta)
            return

        # Text from TextStreamEvent
        if data.get("type") == "TextStreamEvent" and isinstance(data.get("text"), str):
            text_chunks.append(data["text"])
            return

        # Usage metadata (multiple formats)
        usage = data.get("usage")
        if isinstance(usage, dict):
            input_tokens = usage.get("inputTokens", input_tokens)
            output_tokens = usage.get("outputTokens", output_tokens)
        # Nested format: event.metadata.usage
        meta = _safe_get(data, "event", "metadata")
        if isinstance(meta, dict):
            mu = meta.get("usage")
            if isinstance(mu, dict):
                input_tokens = mu.get("inputTokens", input_tokens)
                output_tokens = mu.get("outputTokens", output_tokens)

    for chunk in stream:
        raw_bytes = None
        if isinstance(chunk, bytes):
            raw_bytes = chunk
        elif isinstance(chunk, str):
            buffer += chunk
            raw_bytes = None
        elif isinstance(chunk, dict):
            if "chunk" in chunk:
                cd = chunk["chunk"]
                raw_bytes = cd.get("bytes", b"") if isinstance(cd, dict) else cd if isinstance(cd, bytes) else None
            elif "bytes" in chunk:
                raw_bytes = chunk["bytes"]

        if raw_bytes is not None:
            buffer += utf8_decoder.decode(raw_bytes, final=False)

        lines = buffer.split("\n")
        buffer = lines.pop()

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("data: "):
                stripped = stripped[6:]
            _parse_line(stripped)

    # Flush decoder
    final_text = utf8_decoder.decode(b"", final=True)
    if final_text:
        buffer += final_text
    if buffer.strip():
        stripped = buffer.strip()
        if stripped.startswith("data: "):
            stripped = stripped[6:]
        _parse_line(stripped)

    return {
        "response_text": "".join(text_chunks),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


# ── DynamoDB helpers ─────────────────────────────────────────────────────

def _get_workflow(workflow_id: str, user_email: str) -> dict | None:
    """Read workflow record from DynamoDB using direct GetItem (PK=user_email, SK=workflow_id)."""
    resp = ddb.get_item(
        TableName=WORKFLOWS_TABLE,
        Key={
            "user_email": {"S": user_email},
            "workflow_id": {"S": workflow_id},
        },
    )
    return resp.get("Item")


def _save_result(workflow_id: str, timestamp: str, status: str,
                 response_md: str, input_tokens: int, output_tokens: int,
                 latency_ms: int, priority: str, model_id: str = "") -> None:
    """Write a workflow result record to the results table."""
    ddb.put_item(
        TableName=RESULTS_TABLE,
        Item={
            "workflow_id": {"S": workflow_id},
            "timestamp": {"S": timestamp},
            "status": {"S": status},
            "response_md": {"S": response_md},
            "input_tokens": {"N": str(input_tokens)},
            "output_tokens": {"N": str(output_tokens)},
            "latency_ms": {"N": str(latency_ms)},
            "priority": {"S": priority},
            "triggered_by": {"S": "schedule"},
            "model_id": {"S": model_id},
        },
    )


# ── Lambda handler ───────────────────────────────────────────────────────

def handler(event, context):
    """EventBridge Scheduler invokes this with {"workflow_id": "..."} payload."""
    workflow_id = event.get("workflow_id")
    user_email = event.get("user_email")
    if not workflow_id or not user_email:
        logger.error("Missing workflow_id or user_email in event payload")
        return {"statusCode": 400, "body": "Missing workflow_id or user_email"}

    logger.info("Executing workflow %s for user %s", workflow_id, user_email)

    # Read workflow definition
    item = _get_workflow(workflow_id, user_email)
    if not item:
        logger.error("Workflow %s not found", workflow_id)
        return {"statusCode": 404, "body": "Workflow not found"}

    prompt = item.get("prompt", {}).get("S", "")
    enabled = item.get("enabled", {}).get("BOOL", True)
    model_id = item.get("model_id", {}).get("S", "")
    if not enabled:
        logger.info("Workflow %s is disabled, skipping", workflow_id)
        return {"statusCode": 200, "body": "Workflow disabled"}

    start = datetime.now(timezone.utc)
    session_id = f"workflow-{workflow_id}-{start.strftime('%Y%m%dT%H%M%S')}"
    status = "success"
    response_text = ""
    input_tokens = 0
    output_tokens = 0

    try:
        result = _invoke_agent(prompt, session_id)
        response_text = result["response_text"]
        input_tokens = result["input_tokens"]
        output_tokens = result["output_tokens"]
    except Exception as e:
        logger.error("Agent invocation failed for workflow %s: %s", workflow_id, e)
        status = "error"
        response_text = f"Error: {e}"

    end = datetime.now(timezone.utc)
    latency_ms = int((end - start).total_seconds() * 1000)
    priority = _infer_priority(response_text, status, session_id)

    _save_result(
        workflow_id=workflow_id,
        timestamp=start.isoformat(),
        status=status,
        response_md=response_text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        priority=priority,
        model_id=model_id,
    )

    logger.info("Workflow %s completed: status=%s priority=%s latency=%dms",
                workflow_id, status, priority, latency_ms)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "workflow_id": workflow_id,
            "status": status,
            "priority": priority,
            "latency_ms": latency_ms,
        }),
    }
