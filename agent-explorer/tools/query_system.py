"""
Generic query_system Tool — Strands Native

Replaces the three hardcoded Lambda tools (query_erp, query_mes, query_cmms) with a
single config-driven tool that routes queries to any registered system based on its
protocol field in the DynamoDB System Registry.

Supported protocols:
  - rds-data-api: AWS RDS Data API (Aurora PostgreSQL/MySQL)
  - athena: Amazon Athena (S3 Tables / Iceberg / Glue-cataloged data)
  - s3tables: Amazon Athena with S3 Tables catalog (Apache Iceberg)
  - openapi: REST APIs via httpx (GET-only)
  - mcp: MCP servers via MCPClient (streamable HTTP)
"""

import json
import logging
import os
import re
import time
from typing import Any, AsyncIterator

import boto3
import httpx
from strands import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sub-agent prompt for query result analysis
# ---------------------------------------------------------------------------

_QUERY_ANALYST_PROMPT = """\
You are a data analyst. You receive raw query results from a manufacturing system and must \
produce a concise analytical summary.

Your summary MUST include:
1. Row count and key statistics (counts, averages, min/max for numeric columns)
2. Top values / distribution for categorical columns (e.g. top 5 failure codes by count)
3. Notable patterns or outliers
4. A sample of 3-5 representative rows formatted as a compact table

Keep your response under 500 words. Return ONLY the analysis text, no JSON wrapping.
If the query returned an error, explain the error clearly.
If the query returned 0 rows, state that clearly.
"""

# SQL mutation keywords to reject (case-insensitive)
_MUTATION_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE)\b",
    re.IGNORECASE,
)

# Pattern to detect existing LIMIT clause in SQL
_LIMIT_PATTERN = re.compile(
    r"\bLIMIT\s+(\d+)\b",
    re.IGNORECASE,
)

# Max allowed LIMIT for SQL protocols
_MAX_LIMIT = 1000

# Athena polling config
_ATHENA_POLL_MAX_SECONDS = 60
_ATHENA_POLL_INITIAL_DELAY = 0.5
_ATHENA_POLL_BACKOFF_FACTOR = 1.5

# Patterns to strip from error messages to avoid credential leakage
_CREDENTIAL_PATTERNS = [
    re.compile(r"arn:aws:[a-z0-9\-]+:[a-z0-9\-]*:\d{12}:[^\s,\"'}\]]+", re.IGNORECASE),
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"secret\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"jdbc:[^\s,\"'}\]]+", re.IGNORECASE),
    re.compile(r"postgresql://[^\s,\"'}\]]+", re.IGNORECASE),
    re.compile(r"mysql://[^\s,\"'}\]]+", re.IGNORECASE),
]


def _sanitize_error(message: str) -> str:
    """Strip ARNs, connection strings, and credentials from error messages."""
    sanitized = str(message)
    for pattern in _CREDENTIAL_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


def _get_registry_table():
    """Get the DynamoDB Table resource for the System Registry."""
    table_name = os.getenv("REGISTRY_TABLE_NAME")
    if not table_name:
        raise ValueError(
            "REGISTRY_TABLE_NAME environment variable is required."
        )
    dynamodb = boto3.resource("dynamodb", region_name=os.getenv("AWS_REGION", "us-east-1"))
    return dynamodb.Table(table_name)


def _lookup_system_metadata(system_id: str) -> dict:
    """Fetch system METADATA from DynamoDB by system_id.

    Args:
        system_id: The unique system identifier.

    Returns:
        The system metadata item dict.

    Raises:
        ValueError: If system_id is not found in the registry.
    """
    table = _get_registry_table()
    response = table.get_item(Key={"PK": f"SYSTEM#{system_id}", "SK": "METADATA"})
    item = response.get("Item")
    if not item:
        raise ValueError(f"System '{system_id}' not found in registry")
    return item


def _check_sql_mutation(query: str) -> None:
    """Reject SQL queries containing mutation keywords.

    Args:
        query: The SQL query string.

    Raises:
        ValueError: If mutation keywords are detected.
    """
    match = _MUTATION_KEYWORDS.search(query)
    if match:
        raise ValueError(
            f"Query rejected: mutation keyword '{match.group(0).upper()}' detected. "
            "Only read-only SELECT queries are allowed."
        )


def _enforce_sql_limit(query: str, requested_limit: int) -> str:
    """Enforce LIMIT clause on SQL queries, capping at _MAX_LIMIT.

    If the query has no LIMIT, appends LIMIT min(requested_limit, 1000).
    If the query has LIMIT > 1000, caps it at 1000.

    Args:
        query: The SQL query string.
        requested_limit: The user-requested limit (default 100, max 1000).

    Returns:
        The query with an enforced LIMIT clause.
    """
    effective_limit = min(requested_limit, _MAX_LIMIT)

    existing = _LIMIT_PATTERN.search(query)
    if existing:
        current_limit = int(existing.group(1))
        if current_limit > _MAX_LIMIT:
            query = _LIMIT_PATTERN.sub(f"LIMIT {_MAX_LIMIT}", query)
    else:
        # Strip trailing semicolons/whitespace before appending LIMIT
        query = query.rstrip().rstrip(";").rstrip()
        query = f"{query} LIMIT {effective_limit}"

    return query


def _build_error_response(error_type: str, system_id: str, message: str) -> str:
    """Build a structured error JSON response with credential masking.

    Args:
        error_type: Category of error (e.g. 'system_not_found', 'connection_error').
        system_id: The system that caused the error.
        message: Descriptive error message (will be sanitized).

    Returns:
        JSON string with error details.
    """
    return json.dumps({
        "success": False,
        "error_type": error_type,
        "system_id": system_id,
        "error": _sanitize_error(message),
    })


def _build_success_response(
    system_id: str,
    metadata: dict,
    data: Any,
    row_count: int | None = None,
) -> str:
    """Build a success JSON response with system metadata attached.

    Args:
        system_id: The queried system identifier.
        metadata: The system metadata item from the registry.
        data: The query result data (rows, API response, etc.).
        row_count: Optional row count for SQL results.

    Returns:
        JSON string with results and system metadata.
    """
    result = {
        "success": True,
        "system_id": system_id,
        "plant": metadata.get("plant", "unknown"),
        "system_name": metadata.get("name", "unknown"),
        "protocol": metadata.get("protocol"),
        "data": data,
    }
    if row_count is not None:
        result["row_count"] = row_count

    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Protocol backends
# ---------------------------------------------------------------------------

def _execute_rds_data_api(query: str, connection_config: dict, limit: int) -> dict:
    """Execute a read-only SQL query via the AWS RDS Data API.

    Args:
        query: SQL SELECT query.
        connection_config: Must contain rds_cluster_arn, rds_secret_arn, database.
        limit: Requested row limit.

    Returns:
        Dict with 'columns' and 'rows' keys.
    """
    _check_sql_mutation(query)
    query = _enforce_sql_limit(query, limit)

    region = os.getenv("AWS_REGION", "us-east-1")
    client = boto3.client("rds-data", region_name=region)

    response = client.execute_statement(
        resourceArn=connection_config["rds_cluster_arn"],
        secretArn=connection_config["rds_secret_arn"],
        database=connection_config["database"],
        sql=query,
    )

    # Parse column metadata
    column_metadata = response.get("columnMetadata", [])
    columns = [col.get("name", f"col_{i}") for i, col in enumerate(column_metadata)]

    # Parse records into list of dicts
    rows = []
    for record in response.get("records", []):
        row = {}
        for i, field in enumerate(record):
            col_name = columns[i] if i < len(columns) else f"col_{i}"
            # RDS Data API returns typed values: stringValue, longValue, etc.
            value = None
            for type_key in ("stringValue", "longValue", "doubleValue", "booleanValue", "blobValue"):
                if type_key in field:
                    value = field[type_key]
                    break
            if "isNull" in field and field["isNull"]:
                value = None
            row[col_name] = value
        rows.append(row)

    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def _execute_athena(query: str, connection_config: dict, limit: int) -> dict:
    """Execute a read-only SQL query via Amazon Athena with polling.

    Args:
        query: SQL SELECT query (Trino/Presto dialect).
        connection_config: Must contain workgroup, database, output_location.
            Optional: catalog (defaults to 'AwsDataCatalog').
        limit: Requested row limit.

    Returns:
        Dict with 'columns' and 'rows' keys.
    """
    _check_sql_mutation(query)
    query = _enforce_sql_limit(query, limit)

    region = os.getenv("AWS_REGION", "us-east-1")
    client = boto3.client("athena", region_name=region)

    # Start query execution
    catalog = connection_config.get("catalog", "AwsDataCatalog")
    database = connection_config.get("database", "")
    workgroup = connection_config.get("workgroup", "primary")
    output_location = connection_config.get("output_location", "")
    is_s3tables_catalog = catalog.startswith("s3tablescatalog/")

    if not output_location:
        raise ValueError(f"System has no output_location in connection_config. Re-discover to fix.")

    start_params = {
        "QueryString": query,
        "WorkGroup": workgroup,
        "ResultConfiguration": {
            "OutputLocation": output_location,
        },
    }

    if is_s3tables_catalog:
        # S3 Tables: use fully-qualified table names in SQL, no QueryExecutionContext
        import re
        if not database:
            raise ValueError(f"System has no database in connection_config. Re-discover to fix.")
        fq_prefix = f'"{catalog}"."{database}".'

        def _qualify_table(m):
            keyword = m.group(1)
            table = m.group(2).strip('"')
            if '.' in m.group(2):
                return m.group(0)
            return f'{keyword} {fq_prefix}"{table}"'

        query = re.sub(
            r'\b(FROM|JOIN)\s+("?[a-zA-Z_][a-zA-Z0-9_]*"?)',
            _qualify_table,
            query,
            flags=re.IGNORECASE,
        )
        start_params["QueryString"] = query
    else:
        # Standard Glue catalog: set Database and Catalog in context
        start_params["QueryExecutionContext"] = {
            "Database": database,
            "Catalog": catalog,
        }

    logger.info("Athena start_query_execution: catalog=%s, query=%s", catalog, start_params["QueryString"][:200])

    start_response = client.start_query_execution(**start_params)
    execution_id = start_response["QueryExecutionId"]

    # Poll until completion with exponential backoff
    delay = _ATHENA_POLL_INITIAL_DELAY
    elapsed = 0.0

    while elapsed < _ATHENA_POLL_MAX_SECONDS:
        # Athena does not offer a blocking waiter; polling with backoff is required.
        time.sleep(delay)  # nosemgrep: arbitrary-sleep
        elapsed += delay

        status_response = client.get_query_execution(QueryExecutionId=execution_id)
        state = status_response["QueryExecution"]["Status"]["State"]

        if state == "SUCCEEDED":
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = status_response["QueryExecution"]["Status"].get(
                "StateChangeReason", "Unknown error"
            )
            raise RuntimeError(f"Athena query {state}: {_sanitize_error(reason)}")

        delay = min(delay * _ATHENA_POLL_BACKOFF_FACTOR, 5.0)
    else:
        raise TimeoutError(
            f"Athena query timed out after {_ATHENA_POLL_MAX_SECONDS}s (execution_id={execution_id})"
        )

    # Fetch results
    results_response = client.get_query_results(QueryExecutionId=execution_id)
    result_set = results_response.get("ResultSet", {})

    # Parse columns from first row (header)
    column_info = result_set.get("ResultSetMetadata", {}).get("ColumnInfo", [])
    columns = [col.get("Name", f"col_{i}") for i, col in enumerate(column_info)]

    # Parse data rows (skip header row which is the first row in Athena results)
    raw_rows = result_set.get("Rows", [])
    rows = []
    for raw_row in raw_rows[1:]:  # Skip header row
        data = raw_row.get("Data", [])
        row = {}
        for i, datum in enumerate(data):
            col_name = columns[i] if i < len(columns) else f"col_{i}"
            row[col_name] = datum.get("VarCharValue")
        rows.append(row)

    return {"columns": columns, "rows": rows, "row_count": len(rows)}


def _execute_openapi(query: str, connection_config: dict) -> dict:
    """Execute a read-only GET request against an OpenAPI-based REST API.

    The query is parsed as an HTTP operation string, e.g. "GET /equipment/{id}"
    or "GET /work-orders?status=open&limit=100". Only GET methods are allowed.

    Args:
        query: HTTP operation string (e.g. "GET /path?params").
        connection_config: Must contain base_url, auth_type.
            Optional: auth_secret_arn, default_headers.

    Returns:
        Dict with the parsed JSON response.
    """
    # Parse the query as "METHOD /path" or just "/path" (defaults to GET)
    query_stripped = query.strip()
    parts = query_stripped.split(None, 1)

    if len(parts) == 2:
        method, path = parts[0].upper(), parts[1]
    elif len(parts) == 1:
        method, path = "GET", parts[0]
    else:
        raise ValueError("Query must be an HTTP operation, e.g. 'GET /equipment/123'")

    # Enforce GET-only
    if method != "GET":
        raise ValueError(
            f"HTTP method '{method}' is not allowed. Only GET requests are permitted for openapi protocol."
        )

    base_url = connection_config["base_url"].rstrip("/")
    url = f"{base_url}/{path.lstrip('/')}" if not path.startswith("http") else path

    # Build headers
    headers = dict(connection_config.get("default_headers") or {})

    # Fetch credentials from Secrets Manager if needed
    auth_type = connection_config.get("auth_type", "none")
    if auth_type != "none":
        auth_secret_arn = connection_config.get("auth_secret_arn")
        if auth_secret_arn:
            region = os.getenv("AWS_REGION", "us-east-1")
            sm_client = boto3.client("secretsmanager", region_name=region)
            secret_response = sm_client.get_secret_value(SecretId=auth_secret_arn)
            secret_value = secret_response.get("SecretString", "")

            # Try to parse as JSON for structured secrets
            try:
                secret_data = json.loads(secret_value)
            except (json.JSONDecodeError, TypeError):
                secret_data = {"token": secret_value}

            if auth_type == "bearer":
                token = secret_data.get("token") or secret_data.get("access_token") or secret_value
                headers["Authorization"] = f"Bearer {token}"
            elif auth_type == "api_key":
                api_key = secret_data.get("api_key") or secret_data.get("key") or secret_value
                header_name = secret_data.get("header_name", "X-API-Key")
                headers[header_name] = api_key

    # Execute GET request
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()

    # Parse response
    try:
        data = response.json()
    except Exception:
        data = {"raw_response": response.text}

    return {"response": data}


def _execute_mcp(query: str, connection_config: dict) -> dict:
    """Execute a query by invoking an MCP tool via streamable HTTP.

    Args:
        query: The input to pass to the MCP tool.
        connection_config: Must contain mcp_url. Optional: tool_name.

    Returns:
        Dict with the MCP tool result.
    """
    import asyncio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    mcp_url = connection_config["mcp_url"]
    tool_name = connection_config.get("tool_name")

    if not tool_name:
        raise ValueError(
            "MCP connection_config must include 'tool_name' specifying which tool to invoke."
        )

    async def _invoke():
        async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, {"query": query})
                # Extract text content from result
                if hasattr(result, "content") and result.content:
                    contents = []
                    for item in result.content:
                        if hasattr(item, "text"):
                            contents.append(item.text)
                        else:
                            contents.append(str(item))
                    return {"tool_result": "\n".join(contents)}
                return {"tool_result": str(result)}

    # Run the async MCP call
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(asyncio.run, _invoke()).result(timeout=30)
        else:
            result = loop.run_until_complete(_invoke())
    except RuntimeError:
        result = asyncio.run(_invoke())

    return result


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

# Map of supported protocols to their execution functions
_SUPPORTED_PROTOCOLS = {"rds-data-api", "athena", "s3tables", "openapi", "mcp"}


@tool
async def query_system(system_id: str, query: str, limit: int = 100) -> AsyncIterator:
    """Execute a read-only query against any registered system.

    Looks up the system's connection configuration from the DynamoDB registry
    and routes the query to the appropriate backend based on the system's
    protocol field. Results are analyzed by a sub-agent and returned as a
    compact analytical summary to prevent context overflow.

    Supported protocols:
      - rds-data-api: SQL via AWS RDS Data API
      - athena: SQL via Amazon Athena
      - s3tables: SQL via Amazon Athena with S3 Tables catalog (Iceberg)
      - openapi: HTTP GET via httpx
      - mcp: MCP tool invocation via streamable HTTP

    Enforces read-only execution (rejects SQL mutations, GET-only for OpenAPI)
    and caps SQL LIMIT at 1000 rows.

    Args:
        system_id: The unique system identifier from the registry (e.g. "sap-erp-indy").
        query: The query to execute. For SQL protocols, a SELECT statement.
            For openapi, an HTTP operation (e.g. "GET /equipment/123").
            For mcp, the tool input string.
        limit: Maximum rows to return for SQL protocols (default 50, max 1000).

    Returns:
        JSON string with an analytical summary of the query results, or structured error.
    """
    if not system_id or not system_id.strip():
        yield _build_error_response("invalid_input", "", "system_id is required")
        return

    if not query or not query.strip():
        yield _build_error_response("invalid_input", system_id, "query is required")
        return

    # Clamp limit to valid range
    limit = max(1, min(limit, _MAX_LIMIT))

    try:
        # Step 1: Look up system metadata from registry
        metadata = _lookup_system_metadata(system_id)
        protocol = metadata.get("protocol", "")
        connection_config = metadata.get("connection_config", {})

        logger.info("query_system(%s): protocol=%s, connection_config keys=%s",
                     system_id, protocol, list(connection_config.keys()) if connection_config else "EMPTY")

        # Step 2: Validate protocol
        if protocol not in _SUPPORTED_PROTOCOLS:
            yield _build_error_response(
                "unsupported_protocol",
                system_id,
                f"Protocol '{protocol}' is not supported. "
                f"Supported protocols: {', '.join(sorted(_SUPPORTED_PROTOCOLS))}",
            )
            return

        # Step 3: Route to appropriate backend
        result_data = None
        row_count = None

        if protocol == "rds-data-api":
            result = _execute_rds_data_api(query, connection_config, limit)
            result_data = result.get("rows", [])
            row_count = result.get("row_count")

        elif protocol == "athena":
            result = _execute_athena(query, connection_config, limit)
            result_data = result.get("rows", [])
            row_count = result.get("row_count")

        elif protocol == "s3tables":
            if not connection_config.get("catalog") or not connection_config.get("database"):
                yield _build_error_response(
                    "missing_config", system_id,
                    f"System missing connection_config (catalog={connection_config.get('catalog')}, "
                    f"database={connection_config.get('database')}). Re-discover this system to fix.",
                )
                return
            result = _execute_athena(query, connection_config, limit)
            result_data = result.get("rows", [])
            row_count = result.get("row_count")

        elif protocol == "openapi":
            result = _execute_openapi(query, connection_config)
            result_data = result.get("response")

        elif protocol == "mcp":
            result = _execute_mcp(query, connection_config)
            result_data = result.get("tool_result")

        # Step 4: Build raw result and pass to sub-agent for analysis
        raw_result = _build_success_response(system_id, metadata, result_data, row_count=row_count)

        from strands import Agent
        from strands.models import BedrockModel

        _analyst_model_id = (
            os.getenv("QUERY_ANALYST_MODEL_ID")
            or os.getenv("DEFAULT_MODEL_ID")
            or "global.anthropic.claude-sonnet-4-6"
        )
        sub_model = BedrockModel(
            model_id=_analyst_model_id,
            max_tokens=2000,
        )
        sub_agent = Agent(
            model=sub_model,
            system_prompt=_QUERY_ANALYST_PROMPT,
            callback_handler=None,
        )

        try:
            analysis = sub_agent(
                f"Analyze these query results from system '{system_id}' "
                f"(query: {query}):\n\n{raw_result}"
            )

            summary = {
                "success": True,
                "system_id": system_id,
                "system_name": metadata.get("name", "unknown"),
                "query": query,
                "analysis": str(analysis),
                "row_count": row_count if row_count is not None else "unknown",
            }
            yield json.dumps(summary)
        except Exception as e:
            logger.error("Query analyst sub-agent failed: %s", e)
            # Fallback: return truncated raw results if sub-agent fails
            yield raw_result

    except ValueError as e:
        logger.warning("Validation error in query_system(%s): %s", system_id, e)
        yield _build_error_response("validation_error", system_id, str(e))
        return

    except TimeoutError as e:
        logger.error("Timeout in query_system(%s): %s", system_id, e)
        yield _build_error_response("timeout", system_id, str(e))
        return

    except Exception as e:
        protocol_info = "unknown"
        try:
            protocol_info = metadata.get("protocol", "unknown")
        except NameError:
            pass
        logger.error(
            "Error in query_system(%s, protocol=%s): %s — %s",
            system_id,
            protocol_info,
            type(e).__name__,
            _sanitize_error(str(e)),
        )
        yield _build_error_response(
            "execution_error",
            system_id,
            _sanitize_error(str(e)),
        )
        return
