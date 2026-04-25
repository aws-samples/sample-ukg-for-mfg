"""Workflow API routes — user-scoped.

Endpoints:
- GET  /api/workflows          — list workflows for logged-in user
- POST /api/workflows          — create a workflow
- GET  /api/workflows/{id}     — get workflow + recent results
- PUT  /api/workflows/{id}     — update workflow
- DELETE /api/workflows/{id}   — delete workflow
- POST /api/workflows/{id}/run — run workflow via Lambda
"""

import json as _json
import logging
import os

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.storage.workflow import WorkflowStorageService
from app.storage.scheduler import WorkflowSchedulerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])
pages_router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="app/templates")


_storage_instance = None

def _get_storage() -> WorkflowStorageService:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = WorkflowStorageService()
    return _storage_instance


_scheduler_instance = None

def _get_scheduler() -> WorkflowSchedulerService:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = WorkflowSchedulerService()
    return _scheduler_instance


def _get_user_email(request: Request) -> str:
    """Extract user email from auth middleware."""
    user = getattr(request.state, "user", None)
    if not user:
        return ""
    return getattr(user, "email", "") or getattr(user, "username", "") or getattr(user, "user_id", "")


@router.get("")
async def list_workflows(request: Request) -> JSONResponse:
    """List workflows for the logged-in user."""
    email = _get_user_email(request)
    if not email:
        return JSONResponse(content=[])
    storage = _get_storage()
    workflows = await storage.list_workflows_for_user(email)
    workflows.sort(key=lambda w: w.created_at, reverse=True)
    return JSONResponse(content=[w.to_dict() for w in workflows])


@router.post("")
async def create_workflow(request: Request) -> JSONResponse:
    """Create a new workflow for the logged-in user."""
    body = await request.json()
    title = (body.get("title") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    schedule_type = (body.get("schedule_type") or "manual").strip()
    schedule_interval = int(body.get("schedule_interval") or 0)
    schedule_time = (body.get("schedule_time") or "08:00").strip()
    model_id = (body.get("model_id") or "").strip()

    if not title or not prompt:
        raise HTTPException(status_code=400, detail="title and prompt are required")
    if schedule_type not in ("manual", "hours", "daily", "weekdays"):
        schedule_type = "manual"

    email = _get_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="User not authenticated")

    storage = _get_storage()
    wf = await storage.create_workflow(
        user_email=email, title=title, prompt=prompt,
        schedule_type=schedule_type,
        schedule_interval=schedule_interval,
        schedule_time=schedule_time,
        model_id=model_id,
    )

    # Create EventBridge schedule
    scheduler = _get_scheduler()
    if scheduler.enabled and wf.schedule_expression:
        arn = await scheduler.create_or_update_schedule(
            workflow_id=wf.workflow_id,
            schedule_expression=wf.schedule_expression,
            schedule_enabled=True,
            user_email=email,
        )
        if arn:
            await storage.update_workflow(email, wf.workflow_id, schedule_rule_arn=arn)
            wf.schedule_rule_arn = arn

    return JSONResponse(content=wf.to_dict(), status_code=201)


@router.get("/recent-results")
async def recent_results(request: Request) -> JSONResponse:
    """Get the 10 most recent workflow results across all user's workflows."""
    import asyncio
    email = _get_user_email(request)
    if not email:
        return JSONResponse(content=[])
    storage = _get_storage()
    workflows = await storage.list_workflows_for_user(email)
    if not workflows:
        return JSONResponse(content=[])

    # Parallel fetch results for all workflows
    async def _get_results(wf):
        results = await storage.list_results(wf.workflow_id, limit=3)
        out = []
        for r in results:
            d = r.to_dict()
            d["workflow_title"] = wf.title
            d["workflow_prompt"] = wf.prompt
            out.append(d)
        return out

    batches = await asyncio.gather(*[_get_results(wf) for wf in workflows])
    all_results = [r for batch in batches for r in batch]
    all_results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return JSONResponse(content=all_results[:10])


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request) -> JSONResponse:
    """Get a workflow and its recent results."""
    email = _get_user_email(request)
    storage = _get_storage()
    wf = await storage.get_workflow(email, workflow_id) if email else await storage.get_workflow_by_id(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    results = await storage.list_results(workflow_id, limit=10)
    data = wf.to_dict()
    data["results"] = [r.to_dict() for r in results]
    return JSONResponse(content=data)


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, request: Request) -> JSONResponse:
    """Update workflow fields."""
    email = _get_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="User not authenticated")
    body = await request.json()
    storage = _get_storage()
    wf = await storage.update_workflow(email, workflow_id, **body)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    schedule_fields = {"schedule_type", "schedule_interval", "schedule_time", "enabled"}
    if schedule_fields & set(body.keys()):
        scheduler = _get_scheduler()
        if scheduler.enabled:
            if wf.schedule_expression:
                arn = await scheduler.create_or_update_schedule(
                    workflow_id=workflow_id,
                    schedule_expression=wf.schedule_expression,
                    schedule_enabled=wf.enabled,
                    user_email=email,
                )
                if arn and arn != wf.schedule_rule_arn:
                    await storage.update_workflow(email, workflow_id, schedule_rule_arn=arn)
                    wf.schedule_rule_arn = arn
            else:
                await scheduler.delete_schedule(workflow_id)

    return JSONResponse(content=wf.to_dict())


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: str, request: Request) -> JSONResponse:
    """Delete a workflow and all its results."""
    email = _get_user_email(request)
    if not email:
        raise HTTPException(status_code=401, detail="User not authenticated")
    storage = _get_storage()
    scheduler = _get_scheduler()
    if scheduler.enabled:
        await scheduler.delete_schedule(workflow_id)
    ok = await storage.delete_workflow(email, workflow_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete workflow")
    return JSONResponse(content={"deleted": True})


@router.post("/{workflow_id}/run")
async def run_workflow(workflow_id: str, request: Request) -> JSONResponse:
    """Run a workflow via the executor Lambda."""
    executor_arn = os.environ.get("WORKFLOW_EXECUTOR_ARN", "")
    if not executor_arn:
        raise HTTPException(status_code=503, detail="Workflow executor not configured")

    email = _get_user_email(request)

    try:
        import asyncio
        import boto3
        from botocore.config import Config

        region = os.environ.get("AWS_REGION", "us-east-1")
        lam = boto3.client("lambda", config=Config(
            region_name=region, retries={"max_attempts": 2, "mode": "adaptive"},
            read_timeout=600, connect_timeout=30,
        ))
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: lam.invoke(
            FunctionName=executor_arn,
            InvocationType="RequestResponse",
            Payload=_json.dumps({"workflow_id": workflow_id, "user_email": email}).encode("utf-8"),
        ))
        payload = _json.loads(response["Payload"].read())
        if response.get("FunctionError"):
            logger.error("Lambda error", extra={"workflow_id": workflow_id, "error": payload})
            return JSONResponse(content={"status": "error", "detail": str(payload)}, status_code=500)
        body = payload.get("body", "{}")
        if isinstance(body, str):
            body = _json.loads(body)
        return JSONResponse(content=body)
    except Exception as e:
        logger.error("Failed to invoke executor", extra={"workflow_id": workflow_id, "error": str(e)})
        raise HTTPException(status_code=500, detail=f"Failed to invoke executor: {e}")


# ── Admin page ────────────────────────────────────────────────────────────

@pages_router.get("/workflows", response_class=HTMLResponse)
async def admin_workflows_page(request: Request):
    """Workflows page — shows user's workflows (admin sees all)."""
    from app.helpers import get_app_settings
    app_settings = await get_app_settings()

    email = _get_user_email(request)
    is_admin = getattr(request.state, "is_admin", False)
    storage = _get_storage()

    if is_admin:
        workflows = await storage.list_all_workflows()
    else:
        workflows = await storage.list_workflows_for_user(email) if email else []

    workflows.sort(key=lambda w: w.created_at, reverse=True)

    # Gather stats for the dashboard home view
    import asyncio as _asyncio
    total_results = 0
    active_count = sum(1 for w in workflows if w.enabled)
    result_batches = await _asyncio.gather(*[
        storage.list_results(w.workflow_id, limit=50) for w in workflows
    ])
    for batch in result_batches:
        total_results += len(batch)

    return templates.TemplateResponse(
        "workflows.html",
        {
            "request": request,
            "workflows": [w.to_dict() for w in workflows],
            "total_results": total_results,
            "active_count": active_count,
            "is_admin": is_admin,
            **app_settings,
        },
    )

