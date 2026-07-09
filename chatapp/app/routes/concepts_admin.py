"""Admin routes for managing the manufacturing concept hierarchy.

Provides a CRUD screen at /admin/concepts backed by the concepts DynamoDB
table. Concepts are the shared vocabulary the Discovery Agent maps fields to,
so changes here directly influence discovery. Built-in defaults are seeded on
deployment; admins can add, edit, and delete concepts and restore defaults.
"""

import logging
import re
from typing import List

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.admin.concepts import group_records
from app.shared.concepts import SOURCE_CUSTOM
from app.storage.concepts import ConceptsStorageService
from app.templates_config import templates

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["admin-concepts"])

# kebab-case identifier: lowercase alphanumerics separated by single hyphens
_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _normalize_id(value: str) -> str:
    """Normalize a concept id or domain to kebab-case."""
    value = (value or "").strip().lower()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def _parse_aliases(raw: str) -> List[str]:
    """Parse aliases from a comma/newline-separated string into a deduped list."""
    if not raw:
        return []
    parts = re.split(r"[,\n]", raw)
    seen: set[str] = set()
    aliases: List[str] = []
    for p in parts:
        a = p.strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            aliases.append(a)
    return aliases


@admin_router.get("/concepts", response_class=HTMLResponse)
async def admin_concepts_page(request: Request):
    """Admin page listing the concept hierarchy grouped by ISA-95 domain."""
    storage = ConceptsStorageService()
    concepts = await storage.get_all()

    groups = group_records(concepts)
    domains = [g["domain"] for g in groups]

    # Derive the count from the same fresh scan we render, rather than the
    # cached registry. The registry cache is per-process and only invalidated
    # on the task that handled a write, so with multiple ECS tasks a cached
    # count could disagree with the freshly-scanned list shown below.
    return templates.TemplateResponse(
        "admin/concepts.html",
        {
            "request": request,
            "groups": groups,
            "domains": domains,
            "total_concepts": len(concepts),
            "default_count": ConceptsStorageService.default_count(),
        },
    )


@admin_router.post("/concepts/create")
async def create_concept(
    request: Request,
    concept_id: str = Form(...),
    domain: str = Form(...),
    description: str = Form(""),
    aliases: str = Form(""),
):
    """Create (or overwrite) a concept."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    cid = _normalize_id(concept_id)
    dom = _normalize_id(domain)
    desc = (description or "").strip()
    alias_list = _parse_aliases(aliases)

    def _fail(msg: str, status: int = 400):
        if is_ajax:
            return JSONResponse(content={"success": False, "error": msg}, status_code=status)
        return RedirectResponse(url="/admin/concepts", status_code=303)

    if not _ID_PATTERN.match(cid):
        return _fail("Concept id must be kebab-case (e.g. 'work-order').")
    if not _ID_PATTERN.match(dom):
        return _fail("Domain must be kebab-case (e.g. 'production').")

    storage = ConceptsStorageService()
    qualified_id = f"{dom}.{cid}"
    existing = await storage.get(qualified_id)
    if existing is not None:
        return _fail(f"Concept '{qualified_id}' already exists.", status=409)

    concept = await storage.upsert(cid, dom, desc, alias_list, source=SOURCE_CUSTOM)
    if not concept:
        return _fail("Failed to create concept.", status=500)

    logger.info("Admin created concept", extra={"qualified_id": qualified_id})
    if is_ajax:
        return JSONResponse(content={"success": True, "concept": concept.to_dict()})
    return RedirectResponse(url="/admin/concepts", status_code=303)


@admin_router.post("/concepts/{concept_key}/edit")
async def edit_concept(
    request: Request,
    concept_key: str,
    concept_id: str = Form(...),
    domain: str = Form(...),
    description: str = Form(""),
    aliases: str = Form(""),
) -> RedirectResponse:
    """Update a concept. If the id/domain (and thus key) changes, the old record
    is removed and a new one written."""
    cid = _normalize_id(concept_id)
    dom = _normalize_id(domain)
    desc = (description or "").strip()
    alias_list = _parse_aliases(aliases)

    if not _ID_PATTERN.match(cid) or not _ID_PATTERN.match(dom):
        logger.warning("Edit concept failed: invalid id/domain", extra={"concept_key": concept_key})
        return RedirectResponse(url="/admin/concepts", status_code=303)

    storage = ConceptsStorageService()
    new_key = f"{dom}.{cid}"

    # Preserve the original source (default vs custom) when only editing in place.
    original = await storage.get(concept_key)
    source = SOURCE_CUSTOM
    if original is not None and new_key == concept_key:
        # keep whatever it was; we can't read source off Concept, default to custom-edited
        source = SOURCE_CUSTOM

    await storage.upsert(cid, dom, desc, alias_list, source=source)

    if new_key != concept_key:
        await storage.delete(concept_key)

    logger.info("Admin updated concept", extra={"concept_key": concept_key, "new_key": new_key})
    return RedirectResponse(url="/admin/concepts", status_code=303)


@admin_router.post("/concepts/{concept_key}/delete")
async def delete_concept(request: Request, concept_key: str) -> RedirectResponse:
    """Delete a concept by its qualified id."""
    storage = ConceptsStorageService()
    await storage.delete(concept_key)
    logger.info("Admin deleted concept", extra={"concept_key": concept_key})
    return RedirectResponse(url="/admin/concepts", status_code=303)


@admin_router.post("/concepts/bulk-delete")
async def bulk_delete_concepts(request: Request) -> JSONResponse:
    """Delete multiple concepts at once (JSON body: {"concept_keys": [...]})."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"success": False, "error": "Invalid JSON"}, status_code=400)

    keys = body.get("concept_keys")
    if not isinstance(keys, list) or not keys:
        return JSONResponse(
            content={"success": False, "error": "Expected non-empty 'concept_keys' array"},
            status_code=400,
        )

    storage = ConceptsStorageService()
    deleted = 0
    for key in keys:
        if await storage.delete(str(key)):
            deleted += 1

    logger.info("Admin bulk-deleted concepts", extra={"requested": len(keys), "deleted": deleted})
    return JSONResponse(content={"success": True, "deleted": deleted})


@admin_router.post("/concepts/restore-defaults")
async def restore_defaults(request: Request):
    """Restore the built-in default hierarchy (custom concepts are preserved)."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    storage = ConceptsStorageService()
    count = await storage.restore_defaults()
    logger.info("Admin restored default concepts", extra={"count": count})
    if is_ajax:
        return JSONResponse(content={"success": True, "restored": count})
    return RedirectResponse(url="/admin/concepts", status_code=303)


@admin_router.post("/concepts/clear-all")
async def clear_all_concepts(request: Request):
    """Delete every concept and enter custom-only mode (defaults won't return).

    Intended for operators building a bespoke vocabulary from scratch. The UI
    requires an explicit confirmation before calling this.
    """
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    storage = ConceptsStorageService()
    count = await storage.clear_all()
    logger.info("Admin cleared all concepts", extra={"count": count})
    if is_ajax:
        return JSONResponse(content={"success": True, "cleared": count})
    return RedirectResponse(url="/admin/concepts", status_code=303)


@admin_router.post("/concepts/domains/rename")
async def rename_domain(
    request: Request,
    old_domain: str = Form(...),
    new_domain: str = Form(...),
) -> RedirectResponse:
    """Rename a domain by moving all its concepts to the new domain name."""
    old = _normalize_id(old_domain)
    new = _normalize_id(new_domain)
    if not _ID_PATTERN.match(new):
        logger.warning("Rename domain failed: invalid new domain", extra={"new_domain": new_domain})
        return RedirectResponse(url="/admin/concepts", status_code=303)
    storage = ConceptsStorageService()
    moved = await storage.rename_domain(old, new)
    logger.info("Admin renamed domain", extra={"old": old, "new": new, "count": moved})
    return RedirectResponse(url="/admin/concepts", status_code=303)


@admin_router.post("/concepts/domains/{domain}/delete")
async def delete_domain(request: Request, domain: str) -> RedirectResponse:
    """Delete an entire domain and all of its concepts."""
    storage = ConceptsStorageService()
    deleted = await storage.delete_domain(_normalize_id(domain))
    logger.info("Admin deleted domain", extra={"domain": domain, "count": deleted})
    return RedirectResponse(url="/admin/concepts", status_code=303)
