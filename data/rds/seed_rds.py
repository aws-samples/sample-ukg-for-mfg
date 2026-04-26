#!/usr/bin/env python3
"""
Seed the RDS Aurora PostgreSQL database with simulated manufacturing data.

Reads credentials from Secrets Manager and executes seed_manufacturing.sql
via the RDS Data API.

Usage:
    python data/rds/seed_rds.py --region us-east-1
"""

import argparse
import json
import os
import sys
import boto3


def get_rds_config(region: str) -> dict:
    app_name = os.getenv("APP_NAME", "mfg-ukg")
    secret_name = f"{app_name}/appconfig"
    client = boto3.client("secretsmanager", region_name=region)
    secret = json.loads(client.get_secret_value(SecretId=secret_name)["SecretString"])
    cluster_arn = secret.get("rds_cluster_arn")
    secret_arn = secret.get("rds_secret_arn")
    if not cluster_arn or not secret_arn:
        raise ValueError("rds_cluster_arn or rds_secret_arn not found in secret")
    return {"cluster_arn": cluster_arn, "secret_arn": secret_arn}


def execute_sql(client, cluster_arn: str, secret_arn: str, sql: str) -> None:
    client.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database="postgres",
        sql=sql,
    )


def split_statements(sql: str) -> list[str]:
    """Split SQL file into individual statements on semicolons."""
    # Remove comment lines
    lines = [l for l in sql.splitlines() if not l.strip().startswith("--")]
    content = "\n".join(lines)
    statements = [s.strip() for s in content.split(";") if s.strip()]
    return statements


def main():
    parser = argparse.ArgumentParser(description="Seed RDS with manufacturing data")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument(
        "--sql-file",
        default=os.path.join(os.path.dirname(__file__), "seed_manufacturing.sql"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Reading {args.sql_file}...")
    with open(args.sql_file) as f:
        sql_content = f.read()

    statements = split_statements(sql_content)
    print(f"Found {len(statements)} statements")

    if args.dry_run:
        for i, stmt in enumerate(statements, 1):
            print(f"[{i:3d}] {stmt[:100]}...")
        print("\nDry run complete.")
        return

    cfg = get_rds_config(args.region)
    client = boto3.client("rds-data", region_name=args.region)

    success = errors = 0
    for i, stmt in enumerate(statements, 1):
        preview = stmt[:80].replace("\n", " ")
        try:
            execute_sql(client, cfg["cluster_arn"], cfg["secret_arn"], stmt)
            print(f"  [{i:3d}/{len(statements)}] ✓  {preview}...")
            success += 1
        except Exception as e:
            print(f"  [{i:3d}/{len(statements)}] ✗  {preview}...")
            print(f"         Error: {e}")
            errors += 1

    print(f"\nDone: {success} succeeded, {errors} failed")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
