"""Configuration management for the Discovery Agent."""
from dataclasses import dataclass
import os
from typing import Optional


@dataclass
class DiscoveryConfig:
    """Configuration for the Discovery Agent AgentCore agent.

    Attributes:
        registry_table_name: DynamoDB System Registry table name
        aws_region: AWS region for all AWS services
        kb_id: Bedrock Knowledge Base ID (read by the tick Lambda, not the agent)
        kb_source_bucket: S3 bucket where ``remember_discovery`` writes learnings
        kb_sync_state_table: DynamoDB table holding the KB ingestion dirty flag
        guardrail_id: Bedrock guardrail identifier (optional)
        guardrail_version: Bedrock guardrail version
        guardrail_enabled: Whether guardrail evaluation is enabled
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        otel_endpoint: OpenTelemetry collector endpoint (optional)
        otel_enabled: Whether to enable OpenTelemetry tracing
        otel_console_export: Whether to export traces to console (for debugging)
        rds_cluster_arn: Default RDS cluster ARN for inspect_rds_schema (optional convenience)
        rds_secret_arn: Default RDS secret ARN for inspect_rds_schema (optional convenience)
    """
    # Required fields
    registry_table_name: str
    aws_region: str
    kb_id: str
    kb_source_bucket: str
    kb_sync_state_table: str
    # Optional fields with defaults
    guardrail_id: Optional[str] = None
    guardrail_version: str = "DRAFT"
    guardrail_enabled: bool = True
    log_level: str = "INFO"
    otel_endpoint: Optional[str] = None
    otel_enabled: bool = True
    otel_console_export: bool = False
    rds_cluster_arn: Optional[str] = None
    rds_secret_arn: Optional[str] = None
    memory_id: Optional[str] = None

    @classmethod
    def from_env(cls) -> "DiscoveryConfig":
        """Load configuration from environment variables.

        Checks for AgentCore-provided environment variables first,
        then falls back to custom environment variables for local development.

        Returns:
            DiscoveryConfig instance with values from environment

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

        kb_id = os.getenv("KB_ID")
        if not kb_id:
            raise ValueError(
                "KB_ID environment variable is required. "
                "Set it in your .env file or deploy the Bedrock stack via CDK."
            )

        kb_source_bucket = os.getenv("KB_SOURCE_BUCKET")
        if not kb_source_bucket:
            raise ValueError(
                "KB_SOURCE_BUCKET environment variable is required. "
                "Set it in your .env file or deploy the Bedrock stack via CDK."
            )

        kb_sync_state_table = os.getenv("KB_SYNC_STATE_TABLE")
        if not kb_sync_state_table:
            raise ValueError(
                "KB_SYNC_STATE_TABLE environment variable is required. "
                "Set it in your .env file or deploy the Bedrock stack via CDK."
            )

        guardrail_id = os.getenv("GUARDRAIL_ID")
        guardrail_version = os.getenv("GUARDRAIL_VERSION", "DRAFT")
        guardrail_enabled = os.getenv("GUARDRAIL_ENABLED", "true").lower() in ("true", "1", "yes")

        log_level = os.getenv("LOG_LEVEL", "INFO")

        otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        otel_enabled = os.getenv("OTEL_ENABLED", "true").lower() in ("true", "1", "yes")
        otel_console_export = os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() in ("true", "1", "yes")

        rds_cluster_arn = os.getenv("RDS_CLUSTER_ARN")
        rds_secret_arn = os.getenv("RDS_SECRET_ARN")
        memory_id = os.getenv("MEMORY_ID")

        return cls(
            registry_table_name=registry_table_name,
            aws_region=aws_region,
            kb_id=kb_id,
            kb_source_bucket=kb_source_bucket,
            kb_sync_state_table=kb_sync_state_table,
            guardrail_id=guardrail_id,
            guardrail_version=guardrail_version,
            guardrail_enabled=guardrail_enabled,
            log_level=log_level,
            otel_endpoint=otel_endpoint,
            otel_enabled=otel_enabled,
            otel_console_export=otel_console_export,
            rds_cluster_arn=rds_cluster_arn,
            rds_secret_arn=rds_secret_arn,
            memory_id=memory_id,
        )
