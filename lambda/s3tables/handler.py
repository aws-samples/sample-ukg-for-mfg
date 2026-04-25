"""Lambda MCP target handler using mcp_lambda adapter.

Wraps awslabs.s3-tables-mcp-server as a subprocess via the mcp_lambda adapter.
The handler delegates entirely to the adapter — zero custom Athena query logic.
Each domain Lambda runs this identical handler; only the S3_TABLES_NAMESPACE
environment variable (set in CDK) differs between domains.
"""

import os
import sys

from mcp.client.stdio import StdioServerParameters
from mcp_lambda import (
    BedrockAgentCoreGatewayTargetHandler,
    StdioServerAdapterRequestHandler,
)

# Configure MCP server subprocess with only the required env vars.
# No ambient Lambda environment variables leak to the subprocess.
server_params = StdioServerParameters(
    command=sys.executable,
    args=["-m", "awslabs.s3_tables_mcp_server"],
    env={
        "AWS_REGION": os.environ["AWS_REGION"],
        "S3_TABLES_BUCKET_NAME": os.environ["S3_TABLES_BUCKET_NAME"],
        "S3_TABLES_NAMESPACE": os.environ["S3_TABLES_NAMESPACE"],
    },
)

request_handler = StdioServerAdapterRequestHandler(server_params)
event_handler = BedrockAgentCoreGatewayTargetHandler(request_handler)


def handler(event, context):
    """Lambda entry point — delegates entirely to mcp_lambda adapter."""
    return event_handler.handle(event, context)
