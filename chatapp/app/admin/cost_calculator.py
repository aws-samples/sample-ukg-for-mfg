"""Cost calculator and shared model registry.

This module is the **single source of truth** for which Bedrock models the
app supports and what they cost. Both the cost calculator (used by the
admin dashboard) and the ``/api/models`` endpoint (used by the frontend
dropdowns) derive their data from the ``MODELS`` list below.

Update ``MODELS`` to add/remove a model or adjust pricing — every other
surface that depends on model metadata reads from here.
"""

import os
from typing import Dict, List, Optional


# Canonical model registry. Exactly one entry should have ``default: True``.
# Prices are USD per 1 million tokens.
MODELS: List[Dict[str, object]] = [
    {
        "id": "global.anthropic.claude-opus-4-7",
        "name": "Claude Opus 4.7",
        "input": 5.00,
        "output": 25.00,
    },
    {
        "id": "global.anthropic.claude-opus-4-6-v1",
        "name": "Claude Opus 4.6",
        "input": 5.00,
        "output": 25.00,
    },
    {
        "id": "global.anthropic.claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "input": 3.00,
        "output": 15.00,
        "default": True,
    },
    {
        "id": "global.anthropic.claude-opus-4-5-20251101-v1:0",
        "name": "Claude Opus 4.5",
        "input": 5.00,
        "output": 25.00,
    },
    {
        "id": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "name": "Claude Sonnet 4.5",
        "input": 3.00,
        "output": 15.00,
    },
    {
        "id": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "name": "Claude Haiku 4.5",
        "input": 1.00,
        "output": 5.00,
    },
]


def _default_from_registry() -> str:
    """Return the model id flagged ``default: True`` in ``MODELS``.

    Falls back to the first entry if nothing is flagged — keeps the app
    functional if someone forgets the flag during a registry edit.
    """
    for m in MODELS:
        if m.get("default"):
            return str(m["id"])
    return str(MODELS[0]["id"]) if MODELS else ""


# Env var wins so CDK / operators can pin a different default per
# environment without editing code. Falls back to the registry-flagged
# default.
DEFAULT_MODEL_ID: str = os.getenv("DEFAULT_MODEL_ID", _default_from_registry())


# Flat pricing map derived from MODELS. Consumers that want a simple
# ``{model_id: {"input": r, "output": r}}`` lookup use this instead of
# iterating the richer MODELS registry.
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    str(m["id"]): {"input": float(m["input"]), "output": float(m["output"])}
    for m in MODELS
}


# Default pricing for unknown models
DEFAULT_PRICING = {"input": 0.00, "output": 0.00}


def list_models_public() -> List[Dict[str, object]]:
    """Return model metadata in the shape the frontend consumes.

    Keeps ``description`` as a pre-formatted pricing string so the JS
    dropdowns don't have to know how to format prices — the Python side
    stays authoritative.
    """
    out: List[Dict[str, object]] = []
    for m in MODELS:
        mid = str(m["id"])
        out.append({
            "id": mid,
            "name": str(m["name"]),
            "description": f"IN [${float(m['input']):.2f}] - OUT [${float(m['output']):.2f}]",
            "default": mid == DEFAULT_MODEL_ID,
        })
    return out


class CostCalculator:
    """Calculate costs based on token usage and model pricing.

    This class provides methods to calculate costs for token usage
    and project monthly costs based on usage patterns.
    """

    def __init__(self, pricing: Optional[Dict[str, Dict[str, float]]] = None):
        """Initialize the cost calculator.

        Args:
            pricing: Optional custom pricing dictionary. Defaults to MODEL_PRICING.
        """
        self.pricing = pricing or MODEL_PRICING

    def calculate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model_id: str,
    ) -> float:
        """Calculate cost in USD for given token usage.

        Uses the formula: (input_tokens / 1,000,000 * input_rate) +
                         (output_tokens / 1,000,000 * output_rate)

        Args:
            input_tokens: Number of input/prompt tokens
            output_tokens: Number of output/response tokens
            model_id: The model identifier for pricing lookup

        Returns:
            Cost in USD
        """
        rates = self.pricing.get(model_id, DEFAULT_PRICING)

        input_cost = (input_tokens / 1_000_000) * rates["input"]
        output_cost = (output_tokens / 1_000_000) * rates["output"]

        return input_cost + output_cost

    def calculate_monthly_projection(
        self,
        total_cost: float,
        days_in_period: int,
    ) -> float:
        """Project monthly cost based on average daily usage.

        Uses the formula: (total_cost / days_in_period) * 20

        Args:
            total_cost: Total cost for the period in USD
            days_in_period: Number of days in the measurement period

        Returns:
            Projected monthly cost in USD
        """
        if days_in_period <= 0:
            return 0.0

        daily_average = total_cost / days_in_period
        return daily_average * 20

    def get_model_rates(self, model_id: str) -> Dict[str, float]:
        """Get pricing rates for a specific model.

        Args:
            model_id: The model identifier

        Returns:
            Dictionary with 'input' and 'output' rates per 1M tokens
        """
        return self.pricing.get(model_id, DEFAULT_PRICING)
