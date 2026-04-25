"""Evaluation repository for querying and aggregating AgentCore online evaluation data.

This module provides the EvaluationRepository class for querying evaluation result
records from CloudWatch Logs and computing aggregate statistics for admin views.
Evaluation results are written by AgentCore to CloudWatch Logs at
`/aws/bedrock-agentcore/evaluations/results/{config-id}`.
"""

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """A single evaluation output record from AgentCore Online Evaluation.

    Attributes:
        timestamp: ISO 8601 timestamp of the evaluation event
        evaluator_id: Built-in evaluator identifier (e.g., 'Builtin.GoalSuccessRate')
        score: Numeric score between 0.0 and 1.0
        label: Evaluation label ('pass' or 'fail')
        reasoning: Evaluator reasoning text
        session_id: Associated session identifier
        trace_id: Optional trace identifier
        input_tokens: Number of input tokens consumed
        output_tokens: Number of output tokens consumed
    """

    timestamp: str = ""
    evaluator_id: str = ""
    score: float = 0.0
    label: str = "unknown"
    reasoning: str = ""
    session_id: str = ""
    trace_id: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class EvaluatorStats:
    """Aggregate statistics for a single evaluator type.

    Attributes:
        evaluator_id: Built-in evaluator identifier
        evaluator_name: Human-readable name derived from evaluator ID
        average_score: Mean score across all evaluations for this evaluator
        evaluation_count: Total number of evaluations for this evaluator
        pass_count: Number of evaluations with 'pass' label
        fail_count: Number of evaluations with 'fail' label
    """

    evaluator_id: str = ""
    evaluator_name: str = ""
    average_score: float = 0.0
    evaluation_count: int = 0
    pass_count: int = 0
    fail_count: int = 0


@dataclass
class AgentEvaluationSummary:
    """Summary of evaluation results for a single agent runtime.

    Attributes:
        agent_name: Display name for the agent (e.g., 'Orchestrator')
        average_score: Mean score across all evaluators
        total_evaluations: Total number of evaluation results
        total_sessions: Count of unique session IDs
        fail_count: Number of evaluations with 'fail' label
        evaluator_stats: Per-evaluator aggregated statistics
        recent_results: Most recent evaluation results (limited to 50)
    """

    agent_name: str = ""
    average_score: float = 0.0
    total_evaluations: int = 0
    total_sessions: int = 0
    fail_count: int = 0
    evaluator_stats: List[EvaluatorStats] = field(default_factory=list)
    recent_results: List[EvaluationResult] = field(default_factory=list)


class EvaluationRepository:
    """Repository for querying AgentCore online evaluation data from CloudWatch Logs.

    This class provides methods for querying and aggregating evaluation result
    records from CloudWatch Logs, including time range filtering, per-evaluator
    breakdown, and score color classification.
    """

    LOG_GROUP_PREFIX = "/aws/bedrock-agentcore/evaluations/results"
    MAX_RECENT_RESULTS = 200

    def __init__(
        self,
        region: Optional[str] = None,
    ):
        """Initialize the evaluation repository.

        Args:
            region: AWS region (defaults to AWS_REGION env var)
        """
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self.orchestrator_config_id = os.environ.get(
            "ORCHESTRATOR_EVAL_CONFIG_ID", ""
        )
        self.discovery_config_id = os.environ.get(
            "DISCOVERY_EVAL_CONFIG_ID", ""
        )

        boto_config = Config(
            region_name=self.region,
            retries={"max_attempts": 3, "mode": "adaptive"},
        )

        self._client = boto3.client("logs", config=boto_config)

    @staticmethod
    def get_score_color(score: float) -> str:
        """Classify a score into a color category.

        Args:
            score: Numeric score between 0.0 and 1.0

        Returns:
            'green' if score > 0.8, 'amber' if 0.5 <= score <= 0.8, 'red' if score < 0.5
        """
        if score > 0.8:
            return "green"
        elif score >= 0.5:
            return "amber"
        else:
            return "red"

    async def get_agent_summary(
        self,
        config_id: str,
        start_time: datetime,
        end_time: datetime,
        agent_name: str = "",
    ) -> AgentEvaluationSummary:
        """Get evaluation summary for an agent runtime.

        Args:
            config_id: Online evaluation config ID
            start_time: Start of the time range (inclusive)
            end_time: End of the time range (inclusive)
            agent_name: Display name for the agent

        Returns:
            AgentEvaluationSummary with aggregated stats and recent results
        """
        if not config_id:
            logger.info(
                "Evaluation config ID is empty, returning empty summary",
                extra={"agent_name": agent_name},
            )
            return AgentEvaluationSummary(agent_name=agent_name)

        log_group_name = f"{self.LOG_GROUP_PREFIX}/{config_id}"

        try:
            log_events = self._fetch_log_events(
                log_group_name, start_time, end_time
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                logger.warning(
                    "Evaluation log group not found, returning empty summary",
                    extra={
                        "log_group_name": log_group_name,
                        "agent_name": agent_name,
                    },
                )
                return AgentEvaluationSummary(agent_name=agent_name)
            logger.error(
                "Failed to fetch evaluation log events",
                extra={
                    "error_code": error_code,
                    "error_message": str(e),
                    "agent_name": agent_name,
                },
            )
            return AgentEvaluationSummary(agent_name=agent_name)

        # Parse log events into evaluation results
        results: List[EvaluationResult] = []
        for event in log_events:
            parsed = self._parse_evaluation_result(event)
            if parsed is not None:
                results.append(parsed)

        if not results:
            return AgentEvaluationSummary(agent_name=agent_name)

        # Compute overall stats
        total_evaluations = len(results)
        unique_sessions = set(r.session_id for r in results)
        total_sessions = len(unique_sessions)
        fail_count = sum(1 for r in results if r.label == "fail")
        average_score = sum(r.score for r in results) / total_evaluations

        # Per-evaluator aggregation
        evaluator_groups: Dict[str, List[EvaluationResult]] = defaultdict(list)
        for result in results:
            evaluator_groups[result.evaluator_id].append(result)

        evaluator_stats: List[EvaluatorStats] = []
        for evaluator_id, group in evaluator_groups.items():
            group_scores = [r.score for r in group]
            avg_score = sum(group_scores) / len(group_scores)
            pass_count = sum(1 for r in group if r.label == "pass")
            group_fail_count = sum(1 for r in group if r.label == "fail")

            evaluator_stats.append(
                EvaluatorStats(
                    evaluator_id=evaluator_id,
                    evaluator_name=self._format_evaluator_name(evaluator_id),
                    average_score=avg_score,
                    evaluation_count=len(group),
                    pass_count=pass_count,
                    fail_count=group_fail_count,
                )
            )

        # Sort evaluator stats ascending by average score
        evaluator_stats.sort(key=lambda s: s.average_score)

        # Limit detailed results to 50 most recent, sorted descending by timestamp
        sorted_results = sorted(
            results, key=lambda r: r.timestamp, reverse=True
        )
        recent_results = sorted_results[: self.MAX_RECENT_RESULTS]

        return AgentEvaluationSummary(
            agent_name=agent_name,
            average_score=average_score,
            total_evaluations=total_evaluations,
            total_sessions=total_sessions,
            fail_count=fail_count,
            evaluator_stats=evaluator_stats,
            recent_results=recent_results,
        )

    def _fetch_log_events(
        self,
        log_group_name: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[dict]:
        """Fetch log events from CloudWatch Logs for a time range.

        Args:
            log_group_name: CloudWatch Logs log group name
            start_time: Start of the time range
            end_time: End of the time range

        Returns:
            List of log event dictionaries

        Raises:
            ClientError: If the CloudWatch Logs API call fails
        """
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)

        events: List[dict] = []
        next_token = None

        while True:
            kwargs = {
                "logGroupName": log_group_name,
                "startTime": start_ms,
                "endTime": end_ms,
                "interleaved": True,
            }
            if next_token:
                kwargs["nextToken"] = next_token

            response = self._client.filter_log_events(**kwargs)
            events.extend(response.get("events", []))

            next_token = response.get("nextToken")
            if not next_token:
                break

        return events

    def _parse_evaluation_result(
        self, log_event: dict
    ) -> Optional[EvaluationResult]:
        """Parse a CloudWatch log event into an EvaluationResult.

        Args:
            log_event: Raw log event dictionary from filter_log_events

        Returns:
            EvaluationResult if parsing succeeds, None if the record is malformed
        """
        message = log_event.get("message", "")

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse evaluation log event JSON, skipping record",
                extra={"event_id": log_event.get("eventId", "unknown")},
            )
            return None

        # Extract timestamp from log event (milliseconds epoch) and convert to ISO
        event_timestamp_ms = log_event.get("timestamp", 0)
        try:
            timestamp = datetime.fromtimestamp(
                event_timestamp_ms / 1000
            ).isoformat()
        except (OSError, ValueError, OverflowError):
            timestamp = ""

        # Extract token usage with defaults
        token_usage = data.get("tokenUsage", {})
        if not isinstance(token_usage, dict):
            token_usage = {}

        # AgentCore writes evaluation results in OpenTelemetry format with
        # fields nested under "attributes" using dotted keys.
        attrs = data.get("attributes", {})
        if not isinstance(attrs, dict):
            attrs = {}

        # Try OTel format first, fall back to flat format for backwards compat
        evaluator_id = (
            attrs.get("gen_ai.evaluation.name")
            or attrs.get("aws.bedrock_agentcore.evaluator.arn", "").split("/")[-1]
            or data.get("evaluatorId", "")
        )
        score = (
            attrs.get("gen_ai.evaluation.score.value")
            if attrs.get("gen_ai.evaluation.score.value") is not None
            else data.get("score", 0.0)
        )
        label = (
            attrs.get("gen_ai.evaluation.score.label")
            or data.get("label", "unknown")
        )
        reasoning = (
            attrs.get("gen_ai.evaluation.explanation")
            or data.get("reasoning", "")
        )
        session_id = (
            attrs.get("session.id")
            or data.get("sessionId", "")
        )
        trace_id = data.get("traceId") or data.get("traceId")
        input_tokens = token_usage.get("inputTokens", 0)
        output_tokens = token_usage.get("outputTokens", 0)

        # Validate score is numeric
        if not isinstance(score, (int, float)):
            logger.warning(
                "Non-numeric score in evaluation result, defaulting to 0.0",
                extra={"evaluator_id": evaluator_id, "raw_score": str(score)},
            )
            score = 0.0

        return EvaluationResult(
            timestamp=timestamp,
            evaluator_id=evaluator_id,
            score=float(score),
            label=str(label),
            reasoning=str(reasoning),
            session_id=str(session_id),
            trace_id=trace_id,
            input_tokens=int(input_tokens) if isinstance(input_tokens, (int, float)) else 0,
            output_tokens=int(output_tokens) if isinstance(output_tokens, (int, float)) else 0,
        )

    @staticmethod
    def _format_evaluator_name(evaluator_id: str) -> str:
        """Convert an evaluator ID to a human-readable name.

        Args:
            evaluator_id: e.g., 'Builtin.GoalSuccessRate'

        Returns:
            Human-readable name, e.g., 'Goal Success Rate'
        """
        # Strip 'Builtin.' prefix if present
        name = evaluator_id
        if name.startswith("Builtin."):
            name = name[len("Builtin."):]

        # Insert spaces before uppercase letters for CamelCase splitting
        result = []
        for i, char in enumerate(name):
            if char.isupper() and i > 0:
                result.append(" ")
            result.append(char)

        return "".join(result)
