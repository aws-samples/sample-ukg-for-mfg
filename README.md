# Manufacturing Universal Knowledge Graph — Agentic Data Explorer

A multi-agent AI application that unifies manufacturing data across ERP, MES, CMMS, PLM, and IoT systems through autonomous data discovery, a dynamic system registry, and natural language exploration. Built with Amazon Bedrock AgentCore, Strands Agents SDK, and FastAPI.

The agents discover and register data sources at runtime — no hardcoded schemas or system knowledge. New systems become queryable immediately after registration.

![Data Explorer Chat UI](/assets/app_home.png?raw=true "Data Explorer Chat UI")

---

## Table of Contents

- [Why This Project?](#why-this-project)
- [Key Features](#key-features)
- [Admin Dashboard](#admin-dashboard)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Deployment Options](#deployment-options)
- [Stack Architecture](#stack-architecture)
- [Multi-Region Deployment](#multi-region-deployment)
- [Local Development](#local-development)
- [Synthetic Data Generation](#synthetic-data-generation)
- [Knowledge Base Integration](#knowledge-base-integration)
- [Useful Commands](#useful-commands)
- [Environment Variables](#environment-variables)
- [Project Structure](#project-structure)
- [Cost Tracking](#cost-tracking)
- [Cleanup](#cleanup)
- [Security](#security)
- [License](#license)

---

## Why This Project?

Manufacturing organizations run dozens of disconnected systems — ERP, MES, CMMS, PLM, historians — each with its own schema, naming conventions, and data model. Tracing a part from design through production to maintenance requires manual cross-referencing across all of them.

This project solves that with two cooperating AI agents:

- **Zero-config data onboarding** — Point the Discovery Agent at an S3 Tables bucket, RDS database, or API and it autonomously inspects schemas, maps fields to manufacturing concepts (ISA-95 aligned), and registers cross-system equivalences
- **Natural language data exploration** — Ask questions that span multiple systems ("Trace ORD-000003 from design to delivery") and the Data Explorer Agent resolves entities across systems, queries each source, and synthesizes a unified answer with source citations
- **Automated workflows** — Turn any chat prompt into a scheduled workflow that runs on EventBridge, delivering recurring insights without manual intervention
- **Built-in cost intelligence** — Track token usage, runtime costs, and tool invocations with projections to forecast production spending before you scale
- **Flexible deployment** — Choose between always-on ECS (~$60/mo) or serverless Lambda Web Adapter (~$5/mo) based on your traffic patterns

---

## Key Features

**Data Discovery & Registration**
- 🔍 Autonomous multi-phase data discovery (inspect → analyze → map concepts → register equivalences)
- 🗂️ Dynamic system registry with field-to-concept mappings and cross-system equivalences
- 🏭 ISA-95 aligned concept taxonomy (customizable per customer)
- 📊 Interactive graph visualization of system relationships

**Chat & Exploration**
- 🤖 AI-powered conversational agent with short-term and long-term memory
- ⚡ Real-time SSE streaming with token-by-token responses and embedded trace viewer
- 📝 Prompt templates for one-click access to pre-defined queries
- 💡 Smart follow-up suggestions and recommended actions after each response
- 🎨 Customizable branding — title, logos, and theme colors

**Workflows**
- ⚙️ Create scheduled workflows from any chat prompt
- 📅 EventBridge-powered scheduling (hourly, daily, custom cron)
- 📋 Per-user workflow library with run history and results

**POC Analytics & Insights**
- 📊 Admin dashboard with usage analytics and cost tracking
- 💰 Cost projections based on actual usage patterns (token + runtime costs)
- 👍 User feedback capture with sentiment ratings and comments
- 🛡️ Guardrails analytics with violation tracking and content filtering
- 🔧 Tool usage analytics with per-tool invocation metrics and success rates
- 🧪 Agent evaluation tracking

**Agent Capabilities**
- 🧠 Amazon Bedrock AgentCore with Strands Agents SDK
- 📚 Knowledge Base integration for semantic search over curated documents (S3 Vectors)
- 🛠️ Pre-built tools — system query, concept search, knowledge base, web search, URL fetcher

**Infrastructure**
- ☁️ Flexible deployment — ECS Express Mode or CloudFront + Lambda Web Adapter
- 🔐 Cognito authentication with secure token management
- 📡 OpenTelemetry and Bedrock AgentCore Observability with logs, traces, and metrics
- 🗄️ API Gateway + Lambda for the System Registry API

---

## Admin Dashboard

The built-in admin dashboard (`/admin`) provides comprehensive usage analytics and system management:

<!-- 📸 Recommended screenshot: Admin dashboard overview showing cost breakdown and usage charts -->

<table width="100%">
<tr>
<td width="50%" valign="top">

**📊 Dashboard Overview** `/admin`
- Total cost breakdown (token cost + runtime cost)
- Top users and tools by usage
- Model breakdown with per-model costs

</td>
<td width="50%" valign="top">

**🔢 Token Usage** `/admin/tokens`
- Token usage breakdown by model
- Input vs output distribution
- Monthly projections

</td>
</tr>
<tr>
<td width="50%" valign="top">

**💬 Chat History** `/admin/history`
- Browse all chat sessions with time filtering
- Token cost vs runtime cost breakdown

</td>
<td width="50%" valign="top">

**📋 Session Details** `/admin/sessions/{id}`
- Complete session token and runtime usage
- Tools invoked with success/error rates

</td>
</tr>
<tr>
<td width="50%" valign="top">

**👍 Feedback Analytics** `/admin/feedback`
- User sentiment and comments capture
- Review related conversation context

</td>
<td width="50%" valign="top">

**👥 User Analytics** `/admin/users`
- Per-user token usage and session counts

</td>
</tr>
<tr>
<td width="50%" valign="top">

**🛡️ Guardrails Analytics** `/admin/guardrails`
- Violation tracking by filter type
- Filter strength and confidence levels

</td>
<td width="50%" valign="top">

**🔧 Tool Analytics** `/admin/tools`
- Call counts per tool with success/error rates
- Average execution times

</td>
</tr>
<tr>
<td width="50%" valign="top">

**🔍 Data Discovery** `/admin/discover`
- Discovery agent chat interface
- Discovery history with drill-down

</td>
<td width="50%" valign="top">

**🗂️ System Registry** `/admin/registry`
- Registered systems, concepts, and equivalences
- Per-system schema and field detail views

</td>
</tr>
<tr>
<td width="50%" valign="top">

**📝 Prompt Templates** `/admin/templates`
- Create reusable prompt templates for the chat UI
- Bulk upload from external tools

</td>
<td width="50%" valign="top">

**🎨 Application Settings** `/admin/settings`
- Customize app title, subtitle, and welcome message
- Set app theme including color and custom logos

</td>
</tr>
<tr>
<td width="50%" valign="top">

**🧪 Evaluations** `/admin/evaluations`
- Agent evaluation tracking and results

</td>
<td width="50%" valign="top">

**📈 Data Graph** `/admin/data-graph`
- Visual graph of system relationships and concept mappings

</td>
</tr>
</table>

![Data Discovery Dashboard](/assets/app_discovery.png?raw=true "Data Discovery Dashboard")

---

## Architecture

The application uses two cooperating agents backed by a shared system registry, with flexible ingress via ECS Express Gateway or CloudFront + Lambda Web Adapter.

![System Architecture](/assets/ukg_arch.png?raw=true "System Architecture")

### Strands Agents

| Agent | Purpose |
|-------|---------|
| **Data Explorer** | Answers manufacturing questions by discovering systems from the registry, resolving entities across systems, querying data sources, and synthesizing unified answers with source citations. Tools: `query_system`, `find_by_concept`, `search_knowledge_base`, `web_search`, `url_fetcher`. |
| **Discovery** | Admin-only agent that inspects and registers new data sources (S3 Tables, APIs, databases). Runs a multi-phase process: inspect schema → analyze fields → map to ISA-95 concepts → register cross-system equivalences. Tools: `inspect`, `analyze`, `register`, `discovery_helpers`. |

### Key Components

| Component | Description |
|-----------|-------------|
| **System Registry** | DynamoDB-backed registry of all connected systems, their schemas, field-to-concept mappings, and cross-system equivalences. Served via API Gateway + Lambda. Shared by both agents via AgentCore Gateway. |
| **Knowledge Base** | Bedrock Knowledge Base (S3 Vectors) for semantic search over manufacturing reference documents (ISA-95, glossary, standards). |
| **S3 Tables** | Apache Iceberg tables on S3 for manufacturing data (ERP, MES, CMMS, PLM, IoT). Queried via Lambda + Athena. |
| **AgentCore Memory** | Event and semantic memory for conversation persistence across sessions. Separate memory stores for Explorer and Discovery agents. |
| **Guardrails** | Bedrock Guardrails for content filtering with violation tracking and analytics. |
| **Workflow Scheduler** | EventBridge Scheduler + Lambda for running prompts on a schedule with per-user result tracking. |

---

## Prerequisites

| Tool | Minimum Version | Purpose |
|------|----------------|---------|
| **Node.js** | 18.x+ | CDK runtime |
| **AWS CDK CLI** | 2.x | Infrastructure deployment |
| **AWS CLI** | 2.x | AWS resource management |
| **Python** | 3.11+ | Agent and ChatApp runtime |

Install CDK CLI globally:

```bash
npm install -g aws-cdk
```

> Docker is not required locally — all container builds are handled by AWS CodeBuild.

### AWS Requirements

- AWS Account with a Default VPC
- IAM permissions with access to Bedrock, Bedrock AgentCore, ECS, Cognito, ECR, DynamoDB, Secrets Manager, S3, Athena, API Gateway, EventBridge

---

## Quick Start

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd sample-ukg-for-mfg
   ```

2. **Install CDK dependencies**:
   ```bash
   cd cdk && npm install
   ```

3. **Deploy all stacks** (default: Lambda Web Adapter ingress):
   ```bash
   ./deploy-all.sh --region us-east-1 --profile your-profile
   ```

4. **Create a test user** (add `--admin` for admin access):
   ```bash
   cd ../chatapp/scripts
   ./create-user.sh --region us-east-1 --profile your-profile your-email@example.com YourPassword123@ --admin
   ```

5. **Access the application** using the URL shown in the deployment output.

The deployment creates:
- Cognito User Pool for authentication
- DynamoDB tables for usage analytics, feedback, guardrails, workflows, system registry
- Bedrock Guardrail for content filtering
- Bedrock Knowledge Base with S3 Vectors and reference documents
- AgentCore Memory (separate stores for Explorer and Discovery)
- AgentCore Runtimes for both agents
- API Gateway + Lambda for the System Registry
- Workflow Scheduler (EventBridge + Lambda)
- ChatApp ingress (ECS Express Mode and/or CloudFront + Lambda Web Adapter)

---

## Deployment Options

The application supports three ingress modes:

| Mode | Description | Est. Monthly Cost | Use Case |
|------|-------------|-------------------|----------|
| **furl** (default) | CloudFront + Lambda Web Adapter | ~$5 | Development, PoC, sporadic usage, cost optimization |
| **ecs** | ECS Express Gateway — always-on container | ~$60 | Production workloads, consistent traffic, no cold starts |
| **both** | Deploy both simultaneously | ~$65 | A/B testing, migration, redundancy |

### Deployment Command

```bash
./deploy-all.sh [options]

Options:
  --region <region>    AWS region (default: us-east-1)
  --profile <profile>  AWS CLI profile to use
  --ingress <mode>     Ingress mode: ecs, furl, or both (default: furl)
  --skip-chatapp       Deploy Foundation + Bedrock + Agent only (skip ChatApp)
  --dry-run            Show what would be deployed without deploying
  -h, --help           Show this help message
```

### Examples

```bash
# Deploy with CloudFront + Lambda Web Adapter (default, cheapest)
./deploy-all.sh --region us-east-1 --ingress furl

# Deploy with ECS Express Gateway (always-on, no cold starts)
./deploy-all.sh --region us-east-1 --ingress ecs

# Deploy both simultaneously
./deploy-all.sh --region us-east-1 --ingress both

# Dry run — see what would be deployed
./deploy-all.sh --region us-east-1 --dry-run
```

### Cost Breakdown

**Lambda Web Adapter Mode** (~$5/month typical):
- CloudFront distribution: ~$1/mo
- Lambda compute: ~$3/mo (pay-per-use)
- Lambda@Edge: ~$0.50/mo
- No charges for IPv4, ALB, or idle time
- Cold starts: first request after idle may take 3-5 seconds

**ECS Mode** (~$60/month):
- ECS Fargate: ~$18/mo (0.5 vCPU, 1GB RAM, always-on)
- Application Load Balancer: ~$16/mo (managed by Express Gateway)
- IPv4 addresses: ~$11/mo
- Data transfer: ~$0.50/mo

---

## Stack Architecture

The CDK deployment creates 7 CloudFormation stacks:

| Stack | Description | Key Resources |
|-------|-------------|---------------|
| **Foundation** | Auth, Storage, IAM, Secrets | Cognito, DynamoDB tables (10), IAM roles, Secrets Manager, System Registry table |
| **Bedrock** | AI/ML Resources | Guardrail, Knowledge Base (S3 Vectors), AgentCore Memory (×2) |
| **Agent** | Agent Infrastructure | ECR (×2), CodeBuild (×2), Explorer + Discovery AgentCore Runtimes, Observability |
| **Gateway** | Registry API | API Gateway + Lambda, AgentCore Gateway for shared registry tools |
| **S3Tables** | Data Layer | S3 Tables namespace, Athena catalog |
| **WorkflowScheduler** | Automation | Lambda executor, EventBridge Scheduler group |
| **ChatApp** | Application | ECS Express Mode and/or CloudFront + Lambda Web Adapter |

Deployment order: Foundation → Bedrock → Agent + Gateway + S3Tables → WorkflowScheduler → ChatApp

### Multi-Region Deployment

The CDK stacks support deploying to multiple regions in the same AWS account. IAM roles are automatically suffixed with the region name to avoid conflicts.

```bash
# Deploy to us-east-1
./deploy-all.sh --region us-east-1

# Deploy to eu-west-1 (same account)
./deploy-all.sh --region eu-west-1
```

---

## Local Development

Prerequisites: CDK stacks must be deployed first (`./deploy-all.sh`).

```bash
cd chatapp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Sync .env from AWS Secrets Manager (auto-populates all values)
./sync-env.sh --region us-east-1

# Or with DEV_MODE (bypasses Cognito authentication)
./sync-env.sh --region us-east-1 --dev-mode

# Run locally
uvicorn app.main:app --reload --port 8080
```

- Home / Chat: http://localhost:8080
- Admin: http://localhost:8080/admin

**DEV_MODE**: When enabled, Cognito authentication is bypassed and requests use a default `dev-user-001` user ID. Useful for rapid iteration without needing to log in.

---

## Synthetic Data Generation

Since this project connects to live data sources, you need representative manufacturing data for demos and testing. We provide a companion synthetic data generator that creates logically consistent data across ISA-95 systems.

<!-- 📸 Recommended screenshot: Synthetic data generator UI showing scenario selection -->

### Workflow

1. Use the standalone synthetic data generator app to create a data batch (e.g., Construction scenario, 2000 rows)
2. Load the generated data into an S3 Tables bucket via the generator's Load page
3. In the Universal Knowledge Graph app, go to Data Discovery and tell the agent: `Register S3 tables in "<bucket-name>"`
4. The Discovery Agent runs its 5-phase process (inspect → analyze → map → register → verify)
5. Registered systems appear in the Home page Systems and Graph tabs
6. Use the generator's "Generate Questions" feature to create prompt templates matching your data
7. Bulk upload the generated templates via Admin → Prompt Templates

---

## Knowledge Base Integration

The agent includes a Bedrock Knowledge Base for semantic search over curated manufacturing reference documents.

### Setup

The Knowledge Base is automatically created during CDK deployment with:
- S3 bucket for source documents
- S3 Vectors bucket and index for embeddings
- Bedrock Knowledge Base with Titan Embed Text v2
- Pre-loaded reference documents (ISA-95 reference, manufacturing glossary, standards comparison)

### Adding Documents

```bash
# Get the source bucket name from CDK outputs
SOURCE_BUCKET=$(cat cdk/cdk-outputs.json | jq -r '."mfg-ukg-bedrock".SourceBucketName')

# Upload documents
aws s3 cp my-document.pdf s3://${SOURCE_BUCKET}/documents/
aws s3 cp my-folder/ s3://${SOURCE_BUCKET}/documents/ --recursive

# Trigger ingestion
KB_ID=$(cat cdk/cdk-outputs.json | jq -r '."mfg-ukg-bedrock".KnowledgeBaseId')
DS_ID=$(aws bedrock-agent list-data-sources --knowledge-base-id $KB_ID \
  --query "dataSourceSummaries[0].dataSourceId" --output text --no-cli-pager)

aws bedrock-agent start-ingestion-job \
  --knowledge-base-id $KB_ID \
  --data-source-id $DS_ID --no-cli-pager
```

### Supported Formats

PDF, Plain text, Markdown, HTML, Word (.doc/.docx), CSV

### How the Agent Uses It

The Data Explorer Agent searches the Knowledge Base for relevant context before falling back to web search. Domain-specific knowledge takes precedence over general web content.

---

## Useful Commands

```bash
# List all CDK stacks
cd cdk && npx cdk list

# Deploy a specific stack
npx cdk deploy mfg-ukg-ChatApp --require-approval never

# View stack differences before deploying
npx cdk diff

# Synthesize CloudFormation templates
npx cdk synth

# View stack outputs
cat cdk/cdk-outputs.json

# Deploy test/demo data stack
npx cdk --app 'npx ts-node bin/testdata-app.ts' deploy

# Update only the ChatApp (faster for UI changes)
npx cdk deploy mfg-ukg-ChatApp --require-approval never
```

### Testing Agents via CLI

```bash
# Get the Explorer runtime ARN
ORCH_ARN=$(aws cloudformation describe-stacks \
  --stack-name mfg-ukg-Agent --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ExplorerRuntimeArn`].OutputValue' \
  --output text --no-cli-pager)

# Invoke
echo '{"prompt": "What systems are registered?"}' > /tmp/payload.json
aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn "$ORCH_ARN" \
  --runtime-session-id "$(uuidgen | tr '[:upper:]' '[:lower:]')" \
  --payload fileb:///tmp/payload.json \
  --region us-east-1 outfile.json --no-cli-pager
```

---

## Environment Variables

### ChatApp

| Variable | Required | Description |
|----------|----------|-------------|
| `COGNITO_USER_POOL_ID` | Yes | Cognito User Pool ID |
| `COGNITO_CLIENT_ID` | Yes | Cognito App Client ID |
| `COGNITO_CLIENT_SECRET` | Yes | Cognito App Client Secret |
| `AGENTCORE_RUNTIME_ARN` | Yes | Explorer AgentCore Runtime ARN |
| `DISCOVERY_RUNTIME_ARN` | Yes | Discovery AgentCore Runtime ARN |
| `MEMORY_ID` | Yes | Explorer AgentCore Memory ID |
| `DISCOVERY_MEMORY_ID` | Yes | Discovery AgentCore Memory ID |
| `USAGE_TABLE_NAME` | Yes | DynamoDB table for usage records |
| `FEEDBACK_TABLE_NAME` | Yes | DynamoDB table for feedback records |
| `GUARDRAIL_TABLE_NAME` | Yes | DynamoDB table for guardrail violations |
| `GUARDRAIL_ID` | No | Bedrock Guardrail ID for content filtering |
| `GUARDRAIL_VERSION` | No | Bedrock Guardrail version (default: DRAFT) |
| `GUARDRAIL_ENABLED` | No | Enable/disable guardrail evaluation (default: true) |
| `PROMPT_TEMPLATES_TABLE_NAME` | Yes | DynamoDB table for prompt templates |
| `APP_SETTINGS_TABLE_NAME` | Yes | DynamoDB table for application settings |
| `RUNTIME_USAGE_TABLE_NAME` | Yes | DynamoDB table for AgentCore runtime usage |
| `DISCOVERY_HISTORY_TABLE_NAME` | Yes | DynamoDB table for discovery history |
| `WORKFLOWS_TABLE_NAME` | Yes | DynamoDB table for saved workflows |
| `WORKFLOW_RESULTS_TABLE_NAME` | Yes | DynamoDB table for workflow results |
| `REGISTRY_TABLE_NAME` | Yes | DynamoDB table for system registry |
| `WORKFLOW_EXECUTOR_ARN` | No | Lambda ARN for workflow execution |
| `WORKFLOW_SCHEDULER_GROUP` | No | EventBridge Scheduler group name |
| `WORKFLOW_SCHEDULER_ROLE_ARN` | No | IAM role ARN for scheduler |
| `AWS_REGION` | Yes | AWS region |

> All values are auto-populated by `./sync-env.sh` from AWS Secrets Manager after CDK deployment.

### Agent (Explorer / Discovery)

| Variable | Description |
|----------|-------------|
| `BEDROCK_AGENTCORE_MEMORY_ID` | AgentCore Memory ID |
| `AWS_REGION` | AWS region |

---

## Project Structure

```
mfg-ukg/
├── agent-explorer/               # Explorer agent (Strands + AgentCore)
│   ├── explorer.py               # Dynamic system prompt, model config
│   ├── my_agent.py               # Agent definition with memory hooks
│   ├── guardrails.py             # Guardrail evaluation logic
│   ├── telemetry.py              # OpenTelemetry instrumentation
│   └── tools/
│       ├── query_system.py       # Query registered data sources
│       ├── knowledge_base.py     # Knowledge Base semantic search
│       ├── web_search.py         # Web search fallback
│       └── url_fetcher.py        # URL content fetching
│
├── agent-discovery/              # Discovery agent (admin-only)
│   ├── my_agent.py               # Discovery agent definition
│   ├── concepts.py               # ISA-95 concept taxonomy
│   └── tools/
│       ├── inspect.py            # Inspect data source schemas
│       ├── analyze.py            # Analyze fields and map to concepts
│       ├── register.py           # Register systems and equivalences
│       ├── discovery_helpers.py  # Shared discovery utilities
│       └── state.py              # Discovery state management
│
├── chatapp/                      # FastAPI web application
│   ├── app/
│   │   ├── main.py               # FastAPI app with routes and middleware
│   │   ├── agentcore/            # AgentCore client + memory client
│   │   ├── auth/                 # Cognito authentication
│   │   ├── admin/                # Usage analytics, evaluations, concepts
│   │   ├── routes/
│   │   │   ├── chat.py           # SSE streaming chat endpoint
│   │   │   ├── discovery.py      # Discovery agent chat endpoint
│   │   │   ├── workflows.py      # Workflow CRUD and scheduling
│   │   │   ├── ukg.py             # Universal Knowledge Graph explorer
│   │   │   ├── registry.py       # System registry API
│   │   │   ├── registry_graph.py # Graph visualization data
│   │   │   ├── admin.py          # Admin dashboard routes
│   │   │   ├── memory.py         # Memory viewer API
│   │   │   ├── feedback.py       # Feedback capture
│   │   │   ├── prompt_templates.py # Template management
│   │   │   └── app_settings.py   # Branding settings
│   │   ├── storage/              # DynamoDB storage services
│   │   ├── static/js/            # Home page, chat, system explorer, cache
│   │   └── templates/            # Jinja2 templates (home, chat, admin)
│   ├── scripts/
│   │   ├── create-user.sh        # User creation script
│   │   └── generate_test_data.py # Test data generator
│   └── sync-env.sh               # Sync .env from Secrets Manager
│
├── cdk/                          # CDK infrastructure (TypeScript)
│   ├── lib/
│   │   ├── foundation-stack.ts   # Cognito, DynamoDB, IAM, Secrets
│   │   ├── bedrock-stack.ts      # Guardrail, KB, AgentCore Memory
│   │   ├── agent-stack.ts        # ECR, CodeBuild, AgentCore Runtimes
│   │   ├── gateway-stack.ts      # API Gateway + Lambda (registry)
│   │   ├── s3tables-stack.ts     # S3 Tables + Athena catalog
│   │   ├── workflow-scheduler-stack.ts # EventBridge + Lambda
│   │   ├── chatapp-stack.ts      # ECS Express / Lambda Web Adapter
│   │   └── config.ts             # Centralized naming and configuration
│   ├── deploy-all.sh             # Full deployment script
│   └── destroy-all.sh            # Full cleanup script
│
├── data/
│   ├── kb-docs/                  # Knowledge Base source documents
│   ├── rds/                      # RDS seed SQL and loader
│   ├── s3tables/                 # S3 Tables seed data
│   └── seed/                     # DynamoDB seed scripts (templates, settings)
│
├── lambda/
│   ├── registry-gateway/         # System Registry API (API Gateway handler)
│   ├── s3tables/                 # S3 Tables query Lambda
│   └── workflow-executor/        # Scheduled workflow executor
│
├── scripts/                      # Utility scripts
│   └── cleanup-s3tables.sh       # S3 Tables cleanup
│
└── assets/                       # Documentation assets and design docs
```

---

## Cost Tracking

The system tracks usage metrics for cost analysis and projection.

> **Note:** Telemetry data is provided for monitoring purposes. Actual billing is calculated based on metered usage data and may differ. Refer to your AWS billing statement for authoritative charges.

### Captured Metrics

- **Input/Output Tokens**: Per invocation token counts by model
- **Latency**: Response time in milliseconds
- **Tool Usage**: Call counts, success/error rates per tool
- **Guardrails Violations**: Per filter type, user, and session
- **Runtime Usage**: vCPU hours, memory GB-hours per AgentCore invocation

### AgentCore Runtime Usage Costs

| Metric | Rate |
|--------|------|
| vCPU Hours | $0.0895/hour |
| Memory GB-Hours | $0.00945/GB-hour |

**How it works:**
1. AgentCore Runtime emits USAGE_LOGS with metrics per operation
2. Logs are streamed via Kinesis Data Firehose to Lambda transform functions
3. Lambda parses the logs and writes usage records to DynamoDB (keyed by session_id)
4. The admin dashboard aggregates runtime costs alongside token costs

The dashboard shows **Total Cost** = Token Cost + Runtime Cost, with per-session breakdowns and monthly projections calculated as:

```
projected_monthly = (total_cost / days_in_period) * 30
```

---

## Cleanup

To destroy all CDK-managed resources:

```bash
cd cdk
./destroy-all.sh --region us-east-1
```

Options:

```bash
./destroy-all.sh [options]

Options:
  --region <region>    AWS region (default: us-east-1)
  --profile <profile>  AWS CLI profile to use
  --yes                Auto-confirm all prompts (use with caution)
  --dry-run            Show what would be destroyed without destroying
```

---

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
