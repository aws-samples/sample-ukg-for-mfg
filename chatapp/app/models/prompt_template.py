"""Prompt template data models for DynamoDB storage.

This module defines dataclasses for storing and querying prompt templates.
Records are stored in DynamoDB with template_id as partition key.
"""

from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class PromptTemplate:
    """A prompt template record.
    
    Stored in DynamoDB with template_id as partition key.
    
    Attributes:
        template_id: Unique identifier (UUID)
        title: Display title for the template
        description: Brief description shown in the list
        prompt_detail: The actual prompt text to insert
        sort_order: Integer for display ordering (lower = first)
        created_at: ISO 8601 timestamp
        updated_at: ISO 8601 timestamp
    """
    template_id: str
    title: str
    description: str
    prompt_detail: str
    created_at: str
    updated_at: str
    sort_order: int = 0
    
    def to_dynamodb_item(self) -> Dict[str, Any]:
        return {
            "template_id": {"S": self.template_id},
            "title": {"S": self.title},
            "description": {"S": self.description},
            "prompt_detail": {"S": self.prompt_detail},
            "sort_order": {"N": str(self.sort_order)},
            "created_at": {"S": self.created_at},
            "updated_at": {"S": self.updated_at},
        }
    
    @classmethod
    def from_dynamodb_item(cls, item: Dict[str, Any]) -> "PromptTemplate":
        return cls(
            template_id=item.get("template_id", {}).get("S", ""),
            title=item.get("title", {}).get("S", ""),
            description=item.get("description", {}).get("S", ""),
            prompt_detail=item.get("prompt_detail", {}).get("S", ""),
            sort_order=int(item.get("sort_order", {}).get("N", "0")),
            created_at=item.get("created_at", {}).get("S", ""),
            updated_at=item.get("updated_at", {}).get("S", ""),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_id": self.template_id,
            "title": self.title,
            "description": self.description,
            "prompt_detail": self.prompt_detail,
            "sort_order": self.sort_order,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
