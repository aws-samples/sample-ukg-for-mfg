"""
Discovery Helper Tools — Strands Native

Provides helper tools for the Discovery Agent:
- get_canonical_concepts: Returns the canonical manufacturing vocabulary
"""

import json
import logging

from strands import tool

from concepts import CANONICAL_CONCEPTS, get_all_concepts_serializable

logger = logging.getLogger(__name__)


@tool
def get_canonical_concepts() -> str:
    """Return the canonical manufacturing vocabulary as a JSON list.

    Used during the Discovery Agent's Understanding phase to map discovered
    fields to standard manufacturing concepts.

    Returns:
        JSON string containing the list of canonical concept identifiers.
    """
    concepts = get_all_concepts_serializable()
    return json.dumps({
        "success": True,
        "count": len(concepts),
        "concepts": concepts,
    })

