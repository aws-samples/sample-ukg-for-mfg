"""Configuration module with environment variable validation.

This module provides configuration management for the HTMX ChatApp,
loading settings from environment variables and validating required values.
"""

import os
from dataclasses import dataclass
from typing import Optional
from functools import lru_cache


class ConfigurationError(Exception):
    """Raised when a required configuration variable is missing or invalid."""

    def __init__(self, variable_name: str, message: Optional[str] = None):
        self.variable_name = variable_name
        if message:
            super().__init__(f"{variable_name}: {message}")
        else:
            super().__init__(f"Required environment variable '{variable_name}' is missing or empty")


@dataclass(frozen=True)
class AppConfig:
    """Application configuration loaded from environment variables.
    
    Attributes:
        cognito_user_pool_id: The Cognito User Pool ID
        cognito_client_id: The Cognito app client ID
        cognito_client_secret: The Cognito app client secret
        aws_region: The AWS region for services
        memory_id: The AgentCore Memory ID
        app_url: The application URL (optional)
        dev_mode: Enable development mode (bypasses auth)
        dev_user_id: User ID to use in dev mode
        guardrail_id: Bedrock guardrail identifier (optional)
        guardrail_version: Guardrail version to use (optional)
        guardrail_enabled: Whether guardrail evaluation is enabled
        guardrail_table_name: DynamoDB table for guardrail violations
        prompt_templates_table_name: DynamoDB table for prompt templates
        explorer_runtime_arn: ARN for the Explorer runtime (optional)
        discovery_runtime_arn: ARN for the Discovery Agent runtime (optional)
    """

    cognito_user_pool_id: str
    cognito_client_id: str
    cognito_client_secret: str
    aws_region: str
    memory_id: str
    app_url: str = "http://localhost:8080"
    dev_mode: bool = False
    dev_user_id: str = "dev-user-001"
    guardrail_id: Optional[str] = None
    guardrail_version: Optional[str] = None
    guardrail_enabled: bool = True
    guardrail_table_name: str = "agentcore-guardrail-violations"
    prompt_templates_table_name: str = "agentcore-prompt-templates"
    app_settings_table_name: str = "agentcore-app-settings"
    runtime_usage_table_name: str = "agentcore-runtime-usage"
    registry_table_name: Optional[str] = None
    discovery_history_table_name: Optional[str] = None
    workflows_table_name: str = "mfg-ukg-saved-workflows"
    workflow_results_table_name: str = "mfg-ukg-workflow-results"
    explorer_runtime_arn: str = ""
    discovery_runtime_arn: Optional[str] = None

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load configuration from environment variables.
        
        Returns:
            AppConfig instance with values from environment
            
        Raises:
            ConfigurationError: If a required environment variable is missing or empty
        """
        # Check for dev mode first
        dev_mode = os.environ.get("DEV_MODE", "").lower() in ("true", "1", "yes")
        dev_user_id = os.environ.get("DEV_USER_ID", "dev-user-001").strip()
        
        # In dev mode, Cognito vars are optional
        if dev_mode:
            cognito_user_pool_id = os.environ.get("COGNITO_USER_POOL_ID", "").strip() or "dev-pool"
            cognito_client_id = os.environ.get("COGNITO_CLIENT_ID", "").strip() or "dev-client"
            cognito_client_secret = os.environ.get("COGNITO_CLIENT_SECRET", "").strip() or "dev-secret"
        else:
            # Production mode - require Cognito vars
            cognito_user_pool_id = os.environ.get("COGNITO_USER_POOL_ID", "").strip()
            if not cognito_user_pool_id:
                raise ConfigurationError("COGNITO_USER_POOL_ID")
            cognito_client_id = os.environ.get("COGNITO_CLIENT_ID", "").strip()
            if not cognito_client_id:
                raise ConfigurationError("COGNITO_CLIENT_ID")
            cognito_client_secret = os.environ.get("COGNITO_CLIENT_SECRET", "").strip()
            if not cognito_client_secret:
                raise ConfigurationError("COGNITO_CLIENT_SECRET")
        
        # Always required vars
        required_vars = [
            ("EXPLORER_RUNTIME_ARN", "explorer_runtime_arn"),
            ("AWS_REGION", "aws_region"),
            ("MEMORY_ID", "memory_id"),
        ]

        values = {
            "cognito_user_pool_id": cognito_user_pool_id,
            "cognito_client_id": cognito_client_id,
            "cognito_client_secret": cognito_client_secret,
            "dev_mode": dev_mode,
            "dev_user_id": dev_user_id,
        }
        
        for env_var, attr_name in required_vars:
            value = os.environ.get(env_var, "").strip()
            if not value:
                raise ConfigurationError(env_var)
            values[attr_name] = value

        # Optional variables with defaults
        values["app_url"] = os.environ.get("APP_URL", "http://localhost:8080").strip()

        # Guardrail configuration (optional)
        guardrail_id = os.environ.get("GUARDRAIL_ID", "").strip()
        values["guardrail_id"] = guardrail_id if guardrail_id else None
        
        guardrail_version = os.environ.get("GUARDRAIL_VERSION", "").strip()
        values["guardrail_version"] = guardrail_version if guardrail_version else None
        
        guardrail_enabled = os.environ.get("GUARDRAIL_ENABLED", "true").strip().lower()
        values["guardrail_enabled"] = guardrail_enabled in ("true", "1", "yes")
        
        values["guardrail_table_name"] = os.environ.get(
            "GUARDRAIL_TABLE_NAME", "agentcore-guardrail-violations"
        ).strip()

        # Prompt templates configuration
        values["prompt_templates_table_name"] = os.environ.get(
            "PROMPT_TEMPLATES_TABLE_NAME", "agentcore-prompt-templates"
        ).strip()

        # App settings configuration
        values["app_settings_table_name"] = os.environ.get(
            "APP_SETTINGS_TABLE_NAME", "agentcore-app-settings"
        ).strip()

        # Runtime usage configuration
        values["runtime_usage_table_name"] = os.environ.get(
            "RUNTIME_USAGE_TABLE_NAME", "agentcore-runtime-usage"
        ).strip()

        # System Registry configuration (V2)
        registry_table = os.environ.get("REGISTRY_TABLE_NAME", "").strip()
        values["registry_table_name"] = registry_table if registry_table else None

        # Discovery History configuration
        discovery_history_table = os.environ.get("DISCOVERY_HISTORY_TABLE_NAME", "").strip()
        values["discovery_history_table_name"] = discovery_history_table if discovery_history_table else None

        # Workflows table configuration
        values["workflows_table_name"] = os.environ.get(
            "WORKFLOWS_TABLE_NAME", "mfg-ukg-saved-workflows"
        ).strip()

        values["workflow_results_table_name"] = os.environ.get(
            "WORKFLOW_RESULTS_TABLE_NAME", "mfg-ukg-workflow-results"
        ).strip()

        # Discovery agent runtime ARN (optional — admin-only agent)
        discovery_arn = os.environ.get("DISCOVERY_RUNTIME_ARN", "").strip()
        values["discovery_runtime_arn"] = discovery_arn if discovery_arn else None

        return cls(**values)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Get the application configuration (cached).
    
    Returns:
        AppConfig instance
        
    Raises:
        ConfigurationError: If configuration is invalid
    """
    return AppConfig.from_env()


def validate_config() -> bool:
    """Validate that all required configuration is present.
    
    Returns:
        True if configuration is valid
        
    Raises:
        ConfigurationError: If configuration is invalid
    """
    get_config()
    return True
