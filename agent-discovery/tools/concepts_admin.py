"""
Discovery Agent — Concept Hierarchy Administration Tools

Tools that let the Discovery Agent read and administer the manufacturing
concept vocabulary stored in DynamoDB (the ``CONCEPTS_TABLE_NAME`` table).
The agent maps discovered fields to these concepts, so keeping the vocabulary
current improves discovery accuracy.

All writes invalidate the in-process concept cache so subsequent calls to
``get_canonical_concepts`` reflect the change immediately.

Tools:
  - list_concepts:  List the vocabulary, optionally filtered by domain.
  - add_concept:    Add a new concept (or overwrite an existing one).
  - update_concept: Update an existing concept's description/aliases/domain.
  - delete_concept: Remove a concept from the vocabulary.
"""

import json
import logging

from strands import tool

from concepts import (
    Concept,
    ConceptStore,
    SOURCE_CUSTOM,
    get_concepts_table_name,
    get_concepts_by_domain,
    invalidate_cache,
)

logger = logging.getLogger(__name__)


def _store() -> ConceptStore:
    """Create a ConceptStore, raising a clear error if the table is unset."""
    if not get_concepts_table_name():
        raise ValueError(
            "CONCEPTS_TABLE_NAME is not configured; the concept hierarchy is "
            "running on built-in defaults and cannot be modified."
        )
    return ConceptStore()


def _normalize(value: str) -> str:
    """Lightweight kebab-case normalization for ids/domains."""
    return "-".join(str(value or "").strip().lower().replace("_", "-").split())


@tool
def list_concepts(domain: str = "") -> str:
    """List the manufacturing concept vocabulary, optionally filtered by domain.

    Use this to inspect the current hierarchy before adding, updating, or
    deleting concepts. Prefer ``get_canonical_concepts`` when you just need the
    full vocabulary for field mapping.

    Args:
        domain: Optional ISA-95 domain to filter by (e.g. "production",
            "maintenance"). When empty, all concepts are returned.

    Returns:
        JSON string with the matching concepts and a count.
    """
    try:
        if domain:
            dom = _normalize(domain)
            concepts = [c.to_dict() for c in get_concepts_by_domain(dom)]
        else:
            concepts = _store().list_all()
            concepts = [c.to_dict() for c in concepts]
        return json.dumps({
            "success": True,
            "domain": domain or "all",
            "count": len(concepts),
            "concepts": concepts,
        })
    except Exception as e:
        logger.error("list_concepts failed: %s — %s", type(e).__name__, e)
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})


@tool
def add_concept(concept_id: str, domain: str, description: str = "", aliases: list = None) -> str:
    """Add a new manufacturing concept to the vocabulary.

    Use this when discovery encounters a real-world concept that is not yet in
    the hierarchy. Concept ids and domains are kebab-case (e.g. "work-order",
    "production"). The domain-qualified id becomes "{domain}.{concept_id}".

    Args:
        concept_id: Kebab-case concept identifier (e.g. "work-order").
        domain: Kebab-case ISA-95 domain (e.g. "production").
        description: Short human-readable description.
        aliases: List of common field names that map to this concept.

    Returns:
        JSON string confirming the created concept, or an error.
    """
    try:
        cid = _normalize(concept_id)
        dom = _normalize(domain)
        if not cid or not dom:
            return json.dumps({"success": False, "error": "concept_id and domain are required (kebab-case)."})

        store = _store()
        qualified_id = f"{dom}.{cid}"
        if store.get(qualified_id) is not None:
            return json.dumps({
                "success": False,
                "error": f"Concept '{qualified_id}' already exists. Use update_concept to modify it.",
            })

        concept = Concept(id=cid, domain=dom, description=description or "", aliases=list(aliases or []))
        store.put(concept, source=SOURCE_CUSTOM)
        invalidate_cache()
        logger.info("add_concept: created %s", qualified_id)
        return json.dumps({"success": True, "concept": concept.to_dict()})
    except Exception as e:
        logger.error("add_concept failed: %s — %s", type(e).__name__, e)
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})


@tool
def update_concept(
    qualified_id: str,
    description: str = None,
    aliases: list = None,
    domain: str = None,
    concept_id: str = None,
) -> str:
    """Update an existing concept's description, aliases, domain, or id.

    Only the fields you pass are changed; omitted fields keep their current
    values. If ``domain`` or ``concept_id`` change the qualified id, the old
    record is removed and a new one written.

    Args:
        qualified_id: The current domain-qualified id (e.g. "production.work-order").
        description: New description (optional).
        aliases: New full list of aliases (optional; replaces the existing list).
        domain: New domain (optional).
        concept_id: New concept id (optional).

    Returns:
        JSON string confirming the updated concept, or an error.
    """
    try:
        store = _store()
        existing = store.get(qualified_id)
        if existing is None:
            return json.dumps({"success": False, "error": f"Concept '{qualified_id}' not found."})

        new_domain = _normalize(domain) if domain else existing.domain
        new_id = _normalize(concept_id) if concept_id else existing.id
        new_desc = existing.description if description is None else description
        new_aliases = list(existing.aliases) if aliases is None else list(aliases)

        updated = Concept(id=new_id, domain=new_domain, description=new_desc, aliases=new_aliases)
        store.put(updated, source=SOURCE_CUSTOM)

        # If the key changed, remove the old record.
        if updated.qualified_id != qualified_id:
            store.delete(qualified_id)

        invalidate_cache()
        logger.info("update_concept: %s -> %s", qualified_id, updated.qualified_id)
        return json.dumps({"success": True, "concept": updated.to_dict(), "previous_id": qualified_id})
    except Exception as e:
        logger.error("update_concept failed: %s — %s", type(e).__name__, e)
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})


@tool
def delete_concept(qualified_id: str) -> str:
    """Delete a concept from the vocabulary by its domain-qualified id.

    After deletion, discovery will no longer map fields to this concept. Use
    with care — prefer update_concept if you only need to adjust a concept.

    Args:
        qualified_id: The domain-qualified id to delete (e.g. "production.work-order").

    Returns:
        JSON string confirming the deletion, or an error.
    """
    try:
        store = _store()
        existing = store.get(qualified_id)
        if existing is None:
            return json.dumps({"success": False, "error": f"Concept '{qualified_id}' not found."})
        store.delete(qualified_id)
        invalidate_cache()
        logger.info("delete_concept: deleted %s", qualified_id)
        return json.dumps({"success": True, "deleted": qualified_id})
    except Exception as e:
        logger.error("delete_concept failed: %s — %s", type(e).__name__, e)
        return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})
