"""Universal Knowledge Graph API routes for the sidebar and admin views.

Provides JSON endpoints for:
- /api/registry/systems — registered systems (sidebar)
- /api/registry/vocabulary — canonical concept vocabulary grouped by domain (sidebar)
- /api/registry/concepts — field-to-concept mappings across systems (admin)
- /api/registry/equivalences — cross-system field equivalences (admin)
"""

import logging
from collections import defaultdict
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.admin.concepts import CONCEPT_GROUPS, get_total_concept_count
from app.admin.registry_repository import RegistryRepository
from app.config import get_config

logger = logging.getLogger(__name__)

ukg_router = APIRouter(prefix="/api/registry", tags=["ukg"])


@ukg_router.get("/systems")
async def get_systems(request: Request):
    """Get all registered systems for the Universal Knowledge Graph sidebar."""
    config = get_config()
    if not config.registry_table_name:
        return JSONResponse(content={"systems": [], "count": 0, "configured": False})

    repo = RegistryRepository()
    systems = await repo.get_all_systems()
    systems.sort(key=lambda s: (s.system_type, s.system_id))

    return JSONResponse(content={
        "systems": [asdict(s) for s in systems],
        "count": len(systems),
        "configured": True,
    })


@ukg_router.get("/systems/{system_id}")
async def get_system_detail(request: Request, system_id: str):
    """Get full detail for a single system — schemas, fields (with concept
    mappings), and cross-system equivalences. Used by the sidebar's system
    explorer to render the per-table field list without scraping the admin
    page's HTML.
    """
    config = get_config()
    if not config.registry_table_name:
        return JSONResponse(
            status_code=503,
            content={"configured": False, "system_id": system_id},
        )

    repo = RegistryRepository()
    detail = await repo.get_system_detail(system_id)
    if not detail:
        return JSONResponse(
            status_code=404,
            content={"configured": True, "system_id": system_id, "error": "not_found"},
        )

    # Equivalences come straight from DynamoDB and contain Decimal values
    # (notably ``confidence``) which the default JSON encoder can't handle.
    # Normalize to JSON-safe primitives, matching the shape exposed by
    # GET /api/registry/equivalences.
    normalized_equivs = [
        {
            "source_system": eq.get("source_system", ""),
            "source_table": eq.get("source_table", ""),
            "source_field": eq.get("source_field", ""),
            "target_system": eq.get("target_system", ""),
            "target_table": eq.get("target_table", ""),
            "target_field": eq.get("target_field", ""),
            "concept_id": eq.get("concept_id", ""),
            "confidence": float(eq.get("confidence", 0)),
            "transform": eq.get("transform", ""),
            "inferred_at": eq.get("inferred_at", ""),
        }
        for eq in detail.equivalences
    ]

    return JSONResponse(content={
        "configured": True,
        "system": asdict(detail.metadata),
        "schemas": [asdict(s) for s in detail.schemas],
        "fields": [asdict(f) for f in detail.fields],
        "equivalences": normalized_equivs,
    })


@ukg_router.get("/vocabulary")
async def get_vocabulary(request: Request):
    """Get the canonical manufacturing vocabulary grouped by ISA-95 domain."""
    return JSONResponse(content={
        "groups": CONCEPT_GROUPS,
        "total_concepts": get_total_concept_count(),
    })


@ukg_router.get("/concepts")
async def get_concepts(request: Request):
    """Get fields grouped by concept across all systems (admin view)."""
    config = get_config()
    if not config.registry_table_name:
        return JSONResponse(content={"concepts": [], "count": 0, "configured": False})

    repo = RegistryRepository()
    systems = await repo.get_all_systems()

    concept_map: dict[str, list[dict]] = defaultdict(list)

    for system in systems:
        detail = await repo.get_system_detail(system.system_id)
        if not detail:
            continue
        for field in detail.fields:
            if field.concept_id:
                concept_map[field.concept_id].append({
                    "system_id": system.system_id,
                    "system_name": system.name or system.system_id,
                    "system_type": system.system_type,
                    "table_name": field.table_name,
                    "field_name": field.field_name,
                    "data_type": field.data_type,
                    "confidence": field.concept_confidence,
                })

    concepts = []
    for concept_id, mappings in sorted(
        concept_map.items(), key=lambda x: -len(x[1])
    ):
        unique_systems = len({m["system_id"] for m in mappings})
        concepts.append({
            "concept_id": concept_id,
            "mapping_count": len(mappings),
            "system_count": unique_systems,
            "mappings": mappings,
        })

    return JSONResponse(content={
        "concepts": concepts,
        "count": len(concepts),
        "configured": True,
    })


@ukg_router.get("/equivalences")
async def get_equivalences(request: Request):
    """Get all cross-system field equivalences (admin view)."""
    config = get_config()
    if not config.registry_table_name:
        return JSONResponse(content={
            "equivalences": [], "count": 0, "configured": False,
        })

    repo = RegistryRepository()
    systems = await repo.get_all_systems()

    equivalences = []
    for system in systems:
        detail = await repo.get_system_detail(system.system_id)
        if not detail:
            continue
        for eq in detail.equivalences:
            equivalences.append({
                "source_system": eq.get("source_system", ""),
                "source_table": eq.get("source_table", ""),
                "source_field": eq.get("source_field", ""),
                "target_system": eq.get("target_system", ""),
                "target_table": eq.get("target_table", ""),
                "target_field": eq.get("target_field", ""),
                "concept_id": eq.get("concept_id", ""),
                "confidence": float(eq.get("confidence", 0)),
                "transform": eq.get("transform", ""),
                "inferred_at": eq.get("inferred_at", ""),
            })

    return JSONResponse(content={
        "equivalences": equivalences,
        "count": len(equivalences),
        "configured": True,
    })
