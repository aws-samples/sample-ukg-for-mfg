"""Canonical manufacturing vocabulary grouped by ISA-95 domain for UI display.

Imports from app.shared.concepts which is a copy of agent-discovery/concepts.py
bundled into the chatapp Docker image.
"""

from __future__ import annotations

from app.shared.concepts import CONCEPTS, Concept, get_domains, get_concepts_by_domain

# Domain display metadata: (domain_key, display_name, icon)
_DOMAIN_META: list[tuple[str, str, str]] = [
    ("equipment", "Equipment", "🏗️"),
    ("physical-asset", "Physical Asset", "🏗️"),
    ("material", "Material", "🏗️"),
    ("personnel", "Personnel", "🏗️"),
    ("production", "Production Operations", "⚙️"),
    ("maintenance", "Maintenance Operations", "🔧"),
    ("quality", "Quality Operations", "✅"),
    ("inventory", "Inventory Operations", "📦"),
    ("performance", "OEE & Performance", "📊"),
    ("iot", "IoT / SCADA / Sensors", "📡"),
    ("energy", "Energy & Utilities", "⚡"),
    ("plm", "Product Lifecycle (PLM)", "📐"),
    ("supply-chain", "Supply Chain & Logistics", "🚚"),
    ("safety", "Safety & Environmental", "🛡️"),
    ("weather", "Weather & Conditions", "🌤️"),
    ("facility", "Facility & Site", "🏭"),
    ("traceability", "Traceability & Compliance", "📋"),
]


def _build_concept_groups() -> list[dict]:
    """Build the grouped concept structure for UI display."""
    groups = []
    for domain_key, display_name, icon in _DOMAIN_META:
        concepts = get_concepts_by_domain(domain_key)
        if not concepts:
            continue
        groups.append({
            "group": display_name,
            "icon": icon,
            "subgroups": [{
                "name": display_name,
                "concepts": [
                    {"id": c.qualified_id, "desc": c.description}
                    for c in concepts
                ],
            }],
        })
    return groups


CONCEPT_GROUPS: list[dict] = _build_concept_groups()


def get_total_concept_count() -> int:
    """Return the total number of canonical concepts."""
    return len(CONCEPTS)
