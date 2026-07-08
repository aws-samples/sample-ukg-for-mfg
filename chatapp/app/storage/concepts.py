"""Concepts storage service for the manufacturing vocabulary.

Async wrapper around the shared :class:`ConceptStore` (app.shared.concepts),
which is the same DynamoDB access layer used by the Discovery Agent. Reusing
it keeps a single item-schema/marshalling implementation across the agent and
the ChatApp admin UI.

After every write the shared in-process registry cache is invalidated so the
Unified Knowledge Graph vocabulary view reflects changes immediately.
"""

import asyncio
import logging
from typing import List, Optional

from app.config import get_config
from app.shared.concepts import (
    DEFAULT_CONCEPTS,
    SOURCE_CUSTOM,
    Concept,
    ConceptStore,
    invalidate_cache,
)

logger = logging.getLogger(__name__)


class ConceptsStorageService:
    """Async CRUD service for the concepts DynamoDB table."""

    def __init__(self, table_name: Optional[str] = None, region: Optional[str] = None):
        config = get_config()
        self.table_name = table_name or config.concepts_table_name
        self.region = region or config.aws_region
        self._store = ConceptStore(table_name=self.table_name, region=self.region)

    async def _run(self, fn, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fn, *args)

    async def get_all(self) -> List[Concept]:
        """Return every concept in the table (sorted by domain, then id)."""
        try:
            concepts = await self._run(self._store.list_all)
            concepts.sort(key=lambda c: (c.domain, c.id))
            return concepts
        except Exception as e:
            logger.error("Failed to list concepts", extra={"error": str(e)})
            return []

    async def get(self, qualified_id: str) -> Optional[Concept]:
        """Return a single concept by its qualified id."""
        try:
            return await self._run(self._store.get, qualified_id)
        except Exception as e:
            logger.error("Failed to get concept", extra={"qualified_id": qualified_id, "error": str(e)})
            return None

    async def upsert(
        self,
        concept_id: str,
        domain: str,
        description: str,
        aliases: List[str],
        source: str = SOURCE_CUSTOM,
    ) -> Optional[Concept]:
        """Create or replace a concept, then invalidate the registry cache."""
        try:
            concept = Concept(
                id=concept_id,
                domain=domain,
                description=description,
                aliases=aliases,
            )
            await self._run(self._store.put, concept, source)
            invalidate_cache()
            logger.info("Upserted concept", extra={"qualified_id": concept.qualified_id})
            return concept
        except Exception as e:
            logger.error(
                "Failed to upsert concept",
                extra={"concept_id": concept_id, "domain": domain, "error": str(e)},
            )
            return None

    async def delete(self, qualified_id: str) -> bool:
        """Delete a concept by qualified id, then invalidate the registry cache."""
        try:
            await self._run(self._store.delete, qualified_id)
            invalidate_cache()
            logger.info("Deleted concept", extra={"qualified_id": qualified_id})
            return True
        except Exception as e:
            logger.error("Failed to delete concept", extra={"qualified_id": qualified_id, "error": str(e)})
            return False

    async def restore_defaults(self) -> int:
        """(Re)write the built-in default hierarchy, preserving custom concepts.

        Also clears custom-only mode so defaults are visible again (handled
        inside ``seed_defaults``). Returns the number of default concepts written.
        """
        try:
            count = await self._run(self._store.seed_defaults, True)  # overwrite=True
            invalidate_cache()
            logger.info("Restored default concepts", extra={"count": count})
            return count
        except Exception as e:
            logger.error("Failed to restore default concepts", extra={"error": str(e)})
            return 0

    async def clear_all(self) -> int:
        """Delete every concept and enter custom-only mode (suppress defaults).

        Returns the number of concepts deleted.
        """
        try:
            count = await self._run(self._store.clear_all)
            invalidate_cache()
            logger.info("Cleared all concepts (custom-only mode)", extra={"count": count})
            return count
        except Exception as e:
            logger.error("Failed to clear concepts", extra={"error": str(e)})
            return 0

    async def rename_domain(self, old_domain: str, new_domain: str) -> int:
        """Move every concept from ``old_domain`` to ``new_domain``.

        Each concept is rewritten under the new domain (new qualified id) and
        the old record removed. Returns the number of concepts moved.
        """
        if not old_domain or not new_domain or old_domain == new_domain:
            return 0
        try:
            concepts = await self._run(self._store.list_all)
            moved = 0
            for c in concepts:
                if c.domain != old_domain:
                    continue
                new_concept = Concept(
                    id=c.id, domain=new_domain, description=c.description, aliases=list(c.aliases)
                )
                old_key = c.qualified_id
                await self._run(self._store.put, new_concept, SOURCE_CUSTOM)
                if new_concept.qualified_id != old_key:
                    await self._run(self._store.delete, old_key)
                moved += 1
            invalidate_cache()
            logger.info(
                "Renamed domain", extra={"old": old_domain, "new": new_domain, "count": moved}
            )
            return moved
        except Exception as e:
            logger.error(
                "Failed to rename domain",
                extra={"old": old_domain, "new": new_domain, "error": str(e)},
            )
            return 0

    async def delete_domain(self, domain: str) -> int:
        """Delete every concept in ``domain``. Returns the number deleted."""
        if not domain:
            return 0
        try:
            concepts = await self._run(self._store.list_all)
            deleted = 0
            for c in concepts:
                if c.domain == domain:
                    await self._run(self._store.delete, c.qualified_id)
                    deleted += 1
            invalidate_cache()
            logger.info("Deleted domain", extra={"domain": domain, "count": deleted})
            return deleted
        except Exception as e:
            logger.error("Failed to delete domain", extra={"domain": domain, "error": str(e)})
            return 0

    @staticmethod
    def default_count() -> int:
        """Return the number of built-in default concepts."""
        return len(DEFAULT_CONCEPTS)
