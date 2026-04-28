"""EventBridge Scheduler management for workflow schedules.

Creates, updates, and deletes EventBridge Scheduler schedules that trigger
the workflow executor Lambda on the configured cadence.

Environment variables:
    WORKFLOW_SCHEDULER_GROUP: EventBridge Scheduler group name
    WORKFLOW_EXECUTOR_ARN: Lambda function ARN for the workflow executor
    WORKFLOW_SCHEDULER_ROLE_ARN: IAM role ARN for the scheduler to invoke Lambda
"""

import asyncio
import json
import logging
import os
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class WorkflowSchedulerService:
    """Manages EventBridge Scheduler schedules for workflows."""

    def __init__(self):
        region = os.environ.get("AWS_REGION", "us-east-1")
        self._client = boto3.client(
            "scheduler",
            config=Config(region_name=region, retries={"max_attempts": 3, "mode": "adaptive"}),
        )
        self.group_name = os.environ.get("WORKFLOW_SCHEDULER_GROUP", "")
        self.executor_arn = os.environ.get("WORKFLOW_EXECUTOR_ARN", "")
        self.role_arn = os.environ.get("WORKFLOW_SCHEDULER_ROLE_ARN", "")

    @property
    def enabled(self) -> bool:
        """Check if scheduler integration is configured."""
        return bool(self.group_name and self.executor_arn and self.role_arn)

    async def create_or_update_schedule(
        self,
        workflow_id: str,
        schedule_expression: str,
        schedule_enabled: bool = True,
        user_email: str = "",
    ) -> Optional[str]:
        """Create or update an EventBridge schedule for a workflow.

        Args:
            workflow_id: Workflow ID (used as schedule name)
            schedule_expression: EventBridge expression (rate/cron)
            schedule_enabled: Whether the schedule is active
            user_email: Owner email (passed to Lambda for direct GetItem lookup)

        Returns:
            Schedule ARN on success, None on failure
        """
        if not self.enabled:
            logger.warning("Scheduler not configured, skipping schedule creation")
            return None

        if not schedule_expression:
            # Manual-only workflow — delete any existing schedule
            await self.delete_schedule(workflow_id)
            return None

        schedule_name = f"wf-{workflow_id}"
        state = "ENABLED" if schedule_enabled else "DISABLED"

        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: self._client.create_schedule(
                Name=schedule_name,
                GroupName=self.group_name,
                ScheduleExpression=schedule_expression,
                ScheduleExpressionTimezone="UTC",
                State=state,
                FlexibleTimeWindow={"Mode": "OFF"},
                Target={
                    "Arn": self.executor_arn,
                    "RoleArn": self.role_arn,
                    "Input": json.dumps({"workflow_id": workflow_id, "user_email": user_email}),
                },
                ActionAfterCompletion="NONE",
            ))
            arn = resp.get("ScheduleArn", "")
            logger.info("Created schedule %s for workflow %s", schedule_name, workflow_id)
            return arn
        except self._client.exceptions.ConflictException:
            # Schedule already exists — update it
            try:
                resp = await loop.run_in_executor(None, lambda: self._client.update_schedule(
                    Name=schedule_name,
                    GroupName=self.group_name,
                    ScheduleExpression=schedule_expression,
                    ScheduleExpressionTimezone="UTC",
                    State=state,
                    FlexibleTimeWindow={"Mode": "OFF"},
                    Target={
                        "Arn": self.executor_arn,
                        "RoleArn": self.role_arn,
                        "Input": json.dumps({"workflow_id": workflow_id, "user_email": user_email}),
                    },
                    ActionAfterCompletion="NONE",
                ))
                arn = resp.get("ScheduleArn", "")
                logger.info("Updated schedule %s for workflow %s", schedule_name, workflow_id)
                return arn
            except ClientError as e:
                logger.error("Failed to update schedule for workflow %s: %s", workflow_id, e)
                return None
        except ClientError as e:
            logger.error("Failed to create schedule for workflow %s: %s", workflow_id, e)
            return None

    async def delete_schedule(self, workflow_id: str) -> bool:
        """Delete an EventBridge schedule for a workflow.

        Returns True if deleted or not found, False on error.
        """
        if not self.enabled:
            return True

        schedule_name = f"wf-{workflow_id}"
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._client.delete_schedule(
                Name=schedule_name,
                GroupName=self.group_name,
            ))
            logger.info("Deleted schedule %s", schedule_name)
            return True
        except self._client.exceptions.ResourceNotFoundException:
            return True
        except ClientError as e:
            logger.error("Failed to delete schedule %s: %s", schedule_name, e)
            return False
