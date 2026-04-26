#!/bin/bash
# Create a user in Cognito User Pool
# Usage: ./create-user.sh <email> [password] [--admin]
#
# Options:
#   --admin    Add user to Admin group (grants access to admin dashboard)

set -e

# Disable AWS CLI pager
export AWS_PAGER=""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

AWS_REGION="${AWS_REGION:-us-east-1}"
AWS_PROFILE_ARG=""
APP_NAME="${APP_NAME:-mfg-ukg}"
POOL_NAME="${APP_NAME}-users"
ADMIN_GROUP_NAME="Admin"

# Parse arguments
EMAIL=""
PASSWORD=""
IS_ADMIN=false

for arg in "$@"; do
    if [ "$GRAB_PROFILE" = true ]; then
        AWS_PROFILE_ARG="--profile $arg"
        GRAB_PROFILE=false
        continue
    fi
    if [ "$GRAB_REGION" = true ]; then
        AWS_REGION="$arg"
        GRAB_REGION=false
        continue
    fi
    case $arg in
        --admin)
            IS_ADMIN=true
            ;;
        --profile=*)
            AWS_PROFILE_ARG="--profile ${arg#*=}"
            ;;
        --profile)
            GRAB_PROFILE=true
            ;;
        --region=*)
            AWS_REGION="${arg#*=}"
            ;;
        --region)
            GRAB_REGION=true
            ;;
        -*)
            echo -e "${RED}Unknown option: $arg${NC}"
            exit 1
            ;;
        *)
            if [ -z "$EMAIL" ]; then
                EMAIL="$arg"
            elif [ -z "$PASSWORD" ]; then
                PASSWORD="$arg"
            fi
            ;;
    esac
done

# If AWS_PROFILE is set in env, use it
if [ -n "$AWS_PROFILE" ] && [ -z "$AWS_PROFILE_ARG" ]; then
    AWS_PROFILE_ARG="--profile $AWS_PROFILE"
fi

# Check arguments
if [ -z "$EMAIL" ]; then
    echo -e "${RED}Usage: ./create-user.sh <email> [password] [--admin] [--region <region>] [--profile <profile>]${NC}"
    echo "  email              - User's email address"
    echo "  password           - Optional password (will prompt if not provided)"
    echo "  --admin            - Add user to Admin group"
    echo "  --region <region>  - AWS region (default: \$AWS_REGION or us-east-1)"
    echo "  --profile <name>   - AWS CLI profile"
    exit 1
fi

# Get User Pool ID
echo -e "${YELLOW}Looking for pool '$POOL_NAME' in $AWS_REGION ${AWS_PROFILE_ARG}${NC}"
USER_POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 --region "$AWS_REGION" $AWS_PROFILE_ARG \
    --query "UserPools[?Name=='$POOL_NAME'].Id" --output text)

if [ -z "$USER_POOL_ID" ]; then
    echo -e "${RED}Error: User Pool '$POOL_NAME' not found in $AWS_REGION${NC}"
    echo -e "${YELLOW}Tips:${NC}"
    echo "  - If you deployed to a different region, pass --region <region>"
    echo "  - If you're using a named profile, pass --profile <profile-name>"
    echo "  - Verify the app was deployed: cd ../../cdk && ./deploy-all.sh --region <region>"
    exit 1
fi

echo -e "${YELLOW}Creating user in Cognito...${NC}"
echo "User Pool: $USER_POOL_ID"
echo "Email: $EMAIL"

# Prompt for password if not provided
if [ -z "$PASSWORD" ]; then
    echo -e "${YELLOW}Enter password (min 8 chars, uppercase, lowercase, number):${NC}"
    read -s PASSWORD
    echo ""
fi

# Create user with admin privileges (no email verification needed)
aws cognito-idp admin-create-user \
    --user-pool-id "$USER_POOL_ID" \
    --username "$EMAIL" \
    --user-attributes Name=email,Value="$EMAIL" Name=email_verified,Value=true \
    --message-action SUPPRESS \
    --region "$AWS_REGION" $AWS_PROFILE_ARG > /dev/null

# Set permanent password
aws cognito-idp admin-set-user-password \
    --user-pool-id "$USER_POOL_ID" \
    --username "$EMAIL" \
    --password "$PASSWORD" \
    --permanent \
    --region "$AWS_REGION" $AWS_PROFILE_ARG

# Add to Admin group if requested
if [ "$IS_ADMIN" = true ]; then
    echo -e "${YELLOW}Adding user to Admin group...${NC}"
    aws cognito-idp admin-add-user-to-group \
        --user-pool-id "$USER_POOL_ID" \
        --username "$EMAIL" \
        --group-name "$ADMIN_GROUP_NAME" \
        --region "$AWS_REGION" $AWS_PROFILE_ARG
    echo -e "${GREEN}User added to Admin group${NC}"
fi

echo -e "${GREEN}User created successfully!${NC}"
echo ""
echo "Email: $EMAIL"
if [ "$IS_ADMIN" = true ]; then
    echo "Role: Administrator (can access /admin dashboard)"
else
    echo "Role: Regular user"
fi
echo "You can now log in to the application."
