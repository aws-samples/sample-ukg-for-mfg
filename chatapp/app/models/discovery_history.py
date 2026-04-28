"""Discovery history data models for DynamoDB storage.

Tracks each discovery session: when a data source was registered,
re-registered, or removed, along with counts of tables, fields,
correlations, and equivalences discovered.
"""

import json
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class DiscoveryHistoryRecord:
    """A single discovery session record.

    PK: discovery_id (UUID)
    SK: timestamp (ISO 8601)
    GSI system-index: system_id + timestamp
    """

    discovery_id: str
    timestamp: str
    system_id: str
    system_name: str
    action: str  # "registered", "re-registered", "removed"
    user_id: str
    system_type: str = ""  # ERP, MES, CMMS, PLM, IoT
    source_type: str = ""  # rds, api, mcp, s3tables
    status: str = "completed"  # completed, failed, partial
    table_count: int = 0
    field_count: int = 0
    correlation_count: int = 0  # concepts mapped
    equivalence_count: int = 0  # cross-system equivalences
    rejected_equivalence_count: int = 0
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    # Detailed phase data (JSON strings, parsed on demand)
    understand_data: Optional[str] = None  # schemas + fields + concepts
    correlate_data: Optional[str] = None   # equivalences

    def to_dynamodb_item(self) -> Dict[str, Any]:
        item = {
            "discovery_id": {"S": self.discovery_id},
            "timestamp": {"S": self.timestamp},
            "system_id": {"S": self.system_id},
            "system_name": {"S": self.system_name},
            "action": {"S": self.action},
            "user_id": {"S": self.user_id},
            "system_type": {"S": self.system_type},
            "source_type": {"S": self.source_type},
            "status": {"S": self.status},
            "table_count": {"N": str(self.table_count)},
            "field_count": {"N": str(self.field_count)},
            "correlation_count": {"N": str(self.correlation_count)},
            "equivalence_count": {"N": str(self.equivalence_count)},
            "rejected_equivalence_count": {"N": str(self.rejected_equivalence_count)},
            "duration_seconds": {"N": str(self.duration_seconds)},
        }
        if self.error_message:
            item["error_message"] = {"S": self.error_message}
        return item

    @classmethod
    def from_dynamodb_item(cls, item: Dict[str, Any]) -> "DiscoveryHistoryRecord":
        return cls(
            discovery_id=item.get("discovery_id", {}).get("S", ""),
            timestamp=item.get("timestamp", {}).get("S", ""),
            system_id=item.get("system_id", {}).get("S", ""),
            system_name=item.get("system_name", {}).get("S", ""),
            action=item.get("action", {}).get("S", ""),
            user_id=item.get("user_id", {}).get("S", ""),
            system_type=item.get("system_type", {}).get("S", ""),
            source_type=item.get("source_type", {}).get("S", ""),
            status=item.get("status", {}).get("S", "completed"),
            table_count=int(item.get("table_count", {}).get("N", "0")),
            field_count=int(item.get("field_count", {}).get("N", "0")),
            correlation_count=int(item.get("correlation_count", {}).get("N", "0")),
            equivalence_count=int(item.get("equivalence_count", {}).get("N", "0")),
            rejected_equivalence_count=int(item.get("rejected_equivalence_count", {}).get("N", "0")),
            duration_seconds=float(item.get("duration_seconds", {}).get("N", "0")),
            error_message=item.get("error_message", {}).get("S"),
            understand_data=item.get("understand_data", {}).get("S"),
            correlate_data=item.get("correlate_data", {}).get("S"),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "discovery_id": self.discovery_id,
            "timestamp": self.timestamp,
            "system_id": self.system_id,
            "system_name": self.system_name,
            "action": self.action,
            "user_id": self.user_id,
            "system_type": self.system_type,
            "source_type": self.source_type,
            "status": self.status,
            "table_count": self.table_count,
            "field_count": self.field_count,
            "correlation_count": self.correlation_count,
            "equivalence_count": self.equivalence_count,
            "rejected_equivalence_count": self.rejected_equivalence_count,
            "duration_seconds": self.duration_seconds,
        }
        if self.error_message:
            d["error_message"] = self.error_message
        return d

    def get_understand_detail(self) -> Optional[Dict]:
        """Parse the understand phase data (schemas, fields, concepts)."""
        if not self.understand_data:
            return None
        try:
            return json.loads(self.understand_data)
        except (json.JSONDecodeError, TypeError):
            return None

    def get_correlate_detail(self) -> Optional[Dict]:
        """Parse the correlate phase data (equivalences)."""
        if not self.correlate_data:
            return None
        try:
            return json.loads(self.correlate_data)
        except (json.JSONDecodeError, TypeError):
            return None
