"""Workflow storage service — two-table design.

Workflows table: PK=user_email, SK=workflow_id
Results table: PK=workflow_id, SK=timestamp
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.models.workflow import Workflow, WorkflowResult

logger = logging.getLogger(__name__)


class WorkflowStorageService:
    """Async service for storing workflows and results in DynamoDB."""

    def __init__(self):
        region = os.environ.get("AWS_REGION", "us-east-1")
        boto_config = Config(
            region_name=region,
            retries={"max_attempts": 3, "mode": "adaptive"},
        )
        self._client = boto3.client("dynamodb", config=boto_config)

        self.workflows_table = os.environ.get("WORKFLOWS_TABLE_NAME", "")
        if not self.workflows_table:
            try:
                from app.config import get_config
                self.workflows_table = get_config().workflows_table_name
            except Exception:
                self.workflows_table = "mfg-ukg-workflows"

        self.results_table = os.environ.get("WORKFLOW_RESULTS_TABLE_NAME", "")
        if not self.results_table:
            try:
                from app.config import get_config
                self.results_table = get_config().workflow_results_table_name
            except Exception:
                self.results_table = "mfg-ukg-workflow-results"

    # ── Workflow CRUD ─────────────────────────────────────────────────────

    async def create_workflow(self, user_email: str, title: str, prompt: str,
                              schedule_type: str = "daily",
                              schedule_interval: int = 0,
                              schedule_time: str = "08:00",
                              model_id: str = "") -> Workflow:
        now = datetime.now(timezone.utc).isoformat()
        wf = Workflow(
            workflow_id=str(uuid.uuid4()),
            user_email=user_email,
            title=title,
            prompt=prompt,
            schedule_type=schedule_type,
            schedule_interval=schedule_interval,
            schedule_time=schedule_time,
            created_at=now,
            updated_at=now,
            enabled=True,
            model_id=model_id,
        )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: self._client.put_item(
            TableName=self.workflows_table, Item=wf.to_dynamodb_item()
        ))
        return wf

    async def get_workflow(self, user_email: str, workflow_id: str) -> Optional[Workflow]:
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: self._client.get_item(
                TableName=self.workflows_table,
                Key={"user_email": {"S": user_email}, "workflow_id": {"S": workflow_id}},
            ))
            item = resp.get("Item")
            return Workflow.from_dynamodb_item(item) if item else None
        except ClientError as e:
            logger.error("Failed to get workflow", extra={"error": str(e)})
            return None

    async def get_workflow_by_id(self, workflow_id: str) -> Optional[Workflow]:
        """Get workflow by ID only (scans — used by Lambda/scheduler)."""
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: self._client.scan(
                TableName=self.workflows_table,
                FilterExpression="workflow_id = :wid",
                ExpressionAttributeValues={":wid": {"S": workflow_id}},
                Limit=1,
            ))
            items = resp.get("Items", [])
            return Workflow.from_dynamodb_item(items[0]) if items else None
        except ClientError as e:
            logger.error("Failed to get workflow by id", extra={"error": str(e)})
            return None

    async def list_workflows_for_user(self, user_email: str) -> List[Workflow]:
        """List all workflows for a specific user (query, no scan)."""
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: self._client.query(
                TableName=self.workflows_table,
                KeyConditionExpression="user_email = :ue",
                ExpressionAttributeValues={":ue": {"S": user_email}},
            ))
            return [Workflow.from_dynamodb_item(i) for i in resp.get("Items", [])]
        except ClientError as e:
            logger.error("Failed to list workflows", extra={"error": str(e)})
            return []

    async def list_all_workflows(self) -> List[Workflow]:
        """List all workflows across all users (scan — admin only)."""
        try:
            loop = asyncio.get_event_loop()
            items = []
            paginator = self._client.get_paginator("scan")
            def _scan():
                result = []
                for page in paginator.paginate(TableName=self.workflows_table):
                    result.extend(page.get("Items", []))
                return result
            items = await loop.run_in_executor(None, _scan)
            return [Workflow.from_dynamodb_item(i) for i in items]
        except Exception as e:
            logger.error("Failed to list all workflows", extra={"error": str(e)})
            return []

    async def update_workflow(self, user_email: str, workflow_id: str, **kwargs) -> Optional[Workflow]:
        allowed = {"title", "prompt", "schedule_type", "schedule_interval", "schedule_time", "enabled", "schedule_rule_arn", "model_id"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return await self.get_workflow(user_email, workflow_id)

        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        expr_parts, attr_names, attr_values = [], {}, {}
        for i, (k, v) in enumerate(updates.items()):
            alias, val_alias = f"#f{i}", f":v{i}"
            expr_parts.append(f"{alias} = {val_alias}")
            attr_names[alias] = k
            if isinstance(v, bool):
                attr_values[val_alias] = {"BOOL": v}
            elif isinstance(v, int):
                attr_values[val_alias] = {"N": str(v)}
            else:
                attr_values[val_alias] = {"S": str(v)}

        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: self._client.update_item(
                TableName=self.workflows_table,
                Key={"user_email": {"S": user_email}, "workflow_id": {"S": workflow_id}},
                UpdateExpression="SET " + ", ".join(expr_parts),
                ExpressionAttributeNames=attr_names,
                ExpressionAttributeValues=attr_values,
                ReturnValues="ALL_NEW",
            ))
            return Workflow.from_dynamodb_item(resp.get("Attributes", {}))
        except ClientError as e:
            logger.error("Failed to update workflow", extra={"error": str(e)})
            return None

    async def delete_workflow(self, user_email: str, workflow_id: str) -> bool:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._client.delete_item(
                TableName=self.workflows_table,
                Key={"user_email": {"S": user_email}, "workflow_id": {"S": workflow_id}},
            ))
            # Delete all results
            results = await self.list_results(workflow_id)
            for r in results:
                await loop.run_in_executor(None, lambda ts=r.timestamp: self._client.delete_item(
                    TableName=self.results_table,
                    Key={"workflow_id": {"S": workflow_id}, "timestamp": {"S": ts}},
                ))
            return True
        except ClientError as e:
            logger.error("Failed to delete workflow", extra={"error": str(e)})
            return False

    # ── Results (separate table) ──────────────────────────────────────────

    async def save_result(self, result: WorkflowResult) -> WorkflowResult:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: self._client.put_item(
            TableName=self.results_table, Item=result.to_dynamodb_item()
        ))
        return result

    async def list_results(self, workflow_id: str, limit: int = 20) -> List[WorkflowResult]:
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(None, lambda: self._client.query(
                TableName=self.results_table,
                KeyConditionExpression="workflow_id = :wid",
                ExpressionAttributeValues={":wid": {"S": workflow_id}},
                ScanIndexForward=False,
                Limit=limit,
            ))
            return [WorkflowResult.from_dynamodb_item(i) for i in resp.get("Items", [])]
        except ClientError as e:
            logger.error("Failed to list results", extra={"error": str(e)})
            return []
