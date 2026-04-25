#!/usr/bin/env python3
"""
Seed DynamoDB app settings with Digital Thread branding.

Usage:
    python data/seed/seed_app_settings.py --region us-east-1
"""

import argparse
import os
from datetime import datetime, timezone
import boto3

SETTINGS = [
    {"setting_key": "app_title",    "setting_value": "Manufacturing Digital Thread",         "setting_type": "text",  "description": "Application title"},
    {"setting_key": "app_subtitle", "setting_value": "AI-Powered Universal Knowledge Graph on AWS",  "setting_type": "text",  "description": "Application subtitle"},
    {"setting_key": "logo_url",     "setting_value": "/static/favicon.svg",                  "setting_type": "image", "description": "Header logo"},
    {"setting_key": "chat_logo_url","setting_value": "/static/chat-placeholder.svg",         "setting_type": "image", "description": "Chat placeholder logo"},
    {"setting_key": "primary_color","setting_value": "#0f4c81",                              "setting_type": "color", "description": "Primary brand color (manufacturing blue)"},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default=os.getenv("APP_SETTINGS_TABLE", "mfg-thread-app-settings"))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(args.table)
    now = datetime.now(timezone.utc).isoformat()

    print(f"Seeding {len(SETTINGS)} settings into {args.table}...")
    for setting in SETTINGS:
        table.put_item(Item={**setting, "updated_at": now})
        print(f"  ✓ {setting['setting_key']}")
    print("Done.")


if __name__ == "__main__":
    main()
