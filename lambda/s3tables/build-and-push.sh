#!/bin/bash
# Build and push the MCP Lambda Docker image to ECR using CodeBuild.
# No local Docker required — everything runs in AWS.
#
# This script:
# 1. Creates an ECR repository (if needed)
# 2. Uploads source to a temporary S3 bucket
# 3. Creates a one-off CodeBuild project
# 4. Runs the build and waits for completion
# 5. Cleans up the CodeBuild project and S3 source
#
# Usage:
#   ./build-and-push.sh --region us-east-2 [--profile my-profile]
#
# Prerequisites:
#   - AWS CLI configured with permissions for ECR, S3, CodeBuild, IAM

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="${APP_NAME:-mfg-thread}"
ECR_REPO_NAME="${APP_NAME}-mcp-lambda"
AWS_REGION="${AWS_REGION:-us-east-2}"
AWS_PROFILE=""
CODEBUILD_PROJECT_NAME="${APP_NAME}-mcp-lambda-build"
S3_BUCKET_NAME="${APP_NAME}-mcp-lambda-build-source"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --region) AWS_REGION="$2"; shift 2 ;;
        --profile) AWS_PROFILE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: ./build-and-push.sh [options]"
            echo "  --region <region>    AWS region (default: us-east-2)"
            echo "  --profile <profile>  AWS CLI profile"
            echo "  -h, --help           Show help"
            exit 0 ;;
        *) echo -e "${RED}Unknown option: $1${NC}"; exit 1 ;;
    esac
done

if [ -n "$AWS_PROFILE" ]; then
    export AWS_PROFILE
fi
export AWS_PAGER=""

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$AWS_REGION" 2>/dev/null)
if [ -z "$AWS_ACCOUNT_ID" ]; then
    echo -e "${RED}Error: Could not get AWS account ID. Check credentials.${NC}"
    exit 1
fi

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO_NAME}"
S3_BUCKET_NAME="${S3_BUCKET_NAME}-${AWS_ACCOUNT_ID}-${AWS_REGION}"

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}MCP Lambda Image Build (via CodeBuild)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  Account:  ${AWS_ACCOUNT_ID}"
echo "  Region:   ${AWS_REGION}"
echo "  ECR Repo: ${ECR_REPO_NAME}"
echo ""

# ============================================================================
# Step 1: Create ECR repository if needed
# ============================================================================
if ! aws ecr describe-repositories --repository-names "${ECR_REPO_NAME}" --region "${AWS_REGION}" >/dev/null 2>&1; then
    echo -e "${YELLOW}Creating ECR repository: ${ECR_REPO_NAME}${NC}"
    aws ecr create-repository \
        --repository-name "${ECR_REPO_NAME}" \
        --region "${AWS_REGION}" \
        --image-scanning-configuration scanOnPush=true \
        --query 'repository.repositoryUri' \
        --output text
else
    echo -e "${GREEN}ECR repository exists${NC}"
fi

# ============================================================================
# Step 2: Create S3 bucket and upload source
# ============================================================================
if ! aws s3api head-bucket --bucket "${S3_BUCKET_NAME}" --region "${AWS_REGION}" 2>/dev/null; then
    echo -e "${YELLOW}Creating S3 bucket: ${S3_BUCKET_NAME}${NC}"
    aws s3api create-bucket \
        --bucket "${S3_BUCKET_NAME}" \
        --region "${AWS_REGION}" \
        --create-bucket-configuration LocationConstraint="${AWS_REGION}" \
        --output text >/dev/null
fi

echo -e "${YELLOW}Uploading source to S3...${NC}"
TMPZIP=$(mktemp /tmp/mcp-lambda-source.XXXXXX.zip)
(cd "${SCRIPT_DIR}" && zip -q "${TMPZIP}" Dockerfile handler.py requirements.txt)
aws s3 cp "${TMPZIP}" "s3://${S3_BUCKET_NAME}/source.zip" --region "${AWS_REGION}"
rm -f "${TMPZIP}"
echo -e "${GREEN}Source uploaded${NC}"

# ============================================================================
# Step 3: Create CodeBuild service role (if needed)
# ============================================================================
ROLE_NAME="${APP_NAME}-mcp-lambda-build-role"
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"

if ! aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
    echo -e "${YELLOW}Creating CodeBuild service role...${NC}"
    aws iam create-role \
        --role-name "${ROLE_NAME}" \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "codebuild.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }' \
        --output text >/dev/null

    aws iam put-role-policy \
        --role-name "${ROLE_NAME}" \
        --policy-name "mcp-lambda-build-policy" \
        --policy-document "{
            \"Version\": \"2012-10-17\",
            \"Statement\": [
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"ecr:GetAuthorizationToken\"
                    ],
                    \"Resource\": \"*\"
                },
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"ecr:BatchCheckLayerAvailability\",
                        \"ecr:GetDownloadUrlForLayer\",
                        \"ecr:BatchGetImage\",
                        \"ecr:PutImage\",
                        \"ecr:InitiateLayerUpload\",
                        \"ecr:UploadLayerPart\",
                        \"ecr:CompleteLayerUpload\"
                    ],
                    \"Resource\": \"arn:aws:ecr:${AWS_REGION}:${AWS_ACCOUNT_ID}:repository/${ECR_REPO_NAME}\"
                },
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"s3:GetObject\",
                        \"s3:GetObjectVersion\"
                    ],
                    \"Resource\": \"arn:aws:s3:::${S3_BUCKET_NAME}/*\"
                },
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"s3:GetBucketLocation\"
                    ],
                    \"Resource\": \"arn:aws:s3:::${S3_BUCKET_NAME}\"
                },
                {
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"logs:CreateLogGroup\",
                        \"logs:CreateLogStream\",
                        \"logs:PutLogEvents\"
                    ],
                    \"Resource\": \"arn:aws:logs:${AWS_REGION}:${AWS_ACCOUNT_ID}:log-group:/aws/codebuild/${CODEBUILD_PROJECT_NAME}*\"
                }
            ]
        }"

    # Wait for role to propagate
    echo -e "${YELLOW}Waiting for IAM role propagation...${NC}"
    sleep 10
else
    echo -e "${GREEN}CodeBuild role exists${NC}"
fi

# ============================================================================
# Step 4: Create or update CodeBuild project
# ============================================================================
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"

BUILDSPEC='{
  "version": "0.2",
  "phases": {
    "pre_build": {
      "commands": [
        "echo Logging in to ECR...",
        "aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
      ]
    },
    "build": {
      "commands": [
        "echo Building Docker image...",
        "docker build -t $ECR_REPO_URI:latest .",
        "docker tag $ECR_REPO_URI:latest $ECR_REPO_URI:$CODEBUILD_BUILD_NUMBER"
      ]
    },
    "post_build": {
      "commands": [
        "echo Pushing to ECR...",
        "docker push $ECR_REPO_URI:latest",
        "docker push $ECR_REPO_URI:$CODEBUILD_BUILD_NUMBER",
        "echo Done"
      ]
    }
  }
}'

if aws codebuild batch-get-projects --names "${CODEBUILD_PROJECT_NAME}" --region "${AWS_REGION}" --query 'projects[0].name' --output text 2>/dev/null | grep -q "${CODEBUILD_PROJECT_NAME}"; then
    echo -e "${GREEN}CodeBuild project exists${NC}"
else
    echo -e "${YELLOW}Creating CodeBuild project...${NC}"
    aws codebuild create-project \
        --name "${CODEBUILD_PROJECT_NAME}" \
        --region "${AWS_REGION}" \
        --source "{\"type\": \"S3\", \"location\": \"${S3_BUCKET_NAME}/source.zip\"}" \
        --artifacts '{"type": "NO_ARTIFACTS"}' \
        --environment "{
            \"type\": \"ARM_CONTAINER\",
            \"image\": \"aws/codebuild/amazonlinux-aarch64-standard:3.0\",
            \"computeType\": \"BUILD_GENERAL1_SMALL\",
            \"privilegedMode\": true,
            \"environmentVariables\": [
                {\"name\": \"AWS_ACCOUNT_ID\", \"value\": \"${AWS_ACCOUNT_ID}\", \"type\": \"PLAINTEXT\"},
                {\"name\": \"AWS_REGION\", \"value\": \"${AWS_REGION}\", \"type\": \"PLAINTEXT\"},
                {\"name\": \"ECR_REPO_URI\", \"value\": \"${ECR_URI}\", \"type\": \"PLAINTEXT\"}
            ]
        }" \
        --service-role "${ROLE_ARN}" \
        --build-timeout-in-minutes 30 \
        --output text >/dev/null
    echo -e "${GREEN}CodeBuild project created${NC}"
fi

# ============================================================================
# Step 5: Start build and wait for completion
# ============================================================================
echo -e "${YELLOW}Starting CodeBuild...${NC}"
BUILD_ID=$(aws codebuild start-build \
    --project-name "${CODEBUILD_PROJECT_NAME}" \
    --region "${AWS_REGION}" \
    --source-type-override S3 \
    --source-location-override "${S3_BUCKET_NAME}/source.zip" \
    --buildspec-override "${BUILDSPEC}" \
    --environment-variables-override \
        "name=AWS_ACCOUNT_ID,value=${AWS_ACCOUNT_ID},type=PLAINTEXT" \
        "name=AWS_REGION,value=${AWS_REGION},type=PLAINTEXT" \
        "name=ECR_REPO_URI,value=${ECR_URI},type=PLAINTEXT" \
    --query 'build.id' \
    --output text)

echo "  Build ID: ${BUILD_ID}"
echo -e "${YELLOW}Waiting for build to complete...${NC}"

while true; do
    STATUS=$(aws codebuild batch-get-builds \
        --ids "${BUILD_ID}" \
        --region "${AWS_REGION}" \
        --query 'builds[0].buildStatus' \
        --output text)

    case "$STATUS" in
        SUCCEEDED)
            echo -e "${GREEN}Build succeeded${NC}"
            break
            ;;
        FAILED|FAULT|STOPPED|TIMED_OUT)
            echo -e "${RED}Build failed with status: ${STATUS}${NC}"
            echo -e "${YELLOW}Check logs: aws codebuild batch-get-builds --ids ${BUILD_ID} --region ${AWS_REGION}${NC}"
            exit 1
            ;;
        IN_PROGRESS)
            echo -n "."
            sleep 15
            ;;
        *)
            echo -n "."
            sleep 15
            ;;
    esac
done

# ============================================================================
# Step 6: Clean up S3 source (keep ECR repo, CodeBuild project, and role)
# ============================================================================
echo -e "${YELLOW}Cleaning up build source...${NC}"
aws s3 rm "s3://${S3_BUCKET_NAME}/source.zip" --region "${AWS_REGION}" 2>/dev/null || true

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}MCP Lambda image pushed to ECR${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  Image: ${ECR_URI}:latest"
