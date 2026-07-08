"""Main FastAPI application entry point for HTMX ChatApp."""

import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, HTMLResponse
from pathlib import Path
from dotenv import load_dotenv
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

# Load environment variables from .env file (override shell env vars)
load_dotenv(override=True)

# Ensure our own loggers emit at INFO by default. Uvicorn's logger config
# only configures the `uvicorn.*` loggers; ours would inherit WARNING from
# the root logger and drop informational messages. Force INFO on app.*.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("app").setLevel(logging.INFO)

# Silence a benign, repetitive botocore notice: the live AgentCore Memory
# service returns a `dateTimeValue` union member that even the latest bundled
# botocore service model doesn't know about yet. Our code only reads
# content.text / createdAt, so the unknown member is harmless. Suppress the
# per-record INFO spam while still surfacing real warnings.
logging.getLogger("botocore.parsers").setLevel(logging.WARNING)

from app.config import get_config, ConfigurationError
from app.auth.middleware import AuthMiddleware
from app.routes.auth import router as auth_router
from app.routes.chat import router as chat_router
from app.routes.memory import router as memory_router
from app.routes.admin import router as admin_router
from app.routes.feedback import router as feedback_router, admin_router as feedback_admin_router
from app.routes.prompt_templates import router as templates_router, admin_router as templates_admin_router
from app.routes.app_settings import api_router as settings_api_router, admin_router as settings_admin_router
from app.routes.discovery import router as discovery_router
from app.routes.registry import router as registry_router
from app.routes.ukg import ukg_router
from app.routes.registry_graph import admin_router as graph_admin_router, api_router as graph_api_router
from app.routes.workflows import router as workflows_router
from app.routes.models import router as models_router

# Set up paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# Check if we're in development mode (reload enabled)
DEV_MODE = os.environ.get("DEV_RELOAD", "false").lower() == "true"

# Live reload setup for development
hot_reload = None
if DEV_MODE:
    try:
        import arel
        TEMPLATES_DIR = BASE_DIR / "templates"
        hot_reload = arel.HotReload(paths=[
            arel.Path(str(TEMPLATES_DIR)),
            arel.Path(str(STATIC_DIR)),
        ])
        print("[DEV] Hot reload enabled - watching templates and static files")
    except ImportError:
        print("[DEV] arel not installed, hot reload disabled. Run: pip install arel")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    try:
        config = get_config()
        mode = "DEV MODE" if config.dev_mode else "PRODUCTION"
        print(f"[{mode}] Configuration loaded for region: {config.aws_region}")
        if config.dev_mode:
            print(f"[DEV MODE] Auth bypassed, using user ID: {config.dev_user_id}")
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        raise
    
    # Initialize template globals with app settings
    from app.templates_config import init_template_globals
    await init_template_globals()
    
    # Start hot reload if enabled
    if hot_reload:
        await hot_reload.startup()
    
    yield
    
    # Shutdown
    if hot_reload:
        await hot_reload.shutdown()


# Initialize FastAPI app
# Note: redirect_slashes=False prevents FastAPI from generating 307 redirects
# that use the origin hostname (Lambda Function URL) instead of CloudFront URL
app = FastAPI(
    title="Agentic Chat App",
    description="HTMX-based chat application with AgentCore backend",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# Import shared templates instance
from app.templates_config import templates

# Inject hot reload script into templates if enabled
if hot_reload:
    templates.env.globals["hot_reload"] = hot_reload

# Add proxy headers middleware (for ALB/reverse proxy HTTPS handling)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

# Add authentication middleware
app.add_middleware(AuthMiddleware)


# ────────────────────────────────────────────────────────────────────────────
# Streaming diagnostic endpoint
# ────────────────────────────────────────────────────────────────────────────
# Emits a timestamped SSE event every second for 10 seconds. Use this to
# isolate whether the chatapp's streaming plumbing (FastAPI → Lambda Web
# Adapter → CloudFront → browser) actually delivers chunks as they're
# written, or buffers the whole response to end-of-stream.
#
# Expected behavior: curl -N {url}/api/test-stream should print a new line
# roughly every second. If all 10 lines appear at once after 10s, the
# streaming path is buffered somewhere downstream of FastAPI.
@app.get("/api/test-stream")
async def test_stream():
    import asyncio as _asyncio
    import time as _time
    from fastapi.responses import StreamingResponse

    async def _generate():
        start = _time.time()
        for i in range(10):
            elapsed = _time.time() - start
            yield f"data: {{\"chunk\": {i}, \"elapsed_sec\": {elapsed:.2f}}}\n\n"
            await _asyncio.sleep(1)
        yield "data: {\"done\": true}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Include routers
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(admin_router)
app.include_router(feedback_router)
app.include_router(feedback_admin_router)
app.include_router(templates_router)
app.include_router(templates_admin_router)
app.include_router(settings_api_router)
app.include_router(settings_admin_router)
app.include_router(discovery_router)
app.include_router(registry_router)
app.include_router(ukg_router)
app.include_router(graph_admin_router)
app.include_router(graph_api_router)
app.include_router(workflows_router)
app.include_router(models_router)
from app.routes.workflows import admin_router as workflows_admin_router
from app.routes.workflows import pages_router as workflows_pages_router
app.include_router(workflows_admin_router)
app.include_router(workflows_pages_router)

# Add hot reload route if enabled
if hot_reload:
    app.add_websocket_route("/hot-reload", hot_reload, name="hot-reload")

# Mount static files if directory exists
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/health")
async def health_check():
    """Health check endpoint for ECS."""
    return {"status": "healthy", "version": "0.1.0"}


@app.get("/")
async def root():
    """Root endpoint - redirects to home control panel."""
    return RedirectResponse(url="/home", status_code=302)


@app.get("/home", response_class=HTMLResponse)
async def home_page(request: Request):
    """Home page — Unified Knowledge Graph control panel."""
    user = getattr(request.state, "user", None)
    user_email = user.email if user else None
    is_admin = getattr(request.state, "is_admin", False)

    from app.helpers import get_app_settings
    app_settings = await get_app_settings()

    # Determine available agents (same logic as chat page)
    config = get_config()
    available_agents = []
    if config.explorer_runtime_arn:
        available_agents.append({"id": "explorer", "name": "Data Explorer", "description": "Dynamic Data Exploration"})
    if config.discovery_runtime_arn and is_admin:
        available_agents.append({"id": "discovery", "name": "Discovery", "description": "Systems Discovery & Correlation"})

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "user_email": user_email,
            "is_admin": is_admin,
            "available_agents": available_agents,
            **app_settings,
        }
    )


@app.get("/admin/discover", response_class=HTMLResponse)
async def discover_page(request: Request):
    """Data Discovery page — full-screen system explorer with Discovery Agent."""
    user = getattr(request.state, "user", None)
    user_email = user.email if user else None
    is_admin = getattr(request.state, "is_admin", False)

    from app.helpers import get_app_settings
    app_settings = await get_app_settings()

    return templates.TemplateResponse(
        "admin/discover.html",
        {
            "request": request,
            "user_email": user_email,
            "is_admin": is_admin,
            **app_settings,
        }
    )

