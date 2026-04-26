"""Configuration management for the Explorer agent."""
from dataclasses import dataclass
import os
from typing import Optional


@dataclass
class ExplorerConfig:
    """Configuration for the Explorer AgentCore agent.

    Attributes:
        registry_table_name: DynamoDB System Registry table name
        aws_region: AWS region for all AWS services
        memory_id: AgentCore Memory ID for conversation persistence
        guardrail_id: Bedrock guardrail identifier (optional)
        guardrail_version: Bedrock guardrail version
        guardrail_enabled: Whether guardrail evaluation is enabled
        kb_id: Bedrock Knowledge Base ID for entity resolution
        kb_max_results: Maximum number of KB search results to return
        kb_min_score: Minimum relevance score threshold for KB results
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        otel_endpoint: OpenTelemetry collector endpoint (optional)
        otel_enabled: Whether to enable OpenTelemetry tracing
        otel_console_export: Whether to export traces to console (for debugging)
    """
    # Required fields
    registry_table_name: str
    aws_region: str
    memory_id: str
    kb_id: str
    # Optional fields with defaults
    guardrail_id: Optional[str] = None
    guardrail_version: str = "DRAFT"
    guardrail_enabled: bool = True
    kb_max_results: int = 5
    kb_min_score: float = 0.5
    log_level: str = "INFO"
    otel_endpoint: Optional[str] = None
    otel_enabled: bool = True
    otel_console_export: bool = False

    @classmethod
    def from_env(cls) -> "ExplorerConfig":
        """Load configuration from environment variables.

        Checks for AgentCore-provided environment variables first,
        then falls back to custom environment variables for local development.

        Returns:
            ExplorerConfig instance with values from environment

        Raises:
            ValueError: If required environment variables are missing
        """
        registry_table_name = os.getenv("REGISTRY_TABLE_NAME")
        if not registry_table_name:
            raise ValueError(
                "REGISTRY_TABLE_NAME environment variable is required. "
                "Set it in your .env file or deploy the Foundation stack via CDK."
            )

        aws_region = os.getenv("AWS_REGION") or "us-east-1"

        memory_id = os.getenv("BEDROCK_AGENTCORE_MEMORY_ID") or os.getenv("MEMORY_ID")
        if not memory_id:
            raise ValueError(
                "MEMORY_ID environment variable is required. "
                "Set it in your .env file or configure memory in .bedrock_agentcore.yaml"
            )

        kb_id = os.getenv("KB_ID")
        if not kb_id:
            raise ValueError(
                "KB_ID environment variable is required. "
                "Set it in your .env file or deploy the Bedrock stack via CDK."
            )

        guardrail_id = os.getenv("GUARDRAIL_ID")
        guardrail_version = os.getenv("GUARDRAIL_VERSION", "DRAFT")
        guardrail_enabled = os.getenv("GUARDRAIL_ENABLED", "true").lower() in ("true", "1", "yes")

        kb_max_results = int(os.getenv("KB_MAX_RESULTS", "5"))
        kb_min_score = float(os.getenv("KB_MIN_SCORE", "0.5"))

        log_level = os.getenv("LOG_LEVEL", "INFO")

        otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        otel_enabled = os.getenv("OTEL_ENABLED", "true").lower() in ("true", "1", "yes")
        otel_console_export = os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() in ("true", "1", "yes")

        return cls(
            registry_table_name=registry_table_name,
            aws_region=aws_region,
            memory_id=memory_id,
            kb_id=kb_id,
            guardrail_id=guardrail_id,
            guardrail_version=guardrail_version,
            guardrail_enabled=guardrail_enabled,
            kb_max_results=kb_max_results,
            kb_min_score=kb_min_score,
            log_level=log_level,
            otel_endpoint=otel_endpoint,
            otel_enabled=otel_enabled,
            otel_console_export=otel_console_export,
        )
