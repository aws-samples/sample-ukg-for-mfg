"""Canonical manufacturing vocabulary grouped by ISA-95 domain for UI display.

Reads the live vocabulary from app.shared.concepts, which overlays the
concepts DynamoDB table on top of the built-in defaults. Groups are built at
request time (not import time) so admin edits are reflected immediately.
"""

from __future__ import annotations

from app.shared.concepts import get_concepts_by_domain, get_domains, get_all_concepts

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

_DEFAULT_ICON = "🧩"


def _domain_display(domain_key: str) -> tuple[str, str]:
    """Return (display_name, icon) for a domain, with a fallback for custom domains."""
    for key, name, icon in _DOMAIN_META:
        if key == domain_key:
            return name, icon
    # Custom/admin-added domain not in the metadata: title-case the key.
    return domain_key.replace("-", " ").title(), _DEFAULT_ICON


def build_concept_groups() -> list[dict]:
    """Build the grouped concept structure for UI display (live from source)."""
    # Preserve the curated domain order first, then append any custom domains.
    ordered_keys = [k for k, _, _ in _DOMAIN_META]
    live_domains = get_domains()
    for d in live_domains:
        if d not in ordered_keys:
            ordered_keys.append(d)

    groups = []
    for domain_key in ordered_keys:
        concepts = get_concepts_by_domain(domain_key)
        if not concepts:
            continue
        display_name, icon = _domain_display(domain_key)
        groups.append({
            "group": display_name,
            "domain": domain_key,
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


def group_records(concepts: list) -> list[dict]:
    """Group Concept records by domain, ordered and annotated for the admin UI.

    Args:
        concepts: A list of ``Concept`` objects (from the storage service).

    Returns:
        A list of ``{domain, display_name, icon, concepts:[Concept...]}`` dicts,
        ordered by the curated domain order with custom domains appended.
    """
    by_domain: dict[str, list] = {}
    for c in concepts:
        by_domain.setdefault(c.domain, []).append(c)

    ordered_keys = [k for k, _, _ in _DOMAIN_META]
    for d in sorted(by_domain.keys()):
        if d not in ordered_keys:
            ordered_keys.append(d)

    groups = []
    for domain_key in ordered_keys:
        records = by_domain.get(domain_key)
        if not records:
            continue
        records.sort(key=lambda c: c.id)
        display_name, icon = _domain_display(domain_key)
        groups.append({
            "domain": domain_key,
            "display_name": display_name,
            "icon": icon,
            "concepts": records,
        })
    return groups


def get_total_concept_count() -> int:
    """Return the total number of concepts in the active vocabulary."""
    return len(get_all_concepts())


# Backwards-compatible module attribute. Prefer calling build_concept_groups()
# so the view reflects live DynamoDB customizations.
def __getattr__(name: str):
    if name == "CONCEPT_GROUPS":
        return build_concept_groups()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
