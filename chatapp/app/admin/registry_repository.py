"""Registry repository for querying the System Registry DynamoDB table.

Provides read-only access to the V2 System Registry for the admin dashboard.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key, Attr

from app.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class SystemSummary:
    """Summary of a registered system."""
    system_id: str
    name: str = ""
    system_type: str = ""
    vendor: str = ""
    protocol: str = ""
    isa95_level: int = 0
    plant: str = ""
    region: str = ""
    status: str = ""
    table_count: int = 0
    field_count: int = 0
    discovered_at: str = ""
    discovered_by: str = ""


@dataclass
class SchemaEntry:
    """A table schema entry."""
    table_name: str
    schema_name: str = ""
    description: str = ""
    row_count: Optional[int] = None


@dataclass
class FieldEntry:
    """A field entry with concept mapping."""
    table_name: str
    field_name: str
    data_type: str = ""
    description: str = ""
    concept_id: str = ""
    concept_confidence: float = 0.0
    is_key: bool = False


@dataclass
class SystemDetail:
    """Full detail of a registered system."""
    metadata: SystemSummary
    schemas: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    equivalences: list = field(default_factory=list)


def _get_table():
    """Get the DynamoDB Table resource for the System Registry."""
    config = get_config()
    if not config.registry_table_name:
        return None
    dynamodb = boto3.resource("dynamodb", region_name=config.aws_region)
    return dynamodb.Table(config.registry_table_name)


def _safe_int(value, default: int = 0) -> int:
    """Safely convert a value to int, returning default if None or invalid."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _parse_metadata(item: dict) -> SystemSummary:
    """Parse a DynamoDB METADATA item into a SystemSummary."""
    return SystemSummary(
        system_id=item.get("system_id", item.get("PK", "").replace("SYSTEM#", "")),
        name=item.get("name", ""),
        system_type=item.get("system_type", ""),
        vendor=item.get("vendor", ""),
        protocol=item.get("protocol", ""),
        isa95_level=_safe_int(item.get("isa95_level")),
        plant=item.get("plant", ""),
        region=item.get("region", ""),
        status=item.get("status", ""),
        table_count=_safe_int(item.get("table_count")),
        field_count=_safe_int(item.get("field_count")),
        discovered_at=item.get("discovered_at", ""),
        discovered_by=item.get("discovered_by", ""),
    )


class RegistryRepository:
    """Repository for the System Registry DynamoDB table."""

    async def get_all_systems(self) -> list[SystemSummary]:
        """Get all registered systems (METADATA items only)."""
        table = _get_table()
        if not table:
            return []
        try:
            response = table.scan(
                FilterExpression=Attr("SK").eq("METADATA"),
            )
            items = response.get("Items", [])
            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = table.scan(
                    FilterExpression=Attr("SK").eq("METADATA"),
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))
            return [_parse_metadata(item) for item in items]
        except Exception as e:
            logger.error("Failed to scan registry: %s", e)
            return []

    async def get_system_detail(self, system_id: str) -> Optional[SystemDetail]:
        """Get full detail for a system including schemas, fields, and equivalences."""
        table = _get_table()
        if not table:
            return None
        try:
            response = table.query(
                KeyConditionExpression=Key("PK").eq(f"SYSTEM#{system_id}"),
            )
            items = response.get("Items", [])
            while "LastEvaluatedKey" in response:
                response = table.query(
                    KeyConditionExpression=Key("PK").eq(f"SYSTEM#{system_id}"),
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))

            if not items:
                return None

            metadata = None
            schemas = []
            fields = []
            equivalences = []

            for item in items:
                sk = item.get("SK", "")
                if sk == "METADATA":
                    metadata = _parse_metadata(item)
                elif sk.startswith("SCHEMA#"):
                    _rc = item.get("row_count")
                    schemas.append(SchemaEntry(
                        table_name=item.get("table_name", sk.replace("SCHEMA#", "")),
                        schema_name=item.get("schema_name", ""),
                        description=item.get("description", ""),
                        # DDB returns numbers as Decimal; cast so ``asdict``
                        # produces JSON-serializable primitives downstream.
                        row_count=int(_rc) if _rc is not None else None,
                    ))
                elif sk.startswith("FIELD#"):
                    fields.append(FieldEntry(
                        table_name=item.get("table_name", ""),
                        field_name=item.get("field_name", ""),
                        data_type=item.get("data_type", ""),
                        description=item.get("description", ""),
                        concept_id=item.get("concept_id", ""),
                        concept_confidence=float(item.get("concept_confidence") or 0),
                        is_key=bool(item.get("is_key", False)),
                    ))
                elif sk.startswith("EQUIV#"):
                    equivalences.append(item)

            if not metadata:
                return None

            # Sort schemas and fields
            schemas.sort(key=lambda s: s.table_name)
            fields.sort(key=lambda f: (f.table_name, f.field_name))

            return SystemDetail(
                metadata=metadata,
                schemas=schemas,
                fields=fields,
                equivalences=equivalences,
            )
        except Exception as e:
            logger.error("Failed to get system detail for %s: %s", system_id, e)
            return None

    async def delete_system(self, system_id: str) -> int:
        """Delete a system and all its related records (schemas, fields, equivalences).

        Queries all items with PK=SYSTEM#{system_id} and batch-deletes them.

        Args:
            system_id: The system ID to delete.

        Returns:
            Number of items deleted.

        Raises:
            Exception: If the delete operation fails.
        """
        table = _get_table()
        if not table:
            raise RuntimeError("Registry table not configured")

        # Query all items for this system
        pk = f"SYSTEM#{system_id}"
        response = table.query(KeyConditionExpression=Key("PK").eq(pk))
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = table.query(
                KeyConditionExpression=Key("PK").eq(pk),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

        if not items:
            return 0

        # Batch delete (25 items per batch — DynamoDB limit)
        with table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})

        logger.info("Deleted %d items for system %s", len(items), system_id)
        return len(items)
