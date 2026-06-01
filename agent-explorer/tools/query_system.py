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
  - sitewise: AWS IoT SiteWise telemetry via the data-plane APIs
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
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
# AWS IoT SiteWise backend
# ---------------------------------------------------------------------------
#
# SiteWise has no SQL surface enabled in this account (the ExecuteQuery API
# returns "Unknown Operation"), so the backend speaks the SiteWise data-plane
# APIs directly. The Explorer doesn't know SiteWise asset/property *IDs* — the
# registry stores concept-mapped property *names* (e.g. "Temperature_PV") and
# asset-model names (e.g. "Fermenter"). This backend resolves those names to IDs
# at query time, then fetches latest values, history, or aggregates.
#
# Query DSL (a compact JSON object so the LLM doesn't need SiteWise internals):
#   {
#     "asset": "Fermenter100",          # asset instance name (or "asset_id": "...")
#     "properties": ["Temperature_PV"], # property names (or "Temperature_PV" string)
#     "mode": "latest",                 # list | latest | history | aggregate
#     "start": "2026-05-30T00:00:00Z",  # history/aggregate only (ISO8601 or epoch secs)
#     "end":   "2026-05-31T00:00:00Z",
#     "aggregate": "AVERAGE",           # aggregate mode: AVERAGE|MAXIMUM|MINIMUM|SUM|COUNT|STANDARD_DEVIATION
#     "resolution": "1h"                # aggregate mode: 1m | 15m | 1h | 1d
#   }
# A bare asset name string is also accepted and treated as {"asset": <name>, "mode": "latest"}.
#
# List/browse mode (enumerate what assets exist before querying telemetry):
#   {"mode": "list"}                       # all asset models with instance counts
#   {"mode": "list", "model": "Fermenter"} # all asset instances of a given model
#   {"mode": "list", "search": "Ferm"}     # instances whose name contains "Ferm" (any model)
#   {"mode": "list", "under": "Brewing"}   # all descendant assets under a parent (by name or asset_id)
#   {"mode": "list", "model": "Fermenter",  # filter instances by a property value
#    "where": {"property": "State", "equals": "Running"}}
#       where operators: equals | not_equals | gt | gte | lt | lte | contains

_SITEWISE_AGG_TYPES = {
    "AVERAGE", "MAXIMUM", "MINIMUM", "SUM", "COUNT", "STANDARD_DEVIATION",
}
_SITEWISE_RESOLUTIONS = {"1m": "1m", "15m": "15m", "1h": "1h", "1d": "1d"}
_SITEWISE_MAX_PROPERTIES = 20
# Cap on how many asset instances a single list query will enumerate, to keep
# the response bounded on large deployments.
_SITEWISE_MAX_LIST = 500


def _sitewise_client(connection_config: dict):
    """Build a boto3 iotsitewise client for the system's region.

    SiteWise can live in a different region than the platform, so the region is
    read from connection_config first, then SITEWISE_REGION, then AWS_REGION.
    """
    region = (
        connection_config.get("region")
        or os.getenv("SITEWISE_REGION")
        or os.getenv("AWS_REGION", "us-east-1")
    )
    return boto3.client("iotsitewise", region_name=region), region


def _sitewise_parse_time(value: Any, default: datetime) -> datetime:
    """Parse an ISO8601 string or epoch-seconds int into an aware datetime."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    s = str(value).strip()
    if s.isdigit():
        return datetime.fromtimestamp(int(s), tz=timezone.utc)
    # Normalize trailing Z to +00:00 for fromisoformat
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _sitewise_unwrap_value(variant: dict) -> Any:
    """Extract the scalar from a SiteWise Variant ({doubleValue|integerValue|...})."""
    if not isinstance(variant, dict):
        return None
    for k in ("doubleValue", "integerValue", "stringValue", "booleanValue"):
        if k in variant:
            return variant[k]
    return None


def _sitewise_resolve_asset(client, query_obj: dict) -> dict:
    """Resolve the target asset (by explicit id or by name) to a description.

    Returns the describe_asset response. Raises ValueError if not resolvable.
    """
    asset_id = query_obj.get("asset_id")
    if asset_id:
        return client.describe_asset(assetId=asset_id)

    asset_name = (query_obj.get("asset") or "").strip()
    if not asset_name:
        raise ValueError(
            "SiteWise query must specify 'asset' (instance name) or 'asset_id'."
        )

    # Resolve name -> id by scanning asset models' instances. SiteWise has no
    # global 'find asset by name' API, so we enumerate models and their assets.
    next_token = None
    while True:
        kwargs = {"maxResults": 250}
        if next_token:
            kwargs["nextToken"] = next_token
        models_resp = client.list_asset_models(**kwargs)
        for m in models_resp.get("assetModelSummaries", []):
            assets_token = None
            while True:
                a_kwargs = {"assetModelId": m["id"], "maxResults": 250}
                if assets_token:
                    a_kwargs["nextToken"] = assets_token
                a_resp = client.list_assets(**a_kwargs)
                for a in a_resp.get("assetSummaries", []):
                    if a.get("name", "").lower() == asset_name.lower():
                        return client.describe_asset(assetId=a["id"])
                assets_token = a_resp.get("nextToken")
                if not assets_token:
                    break
        next_token = models_resp.get("nextToken")
        if not next_token:
            break

    raise ValueError(f"SiteWise asset '{asset_name}' not found in region.")


def _sitewise_select_properties(asset_desc: dict, requested: Any) -> list[dict]:
    """Map requested property names to the asset's {name, id, unit, dataType}.

    If no properties are requested, returns all of the asset's properties
    (bounded). Matching is case-insensitive on the property name.
    """
    asset_props = asset_desc.get("assetProperties", [])
    by_name = {p.get("name", "").lower(): p for p in asset_props}

    if not requested:
        selected = asset_props[:_SITEWISE_MAX_PROPERTIES]
    else:
        if isinstance(requested, str):
            requested = [requested]
        selected = []
        for name in requested:
            p = by_name.get(str(name).strip().lower())
            if p:
                selected.append(p)
    return [
        {
            "name": p.get("name", ""),
            "property_id": p.get("id", ""),
            "unit": p.get("unit"),
            "data_type": p.get("dataType", ""),
        }
        for p in selected
        if p.get("id")
    ][:_SITEWISE_MAX_PROPERTIES]


def _sitewise_list_models(client) -> list[dict]:
    """List all SiteWise asset models (id + name + description)."""
    models = []
    next_token = None
    while True:
        kwargs = {"maxResults": 250}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = client.list_asset_models(**kwargs)
        models.extend(resp.get("assetModelSummaries", []))
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return models


def _sitewise_list_assets_for_model(client, model_id: str, limit: int) -> list[dict]:
    """List asset instances (name + id) for a single model, bounded by limit."""
    out = []
    next_token = None
    while True:
        kwargs = {"assetModelId": model_id, "maxResults": 250}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = client.list_assets(**kwargs)
        for a in resp.get("assetSummaries", []):
            out.append({"asset_name": a.get("name", ""), "asset_id": a.get("id", "")})
            if len(out) >= limit:
                return out
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return out


def _sitewise_list(client, region: str, query_obj: dict) -> dict:
    """Enumerate SiteWise asset models and/or instances (browse mode).

    Shapes, chosen by which key is present:
      - {"under": "Brewing"}    → all descendant assets beneath a parent (hierarchy).
      - {"model": "Fermenter"}  → all instances of that model.
      - {"model": ..., "where": {"property": "State", "equals": "Running"}}
                                → instances of that model filtered by a property value.
      - {"search": "Ferm"}      → instances across all models whose name contains
                                  the substring (case-insensitive).
      - none of the above       → all models with their instance counts.

    This gives the Explorer a way to discover what assets exist before issuing
    a name-based telemetry query, addressing the "can't browse the asset list"
    limitation.
    """
    models = _sitewise_list_models(client)
    model_by_name = {m.get("name", "").lower(): m for m in models}

    requested_model = (query_obj.get("model") or "").strip()
    search = (query_obj.get("search") or "").strip().lower()
    under = (query_obj.get("under") or "").strip()
    under_id = (query_obj.get("under_id") or "").strip()
    where = query_obj.get("where")

    # A property-value filter must be scoped to a model (to resolve the property).
    if where and not requested_model:
        raise ValueError(
            "A 'where' property filter requires a 'model', e.g. "
            "{\"mode\": \"list\", \"model\": \"Fermenter\", "
            "\"where\": {\"property\": \"State\", \"equals\": \"Running\"}}."
        )
    under = (query_obj.get("under") or "").strip()
    under_id = (query_obj.get("under_id") or "").strip()
    where = query_obj.get("where")

    # --- List descendants under a parent asset (hierarchy/location filter) ---
    if under or under_id:
        if under_id:
            root_id = under_id
            root_name = under_id
        else:
            # Resolve the parent by name using the shared resolver.
            root_desc = _sitewise_resolve_asset(client, {"asset": under})
            root_id = root_desc.get("assetId", "")
            root_name = root_desc.get("assetName", under)
        descendants = _sitewise_descendants(client, root_id, _SITEWISE_MAX_LIST)
        return {
            "region": region,
            "mode": "list",
            "under": root_name,
            "asset_count": len(descendants),
            "assets": descendants,
        }

    # --- List instances of a specific model ---
    if requested_model:
        m = model_by_name.get(requested_model.lower())
        if not m:
            raise ValueError(
                f"SiteWise asset model '{requested_model}' not found. "
                f"Available models: {', '.join(sorted(mm.get('name','') for mm in models))}"
            )
        assets = _sitewise_list_assets_for_model(client, m["id"], _SITEWISE_MAX_LIST)
        # Optional property-value filter within this model.
        if where:
            matched = _sitewise_filter_by_property(client, m["id"], assets, where, _SITEWISE_MAX_LIST)
            return {
                "region": region,
                "mode": "list",
                "model": m.get("name", ""),
                "where": where,
                "evaluated": min(len(assets), _SITEWISE_MAX_LIST),
                "match_count": len(matched),
                "assets": matched,
            }
        return {
            "region": region,
            "mode": "list",
            "model": m.get("name", ""),
            "asset_count": len(assets),
            "assets": assets,
        }

    # --- Search instances by partial name across all models ---
    if search:
        matches = []
        for m in models:
            for a in _sitewise_list_assets_for_model(client, m["id"], _SITEWISE_MAX_LIST):
                if search in a["asset_name"].lower():
                    matches.append({**a, "model": m.get("name", "")})
                    if len(matches) >= _SITEWISE_MAX_LIST:
                        break
            if len(matches) >= _SITEWISE_MAX_LIST:
                break
        return {
            "region": region,
            "mode": "list",
            "search": search,
            "match_count": len(matches),
            "assets": matches,
        }

    # --- List all models with instance counts ---
    model_list = []
    for m in models:
        assets = _sitewise_list_assets_for_model(client, m["id"], _SITEWISE_MAX_LIST)
        model_list.append({
            "model": m.get("name", ""),
            "description": (m.get("description") or "")[:200],
            "asset_count": len(assets),
            "assets": [a["asset_name"] for a in assets],
        })
    return {
        "region": region,
        "mode": "list",
        "model_count": len(model_list),
        "models": model_list,
    }


def _sitewise_descendants(client, root_asset_id: str, limit: int) -> list[dict]:
    """Return all descendant assets under a root asset, walking every hierarchy.

    Breadth-first traversal via ListAssociatedAssets (CHILD direction, the
    default). Bounded by ``limit`` and a visited-set to guard against cycles.
    """
    out = []
    seen = {root_asset_id}
    queue = [root_asset_id]
    while queue and len(out) < limit:
        current = queue.pop(0)
        try:
            desc = client.describe_asset(assetId=current)
        except Exception as e:
            logger.warning("sitewise descendants: describe_asset failed: %s", _sanitize_error(str(e)))
            continue
        for h in desc.get("assetHierarchies", []):
            hid = h.get("id")
            if not hid:
                continue
            token = None
            while True:
                kwargs = {"assetId": current, "hierarchyId": hid, "maxResults": 250}
                if token:
                    kwargs["nextToken"] = token
                try:
                    resp = client.list_associated_assets(**kwargs)
                except Exception as e:
                    logger.warning("sitewise descendants: list_associated_assets failed: %s", _sanitize_error(str(e)))
                    break
                for a in resp.get("assetSummaries", []):
                    aid = a.get("id", "")
                    if aid and aid not in seen:
                        seen.add(aid)
                        out.append({
                            "asset_name": a.get("name", ""),
                            "asset_id": aid,
                            "hierarchy": h.get("name", ""),
                        })
                        queue.append(aid)
                        if len(out) >= limit:
                            return out
                token = resp.get("nextToken")
                if not token:
                    break
    return out


# Comparison operators supported by the list `where` filter.
def _sitewise_compare(actual: Any, op: str, expected: Any) -> bool:
    """Apply a single comparison operator for property-value filtering.

    Numeric operators coerce both sides to float; string operators compare
    case-insensitively. Returns False on any coercion failure rather than
    raising, so one odd value doesn't abort the whole filter.
    """
    if actual is None:
        return False
    op = (op or "equals").lower()
    try:
        if op in ("gt", "gte", "lt", "lte"):
            a = float(actual)
            b = float(expected)
            return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
        sa = str(actual).strip().lower()
        sb = str(expected).strip().lower()
        if op == "equals":
            return sa == sb
        if op == "not_equals":
            return sa != sb
        if op == "contains":
            return sb in sa
    except (ValueError, TypeError):
        return False
    return False


def _sitewise_filter_by_property(client, model_id: str, assets: list[dict],
                                 where: dict, limit: int) -> list[dict]:
    """Filter a model's asset instances by a property value (client-side).

    SiteWise has no server-side property-value query, so we batch-read the
    target property across the instances and keep those matching the operator.
    Bounded by ``limit`` instances to keep the read count sane.

    Args:
        model_id: Asset model id (to resolve the property name → id).
        assets: Instances to filter ([{asset_name, asset_id}, ...]).
        where: {"property": <name>, one-of equals/not_equals/gt/gte/lt/lte/contains: <value>}.
        limit: Max instances to evaluate.
    """
    prop_name = (where.get("property") or "").strip()
    if not prop_name:
        raise ValueError("'where' filter requires a 'property' name.")

    # Determine operator + expected value from the where dict.
    op, expected = "equals", None
    for candidate in ("equals", "not_equals", "gt", "gte", "lt", "lte", "contains"):
        if candidate in where:
            op, expected = candidate, where[candidate]
            break
    if expected is None and "value" in where:
        expected = where["value"]
    if expected is None:
        raise ValueError(
            "'where' filter requires a comparison value, e.g. "
            "{\"property\": \"State\", \"equals\": \"Running\"}."
        )

    # Resolve the property name → id on the model.
    detail = client.describe_asset_model(assetModelId=model_id)
    pid = None
    for p in detail.get("assetModelProperties", []):
        if p.get("name", "").lower() == prop_name.lower():
            pid = p.get("id")
            break
    if not pid:
        avail = ", ".join(p.get("name", "") for p in detail.get("assetModelProperties", [])[:15])
        raise ValueError(f"Property '{prop_name}' not found on model. Available include: {avail}")

    bounded = assets[:limit]
    # Batch in chunks (BatchGetAssetPropertyValue accepts up to 16 entries).
    matched = []
    for i in range(0, len(bounded), 16):
        chunk = bounded[i:i + 16]
        entries = [
            {"entryId": f"e{j}", "assetId": a["asset_id"], "propertyId": pid}
            for j, a in enumerate(chunk)
        ]
        resp = client.batch_get_asset_property_value(entries=entries)
        by_entry = {e["entryId"]: e for e in resp.get("successEntries", [])}
        for j, a in enumerate(chunk):
            entry = by_entry.get(f"e{j}")
            value = None
            if entry and entry.get("assetPropertyValue"):
                value = _sitewise_unwrap_value(entry["assetPropertyValue"].get("value", {}))
            if _sitewise_compare(value, op, expected):
                matched.append({**a, prop_name: value})
    return matched


def _execute_sitewise(query: str, connection_config: dict) -> dict:
    """Execute a read-only SiteWise telemetry query via the data-plane APIs.

    Parses the compact JSON query DSL, resolves asset/property names to IDs,
    and routes to latest / history / aggregate retrieval. Read-only — no write
    APIs are ever called.

    Args:
        query: JSON query DSL string (or a bare asset name for latest values).
        connection_config: May contain 'region'. Optional.

    Returns:
        Dict with resolved asset metadata and per-property results.
    """
    # Parse the query DSL. A bare string is treated as an asset name (latest mode).
    query_obj: dict
    stripped = (query or "").strip()
    if stripped.startswith("{"):
        try:
            query_obj = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise ValueError(f"SiteWise query is not valid JSON: {e}")
    else:
        query_obj = {"asset": stripped, "mode": "latest"}

    mode = str(query_obj.get("mode", "latest")).lower()
    client, region = _sitewise_client(connection_config)

    # List/browse mode runs before asset resolution — it enumerates what
    # exists rather than targeting a single asset.
    if mode == "list":
        return _sitewise_list(client, region, query_obj)

    asset_desc = _sitewise_resolve_asset(client, query_obj)
    asset_id = asset_desc.get("assetId", "")
    asset_name = asset_desc.get("assetName", "")
    props = _sitewise_select_properties(asset_desc, query_obj.get("properties"))

    if not props:
        raise ValueError(
            f"No matching properties found on asset '{asset_name}'. "
            f"Available include: "
            f"{', '.join(p.get('name','') for p in asset_desc.get('assetProperties', [])[:15])}"
        )

    base = {
        "region": region,
        "asset_id": asset_id,
        "asset_name": asset_name,
        "mode": mode,
    }

    # ---- latest values (BatchGetAssetPropertyValue) ----
    if mode == "latest":
        entries = [
            {"entryId": f"e{i}", "assetId": asset_id, "propertyId": p["property_id"]}
            for i, p in enumerate(props)
        ]
        resp = client.batch_get_asset_property_value(entries=entries)
        by_entry = {e["entryId"]: e for e in resp.get("successEntries", [])}
        readings = []
        for i, p in enumerate(props):
            entry = by_entry.get(f"e{i}")
            value = None
            ts = None
            quality = None
            if entry and entry.get("assetPropertyValue"):
                apv = entry["assetPropertyValue"]
                value = _sitewise_unwrap_value(apv.get("value", {}))
                ts = apv.get("timestamp", {}).get("timeInSeconds")
                quality = apv.get("quality")
            readings.append({
                "property": p["name"],
                "unit": p.get("unit"),
                "value": value,
                "timestamp": ts,
                "quality": quality,
            })
        base["readings"] = readings
        return base

    # ---- history (GetAssetPropertyValueHistory) ----
    if mode == "history":
        end = _sitewise_parse_time(query_obj.get("end"), datetime.now(timezone.utc))
        start = _sitewise_parse_time(
            query_obj.get("start"), end - timedelta(hours=1)
        )
        # SiteWise requires integer epoch seconds (no fractional component).
        start_s = int(start.timestamp())
        end_s = int(end.timestamp())
        max_points = max(1, min(int(query_obj.get("max_points", 100)), 250))
        series = []
        for p in props:
            resp = client.get_asset_property_value_history(
                assetId=asset_id,
                propertyId=p["property_id"],
                startDate=start_s,
                endDate=end_s,
                maxResults=max_points,
            )
            points = [
                {
                    "value": _sitewise_unwrap_value(pt.get("value", {})),
                    "timestamp": pt.get("timestamp", {}).get("timeInSeconds"),
                    "quality": pt.get("quality"),
                }
                for pt in resp.get("assetPropertyValueHistory", [])
            ]
            series.append({
                "property": p["name"],
                "unit": p.get("unit"),
                "point_count": len(points),
                "points": points,
            })
        base["start"] = start.isoformat()
        base["end"] = end.isoformat()
        base["series"] = series
        return base

    # ---- aggregate (GetAssetPropertyAggregates) ----
    if mode == "aggregate":
        agg = str(query_obj.get("aggregate", "AVERAGE")).upper()
        if agg not in _SITEWISE_AGG_TYPES:
            raise ValueError(
                f"Unsupported aggregate '{agg}'. "
                f"Supported: {', '.join(sorted(_SITEWISE_AGG_TYPES))}"
            )
        resolution = _SITEWISE_RESOLUTIONS.get(
            str(query_obj.get("resolution", "1h")).lower()
        )
        if not resolution:
            raise ValueError(
                "Unsupported resolution. Supported: 1m, 15m, 1h, 1d."
            )
        end = _sitewise_parse_time(query_obj.get("end"), datetime.now(timezone.utc))
        start = _sitewise_parse_time(
            query_obj.get("start"), end - timedelta(days=1)
        )
        # SiteWise requires integer epoch seconds (no fractional component).
        start_s = int(start.timestamp())
        end_s = int(end.timestamp())
        max_points = max(1, min(int(query_obj.get("max_points", 100)), 250))
        series = []
        for p in props:
            resp = client.get_asset_property_aggregates(
                assetId=asset_id,
                propertyId=p["property_id"],
                aggregateTypes=[agg],
                resolution=resolution,
                startDate=start_s,
                endDate=end_s,
                maxResults=max_points,
            )
            points = [
                {
                    "value": av.get("value", {}).get(agg),
                    "timestamp": (
                        int(av["timestamp"].timestamp())
                        if hasattr(av.get("timestamp"), "timestamp")
                        else av.get("timestamp")
                    ),
                }
                for av in resp.get("aggregatedValues", [])
            ]
            series.append({
                "property": p["name"],
                "unit": p.get("unit"),
                "aggregate": agg,
                "resolution": resolution,
                "point_count": len(points),
                "points": points,
            })
        base["start"] = start.isoformat()
        base["end"] = end.isoformat()
        base["series"] = series
        return base

    raise ValueError(
        f"Unsupported SiteWise mode '{mode}'. Supported: list, latest, history, aggregate."
    )


# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

# Map of supported protocols to their execution functions
_SUPPORTED_PROTOCOLS = {"rds-data-api", "athena", "s3tables", "openapi", "mcp", "sitewise"}


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
      - sitewise: AWS IoT SiteWise telemetry via the data-plane APIs

    Enforces read-only execution (rejects SQL mutations, GET-only for OpenAPI,
    read-only data-plane APIs for SiteWise) and caps SQL LIMIT at 1000 rows.

    Args:
        system_id: The unique system identifier from the registry (e.g. "sap-erp-indy").
        query: The query to execute. For SQL protocols, a SELECT statement.
            For openapi, an HTTP operation (e.g. "GET /equipment/123").
            For mcp, the tool input string.
            For sitewise, a compact JSON object selecting an asset, properties,
            and mode (latest/history/aggregate) — e.g.
            {"asset": "Fermenter100", "properties": ["Temperature_PV"], "mode": "latest"}.
            A bare asset name string returns that asset's latest values.
            To discover what assets exist, use list mode:
            {"mode": "list"} lists all asset models with instance counts;
            {"mode": "list", "model": "Fermenter"} lists all instances of a model;
            {"mode": "list", "search": "Ferm"} finds instances by partial name;
            {"mode": "list", "under": "Brewing"} lists all assets beneath a parent
            (hierarchy/location filter);
            {"mode": "list", "model": "Fermenter", "where": {"property": "State",
            "equals": "Running"}} filters a model's instances by a property value
            (operators: equals, not_equals, gt, gte, lt, lte, contains).
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

        elif protocol == "sitewise":
            result = _execute_sitewise(query, connection_config)
            result_data = result

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
