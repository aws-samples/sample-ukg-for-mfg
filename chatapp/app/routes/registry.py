"""Admin routes for the System Registry viewer.

Displays registered systems, their schemas, fields, concept mappings,
and cross-system equivalences from the V2 System Registry DynamoDB table.
"""

import logging
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse

from app.admin.registry_repository import RegistryRepository
from app.config import get_config
from app.templates_config import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/registry", tags=["admin", "registry"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def registry_list(request: Request):
    """System Registry overview — lists all registered systems."""
    config = get_config()
    if not config.registry_table_name:
        return templates.TemplateResponse(
            "admin/registry.html",
            {"request": request, "systems": [], "registry_configured": False},
        )

    repo = RegistryRepository()
    systems = await repo.get_all_systems()
    systems.sort(key=lambda s: (s.system_type, s.system_id))

    return templates.TemplateResponse(
        "admin/registry.html",
        {"request": request, "systems": systems, "registry_configured": True},
    )


@router.get("/concepts", response_class=HTMLResponse)
async def registry_concepts(request: Request):
    """Concept Mappings — fields grouped by canonical concept across systems."""
    config = get_config()
    if not config.registry_table_name:
        return templates.TemplateResponse(
            "admin/registry_concepts.html",
            {"request": request, "concepts": [], "registry_configured": False},
        )

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
    total_mappings = 0
    cross_system_count = 0
    for concept_id, mappings in sorted(concept_map.items(), key=lambda x: -len(x[1])):
        unique_systems = len({m["system_id"] for m in mappings})
        total_mappings += len(mappings)
        if unique_systems >= 2:
            cross_system_count += 1
        concepts.append({
            "concept_id": concept_id,
            "mapping_count": len(mappings),
            "system_count": unique_systems,
            "mappings": mappings,
        })

    return templates.TemplateResponse(
        "admin/registry_concepts.html",
        {
            "request": request,
            "concepts": concepts,
            "total_mappings": total_mappings,
            "cross_system_count": cross_system_count,
            "registry_configured": True,
        },
    )


@router.get("/equivalences", response_class=HTMLResponse)
async def registry_equivalences(request: Request):
    """Field Equivalences — cross-system field mappings discovered by the agent."""
    config = get_config()
    if not config.registry_table_name:
        return templates.TemplateResponse(
            "admin/registry_equivalences.html",
            {"request": request, "equivalences": [], "registry_configured": False},
        )

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

    unique_concepts = len({eq["concept_id"] for eq in equivalences if eq["concept_id"]})
    avg_confidence = 0
    if equivalences:
        avg_confidence = round(
            sum(eq["confidence"] for eq in equivalences) / len(equivalences) * 100
        )

    return templates.TemplateResponse(
        "admin/registry_equivalences.html",
        {
            "request": request,
            "equivalences": equivalences,
            "unique_concepts": unique_concepts,
            "avg_confidence": avg_confidence,
            "registry_configured": True,
        },
    )


@router.get("/{system_id}", response_class=HTMLResponse)
async def registry_detail(request: Request, system_id: str):
    """System detail — schemas, fields, concept mappings, equivalences."""
    config = get_config()
    if not config.registry_table_name:
        raise HTTPException(status_code=503, detail="Registry table not configured")

    repo = RegistryRepository()
    detail = await repo.get_system_detail(system_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"System '{system_id}' not found")

    # Per-table field counts for the Tables tab (derived from the already-loaded
    # fields; SCHEMA# items don't store a field/column count).
    field_counts: dict[str, int] = {}
    for f in detail.fields:
        field_counts[f.table_name] = field_counts.get(f.table_name, 0) + 1

    return templates.TemplateResponse(
        "admin/registry_detail.html",
        {
            "request": request,
            "system": detail.metadata,
            "schemas": detail.schemas,
            "fields": detail.fields,
            "equivalences": detail.equivalences,
            "field_counts": field_counts,
        },
    )


@router.delete("/{system_id}")
async def registry_delete(request: Request, system_id: str):
    """Delete a system and all related records. Admin-only."""
    is_admin = getattr(request.state, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    config = get_config()
    if not config.registry_table_name:
        raise HTTPException(status_code=503, detail="Registry table not configured")

    repo = RegistryRepository()
    try:
        deleted = await repo.delete_system(system_id)
        if deleted == 0:
            raise HTTPException(status_code=404, detail=f"System '{system_id}' not found")
        return {"deleted": deleted, "system_id": system_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete system %s: %s", system_id, e)
        raise HTTPException(status_code=500, detail=f"Failed to delete system: {e}")


@router.post("/bulk-delete")
async def registry_bulk_delete(request: Request):
    """Delete multiple systems at once. Admin-only."""
    is_admin = getattr(request.state, "is_admin", False)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    config = get_config()
    if not config.registry_table_name:
        raise HTTPException(status_code=503, detail="Registry table not configured")

    body = await request.json()
    system_ids = body.get("system_ids", [])
    if not system_ids:
        raise HTTPException(status_code=400, detail="No system_ids provided")

    repo = RegistryRepository()
    results = {"deleted": [], "failed": []}
    for sid in system_ids:
        try:
            count = await repo.delete_system(sid)
            if count > 0:
                results["deleted"].append({"system_id": sid, "records": count})
            else:
                results["failed"].append({"system_id": sid, "reason": "not found"})
        except Exception as e:
            logger.error("Bulk delete failed for %s: %s", sid, e)
            results["failed"].append({"system_id": sid, "reason": str(e)})

    return results
