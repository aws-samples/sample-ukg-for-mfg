"""Canonical manufacturing vocabulary for the Discovery Agent (DB-driven).

This module provides the comprehensive list of manufacturing Concepts used to
semantically map fields across registered systems. The Discovery Agent maps
every discovered field to one of these concepts during the Understanding phase.

Concepts are organized by ISA-95 domain and cover the four primary information
categories defined by ISA-95 Part 2 (Material, Equipment, Physical Asset,
Personnel) plus operational domains (Production, Maintenance, Quality,
Inventory) and modern manufacturing extensions (IoT/SCADA, Energy, Safety,
Product Lifecycle, Supply Chain).

Each concept carries:
  - id:          Canonical identifier (kebab-case)
  - domain:      ISA-95 domain grouping
  - description: Human-readable description
  - aliases:     Common field names seen in real systems

Source of truth
---------------
The *default* hierarchy lives in ``default_concepts.json`` next to this module.
Edit that file to customize the defaults that get seeded on deployment.

At runtime the vocabulary is loaded from DynamoDB (table name in the
``CONCEPTS_TABLE_NAME`` environment variable) so the hierarchy can be
administered through the Admin UI and the Discovery Agent's concept tools
without redeploying. When the table is unset, empty (not yet seeded), or
unreadable, the module transparently falls back to the built-in defaults, so
local development and unit tests work with no AWS dependency. The one
exception is *custom-only mode*: if an operator clears the defaults to build a
bespoke vocabulary (:meth:`ConceptStore.clear_all`), a ``suppress_defaults``
flag is set so an empty table stays empty instead of resurrecting defaults.

Results are cached in-process for ``CONCEPTS_CACHE_TTL_SECONDS`` (default 300s).
Call :func:`invalidate_cache` after a write to force an immediate refresh.

See: ISA-95 (IEC 62264), ISA-88 (IEC 61512), OPC UA for ISA-95 (OPC 30060),
     Purdue Reference Model (ISA-95 Part 1, Levels 0-4)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Concept:
    """A single canonical manufacturing concept.

    Attributes:
        id:          Kebab-case identifier (e.g. ``"work-order"``).
        domain:      ISA-95 domain this concept belongs to.
        description: Short human-readable description.
        aliases:     Common field names seen in real systems.
    """

    id: str
    domain: str
    description: str
    aliases: list[str] = field(default_factory=list)

    @property
    def qualified_id(self) -> str:
        """Return domain-qualified identifier, e.g. ``"production.work-order"``."""
        return f"{self.domain}.{self.id}"

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict for this concept."""
        return {
            "id": self.id,
            "domain": self.domain,
            "qualified_id": self.qualified_id,
            "description": self.description,
            "aliases": list(self.aliases),
        }


# ============================================================================
# Default hierarchy — loaded from default_concepts.json (single source of truth)
# ============================================================================

_DEFAULTS_PATH = Path(__file__).with_name("default_concepts.json")


def _load_default_concepts() -> list[Concept]:
    """Load the built-in default concept hierarchy from the bundled JSON."""
    try:
        with open(_DEFAULTS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return [
            Concept(
                id=c["id"],
                domain=c["domain"],
                description=c.get("description", ""),
                aliases=list(c.get("aliases", [])),
            )
            for c in data.get("concepts", [])
        ]
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to load default concepts from %s: %s", _DEFAULTS_PATH, exc)
        return []


DEFAULT_CONCEPTS: list[Concept] = _load_default_concepts()
"""The built-in default concepts (from ``default_concepts.json``)."""


# ============================================================================
# DynamoDB-backed store
# ============================================================================

_TABLE_ENV = "CONCEPTS_TABLE_NAME"

# DynamoDB attribute names
_PK = "concept_key"  # partition key == qualified_id (e.g. "production.work-order")

# Reserved partition-key value for the store's control/metadata item. It holds
# flags like ``suppress_defaults`` and is never returned as a Concept.
_META_KEY = "__meta__"

SOURCE_DEFAULT = "default"
SOURCE_CUSTOM = "custom"


def get_concepts_table_name() -> str | None:
    """Return the configured concepts table name, or ``None`` if unset."""
    return os.getenv(_TABLE_ENV) or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _concept_to_item(concept: Concept, source: str = SOURCE_CUSTOM) -> dict:
    """Marshal a Concept into a DynamoDB item (low-level AttributeValue form)."""
    return {
        _PK: {"S": concept.qualified_id},
        "concept_id": {"S": concept.id},
        "domain": {"S": concept.domain},
        "description": {"S": concept.description or ""},
        "aliases": {"L": [{"S": a} for a in concept.aliases]},
        "source": {"S": source},
        "updated_at": {"S": _now_iso()},
    }


def _item_to_concept(item: dict) -> Concept | None:
    """Unmarshal a DynamoDB item into a Concept. Returns ``None`` if invalid."""
    try:
        concept_id = item["concept_id"]["S"]
        domain = item["domain"]["S"]
    except (KeyError, TypeError):
        # Fall back to splitting the partition key when attributes are missing.
        key = item.get(_PK, {}).get("S", "")
        if "." not in key:
            return None
        domain, concept_id = key.split(".", 1)
    description = item.get("description", {}).get("S", "")
    aliases_raw = item.get("aliases", {}).get("L", [])
    aliases = [a.get("S", "") for a in aliases_raw if a.get("S")]
    return Concept(id=concept_id, domain=domain, description=description, aliases=aliases)


class ConceptStore:
    """Synchronous CRUD access to the concepts DynamoDB table.

    Shared by the Discovery Agent's concept-admin tools and (via an async
    wrapper in the ChatApp) the Admin UI. Uses a low-level boto3 client and
    ``BatchWriteItem`` for seeding, mirroring ``tools/register.py``.
    """

    _BATCH_MAX = 25

    def __init__(self, table_name: str | None = None, region: str | None = None):
        self.table_name = table_name or get_concepts_table_name()
        if not self.table_name:
            raise ValueError(
                f"{_TABLE_ENV} environment variable is required to use ConceptStore."
            )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        import boto3  # local import so the module imports without boto3 installed

        self._client = boto3.client("dynamodb", region_name=self.region)

    # -- reads --------------------------------------------------------------

    def list_all(self) -> list[Concept]:
        """Return every concept in the table."""
        concepts: list[Concept] = []
        paginator = self._client.get_paginator("scan")
        for page in paginator.paginate(TableName=self.table_name):
            for item in page.get("Items", []):
                if item.get(_PK, {}).get("S") == _META_KEY:
                    continue  # skip the control/metadata item
                concept = _item_to_concept(item)
                if concept is not None:
                    concepts.append(concept)
        return concepts

    def count(self) -> int:
        """Return the number of items in the table (approximate for large tables)."""
        resp = self._client.scan(TableName=self.table_name, Select="COUNT")
        total = resp.get("Count", 0)
        while "LastEvaluatedKey" in resp:
            resp = self._client.scan(
                TableName=self.table_name,
                Select="COUNT",
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            total += resp.get("Count", 0)
        return total

    def get(self, qualified_id: str) -> Concept | None:
        """Return a single concept by its qualified id, or ``None``."""
        resp = self._client.get_item(
            TableName=self.table_name, Key={_PK: {"S": qualified_id}}
        )
        item = resp.get("Item")
        return _item_to_concept(item) if item else None

    # -- writes -------------------------------------------------------------

    def put(self, concept: Concept, source: str = SOURCE_CUSTOM) -> None:
        """Create or replace a concept."""
        self._client.put_item(
            TableName=self.table_name, Item=_concept_to_item(concept, source)
        )

    def delete(self, qualified_id: str) -> None:
        """Delete a concept by its qualified id."""
        self._client.delete_item(
            TableName=self.table_name, Key={_PK: {"S": qualified_id}}
        )

    # -- control flags ------------------------------------------------------

    def get_suppress_defaults(self) -> bool:
        """Return whether default fallback is suppressed (custom-only mode).

        When ``True``, an empty table represents an intentional blank slate and
        the module will NOT fall back to the built-in defaults.
        """
        resp = self._client.get_item(
            TableName=self.table_name, Key={_PK: {"S": _META_KEY}}
        )
        item = resp.get("Item")
        if not item:
            return False
        return bool(item.get("suppress_defaults", {}).get("BOOL", False))

    def set_suppress_defaults(self, value: bool) -> None:
        """Set the suppress-defaults flag on the control item."""
        self._client.put_item(
            TableName=self.table_name,
            Item={
                _PK: {"S": _META_KEY},
                "suppress_defaults": {"BOOL": bool(value)},
                "updated_at": {"S": _now_iso()},
            },
        )

    def clear_all(self) -> int:
        """Delete every concept and enable custom-only mode (suppress defaults).

        Returns the number of concepts deleted. After this, an empty table is
        treated as an intentional blank slate — the defaults are not restored
        on read or on the next deployment until :meth:`seed_defaults` runs.
        """
        keys = [c.qualified_id for c in self.list_all()]
        self._batch_delete(keys)
        self.set_suppress_defaults(True)
        return len(keys)

    def _batch_delete(self, keys: list[str]) -> None:
        """Delete items by key using BatchWriteItem, retrying unprocessed items."""
        for start in range(0, len(keys), self._BATCH_MAX):
            batch = keys[start : start + self._BATCH_MAX]
            requests = [{"DeleteRequest": {"Key": {_PK: {"S": k}}}} for k in batch]
            unprocessed = {self.table_name: requests}
            attempts = 0
            while unprocessed and attempts < 5:
                resp = self._client.batch_write_item(RequestItems=unprocessed)
                unprocessed = resp.get("UnprocessedItems", {}) or {}
                attempts += 1
                if unprocessed:
                    time.sleep(0.2 * attempts)

    def seed_defaults(self, overwrite: bool = False) -> int:
        """Seed the built-in defaults into the table.

        Args:
            overwrite: When ``False`` (default) only concepts missing from the
                table are written, preserving admin customizations. When
                ``True`` every default is (re)written.

        Returns:
            The number of concepts written.
        """
        existing: set[str] = set()
        if not overwrite:
            existing = {c.qualified_id for c in self.list_all()}

        to_write = [
            c for c in DEFAULT_CONCEPTS if overwrite or c.qualified_id not in existing
        ]
        self._batch_write([_concept_to_item(c, SOURCE_DEFAULT) for c in to_write])
        # Seeding defaults means defaults should be visible again — clear any
        # prior custom-only (suppress) state.
        self.set_suppress_defaults(False)
        return len(to_write)

    def _batch_write(self, items: list[dict]) -> None:
        """Write items using BatchWriteItem, retrying unprocessed items."""
        for start in range(0, len(items), self._BATCH_MAX):
            batch = items[start : start + self._BATCH_MAX]
            requests = [{"PutRequest": {"Item": it}} for it in batch]
            unprocessed = {self.table_name: requests}
            attempts = 0
            while unprocessed and attempts < 5:
                resp = self._client.batch_write_item(RequestItems=unprocessed)
                unprocessed = resp.get("UnprocessedItems", {}) or {}
                attempts += 1
                if unprocessed:
                    time.sleep(0.2 * attempts)


# ============================================================================
# Cached registry — indexes rebuilt from the active concept set
# ============================================================================

_CACHE_TTL = float(os.getenv("CONCEPTS_CACHE_TTL_SECONDS", "300"))
_cache_lock = threading.Lock()
_cached_registry: "_Registry | None" = None
_cache_ts = 0.0


class _Registry:
    """Immutable snapshot of the concept vocabulary plus lookup indexes."""

    __slots__ = (
        "concepts",
        "canonical_ids",
        "qualified_ids",
        "concept_set",
        "qualified_set",
        "by_qualified_id",
        "by_domain",
        "bare_to_qualified",
        "alias_index",
    )

    def __init__(self, concepts: list[Concept]):
        self.concepts = concepts
        self.canonical_ids = [c.id for c in concepts]
        self.qualified_ids = [c.qualified_id for c in concepts]
        self.concept_set = frozenset(self.canonical_ids)
        self.qualified_set = frozenset(self.qualified_ids)
        self.by_qualified_id = {c.qualified_id: c for c in concepts}

        by_domain: dict[str, list[Concept]] = {}
        bare_to_qualified: dict[str, list[str]] = {}
        alias_index: dict[str, list[Concept]] = {}
        for c in concepts:
            by_domain.setdefault(c.domain, []).append(c)
            bare_to_qualified.setdefault(c.id, []).append(c.qualified_id)
            for a in c.aliases:
                alias_index.setdefault(a.lower(), []).append(c)
        self.by_domain = by_domain
        self.bare_to_qualified = bare_to_qualified
        self.alias_index = alias_index


def _load_concepts_from_source() -> list[Concept]:
    """Load the active concept set: DynamoDB when available, else defaults."""
    table = get_concepts_table_name()
    if not table:
        return list(DEFAULT_CONCEPTS)
    try:
        store = ConceptStore(table)
        items = store.list_all()
        if items:
            return items
        # Empty table: only fall back to defaults if the operator hasn't
        # intentionally cleared them (custom-only mode).
        if store.get_suppress_defaults():
            logger.info(
                "Concepts table '%s' is empty with suppress-defaults set; "
                "using an empty (custom-only) vocabulary.",
                table,
            )
            return []
        logger.info(
            "Concepts table '%s' is empty (not yet seeded); using built-in defaults.",
            table,
        )
        return list(DEFAULT_CONCEPTS)
    except Exception as exc:
        logger.warning(
            "Could not load concepts from DynamoDB table '%s'; using defaults: %s",
            table,
            exc,
        )
        return list(DEFAULT_CONCEPTS)


def _registry() -> _Registry:
    """Return the cached registry, refreshing it when the TTL has elapsed."""
    global _cached_registry, _cache_ts
    now = time.monotonic()
    with _cache_lock:
        if _cached_registry is None or (now - _cache_ts) > _CACHE_TTL:
            _cached_registry = _Registry(_load_concepts_from_source())
            _cache_ts = now
        return _cached_registry


def invalidate_cache() -> None:
    """Clear the in-process registry cache so the next call reloads from source."""
    global _cached_registry, _cache_ts
    with _cache_lock:
        _cached_registry = None
        _cache_ts = 0.0


# ============================================================================
# Backwards-compatible module constants (built-in DEFAULTS only)
# ============================================================================
# These reflect the built-in defaults and are retained for backwards
# compatibility. Live code that must reflect DynamoDB customizations should
# call the functions below (get_all_concepts / get_canonical_concept_ids /
# get_all_concepts_serializable / get_concepts_by_domain / ...).

CONCEPTS: list[Concept] = list(DEFAULT_CONCEPTS)
"""All built-in default concepts as ``Concept`` objects (defaults only)."""

CANONICAL_CONCEPTS: list[str] = [c.id for c in DEFAULT_CONCEPTS]
"""Flat list of built-in default concept IDs (defaults only)."""

QUALIFIED_CONCEPTS: list[str] = [c.qualified_id for c in DEFAULT_CONCEPTS]
"""Domain-qualified IDs for the built-in defaults (defaults only)."""


# ============================================================================
# Public API (DynamoDB-overlaid, cached)
# ============================================================================

def get_all_concepts() -> list[Concept]:
    """Return the active concept vocabulary (DynamoDB overlay, cached)."""
    return list(_registry().concepts)


def get_canonical_concept_ids() -> list[str]:
    """Return the flat list of active concept IDs."""
    return list(_registry().canonical_ids)


def get_qualified_concept_ids() -> list[str]:
    """Return the list of active domain-qualified concept IDs."""
    return list(_registry().qualified_ids)


def is_valid_concept(concept_id: str) -> bool:
    """Check whether *concept_id* belongs to the active vocabulary.

    Accepts both simple IDs (``"oee"``) and domain-qualified IDs
    (``"performance.oee"``).
    """
    reg = _registry()
    return concept_id in reg.concept_set or concept_id in reg.qualified_set


def get_concept(qualified_id: str) -> Concept | None:
    """Return a Concept by its domain-qualified ID, or ``None``."""
    return _registry().by_qualified_id.get(qualified_id)


def get_concepts_by_domain(domain: str) -> list[Concept]:
    """Return all concepts in a given domain."""
    return list(_registry().by_domain.get(domain, []))


def get_domains() -> list[str]:
    """Return sorted list of all domain names."""
    return sorted(_registry().by_domain.keys())


def lookup_by_alias(alias: str) -> list[Concept]:
    """Find concepts whose aliases match *alias* (case-insensitive).

    Returns a list because the same alias may map to concepts in different
    domains (e.g. ``"wo"`` → production.work-order AND maintenance.work-order).
    """
    return list(_registry().alias_index.get(alias.lower(), []))


def resolve_to_qualified(concept_id: str) -> list[str]:
    """Resolve a concept ID to domain-qualified ID(s).

    If *concept_id* is already qualified (contains a dot and is in the
    vocabulary), returns it as a single-element list. If it's a bare ID,
    returns all domain-qualified variants. Returns an empty list if unknown.
    """
    reg = _registry()
    if concept_id in reg.qualified_set:
        return [concept_id]
    return list(reg.bare_to_qualified.get(concept_id, []))


def get_all_concepts_serializable() -> list[dict]:
    """Return all active concepts as dicts suitable for JSON serialization.

    Used by the ``get_canonical_concepts`` tool to send the full vocabulary
    (with aliases) to the analysis sub-agent.
    """
    return [c.to_dict() for c in _registry().concepts]
