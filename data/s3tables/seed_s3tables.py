#!/usr/bin/env python3
"""
Seed S3 Tables with simulated manufacturing data using PyIceberg.

S3 Tables uses Apache Iceberg format. We use PyIceberg to write data
directly to the tables via the S3 Tables catalog.

Usage:
    pip install pyiceberg[s3tables] pyarrow boto3
    AWS_REGION=us-east-2 python3 data/s3tables/seed_s3tables.py --region us-east-2

Prerequisites:
    - S3 Tables bucket must exist (deployed via CDK)
    - Namespaces must exist (created by this script)
    - AWS credentials with s3tables:* permissions
"""

import argparse
import json
import os
import sys
import boto3
import pyarrow as pa
from datetime import date, datetime

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def get_bucket_name(region: str) -> str:
    app_name = os.getenv("APP_NAME", "mfg-ukg")
    secret_name = f"{app_name}/appconfig"
    client = boto3.client("secretsmanager", region_name=region)
    secret = json.loads(client.get_secret_value(SecretId=secret_name)["SecretString"])
    bucket = secret.get("s3tables_bucket_name")
    if not bucket:
        raise ValueError("s3tables_bucket_name not found in secret")
    return bucket


def get_account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def get_catalog(bucket_name: str, region: str):
    """Get PyIceberg catalog for S3 Tables."""
    from pyiceberg.catalog.rest import RestCatalog
    
    account_id = get_account_id()
    catalog_endpoint = f"https://s3tables.{region}.amazonaws.com/iceberg"
    
    return RestCatalog(
        name="s3tables",
        **{
            "uri": catalog_endpoint,
            "rest.sigv4-enabled": "true",
            "rest.signing-name": "s3tables",
            "rest.signing-region": region,
            "warehouse": f"arn:aws:s3tables:{region}:{account_id}:bucket/{bucket_name}",
        }
    )


def ensure_namespace(catalog, namespace: str) -> None:
    try:
        catalog.create_namespace(namespace)
        print(f"  Created namespace: {namespace}")
    except Exception as e:
        if "already exists" in str(e).lower() or "AlreadyExistsException" in str(type(e).__name__):
            print(f"  Namespace exists: {namespace}")
        else:
            raise


def write_table(catalog, namespace: str, table_name: str, schema: pa.Schema, data: pa.Table, dry_run: bool = False) -> None:
    """Create table if not exists and write data."""
    full_name = f"{namespace}.{table_name}"
    
    if dry_run:
        print(f"  [DRY RUN] Would write {len(data)} rows to {full_name}")
        return
    
    try:
        # Create table
        table = catalog.create_table(
            identifier=full_name,
            schema=schema,
        )
        print(f"  Created table: {full_name}")
    except Exception as e:
        if "already exists" in str(e).lower():
            table = catalog.load_table(full_name)
            print(f"  Table exists: {full_name}")
        else:
            raise
    
    # Write data
    table.overwrite(data)
    print(f"  Wrote {len(data)} rows to {full_name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    global AWS_REGION
    AWS_REGION = args.region

    bucket_name = get_bucket_name(args.region)
    print(f"S3 Tables bucket: {bucket_name}")
    print(f"Region: {args.region}")

    catalog = get_catalog(bucket_name, args.region)

    # ---- Create namespaces ----
    print("\nCreating namespaces...")
    for ns in ["erp", "mes", "cmms", "plm", "iot"]:
        ensure_namespace(catalog, ns)

    print("\nCreating tables and writing data...")

    # ---- ERP: SAP Indianapolis ----
    sap_schema = pa.schema([
        pa.field("aufnr", pa.string()),
        pa.field("auart", pa.string()),
        pa.field("matnr", pa.string()),
        pa.field("gamng", pa.float64()),
        pa.field("gstrp", pa.date32()),
        pa.field("gltrp", pa.date32()),
        pa.field("status", pa.string()),
        pa.field("plant", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    sap_data = pa.table({
        "aufnr": ["4521", "4522", "4523", "4524", "4525"],
        "auart": ["PP01"] * 5,
        "matnr": ["000000000000882", "000000000000883", "000000000000882", "000000000000884", "000000000000882"],
        "gamng": [500.0, 200.0, 300.0, 150.0, 400.0],
        "gstrp": [date(2026, 2, 10), date(2026, 2, 12), date(2026, 2, 14), date(2026, 2, 16), date(2026, 2, 18)],
        "gltrp": [date(2026, 2, 18), date(2026, 2, 20), date(2026, 2, 22), date(2026, 2, 25), date(2026, 2, 28)],
        "status": ["DELAYED", "RELEASED", "RELEASED", "RELEASED", "CREATED"],
        "plant": ["Indianapolis"] * 5,
        "system_name": ["SAP S/4HANA"] * 5,
    }, schema=sap_schema)
    write_table(catalog, "erp", "sap_indianapolis", sap_schema, sap_data, args.dry_run)

    # ---- ERP: Oracle Pune ----
    oracle_schema = pa.schema([
        pa.field("wip_entity_name", pa.string()),
        pa.field("primary_item_id", pa.int32()),
        pa.field("start_quantity", pa.float64()),
        pa.field("date_released", pa.date32()),
        pa.field("date_completed", pa.date32()),
        pa.field("status_type", pa.int32()),
        pa.field("status_label", pa.string()),
        pa.field("plant", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    oracle_data = pa.table({
        "wip_entity_name": ["WO-PUN-2026-0341", "WO-PUN-2026-0342", "WO-PUN-2026-0355", "WO-PUN-2026-0360"],
        "primary_item_id": [44821, 44821, 44822, 44821],
        "start_quantity": [400.0, 350.0, 600.0, 500.0],
        "date_released": [date(2026, 1, 15), date(2026, 1, 20), date(2026, 2, 1), date(2026, 2, 10)],
        "date_completed": [date(2026, 1, 28), None, None, None],
        "status_type": [4, 12, 3, 3],
        "status_label": ["COMPLETE", "DELAYED", "RELEASED", "RELEASED"],
        "plant": ["Pune"] * 4,
        "system_name": ["Oracle EBS"] * 4,
    }, schema=oracle_schema)
    write_table(catalog, "erp", "oracle_pune", oracle_schema, oracle_data, args.dry_run)

    # ---- ERP: Custom Monterrey ----
    mty_schema = pa.schema([
        pa.field("numero_orden", pa.string()),
        pa.field("producto_id", pa.string()),
        pa.field("cantidad_planificada", pa.int32()),
        pa.field("fecha_inicio", pa.date32()),
        pa.field("fecha_fin", pa.date32()),
        pa.field("estado", pa.string()),
        pa.field("status_english", pa.string()),
        pa.field("plant", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    mty_data = pa.table({
        "numero_orden": ["OT-MTY-2026-0101", "OT-MTY-2026-0102", "OT-MTY-2026-0103", "OT-MTY-2026-0104"],
        "producto_id": ["mat_882", "mat_883", "mat_882", "mat_884"],
        "cantidad_planificada": [250, 150, 300, 100],
        "fecha_inicio": [date(2026, 2, 5), date(2026, 2, 10), date(2026, 2, 12), date(2026, 2, 15)],
        "fecha_fin": [date(2026, 2, 15), None, None, None],
        "estado": ["COMPLETADO", "RETRASADO", "EN_PROCESO", "PENDIENTE"],
        "status_english": ["COMPLETE", "DELAYED", "IN_PROGRESS", "PENDING"],
        "plant": ["Monterrey"] * 4,
        "system_name": ["Custom ERP"] * 4,
    }, schema=mty_schema)
    write_table(catalog, "erp", "custom_monterrey", mty_schema, mty_data, args.dry_run)

    # ---- MES: Production Runs ----
    runs_schema = pa.schema([
        pa.field("run_id", pa.int32()),
        pa.field("order_number", pa.string()),
        pa.field("line", pa.string()),
        pa.field("machine", pa.string()),
        pa.field("planned_qty", pa.int32()),
        pa.field("completed_qty", pa.int32()),
        pa.field("scrap_qty", pa.int32()),
        pa.field("scrap_rate_pct", pa.float64()),
        pa.field("start_time", pa.timestamp("us")),
        pa.field("end_time", pa.timestamp("us")),
        pa.field("status", pa.string()),
        pa.field("plant", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    runs_data = pa.table({
        "run_id": [1, 2, 3],
        "order_number": ["4521", "4522", "4523"],
        "line": ["Line 3", "Line 3", "Line 3"],
        "machine": ["CMX-441", "CMX-442", "CMX-442"],
        "planned_qty": [500, 200, 300],
        "completed_qty": [340, 200, 180],
        "scrap_qty": [12, 3, 5],
        "scrap_rate_pct": [2.4, 1.5, 1.7],
        "start_time": [datetime(2026, 2, 10, 6, 0), datetime(2026, 2, 12, 6, 0), datetime(2026, 2, 16, 6, 0)],
        "end_time": [datetime(2026, 2, 16, 14, 30), datetime(2026, 2, 15, 18, 0), None],
        "status": ["STOPPED", "COMPLETED", "RUNNING"],
        "plant": ["Indianapolis"] * 3,
        "system_name": ["Ignition MES"] * 3,
    }, schema=runs_schema)
    write_table(catalog, "mes", "ignition_indianapolis", runs_schema, runs_data, args.dry_run)

    # ---- MES: OEE Daily ----
    oee_schema = pa.schema([
        pa.field("line", pa.string()),
        pa.field("oee_date", pa.date32()),
        pa.field("oee", pa.float64()),
        pa.field("availability", pa.float64()),
        pa.field("performance", pa.float64()),
        pa.field("quality", pa.float64()),
        pa.field("plant", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    oee_data = pa.table({
        "line": ["Line 3"] * 6,
        "oee_date": [date(2026, 2, 13), date(2026, 2, 14), date(2026, 2, 15), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18)],
        "oee": [82.1, 78.5, 72.1, 34.2, 0.0, 65.3],
        "availability": [90.0, 85.0, 80.0, 42.0, 0.0, 75.0],
        "performance": [94.0, 95.0, 93.0, 88.0, 0.0, 90.0],
        "quality": [97.0, 97.2, 96.9, 92.5, 0.0, 96.8],
        "plant": ["Indianapolis"] * 6,
        "system_name": ["Ignition MES"] * 6,
    }, schema=oee_schema)
    write_table(catalog, "mes", "oee_daily", oee_schema, oee_data, args.dry_run)

    # ---- MES: Machine Status ----
    status_schema = pa.schema([
        pa.field("machine", pa.string()),
        pa.field("line", pa.string()),
        pa.field("status", pa.string()),
        pa.field("last_update", pa.timestamp("us")),
        pa.field("reason", pa.string()),
        pa.field("plant", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    status_data = pa.table({
        "machine": ["CMX-441", "CMX-442", "CTX-310"],
        "line": ["Line 3", "Line 3", "Line 2"],
        "status": ["DOWN", "RUNNING", "RUNNING"],
        "last_update": [datetime(2026, 2, 16, 14, 30), datetime(2026, 2, 18, 8, 0), datetime(2026, 2, 18, 8, 0)],
        "reason": ["Unplanned maintenance - bearing failure", None, None],
        "plant": ["Indianapolis"] * 3,
        "system_name": ["Ignition MES"] * 3,
    }, schema=status_schema)
    write_table(catalog, "mes", "machine_status", status_schema, status_data, args.dry_run)

    # ---- CMMS: Work Orders ----
    wo_schema = pa.schema([
        pa.field("wonum", pa.string()),
        pa.field("assetnum", pa.string()),
        pa.field("description", pa.string()),
        pa.field("status", pa.string()),
        pa.field("reportdate", pa.timestamp("us")),
        pa.field("schedstart", pa.timestamp("us")),
        pa.field("actfinish", pa.timestamp("us")),
        pa.field("worktype", pa.string()),
        pa.field("worktype_label", pa.string()),
        pa.field("failurecode", pa.string()),
        pa.field("plant", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    wo_data = pa.table({
        "wonum": ["WO-2026-0891", "WO-2026-0885", "WO-2026-0878"],
        "assetnum": ["CMX-441-IND", "CMX-441-IND", "CMX-442-IND"],
        "description": ["Unplanned - bearing failure spindle assembly", "Scheduled PM - spindle lubrication", "Scheduled PM - coolant system flush"],
        "status": ["COMP", "COMP", "COMP"],
        "reportdate": [datetime(2026, 2, 16, 14, 45), datetime(2026, 2, 1, 8, 0), datetime(2026, 1, 28, 8, 0)],
        "schedstart": [datetime(2026, 2, 16, 15, 0), datetime(2026, 2, 3, 6, 0), datetime(2026, 1, 30, 6, 0)],
        "actfinish": [datetime(2026, 2, 17, 22, 0), datetime(2026, 2, 3, 10, 0), datetime(2026, 1, 30, 9, 0)],
        "worktype": ["EM", "PM", "PM"],
        "worktype_label": ["Emergency/Corrective", "Preventive", "Preventive"],
        "failurecode": ["BEARING", None, None],
        "plant": ["Indianapolis"] * 3,
        "system_name": ["IBM Maximo"] * 3,
    }, schema=wo_schema)
    write_table(catalog, "cmms", "maximo_indianapolis", wo_schema, wo_data, args.dry_run)

    # ---- CMMS: Assets ----
    asset_schema = pa.schema([
        pa.field("assetnum", pa.string()),
        pa.field("description", pa.string()),
        pa.field("location", pa.string()),
        pa.field("status", pa.string()),
        pa.field("manufacturer", pa.string()),
        pa.field("serialnum", pa.string()),
        pa.field("plant", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    asset_data = pa.table({
        "assetnum": ["CMX-441-IND", "CMX-442-IND", "CTX-310-IND"],
        "description": ["CMX-441 5-axis Milling Center", "CMX-442 5-axis Milling Center", "CTX-310 CNC Turning Center"],
        "location": ["Line 3", "Line 3", "Line 2"],
        "status": ["OPERATING", "OPERATING", "OPERATING"],
        "manufacturer": ["DMG Mori", "DMG Mori", "DMG Mori"],
        "serialnum": ["CMX441-2019-0087", "CMX442-2020-0112", "CTX310-2018-0045"],
        "plant": ["Indianapolis"] * 3,
        "system_name": ["IBM Maximo"] * 3,
    }, schema=asset_schema)
    write_table(catalog, "cmms", "asset", asset_schema, asset_data, args.dry_run)

    # ---- PLM: BOM Items ----
    bom_schema = pa.schema([
        pa.field("part_number", pa.string()),
        pa.field("parent_part", pa.string()),
        pa.field("quantity", pa.float64()),
        pa.field("unit", pa.string()),
        pa.field("description", pa.string()),
        pa.field("revision", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    bom_data = pa.table({
        "part_number": ["SKU-882", "BEARING-001", "GEAR-SHAFT-01", "HOUSING-001", "SEAL-KIT-01"],
        "parent_part": [None, "SKU-882", "SKU-882", "SKU-882", "SKU-882"],
        "quantity": [1.0, 2.0, 1.0, 1.0, 1.0],
        "unit": ["EA", "EA", "EA", "EA", "KIT"],
        "description": ["Precision Gear Assembly", "Spindle Bearing", "Main Gear Shaft", "Gear Housing", "Seal Kit"],
        "revision": ["C", "B", "A", "B", "A"],
        "system_name": ["Teamcenter PLM"] * 5,
    }, schema=bom_schema)
    write_table(catalog, "plm", "bom_items", bom_schema, bom_data, args.dry_run)

    # ---- PLM: Part Specs ----
    parts_schema = pa.schema([
        pa.field("part_number", pa.string()),
        pa.field("name", pa.string()),
        pa.field("material_type", pa.string()),
        pa.field("weight_kg", pa.float64()),
        pa.field("drawing_number", pa.string()),
        pa.field("revision", pa.string()),
        pa.field("system_name", pa.string()),
    ])
    parts_data = pa.table({
        "part_number": ["SKU-882", "BEARING-001", "GEAR-SHAFT-01"],
        "name": ["Precision Gear Assembly", "Spindle Bearing", "Main Gear Shaft"],
        "material_type": ["Alloy Steel", "Bearing Steel", "Alloy Steel"],
        "weight_kg": [4.2, 0.3, 1.8],
        "drawing_number": ["DWG-882-C", "DWG-BEAR-001-B", "DWG-GS-001-A"],
        "revision": ["C", "B", "A"],
        "system_name": ["Teamcenter PLM"] * 3,
    }, schema=parts_schema)
    write_table(catalog, "plm", "part_specs", parts_schema, parts_data, args.dry_run)

    print("\nSeeding complete.")


if __name__ == "__main__":
    main()
