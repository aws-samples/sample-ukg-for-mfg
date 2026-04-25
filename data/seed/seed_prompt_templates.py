#!/usr/bin/env python3
"""
Seed DynamoDB prompt templates for Digital Thread demo scenarios.

Usage:
    python data/seed/seed_prompt_templates.py --region us-east-1
"""

import argparse
import os
from datetime import datetime, timezone
import boto3

TEMPLATES = [
    {
        "template_id": "dt-agent-capabilities",
        "title": "🤖 Agent Capabilities",
        "description": "Discover how the agent can help",
        "prompt_detail": "How can you help me?",
    },
    {
        "template_id": "dt-systems-overview",
        "title": "🏭 Systems Overview",
        "description": "Discover what manufacturing systems are connected",
        "prompt_detail": "What manufacturing systems are connected to the digital thread?",
    },
    # {
    #     "template_id": "dt-batch-quality",
    #     "title": "🧐 Batch Quality",
    #     "description": "Analyze BAT-000001 quality",
    #     "prompt_detail": "What's the full quality picture for batch BAT-000001?",
    # },
    # {
    #     "template_id": "dt-correlations",
    #     "title": "📊 Learned Correlations",
    #     "description": "Retrieve patterns discovered across manufacturing systems",
    #     "prompt_detail": "Are there any patterns related to equipment failures?",
    # },
    # {
    #     "template_id": "dt-failure-impact",
    #     "title": "⚠️ Failure Impact Analysis",
    #     "description": "Trace how equipment failures ripple into production orders",
    #     "prompt_detail": "How did equipment failures on FUEL-INJ-01-SITE-001 affect production for ORD-000001?",
    # },
    # {
    #     "template_id": "dt-equipment-health-oee",
    #     "title": "📈 Equipment Health & OEE",
    #     "description": "Assess current health, OEE metrics, and performance for an asset",
    #     "prompt_detail": "What does the current health and OEE data say about the Balancing Machine EQ-10001?",
    # },
    # {
    #     "template_id": "dt-order-lifecycle",
    #     "title": "🔄 Order Lifecycle Trace",
    #     "description": "Trace a production order from design through delivery",
    #     "prompt_detail": "Trace the full lifecycle of ORD-000003 from design to delivery.",
    # },
    # {
    #     "template_id": "dt-reliability-risk",
    #     "title": "🛡️ Reliability Risk Assessment",
    #     "description": "Evaluate whether an asset is a reliability risk from maintenance and production data",
    #     "prompt_detail": "Is LEAK-TES-02-SITE-003 a reliability risk based on its maintenance and production history?",
    # },
    # {
    #     "template_id": "dt-downtime-history",
    #     "title": "🔧 Downtime & Production Impact",
    #     "description": "Review maintenance and downtime history with production impact",
    #     "prompt_detail": "What's the maintenance and downtime history for FUEL-INJ-01-SITE-001 and its production impact?",
    # },
    # {
    #     "template_id": "dt-station-health",
    #     "title": "🩺 Station Health Check",
    #     "description": "Real-time health assessment from OEE, alarms, and sensor data",
    #     "prompt_detail": "How healthy is the Leak Test Station at SITE-001 right now based on OEE, alarms, and sensor data?",
    # },
    # {
    #     "template_id": "dt-cost-quality",
    #     "title": "💰 Cost & Quality Analysis",
    #     "description": "Investigate cost overruns and production quality for an order",
    #     "prompt_detail": "Why did ORD-000003 have a cost overrun and what was the production quality like?",
    # },
    # {
    #     "template_id": "dt-eco-validation",
    #     "title": "📋 ECO Validation",
    #     "description": "Check if engineering change orders are backed by production issues",
    #     "prompt_detail": "Are the open engineering change orders for ITM-00013 and ITM-00016 backed by quality or performance issues in production?",
    # },
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default=os.getenv("PROMPT_TEMPLATES_TABLE", "mfg-thread-prompt-templates"))
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    dynamodb = boto3.resource("dynamodb", region_name=args.region)
    table = dynamodb.Table(args.table)
    now = datetime.now(timezone.utc).isoformat()

    print(f"Seeding {len(TEMPLATES)} templates into {args.table}...")
    for idx, tmpl in enumerate(TEMPLATES):
        table.put_item(Item={**tmpl, "sort_order": idx, "created_at": now, "updated_at": now})
        print(f"  ✓ [{idx}] {tmpl['title']}")
    print("Done.")


if __name__ == "__main__":
    main()
