#!/usr/bin/env bash
# cleanup-s3tables.sh — Delete all tables and namespaces from an S3 Tables bucket.
#
# Usage:
#   ./scripts/cleanup-s3tables.sh <bucket-name> [--region us-east-1] [--yes]
#
# Examples:
#   ./scripts/cleanup-s3tables.sh ukg-data
#   ./scripts/cleanup-s3tables.sh ukg-data --region us-east-2 --yes

set -euo pipefail

BUCKET=""
REGION="us-east-1"
AUTO_YES=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --region) REGION="$2"; shift 2 ;;
        --yes|-y) AUTO_YES=true; shift ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *) BUCKET="$1"; shift ;;
    esac
done

if [[ -z "$BUCKET" ]]; then
    echo "Usage: $0 <bucket-name> [--region us-east-1] [--yes]"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --no-cli-pager)
BUCKET_ARN="arn:aws:s3tables:${REGION}:${ACCOUNT_ID}:bucket/${BUCKET}"

echo "Bucket ARN: ${BUCKET_ARN}"
echo ""

# List all namespaces
NAMESPACES=$(aws s3tables list-namespaces \
    --table-bucket-arn "$BUCKET_ARN" \
    --region "$REGION" \
    --query 'namespaces[].namespace[0]' \
    --output text \
    --no-cli-pager 2>/dev/null || echo "")

if [[ -z "$NAMESPACES" || "$NAMESPACES" == "None" ]]; then
    echo "No namespaces found in bucket ${BUCKET}. Nothing to clean up."
    exit 0
fi

# Preview: count tables per namespace
TOTAL_TABLES=0
for NS in $NAMESPACES; do
    TABLES=$(aws s3tables list-tables \
        --table-bucket-arn "$BUCKET_ARN" \
        --namespace "$NS" \
        --region "$REGION" \
        --query 'tables[].name' \
        --output text \
        --no-cli-pager 2>/dev/null || echo "")
    COUNT=0
    if [[ -n "$TABLES" && "$TABLES" != "None" ]]; then
        COUNT=$(echo "$TABLES" | wc -w | tr -d ' ')
    fi
    TOTAL_TABLES=$((TOTAL_TABLES + COUNT))
    echo "  ${NS}: ${COUNT} tables"
done

NS_COUNT=$(echo $NAMESPACES | wc -w | tr -d ' ')
echo ""
echo "Total: ${NS_COUNT} namespaces, ${TOTAL_TABLES} tables"
echo ""

if [[ "$AUTO_YES" != true ]]; then
    read -p "Delete all tables and namespaces? This is irreversible. [y/N] " CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        echo "Aborted."
        exit 0
    fi
fi

echo ""

# Delete tables then namespaces (re-list tables per namespace to avoid storing in associative array)
for NS in $NAMESPACES; do
    TABLES=$(aws s3tables list-tables \
        --table-bucket-arn "$BUCKET_ARN" \
        --namespace "$NS" \
        --region "$REGION" \
        --query 'tables[].name' \
        --output text \
        --no-cli-pager 2>/dev/null || echo "")

    if [[ -n "$TABLES" && "$TABLES" != "None" ]]; then
        for TBL in $TABLES; do
            echo "  Deleting table ${NS}.${TBL}..."
            aws s3tables delete-table \
                --table-bucket-arn "$BUCKET_ARN" \
                --namespace "$NS" \
                --name "$TBL" \
                --region "$REGION" \
                --no-cli-pager
        done
    fi

    echo "  Deleting namespace ${NS}..."
    aws s3tables delete-namespace \
        --table-bucket-arn "$BUCKET_ARN" \
        --namespace "$NS" \
        --region "$REGION" \
        --no-cli-pager
    echo "  ✓ ${NS} deleted"
done

echo ""
echo "Done. All namespaces and tables removed from ${BUCKET}."
