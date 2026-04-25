#!/bin/bash
# Seed all Digital Thread demo data after CDK deployment.
#
# Usage:
#   ./data/seed/seed-all.sh --region us-east-1
#   ./data/seed/seed-all.sh --region us-east-1 --skip-s3tables

set -e

REGION="us-east-1"
SKIP_S3TABLES=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --region)        REGION="$2"; shift 2 ;;
        --skip-s3tables) SKIP_S3TABLES=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Digital Thread - Seed All Data ==="
echo "Region: $REGION"
echo ""

echo "--- Seeding prompt templates ---"
python3 "$SCRIPT_DIR/seed_prompt_templates.py" --region "$REGION"
echo ""

echo "--- Seeding app settings ---"
python3 "$SCRIPT_DIR/seed_app_settings.py" --region "$REGION"
echo ""

if [ "$SKIP_S3TABLES" = false ]; then
    echo "--- Seeding S3 Tables manufacturing data ---"
    python3 "$ROOT_DIR/data/s3tables/seed_s3tables.py" --region "$REGION"
    echo ""
else
    echo "--- Skipping S3 Tables (--skip-s3tables) ---"
    echo ""
fi

echo "=== Seeding complete ==="
