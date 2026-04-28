"""AgentCore client for invoking the Bedrock AgentCore Runtime.

This module provides the AgentCoreClient class for streaming responses
from the AgentCore Runtime and converting them to typed SSE events.
"""

import json
import logging
import re
from typing import AsyncGenerator, Optional, Dict, Any

logger = logging.getLogger(__name__)

import boto3
from botocore.config import Config

from app.config import get_config
from app.admin.cost_calculator import DEFAULT_MODEL_ID
from app.models.events import (
    SSEEvent,
    MessageEvent,
    ToolUseEvent,
    ToolResultEvent,
    ErrorEvent,
    MetadataEvent,
    DoneEvent,
    GuardrailEvent,
)


class ThinkingFilter:
    """Stateful filter for removing <thinking> tags and tool XML from streamed content.
    
    This filter accumulates content and removes thinking blocks and tool call XML,
    handling partial tags that may span multiple chunks. It also extracts tool calls
    from Nova's XML format and stores them for emission as proper events.
    """
    
    def __init__(self):
        self._full_content = ""
        self._sent_length = 0
        self._extracted_tools: list = []  # Store extracted tool calls
        self._seen_tool_ids: set = set()  # Track emitted tools to avoid duplicates
    
    def filter(self, text: str) -> Optional[str]:
        """Filter out thinking tags and tool XML from streamed content.
        
        Args:
            text: Raw text chunk from the stream
            
        Returns:
            Filtered text without thinking tags or tool XML, or None if no new content
        """
        self._full_content += text
        
        # Remove complete thinking blocks
        filtered = re.sub(r'<thinking>[\s\S]*?</thinking>', '', self._full_content)
        
        # Extract tool calls from Nova XML format before removing them
        self._extract_tool_calls(self._full_content)
        
        # Remove tool call XML - Nova model uses <__function=name> format (double underscore)
        filtered = re.sub(r'<__function=[^>]*>[\s\S]*?</__function>', '', filtered)
        # Also handle incomplete/malformed function tags
        filtered = re.sub(r'<__function=[^>]*>[\s\S]*?</', '', filtered)
        # Remove standalone function tags that might be partial
        filtered = re.sub(r'<__function=[^>]*>[^<]*$', '', filtered)
        # Remove partial opening function tags at end of content
        filtered = re.sub(r'<__function=[^>]*$', '', filtered)
        
        # Also handle single underscore format just in case
        filtered = re.sub(r'<function=[^>]*>[\s\S]*?</function>', '', filtered)
        filtered = re.sub(r'<function=[^>]*>[^<]*$', '', filtered)
        
        # Remove parameter tags (Nova model format: <__parameter=name>value</__parameter>)
        filtered = re.sub(r'<__parameter=[^>]*>[\s\S]*?</__parameter>', '', filtered)
        # Remove partial parameter tags and standalone tags
        filtered = re.sub(r'<__parameter=[^>]*>[^<]*$', '', filtered)
        filtered = re.sub(r'<__parameter=[^>]*>$', '', filtered)
        filtered = re.sub(r'</__parameter>', '', filtered)
        
        # Remove incomplete opening tag at the end (pattern: <thinking> followed by anything until end)
        open_tag_pattern = r'<thinking>[\s\S]*$'
        open_tag_match = re.search(open_tag_pattern, filtered)
        if open_tag_match:
            filtered = filtered[:len(filtered) - len(open_tag_match.group(0))]
        
        # Check for partial opening tags (pattern: < followed by non-> chars at end)
        partial_tag_pattern = r'<[^>]*$'
        partial_tag_match = re.search(partial_tag_pattern, filtered)
        if partial_tag_match:
            partial = partial_tag_match.group(0)
            if '<thinking>'.startswith(partial):
                filtered = filtered[:len(filtered) - len(partial)]
        
        # Return only new content
        if len(filtered) > self._sent_length:
            new_content = filtered[self._sent_length:]
            self._sent_length = len(filtered)
            return new_content
        
        return None
    
    def _extract_tool_calls(self, content: str) -> None:
        """Extract tool calls from Nova's XML format.
        
        Parses <__function=name>...</__function> blocks and extracts
        tool name and parameters for emission as ToolUseEvent.
        """
        # Match complete function blocks: <__function=name>params</__function>
        pattern = r'<__function=([^>]+)>([\s\S]*?)</__function>'
        matches = re.findall(pattern, content)
        
        for tool_name, params_block in matches:
            # Generate a unique ID for this tool call
            tool_id = f"nova-{tool_name}-{hash(params_block) & 0xFFFFFFFF}"
            
            if tool_id in self._seen_tool_ids:
                continue
            
            self._seen_tool_ids.add(tool_id)
            
            # Extract parameters from <__parameter=name>value</__parameter> tags
            param_pattern = r'<__parameter=([^>]+)>([^<]*)</__parameter>'
            param_matches = re.findall(param_pattern, params_block)
            
            tool_input = {}
            for param_name, param_value in param_matches:
                # Try to parse as JSON if it looks like JSON
                try:
                    if param_value.strip().startswith(('{', '[', '"')):
                        tool_input[param_name] = json.loads(param_value)
                    else:
                        tool_input[param_name] = param_value
                except json.JSONDecodeError:
                    tool_input[param_name] = param_value
            
            self._extracted_tools.append({
                'tool_name': tool_name,
                'tool_input': tool_input,
                'tool_use_id': tool_id,
            })
    
    def get_extracted_tools(self) -> list:
        """Get and clear extracted tool calls.
        
        Returns:
            List of tool call dicts with tool_name, tool_input, tool_use_id
        """
        tools = self._extracted_tools.copy()
        self._extracted_tools.clear()
        return tools


class AgentCoreClient:
    """Client for invoking AgentCore Runtime and streaming responses.
    
    This client handles communication with the Bedrock AgentCore Runtime,
    parsing NDJSON responses and converting them to typed SSE events.
    
    Attributes:
        runtime_arn: ARN of the AgentCore Runtime
        region: AWS region
    """
    
    def __init__(
        self,
        runtime_arn: Optional[str] = None,
        region: Optional[str] = None,
    ):
        """Initialize the AgentCore client.
        
        Args:
            runtime_arn: AgentCore Runtime ARN (defaults to config)
            region: AWS region (defaults to config)
        """
        config = get_config()
        self.runtime_arn = runtime_arn or config.explorer_runtime_arn
        self.region = region or config.aws_region
        
        boto_config = Config(
            region_name=self.region,
            retries={'max_attempts': 3, 'mode': 'adaptive'},
            read_timeout=900,
            connect_timeout=30,
            tcp_keepalive=True,
        )
        
        self._client = boto3.client(
            'bedrock-agentcore',
            config=boto_config,
        )
    
    def _parse_ndjson_line(
        self,
        line: str,
        thinking_filter: ThinkingFilter,
    ) -> Optional[SSEEvent]:
        """Parse a single NDJSON line from AgentCore response.
        
        Args:
            line: Raw JSON line from the response
            thinking_filter: Filter instance for removing thinking tags
            
        Returns:
            Parsed SSE event or None if line should be skipped
        """
        if not line.strip():
            return None
        
        try:
            # Handle SSE format: "data: {...}" or plain JSON
            json_str = line
            if line.startswith('data: '):
                json_str = line[6:]
            
            data: Dict[str, Any] = json.loads(json_str)
            
            # Handle contentBlockDelta streaming events
            if data.get('event', {}).get('contentBlockDelta', {}).get('delta', {}).get('text'):
                text = data['event']['contentBlockDelta']['delta']['text']
                filtered = thinking_filter.filter(text)
                if filtered:
                    return MessageEvent(content=filtered)
                return None
            
            # Handle TextStreamEvent
            if data.get('type') == 'TextStreamEvent' and data.get('text'):
                text = data['text']
                filtered = thinking_filter.filter(text)
                if filtered:
                    return MessageEvent(content=filtered)
                return None
            
            # Handle Strands tool events - direct format
            if data.get('type') == 'tool_use':
                tool_name = data.get('tool_name') or data.get('name') or 'unknown'
                return ToolUseEvent(
                    tool_name=tool_name,
                    tool_input=data.get('tool_input') or data.get('input'),
                    tool_use_id=data.get('tool_use_id') or data.get('id') or f"tool-{id(data)}",
                    status=data.get('status', 'started'),
                )
            
            if data.get('type') == 'tool_result':
                tool_name = data.get('tool_name') or data.get('name') or 'unknown'
                return ToolResultEvent(
                    tool_name=tool_name,
                    tool_result=data.get('tool_result') or data.get('result'),
                    tool_use_id=data.get('tool_use_id') or data.get('id') or f"tool-{id(data)}",
                    status=data.get('status', 'completed'),
                )
            
            # Handle guardrail events from agent
            if data.get('type') == 'guardrail':
                return GuardrailEvent(
                    source=data.get('source', 'INPUT'),
                    action=data.get('action', 'NONE'),
                    assessments=data.get('assessments', []),
                )
            
            # Handle nested content blocks (Bedrock format)
            content = data.get('content', [])
            if isinstance(content, list):
                for block in content:
                    if block.get('toolUse'):
                        tool_use = block['toolUse']
                        return ToolUseEvent(
                            tool_name=tool_use['name'],
                            tool_input=tool_use.get('input'),
                            tool_use_id=tool_use.get('toolUseId'),
                            status='started',
                        )
                    if block.get('toolResult'):
                        tool_result = block['toolResult']
                        return ToolResultEvent(
                            tool_name=tool_result.get('name', 'unknown'),
                            tool_result=tool_result.get('content'),
                            tool_use_id=tool_result.get('toolUseId'),
                            status=tool_result.get('status', 'completed'),
                        )
            
            # Handle final message event — contains the complete response text.
            # Streaming contentBlockDelta events deliver partial text; the final
            # message has the full clean text. Emit anything beyond what was
            # already streamed (tracked by ThinkingFilter._sent_length).
            msg_content = data.get('message', {}).get('content')
            if msg_content and isinstance(msg_content, list):
                text_parts = []
                for block in msg_content:
                    if isinstance(block, dict) and block.get('text'):
                        text_parts.append(block['text'])
                if text_parts:
                    full_text = ''.join(text_parts)
                    already_sent_len = thinking_filter._sent_length
                    if len(full_text) > already_sent_len:
                        new_tail = full_text[already_sent_len:]
                        if new_tail.strip():
                            return MessageEvent(content=new_tail)
                    else:
                        # This marks the end of an agent turn — the streamed text
                        # already covered this turn's content. Emit a paragraph
                        # break to visually separate reasoning steps in the UI.
                        return MessageEvent(content="\n\n")
                return None
            
            # Handle legacy message format
            if data.get('type') == 'message' and isinstance(data.get('content'), list):
                text_content = ''
                for block in data['content']:
                    if block.get('text'):
                        text_content += block['text']
                if text_content:
                    filtered = thinking_filter.filter(text_content)
                    if filtered:
                        return MessageEvent(content=filtered)
                return None
            
            # Handle Strands ModelMetadataEvent format (usage at top level)
            usage = data.get('usage', {})
            metrics_data = data.get('metrics', {})
            if usage or metrics_data:
                return MetadataEvent(data={
                    'inputTokens': usage.get('inputTokens', 0),
                    'outputTokens': usage.get('outputTokens', 0),
                    'totalTokens': usage.get('totalTokens', 0),
                    'latencyMs': metrics_data.get('latencyMs', 0),
                })
            
            # Extract metadata from legacy format (event.metadata.usage)
            metadata = data.get('event', {}).get('metadata', {})
            if metadata:
                usage = metadata.get('usage', {})
                metrics = metadata.get('metrics', {})
                if usage or metrics:
                    return MetadataEvent(data={
                        'inputTokens': usage.get('inputTokens'),
                        'outputTokens': usage.get('outputTokens'),
                        'latencyMs': metrics.get('latencyMs'),
                    })
            
            return None
            
        except json.JSONDecodeError:
            return None
        except Exception:
            return None


    async def invoke_stream(
        self,
        prompt: str,
        session_id: str,
        user_id: str,
        model_id: str = DEFAULT_MODEL_ID,
    ) -> AsyncGenerator[SSEEvent, None]:
        """Invoke AgentCore Runtime and stream the response.
        
        Args:
            prompt: User message to send to the agent
            session_id: Session ID for conversation context
            user_id: User ID for memory operations
            model_id: Model identifier for LLM selection
            
        Yields:
            SSE events as they are received from AgentCore
        """
        import codecs
        
        thinking_filter = ThinkingFilter()
        
        try:
            # Prepare the payload - boto3 streaming body expects a file-like object
            from io import BytesIO
            
            # Get guardrail config from app config
            config = get_config()
            
            payload_dict = {
                'prompt': prompt,
                'userId': user_id,
                'sessionId': session_id,  # Include session ID in payload for usage logs
                'modelId': model_id,
                'guardrailId': config.guardrail_id,
                'guardrailVersion': config.guardrail_version,
                'guardrailEnabled': config.guardrail_enabled,
            }
            payload_bytes = json.dumps(payload_dict).encode('utf-8')
            
            # Invoke the agent runtime
            response = self._client.invoke_agent_runtime(
                runtimeSessionId=session_id,
                agentRuntimeArn=self.runtime_arn,
                payload=BytesIO(payload_bytes),
            )
            
            if 'response' not in response:
                yield ErrorEvent(message='No response from AgentCore')
                return
            
            # Process the streaming response
            stream = response['response']
            buffer = ''
            
            # Use incremental decoder to handle partial UTF-8 sequences
            utf8_decoder = codecs.getincrementaldecoder('utf-8')('replace')
            
            # Read chunks from the stream - StreamingBody yields bytes directly
            chunk_count = 0
            total_bytes = 0
            import time as _timing
            _stream_start = _timing.time()
            _last_log = _stream_start
            for chunk in stream:
                # Diagnostic: log first-chunk latency once per stream, then
                # periodic heartbeats at DEBUG only (avoid log spam in prod).
                _now = _timing.time()
                if chunk_count == 0:
                    logger.info("first chunk received after %.2fs", _now - _stream_start)
                elif _now - _last_log > 2.0:
                    logger.debug(
                        "chunk %d @ t=%.2fs (total_bytes=%d)",
                        chunk_count, _now - _stream_start, total_bytes,
                    )
                    _last_log = _now
                # Handle different chunk formats and get raw bytes
                raw_bytes = None
                if isinstance(chunk, bytes):
                    raw_bytes = chunk
                elif isinstance(chunk, str):
                    # Already decoded, use directly
                    buffer += chunk
                    raw_bytes = None
                elif isinstance(chunk, dict):
                    # Handle wrapped chunk format
                    if 'chunk' in chunk:
                        chunk_data = chunk['chunk']
                        if isinstance(chunk_data, dict):
                            raw_bytes = chunk_data.get('bytes', b'')
                        elif isinstance(chunk_data, bytes):
                            raw_bytes = chunk_data
                        else:
                            buffer += str(chunk_data)
                            raw_bytes = None
                    elif 'bytes' in chunk:
                        raw_bytes = chunk['bytes']
                    else:
                        continue
                else:
                    continue
                
                # Decode bytes using incremental decoder (handles partial UTF-8)
                if raw_bytes is not None:
                    chunk_count += 1
                    total_bytes += len(raw_bytes)
                    text = utf8_decoder.decode(raw_bytes, final=False)
                    buffer += text
                
                # Process complete lines — NDJSON uses newlines as delimiters,
                # but JSON values may contain escaped newlines (\n inside strings).
                # We split on newlines and attempt to parse; if parsing fails,
                # we accumulate lines until we get valid JSON.
                lines = buffer.split('\n')
                buffer = lines.pop()  # Keep last (potentially incomplete) line
                
                json_accumulator = ''
                for line in lines:
                    if not line.strip():
                        continue
                    
                    # If we have accumulated partial JSON, append this line
                    if json_accumulator:
                        json_accumulator += '\n' + line
                        # Try to parse the accumulated content
                        try:
                            json.loads(json_accumulator.strip().removeprefix('data: ') if json_accumulator.strip().startswith('data: ') else json_accumulator.strip())
                            # Valid JSON — process it
                            event = self._parse_ndjson_line(json_accumulator, thinking_filter)
                            if event:
                                yield event
                            for tool in thinking_filter.get_extracted_tools():
                                yield ToolUseEvent(
                                    tool_name=tool['tool_name'],
                                    tool_input=tool['tool_input'],
                                    tool_use_id=tool['tool_use_id'],
                                    status='started',
                                )
                            json_accumulator = ''
                        except (json.JSONDecodeError, ValueError):
                            # Still incomplete — keep accumulating
                            pass
                        continue
                    
                    # Try to parse this line as standalone JSON
                    test_str = line.strip()
                    if test_str.startswith('data: '):
                        test_str = test_str[6:]
                    try:
                        json.loads(test_str)
                        # Valid JSON — process normally
                        event = self._parse_ndjson_line(line, thinking_filter)
                        if event:
                            yield event
                        for tool in thinking_filter.get_extracted_tools():
                            yield ToolUseEvent(
                                tool_name=tool['tool_name'],
                                tool_input=tool['tool_input'],
                                tool_use_id=tool['tool_use_id'],
                                status='started',
                            )
                    except (json.JSONDecodeError, ValueError):
                        # Incomplete JSON — start accumulating
                        json_accumulator = line
                
                # If we have leftover accumulated JSON, prepend it to the buffer
                if json_accumulator:
                    buffer = json_accumulator + '\n' + buffer
            
            # Flush any remaining bytes in the decoder
            logger.debug("stream_complete: %d chunks, %d total bytes, sent_length=%d",
                         chunk_count, total_bytes, thinking_filter._sent_length)
            final_text = utf8_decoder.decode(b'', final=True)
            if final_text:
                buffer += final_text

            # Process remaining buffer
            if buffer.strip():
                logger.debug("remaining_buffer: len=%d", len(buffer))
                event = self._parse_ndjson_line(buffer, thinking_filter)
                if event:
                    yield event
                
                # Emit any remaining tool events
                for tool in thinking_filter.get_extracted_tools():
                    yield ToolUseEvent(
                        tool_name=tool['tool_name'],
                        tool_input=tool['tool_input'],
                        tool_use_id=tool['tool_use_id'],
                        status='started',
                    )
            
            # Send completion event
            yield DoneEvent()
            
        except self._client.exceptions.ValidationException as e:
            logger.error("ValidationException invoking AgentCore: %s", e)
            yield ErrorEvent(
                message='Invalid request to AgentCore',
                details=str(e),
            )
            yield DoneEvent()
        except self._client.exceptions.ThrottlingException as e:
            logger.warning("ThrottlingException invoking AgentCore: %s", e)
            yield ErrorEvent(
                message='Request throttled by AgentCore',
                details=str(e),
            )
            yield DoneEvent()
        except self._client.exceptions.InternalServerException as e:
            logger.error("InternalServerException from AgentCore: %s", e)
            yield ErrorEvent(
                message='AgentCore internal error',
                details=str(e),
            )
            yield DoneEvent()
        except Exception as e:
            logger.error("Unexpected error invoking AgentCore: %s: %s",
                         type(e).__name__, e, exc_info=True)
            
            is_stream_error = 'StreamingError' in type(e).__name__ or 'IncompleteRead' in str(e)
            if not is_stream_error:
                yield ErrorEvent(
                    message='Failed to invoke AgentCore',
                    details=str(e),
                )
            yield DoneEvent()
