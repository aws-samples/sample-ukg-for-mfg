/**
 * Centralized configuration for AgentCore Chat Application CDK stacks.
 * 
 * This module defines all resource naming conventions and environment settings
 * used across the CDK stacks. Configuration can be overridden via environment
 * variables for deployment to different environments.
 */

/**
 * Application configuration interface defining all resource naming conventions
 * and deployment settings.
 */
export interface AppConfig {
  /** Base name for resources (e.g., "htmx-chatapp") */
  appName: string;
  
  /** AWS region for deployment */
  region: string;
  
  /** AWS account ID */
  account: string;

  /** Deployment mode: 'ecs' (ECS Express Gateway), 'furl' (Lambda Function URL), or 'both' */
  deploymentMode: 'ecs' | 'furl' | 'both';

  // Cognito configuration
  /** Cognito User Pool name */
  cognitoPoolName: string;
  
  // DynamoDB table names
  /** Usage records table name */
  usageTableName: string;
  /** Feedback table name */
  feedbackTableName: string;
  /** Guardrail violations table name */
  guardrailTableName: string;
  /** Prompt templates table name */
  promptTemplatesTableName: string;
  /** App settings table name */
  appSettingsTableName: string;
  /** Runtime usage table name */
  runtimeUsageTableName: string;
  /** Discovery history table name */
  discoveryHistoryTableName: string;
  /** Workflows table name */
  workflowsTableName: string;
  /** Workflow results table name */
  workflowResultsTableName: string;

  // Bedrock configuration
  /** Bedrock Guardrail name */
  guardrailName: string;
  /** Knowledge Base name */
  kbName: string;
  /** Knowledge Base source bucket name */
  kbSourceBucketName: string;

  // Secrets configuration
  /** Secrets Manager secret name */
  secretName: string;

  // ECS configuration
  /** ECS service name */
  ecsServiceName: string;
  /** CPU units for ECS tasks */
  cpu: number;
  /** Memory in MB for ECS tasks */
  memory: number;
  /** Minimum number of ECS tasks */
  minTasks: number;
  /** Maximum number of ECS tasks */
  maxTasks: number;
  /** Container port */
  containerPort: number;

  // Lambda configuration (used when deploymentMode = 'furl' or 'both')
  /** Lambda function name */
  lambdaFunctionName: string;
  /** Memory in MB for Lambda function */
  lambdaMemory: number;
  /** Timeout in seconds for Lambda function (max 900 = 15 minutes) */
  lambdaTimeout: number;
  /** Reserved concurrent executions to prevent runaway costs */
  lambdaReservedConcurrency: number;

  // ECR configuration
  /** ChatApp ECR repository name */
  chatappRepoName: string;

  // CodeBuild configuration
  /** S3 bucket name for CodeBuild source */
  buildSourceBucketName: string;

  // V2 Agent configuration (Explorer + Discovery)
  /** System Registry DynamoDB table name */
  registryTableName: string;
  /** Explorer ECR repository name */
  explorerRepoName: string;
  /** Discovery Agent ECR repository name */
  discoveryRepoName: string;
  /** CodeBuild project name for Explorer builds */
  explorerBuildProjectName: string;
  /** CodeBuild project name for Discovery Agent builds */
  discoveryBuildProjectName: string;
  /** Explorer AgentCore Runtime name */
  explorerRuntimeName: string;
  /** Discovery Agent AgentCore Runtime name */
  discoveryRuntimeName: string;
}

/**
 * Get configuration value from environment variable or use default.
 * @param envVar - Environment variable name
 * @param defaultValue - Default value if env var is not set
 * @returns The environment variable value or default
 */
function getEnvOrDefault(envVar: string, defaultValue: string): string {
  return process.env[envVar] || defaultValue;
}

/**
 * Get numeric configuration value from environment variable or use default.
 * @param envVar - Environment variable name
 * @param defaultValue - Default value if env var is not set
 * @returns The parsed number or default
 */
function getEnvNumberOrDefault(envVar: string, defaultValue: number): number {
  const value = process.env[envVar];
  if (value) {
    const parsed = parseInt(value, 10);
    return isNaN(parsed) ? defaultValue : parsed;
  }
  return defaultValue;
}



/**
 * Default application configuration.
 * Values can be overridden via environment variables.
 * Note: deploymentMode is set dynamically from CDK context in bin/app.ts
 */
// Extract appName first so all other defaults can reference it
const appName = getEnvOrDefault('APP_NAME', 'mfg-ukg');

export const config: AppConfig = {
  // Base configuration
  appName,
  region: getEnvOrDefault('AWS_REGION', getEnvOrDefault('CDK_DEFAULT_REGION', 'us-east-1')),
  account: getEnvOrDefault('AWS_ACCOUNT_ID', getEnvOrDefault('CDK_DEFAULT_ACCOUNT', '')),
  
  // Deployment mode (set from CDK context via setDeploymentMode function)
  deploymentMode: 'ecs',

  // Cognito configuration
  cognitoPoolName: getEnvOrDefault('COGNITO_POOL_NAME', `${appName}-users`),

  // DynamoDB table names
  usageTableName: getEnvOrDefault('USAGE_TABLE_NAME', `${appName}-usage-records`),
  feedbackTableName: getEnvOrDefault('FEEDBACK_TABLE_NAME', `${appName}-feedback`),
  guardrailTableName: getEnvOrDefault('GUARDRAIL_TABLE_NAME', `${appName}-guardrail-violations`),
  promptTemplatesTableName: getEnvOrDefault('PROMPT_TEMPLATES_TABLE_NAME', `${appName}-prompt-templates`),
  appSettingsTableName: getEnvOrDefault('APP_SETTINGS_TABLE_NAME', `${appName}-app-settings`),
  runtimeUsageTableName: getEnvOrDefault('RUNTIME_USAGE_TABLE_NAME', `${appName}-runtime-usage`),
  discoveryHistoryTableName: getEnvOrDefault('DISCOVERY_HISTORY_TABLE_NAME', `${appName}-discovery-history`),
  workflowsTableName: getEnvOrDefault('WORKFLOWS_TABLE_NAME', `${appName}-saved-workflows`),
  workflowResultsTableName: getEnvOrDefault('WORKFLOW_RESULTS_TABLE_NAME', `${appName}-workflow-results`),

  // Bedrock configuration
  guardrailName: getEnvOrDefault('GUARDRAIL_NAME', `${appName}-guardrail`),
  kbName: getEnvOrDefault('KB_NAME', `${appName}-kb`),
  kbSourceBucketName: getEnvOrDefault('KB_SOURCE_BUCKET_NAME', `${appName}-kb-source`),

  // Secrets configuration
  secretName: getEnvOrDefault('SECRET_NAME', `${appName}/appconfig`),

  // ECS configuration (used when deploymentMode = 'ecs' or 'both')
  ecsServiceName: getEnvOrDefault('ECS_SERVICE_NAME', `${appName}-express`),
  cpu: getEnvNumberOrDefault('ECS_CPU', 2048),
  memory: getEnvNumberOrDefault('ECS_MEMORY', 4096),
  minTasks: getEnvNumberOrDefault('ECS_MIN_TASKS', 2),
  maxTasks: getEnvNumberOrDefault('ECS_MAX_TASKS', 10),
  containerPort: getEnvNumberOrDefault('CONTAINER_PORT', 8080),

  // Lambda configuration (used when deploymentMode = 'furl' or 'both')
  lambdaFunctionName: getEnvOrDefault('LAMBDA_FUNCTION_NAME', `${appName}-lambda`),
  lambdaMemory: getEnvNumberOrDefault('LAMBDA_MEMORY', 1024),
  lambdaTimeout: getEnvNumberOrDefault('LAMBDA_TIMEOUT', 900),
  lambdaReservedConcurrency: getEnvNumberOrDefault('LAMBDA_RESERVED_CONCURRENCY', 10),

  // ECR configuration
  chatappRepoName: getEnvOrDefault('CHATAPP_REPO_NAME', appName),

  // CodeBuild configuration
  buildSourceBucketName: getEnvOrDefault('BUILD_SOURCE_BUCKET_NAME', `${appName}-build-source`),

  // V2 Agent configuration (Explorer + Discovery)
  registryTableName: getEnvOrDefault('REGISTRY_TABLE_NAME', `${appName}-system-registry`),
  explorerRepoName: getEnvOrDefault('EXPLORER_REPO_NAME', `${appName}-explorer`),
  discoveryRepoName: getEnvOrDefault('DISCOVERY_REPO_NAME', `${appName}-discovery`),
  explorerBuildProjectName: getEnvOrDefault('EXPLORER_BUILD_PROJECT_NAME', `${appName}-explorer-build`),
  discoveryBuildProjectName: getEnvOrDefault('DISCOVERY_BUILD_PROJECT_NAME', `${appName}-discovery-build`),
  explorerRuntimeName: getEnvOrDefault('EXPLORER_RUNTIME_NAME', `${appName}_explorer`.replace(/-/g, '_')),
  discoveryRuntimeName: getEnvOrDefault('DISCOVERY_RUNTIME_NAME', `${appName}_discovery`.replace(/-/g, '_')),
};

/**
 * Stack export name prefixes for cross-stack references.
 * These are used with Fn.importValue for stack isolation.
 * 
 * With the consolidated 4-stack architecture:
 * - Foundation Stack: Auth, Storage, IAM, Secrets
 * - Bedrock Stack: Guardrail, Knowledge Base, Memory
 * - Agent Stack: Agent Infrastructure, Runtime, Observability
 * - ChatApp Stack: ECS Express Mode service
 * 
 * Only exports needed for cross-stack references are defined here.
 * Internal resources within a stack use direct references.
 */
export const exportNames = {
  // ========================================================================
  // Foundation Stack exports (used by ChatApp Stack)
  // ========================================================================
  
  /** ECS task execution role ARN - used by ChatApp for container execution */
  executionRoleArn: `${config.appName}-ExecutionRoleArn`,
  /** ECS task role ARN - used by ChatApp for runtime permissions */
  taskRoleArn: `${config.appName}-TaskRoleArn`,
  /** ECS infrastructure role ARN - used by ChatApp for Express Mode */
  infrastructureRoleArn: `${config.appName}-InfrastructureRoleArn`,
  /** Secrets Manager secret ARN - used by ChatApp to inject configuration */
  secretArn: `${config.appName}-SecretArn`,

  // ========================================================================
  // Bedrock Stack exports (used by Agent Stack)
  // ========================================================================
  
  /** Bedrock Guardrail ID - used by Agent Runtime for content filtering */
  guardrailId: `${config.appName}-GuardrailId`,
  /** Bedrock Guardrail Version - used by Agent Runtime */
  guardrailVersion: `${config.appName}-GuardrailVersion`,
  /** Bedrock Knowledge Base ID - used by Agent Runtime for semantic search */
  knowledgeBaseId: `${config.appName}-KnowledgeBaseId`,
  /** AgentCore Memory ID - used by Agent Runtime for conversation persistence */
  memoryId: `${config.appName}-MemoryId`,
  /** AgentCore Memory ARN - used by Agent Stack for observability setup */
  memoryArn: `${config.appName}-MemoryArn`,
  /** Discovery Agent AgentCore Memory ID */
  discoveryMemoryId: `${config.appName}-DiscoveryMemoryId`,
  /** Discovery Agent AgentCore Memory ARN */
  discoveryMemoryArn: `${config.appName}-DiscoveryMemoryArn`,

  // ========================================================================
  // Agent Stack exports (used by deploy scripts for secrets update)
  // ========================================================================
  
  /** AgentCore Runtime ARN - stored in secrets for ChatApp access */
  agentRuntimeArn: `${config.appName}-AgentRuntimeArn`,

  /** Runtime usage table ARN - used by Agent Stack for Firehose delivery */
  runtimeUsageTableArn: `${config.appName}-RuntimeUsageTableArn`,

  // ========================================================================
  // ChatApp Stack exports (terminal outputs, not used by other stacks)
  // ========================================================================
  
  /** ECS service URL placeholder - actual URL fetched by deploy script (used when mode = 'ecs' or 'both') */
  ecsServiceUrl: `${config.appName}-EcsServiceUrl`,
  /** ECS Express Gateway Service ARN (used when mode = 'ecs' or 'both') */
  ecsServiceArn: `${config.appName}-EcsServiceArn`,
  
  /** Lambda Function URL (used when mode = 'furl' or 'both') */
  lambdaFunctionUrl: `${config.appName}-LambdaFunctionUrl`,
  /** Lambda Function ARN (used when mode = 'furl' or 'both') */
  lambdaFunctionArn: `${config.appName}-LambdaFunctionArn`,
  
  /** ECR repository URI for chat application images (always exported) */
  chatappRepositoryUri: `${config.appName}-ChatAppRepositoryUri`,

  // ========================================================================
  // S3 Tables Stack exports
  // ========================================================================

  s3TablesBucketArn: `${config.appName}-S3TablesBucketArn`,
  s3TablesBucketName: `${config.appName}-S3TablesBucketName`,

  // ========================================================================
  // V2 Agent Stack exports (Explorer + Discovery)
  // ========================================================================

  /** System Registry table name - used by Agent Stack for V2 agents */
  registryTableName: `${config.appName}-RegistryTableName`,
  /** System Registry table ARN - used by Agent Stack for IAM permissions */
  registryTableArn: `${config.appName}-RegistryTableArn`,
  /** Explorer AgentCore Runtime ARN - stored in secrets for ChatApp routing */
  explorerRuntimeArn: `${config.appName}-ExplorerRuntimeArn`,
  /** Discovery Agent AgentCore Runtime ARN - stored in secrets for admin endpoint */
  discoveryRuntimeArn: `${config.appName}-DiscoveryRuntimeArn`,

  // ========================================================================
  // Gateway Stack exports (shared registry tools)
  // ========================================================================

  /** AgentCore Gateway ID for shared registry tools */
  registryGatewayId: `${config.appName}-RegistryGatewayId`,
};

/**
 * Set the deployment mode from CDK context.
 * This should be called from bin/app.ts after the CDK app is created.
 * @param mode - The deployment mode from CDK context
 */
export function setDeploymentMode(mode: string): void {
  if (mode === 'ecs' || mode === 'furl' || mode === 'both') {
    (config as any).deploymentMode = mode;
  } else {
    console.warn(`Invalid deployment mode '${mode}', using default 'ecs'`);
    (config as any).deploymentMode = 'ecs';
  }
}

/**
 * Validate that required configuration is present.
 * Throws an error if required values are missing.
 */
export function validateConfig(): void {
  if (!config.account) {
    throw new Error(
      'AWS account ID is required. Set CDK_DEFAULT_ACCOUNT or AWS_ACCOUNT_ID environment variable.'
    );
  }
  if (!config.region) {
    throw new Error(
      'AWS region is required. Set CDK_DEFAULT_REGION or AWS_REGION environment variable.'
    );
  }
}

export default config;
