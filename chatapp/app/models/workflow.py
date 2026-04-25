"""Workflow data models for DynamoDB storage.

Two-table design:
- Workflows table: PK=user_email, SK=workflow_id
- Results table: PK=workflow_id, SK=timestamp
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional


@dataclass
class Workflow:
    """A saved workflow definition.

    Attributes:
        workflow_id: UUID
        user_email: Owner's email address (partition key)
        title: Short display title
        prompt: The agent prompt to execute
        schedule_type: "manual" | "hours" | "daily" | "weekdays"
        schedule_interval: For hours type — number of hours (1-24)
        schedule_time: For daily/weekdays — HH:MM in UTC
        created_at: ISO 8601
        updated_at: ISO 8601
        enabled: Whether the schedule is active
        schedule_rule_arn: ARN of the EventBridge rule
    """
    workflow_id: str
    user_email: str
    title: str
    prompt: str
    schedule_type: str = "daily"
    schedule_interval: int = 0
    schedule_time: str = "08:00"
    created_at: str = ""
    updated_at: str = ""
    enabled: bool = True
    schedule_rule_arn: str = ""
    model_id: str = ""

    @property
    def schedule_expression(self) -> str:
        if self.schedule_type == "manual":
            return ""
        elif self.schedule_type == "hours":
            h = self.schedule_interval if self.schedule_interval > 0 else 1
            return f"rate({h} hour{'s' if h != 1 else ''})"
        elif self.schedule_type == "weekdays":
            hh, mm = (self.schedule_time or "08:00").split(":")
            return f"cron({mm} {hh} ? * MON-FRI *)"
        else:
            hh, mm = (self.schedule_time or "08:00").split(":")
            return f"cron({mm} {hh} * * ? *)"

    @property
    def schedule_display(self) -> str:
        if self.schedule_type == "manual":
            return "Manual only"
        elif self.schedule_type == "hours":
            h = self.schedule_interval if self.schedule_interval > 0 else 1
            return f"Every {h} hour{'s' if h != 1 else ''}"
        elif self.schedule_type == "weekdays":
            return f"Weekdays at {self.schedule_time or '08:00'} UTC"
        else:
            return f"Daily at {self.schedule_time or '08:00'} UTC"

    def to_dynamodb_item(self) -> Dict[str, Any]:
        return {
            "user_email": {"S": self.user_email},
            "workflow_id": {"S": self.workflow_id},
            "title": {"S": self.title},
            "prompt": {"S": self.prompt},
            "schedule_type": {"S": self.schedule_type},
            "schedule_interval": {"N": str(self.schedule_interval)},
            "schedule_time": {"S": self.schedule_time},
            "created_at": {"S": self.created_at},
            "updated_at": {"S": self.updated_at},
            "enabled": {"BOOL": self.enabled},
            "schedule_rule_arn": {"S": self.schedule_rule_arn},
            "model_id": {"S": self.model_id},
        }

    @classmethod
    def from_dynamodb_item(cls, item: Dict[str, Any]) -> "Workflow":
        return cls(
            workflow_id=item.get("workflow_id", {}).get("S", ""),
            user_email=item.get("user_email", {}).get("S", ""),
            title=item.get("title", {}).get("S", ""),
            prompt=item.get("prompt", {}).get("S", ""),
            schedule_type=item.get("schedule_type", {}).get("S", "manual"),
            schedule_interval=int(item.get("schedule_interval", {}).get("N", "0")),
            schedule_time=item.get("schedule_time", {}).get("S", "08:00"),
            created_at=item.get("created_at", {}).get("S", ""),
            updated_at=item.get("updated_at", {}).get("S", ""),
            enabled=item.get("enabled", {}).get("BOOL", True),
            schedule_rule_arn=item.get("schedule_rule_arn", {}).get("S", ""),
            model_id=item.get("model_id", {}).get("S", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "user_email": self.user_email,
            "title": self.title,
            "prompt": self.prompt,
            "schedule_type": self.schedule_type,
            "schedule_interval": self.schedule_interval,
            "schedule_time": self.schedule_time,
            "schedule_expression": self.schedule_expression,
            "schedule_display": self.schedule_display,
            "created_by": self.user_email,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "enabled": self.enabled,
            "schedule_rule_arn": self.schedule_rule_arn,
            "model_id": self.model_id,
        }


@dataclass
class WorkflowResult:
    """A single execution result for a workflow.

    Stored in the results table: PK=workflow_id, SK=timestamp
    """
    workflow_id: str
    timestamp: str
    status: str = "success"
    response_md: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    priority: str = "normal"
    triggered_by: str = "manual"
    model_id: str = ""

    def to_dynamodb_item(self) -> Dict[str, Any]:
        return {
            "workflow_id": {"S": self.workflow_id},
            "timestamp": {"S": self.timestamp},
            "status": {"S": self.status},
            "response_md": {"S": self.response_md},
            "input_tokens": {"N": str(self.input_tokens)},
            "output_tokens": {"N": str(self.output_tokens)},
            "latency_ms": {"N": str(self.latency_ms)},
            "priority": {"S": self.priority},
            "triggered_by": {"S": self.triggered_by},
            "model_id": {"S": self.model_id},
        }

    @classmethod
    def from_dynamodb_item(cls, item: Dict[str, Any]) -> "WorkflowResult":
        return cls(
            workflow_id=item.get("workflow_id", {}).get("S", ""),
            timestamp=item.get("timestamp", {}).get("S", ""),
            status=item.get("status", {}).get("S", "success"),
            response_md=item.get("response_md", {}).get("S", ""),
            input_tokens=int(item.get("input_tokens", {}).get("N", "0")),
            output_tokens=int(item.get("output_tokens", {}).get("N", "0")),
            latency_ms=int(item.get("latency_ms", {}).get("N", "0")),
            priority=item.get("priority", {}).get("S", "normal"),
            triggered_by=item.get("triggered_by", {}).get("S", "manual"),
            model_id=item.get("model_id", {}).get("S", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "response_md": self.response_md,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "priority": self.priority,
            "triggered_by": self.triggered_by,
            "model_id": self.model_id,
        }
