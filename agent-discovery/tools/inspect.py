"""
Discovery Agent — Inspection Tools

Phase 1 tools for the Discovery Agent workflow. These tools connect to data sources
and extract raw schema information for subsequent LLM-driven understanding and
correlation phases.

Supported inspection targets:
  - RDS databases via AWS RDS Data API (inspect_rds_schema)
  - OpenAPI/Swagger specs via HTTP (inspect_api_spec)
  - MCP servers via streamable HTTP (inspect_mcp_server)
  - Athena/Glue-cataloged data sources (inspect_athena_source)
  - AWS IoT SiteWise asset models / assets (inspect_sitewise_assets)
"""

import json
import logging
import os
import re

import boto3
import httpx
from strands import tool

from tools.state import save_state

logger = logging.getLogger(__name__)


def _save_and_summarize(full_result: dict, source_type: str, namespace: str = None) -> str:
    """Auto-save full inspection result to DDB and return a compact summary.

    The full data is persisted as the 'inspect' phase state. The conversation
    only sees a small summary with table/endpoint names and column counts,
    keeping the context lean while preserving all data for later phases.

    Args:
        full_result: The complete inspection result dict.
        source_type: Source type identifier (e.g. "rds", "s3tables", "api", "mcp").
        namespace: Optional namespace scope. When provided, the state is saved
            under a namespace-scoped DynamoDB key so that multiple namespaces
            can coexist without overwriting each other.
    """
    full_json = json.dumps(full_result, default=str)
    save_state("inspect", full_json, namespace=namespace)

    # Build compact summary for the conversation
    tables = full_result.get("tables", [])
    endpoints = full_result.get("endpoints", [])

    if endpoints:
        # API spec — summarize endpoints
        summary_items = []
        for ep in endpoints:
            summary_items.append({
                "path": ep.get("path", ""),
                "method": ep.get("method", ""),
                "summary": ep.get("summary", ""),
            })
        summary = {
            "success": True,
            "source_type": source_type,
            "endpoint_count": len(endpoints),
            "info": full_result.get("info", {}),
            "endpoints_summary": summary_items,
            "_saved_to_ddb": True,
            "_note": "Full data saved to DDB. Use load_phase_results('inspect') to retrieve.",
        }
    elif tables:
        # Schema — summarize tables with column counts
        summary_items = []
        for t in tables:
            cols = t.get("columns", [])
            summary_items.append({
                "table_name": t.get("table_name", ""),
                "column_count": len(cols),
                "columns": [c.get("column_name", "") for c in cols],
                "primary_keys": t.get("primary_keys", []),
                "row_count": t.get("row_count"),
            })
        summary = {
            "success": True,
            "source_type": source_type,
            "table_count": len(tables),
            "tables_summary": summary_items,
            "_saved_to_ddb": True,
            "_note": "Full data saved to DDB. Use load_phase_results('inspect') to retrieve.",
        }
    else:
        # MCP or other — just save and return as-is if small
        tools_list = full_result.get("tools", [])
        summary = {
            "success": True,
            "source_type": source_type,
            "tool_count": len(tools_list),
            "tools_summary": [{"name": t.get("name", ""), "description": (t.get("description", "") or "")[:80]} for t in tools_list],
            "_saved_to_ddb": True,
        }

    return json.dumps(summary, default=str)

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


def _build_error(error_type: str, message: str) -> str:
    """Build a structured error JSON response with credential masking.

    Args:
        error_type: Category of error (e.g. 'connection_error', 'parse_error').
        message: Descriptive error message (will be sanitized).

    Returns:
        JSON string with error details.
    """
    return json.dumps({
        "success": False,
        "error_type": error_type,
        "error": _sanitize_error(message),
    })


def _execute_rds_sql(client, cluster_arn: str, secret_arn: str,
                     database: str, sql: str) -> list[dict]:
    """Execute a SQL statement via RDS Data API and return parsed rows.

    Args:
        client: boto3 rds-data client.
        cluster_arn: RDS cluster ARN.
        secret_arn: Secrets Manager secret ARN.
        database: Database name.
        sql: SQL query to execute.

    Returns:
        List of dicts, one per row.
    """
    response = client.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
        sql=sql,
    )

    column_metadata = response.get("columnMetadata", [])
    columns = [col.get("name", f"col_{i}") for i, col in enumerate(column_metadata)]

    rows = []
    for record in response.get("records", []):
        row = {}
        for i, field in enumerate(record):
            col_name = columns[i] if i < len(columns) else f"col_{i}"
            value = None
            for type_key in ("stringValue", "longValue", "doubleValue",
                             "booleanValue", "blobValue"):
                if type_key in field:
                    value = field[type_key]
                    break
            if "isNull" in field and field["isNull"]:
                value = None
            row[col_name] = value
        rows.append(row)

    return rows



@tool
def inspect_rds_schema(cluster_arn: str, secret_arn: str, database: str,
                       schema_name: str) -> str:
    """Inspect an RDS database schema via the AWS RDS Data API.

    Connects to the specified Aurora cluster and extracts complete schema
    information including tables, columns, data types, primary keys, foreign
    keys, row counts, and sample rows. Used by the Discovery Agent during
    Phase 1 (INSPECT) to catalog a relational data source.

    Args:
        cluster_arn: ARN of the RDS Aurora cluster.
        secret_arn: ARN of the Secrets Manager secret with DB credentials.
        database: Database name (e.g. "postgres").
        schema_name: Schema name to inspect (e.g. "sap_indianapolis").

    Returns:
        JSON string with tables, columns, types, PKs, FKs, row counts,
        and sample rows. On failure, returns structured error JSON.
    """
    region = os.getenv("AWS_REGION", "us-east-1")

    try:
        client = boto3.client("rds-data", region_name=region)

        # 1. Get all tables in the schema
        tables_sql = (
            f"SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{schema_name}' AND table_type = 'BASE TABLE' "
            f"ORDER BY table_name"
        )
        table_rows = _execute_rds_sql(client, cluster_arn, secret_arn, database, tables_sql)
        table_names = [r["table_name"] for r in table_rows]

        if not table_names:
            return json.dumps({
                "success": True,
                "schema_name": schema_name,
                "database": database,
                "tables": [],
                "table_count": 0,
            })

        # 2. Get columns for all tables in the schema
        columns_sql = (
            f"SELECT table_name, column_name, data_type, is_nullable, column_default "
            f"FROM information_schema.columns "
            f"WHERE table_schema = '{schema_name}' "
            f"ORDER BY table_name, ordinal_position"
        )
        column_rows = _execute_rds_sql(client, cluster_arn, secret_arn, database, columns_sql)

        # Group columns by table
        columns_by_table: dict[str, list[dict]] = {}
        for row in column_rows:
            tname = row["table_name"]
            if tname not in columns_by_table:
                columns_by_table[tname] = []
            columns_by_table[tname].append({
                "column_name": row["column_name"],
                "data_type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
            })

        # 3. Get primary keys and foreign keys
        constraints_sql = (
            f"SELECT tc.table_name, tc.constraint_type, kcu.column_name, "
            f"ccu.table_name AS foreign_table, ccu.column_name AS foreign_column "
            f"FROM information_schema.table_constraints tc "
            f"JOIN information_schema.key_column_usage kcu "
            f"ON tc.constraint_name = kcu.constraint_name "
            f"AND tc.table_schema = kcu.table_schema "
            f"LEFT JOIN information_schema.constraint_column_usage ccu "
            f"ON tc.constraint_name = ccu.constraint_name "
            f"AND tc.table_schema = ccu.table_schema "
            f"WHERE tc.table_schema = '{schema_name}' "
            f"AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY') "
            f"ORDER BY tc.table_name, tc.constraint_type"
        )
        constraint_rows = _execute_rds_sql(client, cluster_arn, secret_arn, database, constraints_sql)

        # Group PKs and FKs by table
        pks_by_table: dict[str, list[str]] = {}
        fks_by_table: dict[str, list[dict]] = {}
        for row in constraint_rows:
            tname = row["table_name"]
            if row["constraint_type"] == "PRIMARY KEY":
                if tname not in pks_by_table:
                    pks_by_table[tname] = []
                pks_by_table[tname].append(row["column_name"])
            elif row["constraint_type"] == "FOREIGN KEY":
                if tname not in fks_by_table:
                    fks_by_table[tname] = []
                fks_by_table[tname].append({
                    "column": row["column_name"],
                    "references_table": row.get("foreign_table"),
                    "references_column": row.get("foreign_column"),
                })

        # 4. Get row counts for each table (skip sample rows — column names/types
        # are sufficient for concept mapping, and samples bloat the context)
        tables = []
        for tname in table_names:
            # Row count
            count_sql = f'SELECT COUNT(*) AS cnt FROM "{schema_name}"."{tname}"'
            try:
                count_rows = _execute_rds_sql(client, cluster_arn, secret_arn, database, count_sql)
                row_count = count_rows[0]["cnt"] if count_rows else 0
            except Exception as e:
                logger.warning("Failed to get row count for %s.%s: %s", schema_name, tname, _sanitize_error(str(e)))
                row_count = None

            tables.append({
                "table_name": tname,
                "columns": columns_by_table.get(tname, []),
                "primary_keys": pks_by_table.get(tname, []),
                "foreign_keys": fks_by_table.get(tname, []),
                "row_count": row_count,
            })

        full_result = {
            "success": True,
            "schema_name": schema_name,
            "database": database,
            "tables": tables,
            "table_count": len(tables),
        }
        return _save_and_summarize(full_result, "rds")

    except Exception as e:
        logger.error(
            "inspect_rds_schema failed for schema '%s': %s — %s",
            schema_name, type(e).__name__, _sanitize_error(str(e)),
        )
        return _build_error("connection_error", str(e))


@tool
def inspect_api_spec(spec_url: str) -> str:
    """Fetch and parse an OpenAPI/Swagger specification from a URL.

    Downloads the spec, parses it as JSON (falling back to YAML), and
    extracts API metadata including endpoints, HTTP methods, parameters,
    and request/response schemas. Used by the Discovery Agent during
    Phase 1 (INSPECT) to catalog a REST API data source.

    Args:
        spec_url: URL to the OpenAPI spec (JSON or YAML format).

    Returns:
        JSON string with API info, servers, endpoints, methods, parameters,
        and response schemas. On failure, returns structured error JSON.
    """
    try:
        with httpx.Client(timeout=30.0) as http_client:
            response = http_client.get(spec_url)
            response.raise_for_status()
            raw_content = response.text

        # Try JSON first, fall back to YAML
        spec = None
        try:
            spec = json.loads(raw_content)
        except (json.JSONDecodeError, ValueError):
            try:
                import yaml
                spec = yaml.safe_load(raw_content)
            except Exception as yaml_err:
                return _build_error(
                    "parse_error",
                    f"Failed to parse spec as JSON or YAML: {yaml_err}",
                )

        if not spec or not isinstance(spec, dict):
            return _build_error("parse_error", "Spec is empty or not a valid object")

        # Extract info
        info = spec.get("info", {})
        api_info = {
            "title": info.get("title", "Unknown"),
            "version": info.get("version", "Unknown"),
            "description": info.get("description"),
        }

        # Extract servers
        servers = []
        for server in spec.get("servers", []):
            servers.append({
                "url": server.get("url"),
                "description": server.get("description"),
            })

        # Extract paths/endpoints
        endpoints = []
        paths = spec.get("paths", {})
        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete", "options", "head"):
                operation = path_item.get(method)
                if not operation or not isinstance(operation, dict):
                    continue

                # Extract parameters — names and types only (no full schemas)
                params = []
                for param in operation.get("parameters", []):
                    if isinstance(param, dict):
                        schema = param.get("schema", {})
                        params.append({
                            "name": param.get("name"),
                            "in": param.get("in"),
                            "required": param.get("required", False),
                            "type": schema.get("type", "") if isinstance(schema, dict) else "",
                        })

                # Extract request body — content type and top-level type only
                request_body = None
                rb = operation.get("requestBody")
                if isinstance(rb, dict):
                    content = rb.get("content", {})
                    for content_type, media in content.items():
                        if isinstance(media, dict):
                            schema = media.get("schema", {})
                            request_body = {
                                "content_type": content_type,
                                "type": schema.get("type", "") if isinstance(schema, dict) else "",
                            }
                            break

                # Extract response codes and descriptions only (skip full schemas)
                responses = {}
                for status_code, resp in operation.get("responses", {}).items():
                    if isinstance(resp, dict):
                        responses[str(status_code)] = resp.get("description", "")

                endpoints.append({
                    "path": path,
                    "method": method.upper(),
                    "summary": operation.get("summary"),
                    "operation_id": operation.get("operationId"),
                    "parameters": params,
                    "request_body": request_body,
                    "responses": responses,
                    "tags": operation.get("tags", []),
                })

        full_result = {
            "success": True,
            "info": api_info,
            "servers": servers,
            "endpoints": endpoints,
            "endpoint_count": len(endpoints),
        }
        return _save_and_summarize(full_result, "api")

    except httpx.HTTPStatusError as e:
        logger.error("HTTP error fetching spec from %s: %s", spec_url, e)
        return _build_error("fetch_error", f"HTTP {e.response.status_code} fetching spec from {spec_url}")

    except httpx.RequestError as e:
        logger.error("Request error fetching spec from %s: %s", spec_url, e)
        return _build_error("fetch_error", f"Failed to fetch spec from {spec_url}: {type(e).__name__}")

    except Exception as e:
        logger.error("inspect_api_spec failed for %s: %s — %s", spec_url, type(e).__name__, str(e))
        return _build_error("parse_error", str(e))


@tool
def inspect_mcp_server(mcp_url: str) -> str:
    """Connect to an MCP server and enumerate its available tools.

    Connects via streamable HTTP transport, initializes a session, and
    calls list_tools() to discover all tools the server exposes. Used by
    the Discovery Agent during Phase 1 (INSPECT) to catalog an MCP-based
    data source.

    Args:
        mcp_url: MCP server URL (streamable HTTP transport endpoint).

    Returns:
        JSON string with tool names, input schemas, and descriptions.
        On failure, returns structured error JSON.
    """
    import asyncio

    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def _list_tools():
            async with streamablehttp_client(mcp_url) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    tools_list = []
                    for t in result.tools:
                        tools_list.append({
                            "name": t.name,
                            "description": t.description,
                            "input_schema": t.inputSchema if hasattr(t, "inputSchema") else None,
                        })
                    return tools_list

        # Run the async MCP call
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    tools_list = pool.submit(asyncio.run, _list_tools()).result(timeout=30)
            else:
                tools_list = loop.run_until_complete(_list_tools())
        except RuntimeError:
            tools_list = asyncio.run(_list_tools())

        full_result = {
            "success": True,
            "mcp_url": mcp_url,
            "tools": tools_list,
            "tool_count": len(tools_list),
        }
        return _save_and_summarize(full_result, "mcp")

    except Exception as e:
        logger.error(
            "inspect_mcp_server failed for %s: %s — %s",
            mcp_url, type(e).__name__, str(e),
        )
        return _build_error("connection_error", f"Failed to connect to MCP server at {mcp_url}: {type(e).__name__}")


@tool
def list_s3tables_namespaces(bucket_name: str) -> str:
    """List all namespaces (databases) in an S3 Tables bucket.

    Use this as the first step when discovering an S3 Tables source — call this
    to get the actual namespace names, then call inspect_athena_source for each.

    Args:
        bucket_name: S3 Tables bucket name (e.g. "mfg-ukg-manufacturing-136380264626-us-east-2").

    Returns:
        JSON string with list of namespace names. On failure, returns structured error JSON.
    """
    region = os.getenv("AWS_REGION", "us-east-1")
    try:
        sts = boto3.client("sts", region_name=region)
        account_id = sts.get_caller_identity()["Account"]
        bucket_arn = f"arn:aws:s3tables:{region}:{account_id}:bucket/{bucket_name}"

        s3tables = boto3.client("s3tables", region_name=region)
        namespaces = []
        paginator_kwargs = {"tableBucketARN": bucket_arn}
        response = s3tables.list_namespaces(**paginator_kwargs)
        while True:
            for ns in response.get("namespaces", []):
                name = ns.get("namespace")
                if isinstance(name, list):
                    namespaces.extend(name)
                elif name:
                    namespaces.append(name)
            next_token = response.get("continuationToken")
            if not next_token:
                break
            response = s3tables.list_namespaces(**paginator_kwargs, continuationToken=next_token)

        return json.dumps({
            "success": True,
            "bucket_name": bucket_name,
            "namespaces": namespaces,
            "namespace_count": len(namespaces),
        })
    except Exception as e:
        logger.error("list_s3tables_namespaces failed for %s: %s", bucket_name, _sanitize_error(str(e)))
        return _build_error("catalog_error", str(e))


@tool
def inspect_athena_source(database: str, catalog: str = "AwsDataCatalog",
                          workgroup: str = "primary",
                          output_location: str = "") -> str:
    """Inspect an Athena/Glue-cataloged data source (S3 Tables, Iceberg, Parquet, CSV).

    For S3 Tables, uses the S3 Tables API to list tables and get column metadata,
    then Athena for row counts and sample rows. For standard Glue catalogs, uses
    Athena throughout.

    For S3 Tables, use catalog="s3tablescatalog/<bucket-name>" (e.g.
    "s3tablescatalog/mfg-ukg-manufacturing-123456789012-us-east-2").

    Args:
        database: Database/namespace name (e.g. "erp", "mes", "cmms").
        catalog: Catalog name. Use "AwsDataCatalog" for standard Glue, or
            "s3tablescatalog/<bucket-name>" for S3 Tables.
        workgroup: Athena workgroup (default "primary").
        output_location: S3 path for Athena query results. If empty, auto-generated.

    Returns:
        JSON string with tables, columns, types, and sample data.
        On failure, returns structured error JSON.
    """
    region = os.getenv("AWS_REGION", "us-east-1")

    try:
        athena = boto3.client("athena", region_name=region)

        # Default output location
        if not output_location:
            account_id = boto3.client("sts").get_caller_identity()["Account"]
            app_name = os.getenv("APP_NAME", "mfg-ukg")
            output_location = f"s3://athena-{account_id}-{region}/results/"

        is_s3tables = catalog.startswith("s3tablescatalog/")

        if is_s3tables:
            # Extract bucket name from catalog string
            bucket_name = catalog.split("/", 1)[1]
            return _inspect_s3tables(
                bucket_name, database, catalog, workgroup, output_location, region, athena,
            )
        else:
            return _inspect_glue_catalog(
                database, catalog, workgroup, output_location, region, athena,
            )

    except Exception as e:
        logger.error(
            "inspect_athena_source failed for database '%s': %s — %s",
            database, type(e).__name__, _sanitize_error(str(e)),
        )
        return _build_error("catalog_error", str(e))


def _get_table_schema(s3tables_client, s3_client, bucket_arn: str,
                      namespace: str, table_name: str) -> list:
    """Get column schema from Iceberg metadata JSON via S3 Tables API.

    Flow:
      1. get_table_metadata_location → metadataLocation (S3 URI to metadata JSON)
      2. s3.get_object on that URI → Iceberg metadata JSON
      3. Parse current-schema-id from schemas array → field list

    Returns:
        List of dicts with column_name, data_type, required keys.
    """
    try:
        meta_resp = s3tables_client.get_table_metadata_location(
            tableBucketARN=bucket_arn,
            namespace=namespace,
            name=table_name,
        )
        metadata_location = meta_resp.get("metadataLocation", "")
        if not metadata_location:
            logger.warning("No metadataLocation for %s.%s", namespace, table_name)
            return []

        # Parse S3 URI: s3://bucket/key
        if metadata_location.startswith("s3://"):
            parts = metadata_location[5:].split("/", 1)
            s3_bucket = parts[0]
            s3_key = parts[1] if len(parts) > 1 else ""
        else:
            logger.warning("Unexpected metadataLocation format: %s", metadata_location)
            return []

        obj_resp = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
        metadata_json = json.loads(obj_resp["Body"].read().decode("utf-8"))

        # Find the current schema
        current_schema_id = metadata_json.get("current-schema-id", 0)
        schemas = metadata_json.get("schemas", [])

        # Find matching schema by id, fall back to last schema
        current_schema = None
        for s in schemas:
            if s.get("schema-id") == current_schema_id:
                current_schema = s
                break
        if not current_schema and schemas:
            current_schema = schemas[-1]

        if not current_schema:
            logger.warning("No schema found in Iceberg metadata for %s.%s", namespace, table_name)
            return []

        # Extract fields
        fields = current_schema.get("fields", [])
        required_ids = set(current_schema.get("identifier-field-ids", []))
        col_list = []
        for field in fields:
            col_list.append({
                "column_name": field.get("name", ""),
                "data_type": str(field.get("type", "")),
                "required": field.get("required", False) or field.get("id") in required_ids,
            })
        return col_list

    except Exception as e:
        logger.warning(
            "Failed to read Iceberg metadata for %s.%s: %s — %s",
            namespace, table_name, type(e).__name__, _sanitize_error(str(e)),
        )
        return []


def _inspect_s3tables(bucket_name: str, namespace: str, catalog: str,
                      workgroup: str, output_location: str, region: str,
                      athena) -> str:
    """Inspect S3 Tables by reading Iceberg metadata JSON directly from S3.

    Uses get_table_metadata_location to find each table's metadata JSON,
    then parses the Iceberg schema for accurate field definitions.
    Falls back to Athena DESCRIBE if metadata read fails.
    """
    s3tables = boto3.client("s3tables", region_name=region)
    s3_client = boto3.client("s3", region_name=region)
    sts = boto3.client("sts", region_name=region)
    account_id = sts.get_caller_identity()["Account"]
    bucket_arn = f"arn:aws:s3tables:{region}:{account_id}:bucket/{bucket_name}"

    # 1. List tables in the namespace via S3 Tables API
    try:
        response = s3tables.list_tables(tableBucketARN=bucket_arn, namespace=namespace)
        s3t_tables = response.get("tables", [])
    except Exception as e:
        logger.error("S3 Tables list_tables failed: %s", _sanitize_error(str(e)))
        return _build_error("catalog_error", f"Failed to list tables in namespace '{namespace}': {type(e).__name__}")

    if not s3t_tables:
        return json.dumps({
            "success": True,
            "database": namespace,
            "catalog": catalog,
            "tables": [],
            "table_count": 0,
        })

    tables = []
    for s3t in s3t_tables:
        table_name = s3t.get("name", "")

        # 2. Get schema from Iceberg metadata JSON
        col_list = _get_table_schema(s3tables, s3_client, bucket_arn, namespace, table_name)

        if not col_list:
            logger.error(
                "Failed to read schema for %s.%s — no columns discovered. "
                "Check IAM permissions for s3tables:GetTableMetadataLocation and s3:GetObject.",
                namespace, table_name,
            )

        tables.append({
            "table_name": table_name,
            "columns": col_list,
            "column_count": len(col_list),
            "schema_error": None if col_list else f"Could not read Iceberg metadata for {namespace}.{table_name}",
            "primary_keys": [],
            "foreign_keys": [],
        })

    failed_tables = [t["table_name"] for t in tables if t.get("schema_error")]
    full_result = {
        "success": True,
        "database": namespace,
        "catalog": catalog,
        "tables": tables,
        "table_count": len(tables),
        "tables_with_schema_errors": failed_tables,
    }
    if failed_tables:
        logger.warning(
            "inspect_s3tables: %d of %d tables had schema read failures: %s",
            len(failed_tables), len(tables), ", ".join(failed_tables),
        )
    return _save_and_summarize(full_result, "s3tables", namespace=namespace)


def _inspect_glue_catalog(database: str, catalog: str, workgroup: str,
                          output_location: str, region: str, athena) -> str:
    """Inspect a standard Glue catalog using the Glue API and Athena."""
    glue = boto3.client("glue", region_name=region)

    tables_response = glue.get_tables(
        DatabaseName=database,
        CatalogId=boto3.client("sts").get_caller_identity()["Account"],
    )
    glue_tables = tables_response.get("TableList", [])

    if not glue_tables:
        return json.dumps({
            "success": True,
            "database": database,
            "catalog": catalog,
            "tables": [],
            "table_count": 0,
        })

    tables = []
    for gt in glue_tables:
        table_name = gt.get("Name", "")
        storage_descriptor = gt.get("StorageDescriptor", {})
        columns = storage_descriptor.get("Columns", [])

        col_list = []
        for col in columns:
            col_list.append({
                "column_name": col.get("Name", ""),
                "data_type": col.get("Type", ""),
                "comment": col.get("Comment"),
                "nullable": True,
            })

        for pk in gt.get("PartitionKeys", []):
            col_list.append({
                "column_name": pk.get("Name", ""),
                "data_type": pk.get("Type", ""),
                "comment": pk.get("Comment"),
                "nullable": False,
                "is_partition_key": True,
            })

        table_info = {
            "table_name": table_name,
            "columns": col_list,
            "primary_keys": [],
            "foreign_keys": [],
            "row_count": None,
            "sample_rows": [],
        }

        try:
            full_table = f'`{database}`.`{table_name}`'
            count_result = _run_athena_query(
                athena, f"SELECT COUNT(*) AS cnt FROM {full_table}",
                database, catalog, workgroup, output_location, region,
            )
            if count_result:
                table_info["row_count"] = int(count_result[0].get("cnt", 0))

            sample_result = _run_athena_query(
                athena, f"SELECT * FROM {full_table} LIMIT 5",
                database, catalog, workgroup, output_location, region,
            )
            if sample_result:
                table_info["sample_rows"] = sample_result
        except Exception as e:
            logger.warning("Athena query failed for %s.%s: %s", database, table_name, _sanitize_error(str(e)))

        tables.append(table_info)

    full_result = {
        "success": True,
        "database": database,
        "catalog": catalog,
        "tables": tables,
        "table_count": len(tables),
    }
    return _save_and_summarize(full_result, "glue")


def _run_athena_query_with_metadata(athena_client, query: str, database: str,
                                     catalog: str, workgroup: str,
                                     output_location: str, region: str,
                                     timeout: int = 30) -> dict:
    """Run an Athena query and return column name → data type mapping from result metadata.

    Returns:
        Dict mapping column_name to data_type, or empty dict on failure.
    """
    import time

    start_params = {
        "QueryString": query,
        "QueryExecutionContext": {"Database": database, "Catalog": catalog},
        "WorkGroup": workgroup,
    }
    if output_location:
        start_params["ResultConfiguration"] = {"OutputLocation": output_location}

    try:
        response = athena_client.start_query_execution(**start_params)
        execution_id = response["QueryExecutionId"]

        elapsed = 0.0
        delay = 0.5
        while elapsed < timeout:
            # Athena does not offer a blocking waiter; polling with backoff is required.
            time.sleep(delay)  # nosemgrep: arbitrary-sleep
            elapsed += delay
            status = athena_client.get_query_execution(QueryExecutionId=execution_id)
            state = status["QueryExecution"]["Status"]["State"]
            if state == "SUCCEEDED":
                break
            elif state in ("FAILED", "CANCELLED"):
                return {}
            delay = min(delay * 1.5, 3.0)
        else:
            return {}

        results = athena_client.get_query_results(QueryExecutionId=execution_id)
        column_info = results.get("ResultSet", {}).get("ResultSetMetadata", {}).get("ColumnInfo", [])
        return {c.get("Name", ""): c.get("Type", "") for c in column_info}
    except Exception:
        return {}


def _run_athena_query(athena_client, query: str, database: str, catalog: str,
                      workgroup: str, output_location: str, region: str,
                      timeout: int = 30) -> list[dict]:
    """Run a simple Athena query and return parsed rows.

    Args:
        athena_client: boto3 Athena client.
        query: SQL query to execute.
        database: Glue database name.
        catalog: Glue catalog name.
        workgroup: Athena workgroup.
        output_location: S3 output location (empty = workgroup default).
        region: AWS region.
        timeout: Max seconds to wait for query completion.

    Returns:
        List of row dicts, or empty list on failure.
    """
    import time

    start_params = {
        "QueryString": query,
        "QueryExecutionContext": {
            "Database": database,
            "Catalog": catalog,
        },
        "WorkGroup": workgroup,
    }
    if output_location:
        start_params["ResultConfiguration"] = {"OutputLocation": output_location}

    response = athena_client.start_query_execution(**start_params)
    execution_id = response["QueryExecutionId"]

    # Poll for completion
    elapsed = 0.0
    delay = 0.5
    while elapsed < timeout:
        # Athena does not offer a blocking waiter; polling with backoff is required.
        time.sleep(delay)  # nosemgrep: arbitrary-sleep
        elapsed += delay
        status = athena_client.get_query_execution(QueryExecutionId=execution_id)
        state = status["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = status.get("QueryExecution", {}).get("Status", {}).get("StateChangeReason", "")
            logger.warning("Athena query %s: %s — %s", state, query[:80], reason)
            return []
        delay = min(delay * 1.5, 3.0)
    else:
        return []

    # Fetch results
    results = athena_client.get_query_results(QueryExecutionId=execution_id)
    result_set = results.get("ResultSet", {})
    column_info = result_set.get("ResultSetMetadata", {}).get("ColumnInfo", [])
    columns = [c.get("Name", f"col_{i}") for i, c in enumerate(column_info)]

    rows = []
    raw_rows = result_set.get("Rows", [])
    for raw_row in raw_rows[1:]:  # Skip header
        data = raw_row.get("Data", [])
        row = {}
        for i, datum in enumerate(data):
            col_name = columns[i] if i < len(columns) else f"col_{i}"
            row[col_name] = datum.get("VarCharValue")
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# AWS IoT SiteWise inspection
# ---------------------------------------------------------------------------
#
# SiteWise has no standalone "schema" — instead it models industrial assets as
# a hierarchy of *asset models* (templates) and *assets* (instances). Each asset
# model defines *properties* of four kinds:
#   - measurement: raw telemetry from a device (e.g. Temperature_PV)
#   - attribute:   static configuration (e.g. Name, serial number)
#   - transform:   a formula over other properties
#   - metric:      a time-windowed aggregate (e.g. avg temperature/hr)
#
# We map this onto the discovery pipeline's table/column model by treating each
# asset model as a "table" and each property as a "column". The 6-phase pipeline
# then maps every property to a canonical ISA-95 concept (most land in the `iot`,
# `equipment`, and `physical-asset` domains) and registers it in the knowledge
# graph, so the Explorer can later query SiteWise telemetry via the `sitewise`
# protocol backend in query_system.

# SiteWise asset-model property "kind" — describes where the value comes from.
_SITEWISE_PROPERTY_KINDS = ("measurement", "attribute", "transform", "metric")


def _sitewise_property_kind(prop_type: dict) -> str:
    """Return the SiteWise property kind (measurement/attribute/transform/metric)."""
    if not isinstance(prop_type, dict):
        return ""
    for kind in _SITEWISE_PROPERTY_KINDS:
        if kind in prop_type:
            return kind
    return ""


def _sitewise_model_columns(model_properties: list) -> list[dict]:
    """Convert SiteWise asset-model properties into inspect 'columns'.

    Each property becomes a column with its data type, unit, the SiteWise
    property kind, and the property_id needed to query telemetry later.
    """
    columns = []
    for prop in model_properties:
        kind = _sitewise_property_kind(prop.get("type", {}))
        columns.append({
            "column_name": prop.get("name", ""),
            "data_type": prop.get("dataType", ""),
            "property_id": prop.get("id", ""),
            "property_kind": kind,
            "unit": prop.get("unit"),
            # Attributes are static config; measurements/metrics/transforms are
            # time-series telemetry. None are NOT NULL in a relational sense.
            "nullable": True,
        })
    return columns


def _sitewise_list_model_assets(client, asset_model_id: str, max_assets: int = 250) -> list[dict]:
    """List asset instances for an asset model (name + id), bounded by max_assets."""
    instances = []
    next_token = None
    while True:
        kwargs = {"assetModelId": asset_model_id, "maxResults": 250}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = client.list_assets(**kwargs)
        for a in resp.get("assetSummaries", []):
            instances.append({"name": a.get("name", ""), "asset_id": a.get("id", "")})
            if len(instances) >= max_assets:
                return instances
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return instances


def _sitewise_build_hierarchy(client, max_nodes: int = 200) -> list[dict]:
    """Traverse the SiteWise asset hierarchy from the top-level assets.

    Returns a compact nested tree of {name, asset_id, model_name, children}.
    Bounded by max_nodes and a max depth to stay safe on large deployments.
    This is captured purely as ISA-95 context for the analysis/memory phases —
    it does not affect field-to-concept mapping.
    """
    tree: list[dict] = []
    try:
        top = client.list_assets(filter="TOP_LEVEL", maxResults=50).get("assetSummaries", [])
    except Exception as e:
        logger.warning("SiteWise hierarchy: list_assets(TOP_LEVEL) failed: %s", _sanitize_error(str(e)))
        return tree

    visited = {"count": 0}

    def _walk(summary: dict, depth: int):
        if visited["count"] >= max_nodes or depth > 6:
            return None
        visited["count"] += 1
        node = {
            "name": summary.get("name", ""),
            "asset_id": summary.get("id", ""),
            "children": [],
        }
        for h in summary.get("hierarchies", []):
            hierarchy_id = h.get("id")
            if not hierarchy_id:
                continue
            try:
                kids = client.list_associated_assets(
                    assetId=summary.get("id"),
                    hierarchyId=hierarchy_id,
                    maxResults=100,
                ).get("assetSummaries", [])
            except Exception as e:
                logger.warning("SiteWise hierarchy: list_associated_assets failed: %s", _sanitize_error(str(e)))
                kids = []
            for k in kids:
                child = _walk(k, depth + 1)
                if child:
                    node["children"].append(child)
        return node

    for t in top:
        node = _walk(t, 0)
        if node:
            tree.append(node)
    return tree


@tool
def inspect_sitewise_assets(region: str = "", include_hierarchy: bool = True) -> str:
    """Inspect an AWS IoT SiteWise deployment (asset models, assets, properties).

    Treats each SiteWise *asset model* as a table and its *properties*
    (measurements, attributes, transforms, metrics) as columns, so the standard
    6-phase discovery pipeline can map them to canonical manufacturing concepts
    and register them in the knowledge graph. Used by the Discovery Agent during
    Phase 1 (INSPECT) to catalog an IoT/SCADA data source.

    The SiteWise deployment may live in a different region than the UKG platform
    (e.g. the platform in us-east-1 but SiteWise in us-east-2). Pass `region`
    explicitly to point at the SiteWise account region; it is persisted in the
    system's connection_config so the Explorer queries the correct region.

    Args:
        region: AWS region of the SiteWise deployment (e.g. "us-east-2"). If
            empty, falls back to SITEWISE_REGION then AWS_REGION.
        include_hierarchy: Whether to capture the ISA-95 asset hierarchy tree as
            additional context (default True).

    Returns:
        JSON summary with one "table" per asset model (columns = properties,
        row_count = number of asset instances). Full data is saved to DDB for
        subsequent phases. On failure, returns structured error JSON.
    """
    sitewise_region = region or os.getenv("SITEWISE_REGION") or os.getenv("AWS_REGION", "us-east-1")

    try:
        client = boto3.client("iotsitewise", region_name=sitewise_region)

        # 1. List all asset models (templates)
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

        if not models:
            full_result = {
                "success": True,
                "source_type": "sitewise",
                "region": sitewise_region,
                "tables": [],
                "table_count": 0,
            }
            return _save_and_summarize(full_result, "sitewise")

        # 2. For each asset model, get its properties (columns) + asset instances
        tables = []
        for m in models:
            model_id = m.get("id")
            model_name = m.get("name", "")
            try:
                detail = client.describe_asset_model(assetModelId=model_id)
            except Exception as e:
                logger.warning(
                    "describe_asset_model failed for '%s': %s",
                    model_name, _sanitize_error(str(e)),
                )
                continue

            columns = _sitewise_model_columns(detail.get("assetModelProperties", []))
            instances = _sitewise_list_model_assets(client, model_id)

            tables.append({
                "table_name": model_name,
                "asset_model_id": model_id,
                "asset_model_description": (m.get("description") or "")[:500],
                "columns": columns,
                "column_count": len(columns),
                "primary_keys": [],
                "foreign_keys": [],
                "row_count": len(instances),
                # Bounded sample of instances — the Explorer resolves names to
                # IDs dynamically at query time, so this is context, not config.
                "asset_instances": instances[:50],
            })

        full_result = {
            "success": True,
            "source_type": "sitewise",
            "region": sitewise_region,
            "tables": tables,
            "table_count": len(tables),
        }
        if include_hierarchy:
            full_result["hierarchy"] = _sitewise_build_hierarchy(client)

        return _save_and_summarize(full_result, "sitewise")

    except Exception as e:
        logger.error(
            "inspect_sitewise_assets failed (region=%s): %s — %s",
            sitewise_region, type(e).__name__, _sanitize_error(str(e)),
        )
        return _build_error("connection_error", str(e))
