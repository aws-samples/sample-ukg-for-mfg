"""Registry Graph — admin page and API for force-graph visualization.

Routes:
  - GET /admin/data-graph — HTML page with interactive force-graph
  - GET /api/registry/graph — JSON graph data in Cytoscape elements format
"""

import logging
from collections import defaultdict
from dataclasses import asdict

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse

from app.admin.registry_repository import RegistryRepository
from app.config import get_config
from app.templates_config import templates

logger = logging.getLogger(__name__)

# Separate routers for admin pages and API
admin_router = APIRouter(prefix="/admin", tags=["admin"])
api_router = APIRouter(prefix="/api/registry", tags=["digital_thread"])

# Color palette for systems (deterministic by index)
_SYSTEM_COLORS = [
    "#00d8ff", "#3fb950", "#f0883e", "#a371f7", "#f85149",
    "#d29922", "#58a6ff", "#db61a2", "#79c0ff", "#7ee787",
]

# Shape by protocol/source type
_PROTOCOL_SHAPES = {
    "rds-data-api": "barrel",       # DB cylinder
    "REST/OpenAPI": "diamond",       # API diamond
    "s3tables": "hexagon",           # S3/Iceberg hexagon
    "athena": "hexagon",
    "mcp": "pentagon",               # MCP pentagon
}


def _system_color(index: int) -> str:
    return _SYSTEM_COLORS[index % len(_SYSTEM_COLORS)]


def _node_shape(protocol: str) -> str:
    return _PROTOCOL_SHAPES.get(protocol, "ellipse")


@api_router.get("/graph")
async def get_graph_data(
    request: Request,
    edge_type: str = Query("equivalences", description="Edge type: equivalences, concepts, or references"),
):
    """Return graph data in Cytoscape.js elements format.

    Nodes = tables from registered systems, grouped by system.
    Edges = based on edge_type parameter:
      - equivalences: cross-system field equivalences (EQUIV items)
      - concepts: tables sharing the same concept (via GSI1)
      - references: intra-system FK-like references (fields referencing other tables)
    """
    config = get_config()
    if not config.registry_table_name:
        return JSONResponse(content={"elements": [], "configured": False})

    repo = RegistryRepository()
    systems = await repo.get_all_systems()

    # Build system index for colors
    system_index = {s.system_id: i for i, s in enumerate(systems)}
    system_protocols = {}

    nodes = []
    edges = []

    # Collect all system details
    all_details = {}
    for system in systems:
        detail = await repo.get_system_detail(system.system_id)
        if not detail:
            continue
        all_details[system.system_id] = detail
        system_protocols[system.system_id] = system.protocol

        # Create compound parent node for each system
        nodes.append({
            "data": {
                "id": system.system_id,
                "label": system.name or system.system_id,
                "type": "system",
                "system_type": system.system_type,
                "protocol": system.protocol,
                "vendor": system.vendor,
                "color": _system_color(system_index[system.system_id]),
            },
        })

        # Create child nodes for each table/schema
        for schema in detail.schemas:
            table_name = schema.table_name if hasattr(schema, "table_name") else schema.get("table_name", "")
            node_id = f"{system.system_id}.{table_name}"
            schema_name = schema.schema_name if hasattr(schema, "schema_name") else schema.get("schema_name", "")
            row_count = schema.row_count if hasattr(schema, "row_count") else schema.get("row_count")
            description = schema.description if hasattr(schema, "description") else schema.get("description", "")

            # Count fields for this table
            field_count = sum(
                1 for f in detail.fields
                if (f.table_name if hasattr(f, "table_name") else f.get("table_name", "")) == table_name
            )

            nodes.append({
                "data": {
                    "id": node_id,
                    "label": table_name,
                    "parent": system.system_id,
                    "type": "table",
                    "system_id": system.system_id,
                    "system_type": system.system_type,
                    "protocol": system.protocol,
                    "schema_name": schema_name,
                    "row_count": row_count,
                    "field_count": field_count,
                    "description": description,
                    "color": _system_color(system_index[system.system_id]),
                    "shape": _node_shape(system.protocol),
                },
            })

    # Build edges based on type
    if edge_type == "equivalences":
        edges = _build_equivalence_edges(all_details)
    elif edge_type == "concepts":
        edges = _build_concept_edges(all_details)
    elif edge_type == "references":
        edges = _build_reference_edges(all_details)

    return JSONResponse(content={
        "elements": {"nodes": nodes, "edges": edges},
        "systems": [asdict(s) for s in systems],
        "edge_type": edge_type,
        "configured": True,
    })


def _build_equivalence_edges(all_details: dict) -> list:
    """Build edges from cross-system field equivalences."""
    edges = []
    seen = set()
    for system_id, detail in all_details.items():
        for eq in detail.equivalences:
            src_sys = eq.get("source_system", "")
            src_tbl = eq.get("source_table", "")
            tgt_sys = eq.get("target_system", "")
            tgt_tbl = eq.get("target_table", "")
            concept = eq.get("concept_id", "")
            confidence = float(eq.get("confidence", 0))
            transform = eq.get("transform", "")
            src_field = eq.get("source_field", "")
            tgt_field = eq.get("target_field", "")

            source_id = f"{src_sys}.{src_tbl}"
            target_id = f"{tgt_sys}.{tgt_tbl}"
            edge_key = tuple(sorted([source_id, target_id]) + [concept])

            if edge_key in seen:
                continue
            seen.add(edge_key)

            edges.append({
                "data": {
                    "id": f"eq-{source_id}-{target_id}-{concept}",
                    "source": source_id,
                    "target": target_id,
                    "label": concept,
                    "edge_type": "equivalence",
                    "confidence": confidence,
                    "transform": transform,
                    "source_field": src_field,
                    "target_field": tgt_field,
                    "detail": f"{src_field} ↔ {tgt_field} ({transform}, {confidence:.0%})",
                },
            })
    return edges


def _build_concept_edges(all_details: dict) -> list:
    """Build edges between tables that share the same concept."""
    # Group tables by concept
    concept_tables: dict[str, list[str]] = defaultdict(list)
    for system_id, detail in all_details.items():
        for field in detail.fields:
            concept = field.concept_id if hasattr(field, "concept_id") else field.get("concept_id", "")
            table = field.table_name if hasattr(field, "table_name") else field.get("table_name", "")
            if concept:
                node_id = f"{system_id}.{table}"
                if node_id not in concept_tables[concept]:
                    concept_tables[concept].append(node_id)

    edges = []
    seen = set()
    for concept, table_ids in concept_tables.items():
        if len(table_ids) < 2:
            continue
        # Connect all pairs
        for i in range(len(table_ids)):
            for j in range(i + 1, len(table_ids)):
                edge_key = tuple(sorted([table_ids[i], table_ids[j]]) + [concept])
                if edge_key in seen:
                    continue
                seen.add(edge_key)
                edges.append({
                    "data": {
                        "id": f"concept-{table_ids[i]}-{table_ids[j]}-{concept}",
                        "source": table_ids[i],
                        "target": table_ids[j],
                        "label": concept,
                        "edge_type": "concept",
                        "detail": f"Shared concept: {concept}",
                    },
                })
    return edges


def _build_reference_edges(all_details: dict) -> list:
    """Build intra-system edges from FK-like field references."""
    edges = []
    for system_id, detail in all_details.items():
        # Build a set of table names and their key fields
        table_keys: dict[str, set] = {}
        for field in detail.fields:
            table = field.table_name if hasattr(field, "table_name") else field.get("table_name", "")
            fname = field.field_name if hasattr(field, "field_name") else field.get("field_name", "")
            is_key = field.is_key if hasattr(field, "is_key") else field.get("is_key", False)
            if is_key:
                table_keys.setdefault(table, set()).add(fname)

        # For each non-key field, check if it matches a key field in another table
        seen = set()
        for field in detail.fields:
            table = field.table_name if hasattr(field, "table_name") else field.get("table_name", "")
            fname = field.field_name if hasattr(field, "field_name") else field.get("field_name", "")
            is_key = field.is_key if hasattr(field, "is_key") else field.get("is_key", False)
            if is_key:
                continue
            # Check if this field name is a key in another table
            for other_table, keys in table_keys.items():
                if other_table == table:
                    continue
                if fname in keys:
                    source_id = f"{system_id}.{table}"
                    target_id = f"{system_id}.{other_table}"
                    edge_key = (source_id, target_id, fname)
                    if edge_key in seen:
                        continue
                    seen.add(edge_key)
                    edges.append({
                        "data": {
                            "id": f"ref-{source_id}-{target_id}-{fname}",
                            "source": source_id,
                            "target": target_id,
                            "label": fname,
                            "edge_type": "reference",
                            "detail": f"FK: {table}.{fname} → {other_table}.{fname}",
                        },
                    })
    return edges


@admin_router.get("/data-graph", response_class=HTMLResponse)
async def data_graph_page(request: Request):
    """Render the force-graph data visualization page."""
    return templates.TemplateResponse("admin/data_graph.html", {
        "request": request,
    })
