"""Model registry API.

Exposes the canonical Bedrock model list from ``app.admin.cost_calculator``
to the frontend so JS dropdowns don't have to maintain their own copy.
"""

from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.admin.cost_calculator import DEFAULT_MODEL_ID, list_models_public


router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models")
async def get_models() -> JSONResponse:
    """Return the canonical model list and the current default model id.

    Response shape::

        {
          "default_model_id": "global.anthropic.claude-sonnet-4-6",
          "models": [
            {
              "id": "...",
              "name": "Claude Sonnet 4.6",
              "description": "IN [$3.00] - OUT [$15.00]",
              "default": true
            },
            ...
          ]
        }

    Cached for 5 minutes client-side — changes require a browser refresh,
    but the list rarely turns over.
    """
    payload: Dict[str, Any] = {
        "default_model_id": DEFAULT_MODEL_ID,
        "models": list_models_public(),
    }
    return JSONResponse(
        payload,
        headers={"Cache-Control": "public, max-age=300"},
    )
