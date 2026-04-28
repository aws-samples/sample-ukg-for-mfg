#!/bin/bash
# CDK Deployment Script for AgentCore Chat Application
# This script deploys all CDK stacks in the correct dependency order.
# All Docker builds are handled by AWS CodeBuild - no local Docker required.
#
# Usage: ./deploy-all.sh [options]
#   --region <region>    AWS region (default: us-east-1)
#   --profile <profile>  AWS CLI profile to use
#   --ingress <mode>     Ingress mode: ecs, furl, or both (default: ecs)
#   --dry-run            Show what would be deployed without deploying
#   --skip-chatapp       Deploy Foundation + Bedrock + Agent only (skip ChatApp)
#   -h, --help           Show this help message

set -e

# Disable AWS CLI pager
export AWS_PAGER=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default configuration
AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_PROFILE=""
INGRESS_MODE="furl"
DRY_RUN=false
SKIP_CHATAPP=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --region)
            AWS_REGION="$2"
            shift 2
            ;;
        --profile)
            AWS_PROFILE="$2"
            shift 2
            ;;
        --ingress)
            INGRESS_MODE="$2"
            # Validate ingress mode
            if [[ "$INGRESS_MODE" != "ecs" && "$INGRESS_MODE" != "furl" && "$INGRESS_MODE" != "both" ]]; then
                echo -e "${RED}Error: Invalid ingress mode '$INGRESS_MODE'. Must be: ecs, furl, or both${NC}"
                exit 1
            fi
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --skip-chatapp)
            SKIP_CHATAPP=true
            shift
            ;;
        -h|--help)
            echo "Usage: ./deploy-all.sh [options]"
            echo ""
            echo "Options:"
            echo "  --region <region>    AWS region (default: us-east-1)"
            echo "  --profile <profile>  AWS CLI profile to use"
            echo "  --ingress <mode>     Ingress mode: ecs, furl, or both (default: ecs)"
            echo "  --skip-chatapp       Deploy Foundation + Bedrock + Agent only (skip ChatApp)"
            echo "  --dry-run            Show what would be deployed without deploying"
            echo "  -h, --help           Show this help message"
            echo ""
            echo "Ingress Modes:"
            echo "  ecs    - Deploy with ECS Express Gateway (~\$59.70/mo)"
            echo "  furl   - Deploy with CloudFront + Lambda Web Adapter (default, ~\$4.60/mo)"
            echo "  both   - Deploy both ECS and Lambda simultaneously"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     AgentCore Chat Application - CDK Deployment            ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Set AWS profile if provided
if [ -n "$AWS_PROFILE" ]; then
    export AWS_PROFILE
    echo -e "${YELLOW}Using AWS Profile: $AWS_PROFILE${NC}"
fi

# Get AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "unknown")
if [ "$AWS_ACCOUNT_ID" = "unknown" ]; then
    echo -e "${RED}Error: Could not get AWS account ID. Check your AWS credentials.${NC}"
    exit 1
fi

# Export environment variables for CDK
export AWS_REGION
export CDK_DEFAULT_REGION="$AWS_REGION"
export CDK_DEFAULT_ACCOUNT="$AWS_ACCOUNT_ID"

echo -e "${YELLOW}Configuration:${NC}"
echo "  AWS Account: $AWS_ACCOUNT_ID"
echo "  AWS Region: $AWS_REGION"
echo "  Ingress Mode: $INGRESS_MODE"
echo "  Skip ChatApp: $SKIP_CHATAPP"
echo "  Dry Run: $DRY_RUN"
echo ""

if [ "$DRY_RUN" = true ]; then
    echo -e "${CYAN}DRY RUN MODE - No resources will be deployed${NC}"
    echo ""
fi

# Change to CDK directory
cd "$SCRIPT_DIR"

APP_NAME="${APP_NAME:-mfg-ukg}"

# ============================================================================
# STEP 1: Install dependencies and build
# ============================================================================
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Step 1: Install dependencies and build${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}Installing npm dependencies...${NC}"
    npm install
fi

echo -e "${YELLOW}Building TypeScript...${NC}"
npm run build

echo -e "${GREEN}Build complete${NC}"

# ============================================================================
# STEP 2: Bootstrap CDK (if needed)
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Step 2: Bootstrap CDK (if needed)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Check if CDK is bootstrapped
BOOTSTRAP_STACK=$(aws cloudformation describe-stacks \
    --stack-name CDKToolkit \
    --region "$AWS_REGION" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$BOOTSTRAP_STACK" = "NOT_FOUND" ]; then
    echo -e "${YELLOW}CDK not bootstrapped in $AWS_REGION. Running cdk bootstrap...${NC}"
    if [ "$DRY_RUN" != true ]; then
        npx cdk bootstrap "aws://$AWS_ACCOUNT_ID/$AWS_REGION"
    else
        echo -e "${CYAN}[DRY RUN] Would run: cdk bootstrap aws://$AWS_ACCOUNT_ID/$AWS_REGION${NC}"
    fi
else
    echo -e "${GREEN}CDK already bootstrapped in $AWS_REGION${NC}"
fi

# Bootstrap us-east-1 for Lambda@Edge (required for CloudFront)
if [ "$AWS_REGION" != "us-east-1" ]; then
    BOOTSTRAP_USEAST1=$(aws cloudformation describe-stacks \
        --stack-name CDKToolkit \
        --region "us-east-1" \
        --query 'Stacks[0].StackStatus' \
        --output text 2>/dev/null || echo "NOT_FOUND")
    
    if [ "$BOOTSTRAP_USEAST1" = "NOT_FOUND" ]; then
        echo -e "${YELLOW}CDK not bootstrapped in us-east-1 (required for Lambda@Edge). Running cdk bootstrap...${NC}"
        if [ "$DRY_RUN" != true ]; then
            npx cdk bootstrap "aws://$AWS_ACCOUNT_ID/us-east-1"
        else
            echo -e "${CYAN}[DRY RUN] Would run: cdk bootstrap aws://$AWS_ACCOUNT_ID/us-east-1${NC}"
        fi
    else
        echo -e "${GREEN}CDK already bootstrapped in us-east-1 (for Lambda@Edge)${NC}"
    fi
fi

# ============================================================================
# STEP 2b: Ensure ECS service-linked role exists
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Step 2b: Ensure ECS service-linked role exists${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Create ECS service-linked role if it doesn't exist (required for new AWS accounts)
if [ "$DRY_RUN" != true ]; then
    if aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com 2>/dev/null; then
        echo -e "${GREEN}ECS service-linked role created${NC}"
    else
        echo -e "${GREEN}ECS service-linked role already exists${NC}"
    fi
else
    echo -e "${CYAN}[DRY RUN] Would ensure ECS service-linked role exists${NC}"
fi

# ============================================================================
# STEP 2c: Enable X-Ray trace segment destination for CloudWatch Logs
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Step 2c: Enable X-Ray CloudWatch Logs trace destination${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Required before creating AWS::Logs::Delivery resources with XRAY destination type.
# Without this, new regions fail with "Please enable the CloudWatch Logs destination
# for your traces using the UpdateTraceSegmentDestination API".
if [ "$DRY_RUN" != true ]; then
    echo -e "${YELLOW}Enabling X-Ray CloudWatch Logs trace destination...${NC}"
    XRAY_OUTPUT=$(aws xray update-trace-segment-destination --destination CloudWatchLogs --region "$AWS_REGION" 2>&1) && \
        echo -e "${GREEN}X-Ray trace segment destination update requested${NC}" || \
        echo -e "${YELLOW}Warning: X-Ray update returned: $XRAY_OUTPUT${NC}"
    
    # Wait for the destination to become ACTIVE (can take 1-3 minutes in new regions)
    echo -e "${YELLOW}Waiting for X-Ray trace destination to become ACTIVE...${NC}"
    for i in {1..30}; do
        XRAY_STATE=$(aws xray get-trace-segment-destination --region "$AWS_REGION" --query '[Destination,Status]' --output text 2>/dev/null || echo "UNKNOWN UNKNOWN")
        XRAY_DEST=$(echo "$XRAY_STATE" | awk '{print $1}')
        XRAY_STATUS=$(echo "$XRAY_STATE" | awk '{print $2}')
        if [ "$XRAY_DEST" = "CloudWatchLogs" ] && [ "$XRAY_STATUS" = "ACTIVE" ]; then
            echo -e "${GREEN}X-Ray trace destination is ACTIVE on CloudWatchLogs${NC}"
            break
        fi
        echo -n "."
        sleep 10
    done
    
    if [ "$XRAY_DEST" != "CloudWatchLogs" ] || [ "$XRAY_STATUS" != "ACTIVE" ]; then
        echo ""
        echo -e "${RED}Error: X-Ray trace destination is '$XRAY_DEST' / '$XRAY_STATUS' after 5 minutes.${NC}"
        echo -e "${RED}Expected 'CloudWatchLogs' / 'ACTIVE'. The Agent stack will fail.${NC}"
        echo -e "${YELLOW}This usually means the CloudWatch Logs resource policy for X-Ray is missing.${NC}"
        echo -e "${YELLOW}If you see an AccessDeniedException above, create the policy manually:${NC}"
        echo -e "${YELLOW}  aws logs put-resource-policy --policy-name AgentCoreTracingPolicy --region $AWS_REGION \\\\${NC}"
        echo -e "${YELLOW}    --policy-document '{...}' (see cdk/lib/agent-stack.ts XRayTracingPolicy for template)${NC}"
        exit 1
    fi
else
    echo -e "${CYAN}[DRY RUN] Would enable X-Ray CloudWatch Logs trace destination${NC}"
fi

# ============================================================================
# STEP 3: Synthesize CloudFormation templates
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Step 3: Synthesize CloudFormation templates${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo -e "${YELLOW}Synthesizing stacks...${NC}"
# Note: cdk-nag may report errors, but we continue deployment
# Security findings are logged to cdk.out/AwsSolutions-NagReport.csv
npx cdk synth --quiet || echo -e "${YELLOW}Note: cdk-nag reported findings (check cdk.out/AwsSolutions-NagReport.csv)${NC}"

echo -e "${GREEN}Synthesis complete${NC}"

# ============================================================================
# STEP 4: Deploy all stacks
# CDK automatically deploys stacks in dependency order:
# Foundation → Bedrock → Agent → ChatApp
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Step 4: Deploy all stack${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ "$DRY_RUN" = true ]; then
    echo -e "${CYAN}[DRY RUN] Would deploy all stacks${NC}"
    echo ""
    echo -e "${YELLOW}Stacks that would be deployed:${NC}"
    npx cdk list
else
    if [ "$SKIP_CHATAPP" = true ]; then
        echo -e "${YELLOW}Deploying Foundation + Bedrock + Agent + WorkflowScheduler stacks (skipping ChatApp)...${NC}"
        echo ""
        
        npx cdk deploy \
            ${APP_NAME}-Agent ${APP_NAME}-WorkflowScheduler \
            --context ingress="$INGRESS_MODE" \
            --require-approval never \
            --outputs-file cdk-outputs.json
    else
        # Phase 1: Deploy Agent + WorkflowScheduler (and their dependencies)
        # WorkflowScheduler must complete before ChatApp so the executor ARN
        # is in Secrets Manager when ChatApp resolves its env vars.
        echo -e "${YELLOW}Phase 1: Deploying Agent + WorkflowScheduler...${NC}"
        echo ""
        
        npx cdk deploy \
            ${APP_NAME}-Agent ${APP_NAME}-WorkflowScheduler \
            --context ingress="$INGRESS_MODE" \
            --require-approval never \
            --outputs-file cdk-outputs.json
    fi
    
    # Update secrets with workflow scheduler values
    echo ""
    echo -e "${YELLOW}Updating secrets with workflow scheduler config...${NC}"
    SCHEDULER_STACK_KEY="${APP_NAME}-workflow-scheduler"
    WF_EXECUTOR_ARN=$(jq -r --arg key "$SCHEDULER_STACK_KEY" '.[$key].ExecutorFunctionArn // ""' cdk-outputs.json 2>/dev/null)
    WF_SCHEDULER_GROUP=$(jq -r --arg key "$SCHEDULER_STACK_KEY" '.[$key].SchedulerGroupName // ""' cdk-outputs.json 2>/dev/null)
    WF_SCHEDULER_ROLE=$(jq -r --arg key "$SCHEDULER_STACK_KEY" '.[$key].SchedulerRoleArn // ""' cdk-outputs.json 2>/dev/null)
    
    if [ -n "$WF_EXECUTOR_ARN" ] && [ "$WF_EXECUTOR_ARN" != "null" ]; then
        SECRET_ID="${APP_NAME}/appconfig"
        CURRENT_SECRET=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ID" --region "$AWS_REGION" --query SecretString --output text 2>/dev/null || echo "{}")
        UPDATED_SECRET=$(echo "$CURRENT_SECRET" | jq \
            --arg executor "$WF_EXECUTOR_ARN" \
            --arg group "$WF_SCHEDULER_GROUP" \
            --arg role "$WF_SCHEDULER_ROLE" \
            '. + {workflow_executor_arn: $executor, workflow_scheduler_group: $group, workflow_scheduler_role_arn: $role}')
        aws secretsmanager put-secret-value --secret-id "$SECRET_ID" --secret-string "$UPDATED_SECRET" --region "$AWS_REGION" > /dev/null
        echo -e "${GREEN}Secrets updated with workflow scheduler config${NC}"
    else
        echo -e "${YELLOW}Workflow scheduler outputs not found — skipping secrets update${NC}"
    fi
    
    # Phase 2: Deploy ChatApp (reads workflow executor ARN from secret)
    if [ "$SKIP_CHATAPP" != true ]; then
        echo ""
        echo -e "${YELLOW}Phase 2: Deploying ChatApp...${NC}"
        echo ""
        
        npx cdk deploy \
            ${APP_NAME}-ChatApp \
            --context ingress="$INGRESS_MODE" \
            --require-approval never \
            --outputs-file cdk-outputs.json
        
        # Patch Lambda env vars directly from secret (CloudFormation dynamic references
        # don't re-resolve if the template string hasn't changed)
        echo ""
        echo -e "${YELLOW}Syncing Lambda env vars from secret...${NC}"
        LAMBDA_NAME="${APP_NAME}-lambda"
        if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$AWS_REGION" > /dev/null 2>&1; then
            SECRET_ID="${APP_NAME}/appconfig"
            SECRET_JSON=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ID" --region "$AWS_REGION" --query SecretString --output text 2>/dev/null || echo "{}")
            CURRENT_ENV=$(aws lambda get-function-configuration --function-name "$LAMBDA_NAME" --region "$AWS_REGION" --query 'Environment' --output json 2>/dev/null)
            PATCHED_ENV=$(python3 -c "
import sys, json
secret = json.loads('''$SECRET_JSON''')
env = json.loads('''$CURRENT_ENV''')
mapping = {
    'WORKFLOW_EXECUTOR_ARN': 'workflow_executor_arn',
    'WORKFLOW_SCHEDULER_GROUP': 'workflow_scheduler_group',
    'WORKFLOW_SCHEDULER_ROLE_ARN': 'workflow_scheduler_role_arn',
}
changed = False
for env_key, secret_key in mapping.items():
    val = secret.get(secret_key, '')
    if val and env['Variables'].get(env_key, '') != val:
        env['Variables'][env_key] = val
        changed = True
if changed:
    print(json.dumps(env))
else:
    print('NO_CHANGE')
")
            if [ "$PATCHED_ENV" != "NO_CHANGE" ]; then
                aws lambda update-function-configuration --function-name "$LAMBDA_NAME" --region "$AWS_REGION" --environment "$PATCHED_ENV" --query 'FunctionName' --output text > /dev/null
                echo -e "${GREEN}Lambda env vars patched with workflow config${NC}"
            else
                echo -e "${GREEN}Lambda env vars already up to date${NC}"
            fi
        fi
    fi
    
    echo -e "${GREEN}Stacks deployed${NC}"
fi

# ============================================================================
# STEP 4.5: Force AgentCore Runtimes to re-pull their :latest images
# ----------------------------------------------------------------------------
# CDK pins both Explorer and Discovery runtimes to ECR's `:latest` tag. Pushing
# a new image to `:latest` does NOT trigger AgentCore to re-pull — the runtime
# keeps serving whatever digest it cached on first load. Because the
# `containerUri` string inside the CloudFormation template is literally
# "...:latest" and never changes, `cdk deploy` also sees no drift and skips
# the UpdateAgentRuntime call. Net effect: code changes silently don't land.
#
# This step forces an explicit UpdateAgentRuntime with the same URI, which
# makes AgentCore re-resolve the tag and pull the current image digest.
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Step 4.5: Refresh AgentCore Runtimes (force re-pull of :latest)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ "$DRY_RUN" = true ]; then
    echo -e "${CYAN}[DRY RUN] Would call UpdateAgentRuntime for Explorer and Discovery${NC}"
else
    AGENT_STACK_KEY="${APP_NAME}-agent"

    for RUNTIME_LABEL in Explorer Discovery; do
        ARN_KEY="${RUNTIME_LABEL}RuntimeArn"
        REPO_KEY="${RUNTIME_LABEL}RepositoryUri"

        RUNTIME_ARN=$(jq -r --arg key "$AGENT_STACK_KEY" --arg k "$ARN_KEY" '.[$key][$k] // ""' cdk-outputs.json 2>/dev/null)
        REPO_URI=$(jq -r --arg key "$AGENT_STACK_KEY" --arg k "$REPO_KEY" '.[$key][$k] // ""' cdk-outputs.json 2>/dev/null)

        if [ -z "$RUNTIME_ARN" ] || [ "$RUNTIME_ARN" = "null" ]; then
            echo -e "${YELLOW}  ${RUNTIME_LABEL}: runtime ARN not found in cdk-outputs, skipping${NC}"
            continue
        fi
        if [ -z "$REPO_URI" ] || [ "$REPO_URI" = "null" ]; then
            echo -e "${YELLOW}  ${RUNTIME_LABEL}: repository URI not found in cdk-outputs, skipping${NC}"
            continue
        fi

        # Runtime ID is the last segment of the ARN
        RUNTIME_ID="${RUNTIME_ARN##*/}"
        CONTAINER_URI="${REPO_URI}:latest"

        echo -e "${YELLOW}  ${RUNTIME_LABEL} (${RUNTIME_ID}): refreshing → ${CONTAINER_URI}${NC}"

        # Fetch the existing runtime config so we can re-supply the required
        # fields (role ARN, network config, protocol, env vars) that
        # update-agent-runtime demands. We only change the container URI
        # here — same value, but the update itself forces a re-pull.
        EXISTING=$(aws bedrock-agentcore-control get-agent-runtime \
            --agent-runtime-id "$RUNTIME_ID" \
            --region "$AWS_REGION" \
            --output json 2>/dev/null)

        if [ -z "$EXISTING" ]; then
            echo -e "${RED}    Failed to fetch existing runtime config — skipping${NC}"
            continue
        fi

        ROLE_ARN=$(echo "$EXISTING" | jq -r '.roleArn // ""')
        NETWORK_CFG=$(echo "$EXISTING" | jq -c '.networkConfiguration // {"networkMode":"PUBLIC"}')
        PROTOCOL_CFG=$(echo "$EXISTING" | jq -c '.protocolConfiguration // {"serverProtocol":"HTTP"}')
        ENV_VARS=$(echo "$EXISTING" | jq -c '.environmentVariables // {}')
        ARTIFACT=$(jq -n --arg uri "$CONTAINER_URI" '{containerConfiguration:{containerUri:$uri}}')

        # UpdateAgentRuntime is a full replacement — we must re-supply every
        # field we want to preserve (env vars, protocol, etc.), using the
        # values already on the runtime (fetched above). Only the artifact
        # URI change matters for forcing the image re-pull.
        UPDATE_OUTPUT=$(aws bedrock-agentcore-control update-agent-runtime \
            --agent-runtime-id "$RUNTIME_ID" \
            --agent-runtime-artifact "$ARTIFACT" \
            --role-arn "$ROLE_ARN" \
            --network-configuration "$NETWORK_CFG" \
            --protocol-configuration "$PROTOCOL_CFG" \
            --environment-variables "$ENV_VARS" \
            --region "$AWS_REGION" \
            --query 'agentRuntimeVersion' \
            --output text 2>&1)

        if [ $? -eq 0 ]; then
            echo -e "${GREEN}    Updated to runtime version ${UPDATE_OUTPUT}${NC}"
        else
            echo -e "${RED}    UpdateAgentRuntime failed:${NC}"
            echo "$UPDATE_OUTPUT" | sed 's/^/      /'
        fi
    done

    echo -e "${GREEN}AgentCore Runtimes refreshed${NC}"
fi

# ============================================================================
# STEP 5: Force ECS deployment (if needed)
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Step 5: Check ECS deployment${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ "$SKIP_CHATAPP" = true ]; then
    echo -e "${GREEN}Skipping ECS deployment check (--skip-chatapp)${NC}"
elif [ "$INGRESS_MODE" = "furl" ]; then
    echo -e "${GREEN}Skipping ECS deployment check (ingress mode: furl)${NC}"
elif [ "$DRY_RUN" != true ]; then
    # Force ECS to pull the new image (if not already deploying)
    echo ""
    echo -e "${YELLOW}Checking ECS deployment status...${NC}"
    DEPLOYMENT_COUNT=$(aws ecs describe-services \
        --cluster default \
        --services "${APP_NAME}-express" \
        --region "$AWS_REGION" \
        --query 'length(services[0].deployments)' \
        --output text 2>/dev/null || echo "1")
    
    if [ "$DEPLOYMENT_COUNT" = "1" ]; then
        echo -e "${YELLOW}Forcing ECS deployment to pull new image...${NC}"
        aws ecs update-service \
            --cluster default \
            --service "${APP_NAME}-express" \
            --force-new-deployment \
            --region "$AWS_REGION" \
            --query 'service.serviceName' \
            --output text > /dev/null
        echo -e "${GREEN}ECS deployment triggered${NC}"
    else
        echo -e "${GREEN}ECS deployment already in progress (${DEPLOYMENT_COUNT} deployments)${NC}"
    fi

    # Reset deployment configuration: 0 bake time, 100% canary (instant rollout)
    # ECS Express uses standard UpdateService for deployment config
    echo -e "${YELLOW}Resetting ECS deployment configuration (bake=0, canary=100%)...${NC}"
    aws ecs update-service \
        --cluster default \
        --service "${APP_NAME}-express" \
        --deployment-configuration "bakeTimeInMinutes=0,canaryConfiguration={canaryPercent=100,canaryBakeTimeInMinutes=0}" \
        --region "$AWS_REGION" \
        --no-cli-pager \
        --query 'service.serviceName' \
        --output text > /dev/null 2>&1 && \
        echo -e "${GREEN}Deployment configuration updated (bake=0, canary=100%)${NC}" || \
        echo -e "${YELLOW}Warning: Could not update deployment configuration (non-fatal)${NC}"
else
    if [ "$INGRESS_MODE" != "furl" ]; then
        echo -e "${CYAN}[DRY RUN] Would check ECS deployment status${NC}"
    fi
fi

# ============================================================================
# STEP 5b: Invalidate CloudFront cache (for furl/both modes)
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Step 5b: Invalidate CloudFront cache${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ "$SKIP_CHATAPP" = true ]; then
    echo -e "${GREEN}Skipping CloudFront invalidation (--skip-chatapp)${NC}"
elif [ "$INGRESS_MODE" = "ecs" ]; then
    echo -e "${GREEN}Skipping CloudFront invalidation (ingress mode: ecs)${NC}"
elif [ "$DRY_RUN" != true ]; then
    # Get CloudFront distribution ID from CDK outputs
    STACK_KEY="${APP_NAME}-chatapp"
    CF_DIST_ID=$(jq -r --arg key "$STACK_KEY" '.[$key].CloudFrontDistributionId // ""' cdk-outputs.json 2>/dev/null)

    if [ -n "$CF_DIST_ID" ] && [ "$CF_DIST_ID" != "null" ]; then
        echo -e "${YELLOW}Invalidating CloudFront cache for distribution: ${CF_DIST_ID}${NC}"
        INVALIDATION_ID=$(aws cloudfront create-invalidation \
            --distribution-id "$CF_DIST_ID" \
            --paths "/*" \
            --query 'Invalidation.Id' \
            --output text \
            --no-cli-pager 2>/dev/null || echo "")

        if [ -n "$INVALIDATION_ID" ]; then
            echo -e "${GREEN}CloudFront invalidation created: ${INVALIDATION_ID}${NC}"
        else
            echo -e "${YELLOW}Warning: Could not create CloudFront invalidation${NC}"
        fi
    else
        echo -e "${YELLOW}CloudFront distribution ID not found in cdk-outputs.json — skipping invalidation${NC}"
    fi
else
    echo -e "${CYAN}[DRY RUN] Would invalidate CloudFront cache${NC}"
fi

# ============================================================================
# STEP 6: Display outputs and next steps
# ============================================================================
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Deployment Summary${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ "$DRY_RUN" != true ]; then
    echo ""
    echo -e "${BLUE}AWS Account:${NC} $AWS_ACCOUNT_ID"
    echo -e "${BLUE}Region:${NC} $AWS_REGION"
    echo ""
    echo -e "${BLUE}Deployed Stacks:${NC}"
    echo "  1. ${APP_NAME}-Foundation (Cognito, DynamoDB, IAM, Secrets, System Registry)"
    echo "  2. ${APP_NAME}-Bedrock (Guardrail, Knowledge Base, Memory)"
    echo "  3. ${APP_NAME}-Gateway (AgentCore Gateway, shared registry tools)"
    echo "  4. ${APP_NAME}-Agent (Explorer + Discovery Agent)"
    if [ "$SKIP_CHATAPP" = true ]; then
        echo "  5. ${APP_NAME}-ChatApp (skipped)"
    elif [ "$INGRESS_MODE" = "ecs" ]; then
        echo "  5. ${APP_NAME}-ChatApp (ECS Express Mode)"
    elif [ "$INGRESS_MODE" = "furl" ]; then
        echo "  5. ${APP_NAME}-ChatApp (Lambda Function URL)"
    else
        echo "  5. ${APP_NAME}-ChatApp (ECS Express Mode + Lambda Function URL)"
    fi
    echo "  6. ${APP_NAME}-WorkflowScheduler (Lambda + EventBridge Scheduler)"
    
    echo ""
    echo -e "${BLUE}Application Endpoints:${NC}"
    
    if [ "$SKIP_CHATAPP" = true ]; then
        echo -e "${YELLOW}  ChatApp not deployed (--skip-chatapp). No endpoints to display.${NC}"
    else
    
    # Handle ECS Express Mode URL (for 'ecs' or 'both' modes)
    if [ "$INGRESS_MODE" = "ecs" ] || [ "$INGRESS_MODE" = "both" ]; then
        ECS_SERVICE_NAME="htmx-chatapp-express"
        SERVICE_URL=""
        
        echo -e "${YELLOW}Fetching ECS Express Mode service URL...${NC}"
        
        # Get the service ARN first
        SERVICE_ARN=$(aws ecs list-services \
            --cluster default \
            --region "$AWS_REGION" \
            --query "serviceArns[?contains(@, '${ECS_SERVICE_NAME}')]" \
            --output text 2>/dev/null | head -1 || echo "")
        
        # Use describe-express-gateway-service to get the actual endpoint URL
        if [ -n "$SERVICE_ARN" ] && [ "$SERVICE_ARN" != "None" ]; then
            # Wait for URL to be available (up to 60 seconds)
            for i in {1..12}; do
                SERVICE_INFO=$(aws ecs describe-express-gateway-service \
                    --service-arn "$SERVICE_ARN" \
                    --region "$AWS_REGION" 2>/dev/null || echo "")
                
                if [ -n "$SERVICE_INFO" ]; then
                    SERVICE_URL=$(echo "$SERVICE_INFO" | jq -r '.service.activeConfigurations[0].ingressPaths[0].endpoint // empty' 2>/dev/null || echo "")
                    
                    if [ -n "$SERVICE_URL" ]; then
                        break
                    fi
                fi
                echo -n "."
                sleep 5
            done
        fi
        
        # Display URL or fallback message
        if [ -n "$SERVICE_URL" ]; then
            echo -e "${GREEN}Application URL:${NC} https://$SERVICE_URL"
        else
            echo -e "${YELLOW}ECS Express Mode: URL not yet available (service may still be initializing)${NC}"
            if [ -n "$SERVICE_ARN" ]; then
                echo -e "${YELLOW}Get URL with:${NC} aws ecs describe-express-gateway-service --service-arn \"$SERVICE_ARN\" --region $AWS_REGION --query 'service.activeConfigurations[0].ingressPaths[0].endpoint' --output text"
            fi
        fi
        echo ""
    fi
    
    # Handle Lambda Function URL (for 'furl' or 'both' modes)
    if [ "$INGRESS_MODE" = "furl" ] || [ "$INGRESS_MODE" = "both" ]; then
        echo -e "${YELLOW}Fetching CloudFront URL...${NC}"
        
        # Get Lambda Function URL from CDK outputs
        STACK_KEY="${APP_NAME}-chatapp"
        LAMBDA_URL=$(jq -r --arg key "$STACK_KEY" '.[$key].LambdaFunctionUrl // ""' cdk-outputs.json 2>/dev/null)
        
        if [ -n "$LAMBDA_URL" ] && [ "$LAMBDA_URL" != "null" ]; then
            echo -e "${GREEN}Application URL:${NC} $LAMBDA_URL"
        else
            echo -e "${YELLOW}  Lambda Function URL: Unable to retrieve from outputs${NC}"
            echo -e "${YELLOW}  Check cdk-outputs.json or AWS Console for the Function URL${NC}"
        fi
        echo ""
    fi
    
    fi  # end skip-chatapp check
    
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║           CDK Deployment Complete!                         ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${YELLOW}Next Steps:${NC}"
    echo "  1. Create a user:    ./create-user.sh <email> <password> --admin --region $AWS_REGION${AWS_PROFILE:+ --profile $AWS_PROFILE}"
    echo "  2. Access the application using the URL(s) shown above"
    echo ""
    echo -e "${YELLOW}Useful Commands:${NC}"
    echo "  View stack outputs:  cat cdk-outputs.json"
    echo "  Update a stack:      npx cdk deploy <StackName>"
    echo "  Destroy all stacks:  ./destroy-all.sh"
    echo ""
else
    echo -e "${CYAN}DRY RUN complete. No resources were deployed.${NC}"
    echo "Run without --dry-run to perform actual deployment."
fi
