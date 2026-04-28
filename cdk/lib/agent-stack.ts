/**
 * Agent Stack - Consolidated stack for agent infrastructure, runtime, and observability.
 * 
 * This stack combines:
 * - Agent Infrastructure - ECR repo, CodeBuild, IAM role
 * - Agent Runtime - S3 deployment, build trigger, CfnRuntime
 * - Observability - CloudWatch logs, X-Ray delivery
 * 
 * Exports:
 * - AgentRuntimeArn
 */

import * as cdk from 'aws-cdk-lib';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as bedrockagentcore from 'aws-cdk-lib/aws-bedrockagentcore';
import * as firehose from 'aws-cdk-lib/aws-kinesisfirehose';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import { config, exportNames } from './config';
import { applyCommonSuppressions, applyBucketDeploymentSuppressions, applyCodeBuildSuppressions, applyBedrockSuppressions } from './nag-suppressions';
import * as path from 'path';

export class AgentStack extends cdk.Stack {
  // Infrastructure resources (shared by all agents)
  /** S3 bucket for CodeBuild source files */
  public readonly sourceBucket: s3.Bucket;
  /** IAM role for AgentCore Runtime */
  public readonly agentRuntimeRole: iam.Role;

  // Runtime resources
  /** The primary AgentCore CfnRuntime (Explorer) */
  public readonly agentRuntime: bedrockagentcore.CfnRuntime;

  // Observability resources
  /** The CloudWatch Log Group for Runtime logs */
  public readonly runtimeLogGroup: logs.LogGroup;
  /** The CloudWatch Log Group for Memory logs */
  public readonly memoryLogGroup: logs.LogGroup;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Import values from Bedrock stack
    const guardrailId = cdk.Fn.importValue(exportNames.guardrailId);
    const guardrailVersion = cdk.Fn.importValue(exportNames.guardrailVersion);
    const knowledgeBaseId = cdk.Fn.importValue(exportNames.knowledgeBaseId);
    const kbSyncStateTableName = cdk.Fn.importValue(exportNames.kbSyncStateTableName);
    const memoryId = cdk.Fn.importValue(exportNames.memoryId);
    const memoryArn = cdk.Fn.importValue(exportNames.memoryArn);
    const discoveryMemoryId = cdk.Fn.importValue(exportNames.discoveryMemoryId);

    // ========================================================================
    // AGENT INFRASTRUCTURE SECTION
    // Requirements: 1.4, 2.1
    // ========================================================================

    // --- S3 Bucket for CodeBuild source ---
    // Import access logs bucket from Foundation stack
    const accessLogsBucketName = cdk.Fn.importValue(`${config.appName}-AccessLogsBucketName`);
    const accessLogsBucket = s3.Bucket.fromBucketName(this, 'ImportedAccessLogsBucket', accessLogsBucketName);

    this.sourceBucket = new s3.Bucket(this, 'BuildSourceBucket', {
      bucketName: `${config.buildSourceBucketName}-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      serverAccessLogsBucket: accessLogsBucket,
      serverAccessLogsPrefix: 'agent-build-source/',
      lifecycleRules: [
        {
          id: 'ExpireOldObjects',
          enabled: true,
          expiration: cdk.Duration.days(7),
        },
      ],
    });

    // Acknowledge that logging permissions are handled in Foundation stack
    cdk.Annotations.of(this.sourceBucket).acknowledgeWarning('@aws-cdk/aws-s3:accessLogsPolicyNotAdded', 'Logging permissions added to access logs bucket in Foundation stack');

    // --- S3 Bucket for Athena query results ---
    const athenaResultsBucket = new s3.Bucket(this, 'AthenaResultsBucket', {
      bucketName: `athena-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      serverAccessLogsBucket: accessLogsBucket,
      serverAccessLogsPrefix: 'athena-results/',
      lifecycleRules: [
        {
          id: 'ExpireAthenaResults',
          enabled: true,
          expiration: cdk.Duration.days(7),
        },
      ],
    });

    cdk.Annotations.of(athenaResultsBucket).acknowledgeWarning('@aws-cdk/aws-s3:accessLogsPolicyNotAdded', 'Logging permissions added to access logs bucket in Foundation stack');

    // --- CodeBuild Role ---
    const codeBuildRole = new iam.Role(this, 'CodeBuildRole', {
      roleName: `${config.appName}-codebuild-role-${this.region}`,
      assumedBy: new iam.ServicePrincipal('codebuild.amazonaws.com'),
      description: 'CodeBuild role for building agent Docker images',
    });

    this.sourceBucket.grantRead(codeBuildRole);

    codeBuildRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CloudWatchLogsAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'logs:CreateLogGroup',
          'logs:CreateLogStream',
          'logs:PutLogEvents',
        ],
        resources: [
          `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/${config.explorerBuildProjectName}*`,
          `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/${config.discoveryBuildProjectName}*`,
        ],
      })
    );

    // --- Agent Runtime IAM Role ---
    this.agentRuntimeRole = new iam.Role(this, 'AgentRuntimeRole', {
      roleName: `${config.appName}-agent-runtime-role-${this.region}`,
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
        new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      ),
      description: 'IAM role for AgentCore Runtime with Bedrock, ECR, and CloudWatch permissions',
    });

    // ECR permissions
    this.agentRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ECRAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'ecr:GetDownloadUrlForLayer',
          'ecr:BatchGetImage',
          'ecr:BatchCheckLayerAvailability',
          'ecr:GetAuthorizationToken',
        ],
        resources: ['*'],
      })
    );

    // CloudWatch Logs permissions
    this.agentRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CloudWatchLogsAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'logs:CreateLogGroup',
          'logs:CreateLogStream',
          'logs:PutLogEvents',
          'logs:DescribeLogStreams',
        ],
        resources: [
          `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`,
        ],
      })
    );

    // X-Ray tracing permissions
    this.agentRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'XRayAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'xray:PutTraceSegments',
          'xray:PutTelemetryRecords',
          'xray:GetSamplingRules',
          'xray:GetSamplingTargets',
          'xray:GetSamplingStatisticSummaries',
        ],
        resources: ['*'],
      })
    );

    // Bedrock model invocation permissions
    this.agentRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockModelAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
          'bedrock:Converse',
          'bedrock:ConverseStream',
        ],
        resources: [
          'arn:aws:bedrock:*::foundation-model/*',
          'arn:aws:bedrock:*:*:inference-profile/*',
        ],
      })
    );

    // Bedrock Guardrails permissions
    this.agentRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockGuardrailAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:ApplyGuardrail',
          'bedrock:GetGuardrail',
        ],
        resources: [
          `arn:aws:bedrock:${this.region}:${this.account}:guardrail/*`,
        ],
      })
    );

    // Bedrock Knowledge Base permissions
    this.agentRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockKnowledgeBaseAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:Retrieve',
          'bedrock:RetrieveAndGenerate',
        ],
        resources: [
          `arn:aws:bedrock:${this.region}:${this.account}:knowledge-base/*`,
        ],
      })
    );

    // AgentCore Memory permissions
    this.agentRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AgentCoreMemoryAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:GetMemory',
          'bedrock-agentcore:CreateMemory',
          'bedrock-agentcore:DeleteMemory',
          'bedrock-agentcore:ListMemories',
          'bedrock-agentcore:CreateEvent',
          'bedrock-agentcore:GetEvent',
          'bedrock-agentcore:ListEvents',
          'bedrock-agentcore:DeleteEvent',
          'bedrock-agentcore:CreateMemoryRecord',
          'bedrock-agentcore:GetMemoryRecord',
          'bedrock-agentcore:ListMemoryRecords',
          'bedrock-agentcore:DeleteMemoryRecord',
          'bedrock-agentcore:SearchMemoryRecords',
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`,
        ],
      })
    );

    // Note: S3 Tables, Athena, Glue, LakeFormation, and SiteWise permissions
    // have been removed — agents use DynamoDB registry tools and generic query_system.

    // ========================================================================
    // V2 AGENT DEPLOYMENTS (Explorer + Discovery)
    // Requirements: 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 12.3
    // ========================================================================

    // Deploy timestamp forces CloudFormation to update runtimes on every deploy
    const deployTimestamp = Date.now().toString();

    // Import registry table from Foundation stack
    const registryTableArn = cdk.Fn.importValue(`${config.appName}-SystemRegistryTableArn`);
    const registryTableName = cdk.Fn.importValue(`${config.appName}-SystemRegistryTableName`);

    // --- Explorer ECR Repository ---
    const explorerRepository = new ecr.Repository(this, 'ExplorerRepository', {
      repositoryName: config.explorerRepoName,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      imageScanOnPush: true,
      lifecycleRules: [
        {
          description: 'Keep only 5 most recent images',
          maxImageCount: 5,
          rulePriority: 1,
          tagStatus: ecr.TagStatus.ANY,
        },
      ],
    });

    // --- Discovery ECR Repository ---
    const discoveryRepository = new ecr.Repository(this, 'DiscoveryRepository', {
      repositoryName: config.discoveryRepoName,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      emptyOnDelete: true,
      imageScanOnPush: true,
      lifecycleRules: [
        {
          description: 'Keep only 5 most recent images',
          maxImageCount: 5,
          rulePriority: 1,
          tagStatus: ecr.TagStatus.ANY,
        },
      ],
    });

    // Grant CodeBuild role access to new ECR repos
    explorerRepository.grantPullPush(codeBuildRole);
    discoveryRepository.grantPullPush(codeBuildRole);

    // --- Explorer S3 Source Deployment ---
    const explorerSourceDeployment = new s3deploy.BucketDeployment(this, 'ExplorerSourceDeployment', {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, '../../agent-explorer'), {
          exclude: [
            '.venv/**',
            'venv/**',
            '__pycache__/**',
            '*.pyc',
            '.git/**',
            'node_modules/**',
            '.env',
            '.bedrock_agentcore/**',
            '.bedrock_agentcore.yaml',
            '*.egg-info/**',
            '.pytest_cache/**',
            '.mypy_cache/**',
            '.ruff_cache/**',
            'deploy/**',
            '*.log',
            '.DS_Store',
          ],
        }),
      ],
      destinationBucket: this.sourceBucket,
      destinationKeyPrefix: 'explorer-source',
      prune: true,
      retainOnDelete: false,
      memoryLimit: 512,
    });

    // --- Discovery S3 Source Deployment ---
    const discoverySourceDeployment = new s3deploy.BucketDeployment(this, 'DiscoverySourceDeployment', {
      sources: [
        s3deploy.Source.asset(path.join(__dirname, '../../agent-discovery'), {
          exclude: [
            '.venv/**',
            'venv/**',
            '__pycache__/**',
            '*.pyc',
            '.git/**',
            'node_modules/**',
            '.env',
            '.bedrock_agentcore/**',
            '.bedrock_agentcore.yaml',
            '*.egg-info/**',
            '.pytest_cache/**',
            '.mypy_cache/**',
            '.ruff_cache/**',
            'deploy/**',
            '*.log',
            '.DS_Store',
          ],
        }),
      ],
      destinationBucket: this.sourceBucket,
      destinationKeyPrefix: 'discovery-source',
      prune: true,
      retainOnDelete: false,
      memoryLimit: 512,
    });

    // --- Explorer CodeBuild Project ---
    const explorerBuildProject = new codebuild.Project(this, 'ExplorerBuildProject', {
      projectName: config.explorerBuildProjectName,
      description: 'Build ARM64 Docker images for Explorer agent',
      role: codeBuildRole,
      source: codebuild.Source.s3({
        bucket: this.sourceBucket,
        path: 'explorer-source.zip',
      }),
      environment: {
        buildImage: codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
        computeType: codebuild.ComputeType.SMALL,
        privileged: true,
        environmentVariables: {
          AWS_ACCOUNT_ID: {
            type: codebuild.BuildEnvironmentVariableType.PLAINTEXT,
            value: this.account,
          },
          AWS_REGION: {
            type: codebuild.BuildEnvironmentVariableType.PLAINTEXT,
            value: this.region,
          },
          ECR_REPO_URI: {
            type: codebuild.BuildEnvironmentVariableType.PLAINTEXT,
            value: explorerRepository.repositoryUri,
          },
        },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          pre_build: {
            commands: [
              'echo Logging in to Amazon ECR...',
              'aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com',
            ],
          },
          build: {
            commands: [
              'echo Build started on `date`',
              'echo Building the Docker image...',
              'docker build -t $ECR_REPO_URI:latest .',
              'docker tag $ECR_REPO_URI:latest $ECR_REPO_URI:$CODEBUILD_BUILD_NUMBER',
            ],
          },
          post_build: {
            commands: [
              'echo Build completed on `date`',
              'echo Pushing the Docker image...',
              'docker push $ECR_REPO_URI:latest',
              'docker push $ECR_REPO_URI:$CODEBUILD_BUILD_NUMBER',
              'echo Image pushed successfully',
            ],
          },
        },
      }),
      timeout: cdk.Duration.minutes(30),
    });

    // --- Discovery CodeBuild Project ---
    const discoveryBuildProject = new codebuild.Project(this, 'DiscoveryBuildProject', {
      projectName: config.discoveryBuildProjectName,
      description: 'Build ARM64 Docker images for Discovery agent',
      role: codeBuildRole,
      source: codebuild.Source.s3({
        bucket: this.sourceBucket,
        path: 'discovery-source.zip',
      }),
      environment: {
        buildImage: codebuild.LinuxArmBuildImage.AMAZON_LINUX_2_STANDARD_3_0,
        computeType: codebuild.ComputeType.SMALL,
        privileged: true,
        environmentVariables: {
          AWS_ACCOUNT_ID: {
            type: codebuild.BuildEnvironmentVariableType.PLAINTEXT,
            value: this.account,
          },
          AWS_REGION: {
            type: codebuild.BuildEnvironmentVariableType.PLAINTEXT,
            value: this.region,
          },
          ECR_REPO_URI: {
            type: codebuild.BuildEnvironmentVariableType.PLAINTEXT,
            value: discoveryRepository.repositoryUri,
          },
        },
      },
      buildSpec: codebuild.BuildSpec.fromObject({
        version: '0.2',
        phases: {
          pre_build: {
            commands: [
              'echo Logging in to Amazon ECR...',
              'aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com',
            ],
          },
          build: {
            commands: [
              'echo Build started on `date`',
              'echo Building the Docker image...',
              'docker build -t $ECR_REPO_URI:latest .',
              'docker tag $ECR_REPO_URI:latest $ECR_REPO_URI:$CODEBUILD_BUILD_NUMBER',
            ],
          },
          post_build: {
            commands: [
              'echo Build completed on `date`',
              'echo Pushing the Docker image...',
              'docker push $ECR_REPO_URI:latest',
              'docker push $ECR_REPO_URI:$CODEBUILD_BUILD_NUMBER',
              'echo Image pushed successfully',
            ],
          },
        },
      }),
      timeout: cdk.Duration.minutes(30),
    });

    // --- Trigger Explorer CodeBuild ---
    const triggerExplorerBuild = new cr.AwsCustomResource(this, 'TriggerExplorerCodeBuild', {
      onCreate: {
        service: 'CodeBuild',
        action: 'startBuild',
        parameters: {
          projectName: explorerBuildProject.projectName,
          sourceTypeOverride: 'S3',
          sourceLocationOverride: `${this.sourceBucket.bucketName}/explorer-source/`,
          environmentVariablesOverride: [
            { name: 'DEPLOY_TIMESTAMP', value: deployTimestamp, type: 'PLAINTEXT' },
          ],
        },
        physicalResourceId: cr.PhysicalResourceId.fromResponse('build.id'),
      },
      onUpdate: {
        service: 'CodeBuild',
        action: 'startBuild',
        parameters: {
          projectName: explorerBuildProject.projectName,
          sourceTypeOverride: 'S3',
          sourceLocationOverride: `${this.sourceBucket.bucketName}/explorer-source/`,
          environmentVariablesOverride: [
            { name: 'DEPLOY_TIMESTAMP', value: deployTimestamp, type: 'PLAINTEXT' },
          ],
        },
        physicalResourceId: cr.PhysicalResourceId.fromResponse('build.id'),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ['codebuild:StartBuild'],
          resources: [explorerBuildProject.projectArn],
        }),
      ]),
    });
    triggerExplorerBuild.node.addDependency(explorerSourceDeployment);

    // --- Trigger Discovery CodeBuild ---
    const triggerDiscoveryBuild = new cr.AwsCustomResource(this, 'TriggerDiscoveryCodeBuild', {
      onCreate: {
        service: 'CodeBuild',
        action: 'startBuild',
        parameters: {
          projectName: discoveryBuildProject.projectName,
          sourceTypeOverride: 'S3',
          sourceLocationOverride: `${this.sourceBucket.bucketName}/discovery-source/`,
          environmentVariablesOverride: [
            { name: 'DEPLOY_TIMESTAMP', value: deployTimestamp, type: 'PLAINTEXT' },
          ],
        },
        physicalResourceId: cr.PhysicalResourceId.fromResponse('build.id'),
      },
      onUpdate: {
        service: 'CodeBuild',
        action: 'startBuild',
        parameters: {
          projectName: discoveryBuildProject.projectName,
          sourceTypeOverride: 'S3',
          sourceLocationOverride: `${this.sourceBucket.bucketName}/discovery-source/`,
          environmentVariablesOverride: [
            { name: 'DEPLOY_TIMESTAMP', value: deployTimestamp, type: 'PLAINTEXT' },
          ],
        },
        physicalResourceId: cr.PhysicalResourceId.fromResponse('build.id'),
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ['codebuild:StartBuild'],
          resources: [discoveryBuildProject.projectArn],
        }),
      ]),
    });
    triggerDiscoveryBuild.node.addDependency(discoverySourceDeployment);

    // --- Build Waiter Lambda (shared by Explorer + Discovery builds) ---
    const buildWaiterFunction = new lambda.Function(this, 'BuildWaiterFunction', {
      functionName: `${config.appName}-build-waiter`,
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(14),
      memorySize: 128,
      code: lambda.Code.fromInline(`
import boto3
import time
import json
import cfnresponse

def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    if event['RequestType'] == 'Delete':
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return
    
    try:
        build_id = event['ResourceProperties']['BuildId']
        codebuild = boto3.client('codebuild')
        
        max_attempts = 28
        for attempt in range(max_attempts):
            response = codebuild.batch_get_builds(ids=[build_id])
            
            if not response['builds']:
                raise Exception(f"Build {build_id} not found")
            
            build = response['builds'][0]
            status = build['buildStatus']
            
            print(f"Attempt {attempt + 1}: Build status = {status}")
            
            if status == 'SUCCEEDED':
                cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                    'BuildId': build_id,
                    'Status': status
                })
                return
            elif status in ['FAILED', 'FAULT', 'STOPPED', 'TIMED_OUT']:
                error_msg = f"Build {build_id} failed with status: {status}"
                print(error_msg)
                cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=error_msg)
                return
            
            time.sleep(30)
        
        error_msg = f"Build {build_id} timed out after 14 minutes"
        print(error_msg)
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=error_msg)
        
    except Exception as e:
        print(f"Error: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=str(e))
`),
    });

    const buildWaiterProviderLogGroup = new logs.LogGroup(this, 'BuildWaiterProviderLogs', {
      retention: logs.RetentionDays.ONE_DAY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const buildWaiterProvider = new cr.Provider(this, 'BuildWaiterProvider', {
      onEventHandler: buildWaiterFunction,
      logGroup: buildWaiterProviderLogGroup,
    });

    // --- Build Waiters for V2 agents ---
    // Grant build waiter permission to check V2 build projects
    buildWaiterFunction.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['codebuild:BatchGetBuilds'],
        resources: [explorerBuildProject.projectArn, discoveryBuildProject.projectArn],
      })
    );

    const explorerBuildWaiter = new cdk.CustomResource(this, 'ExplorerBuildWaiter', {
      serviceToken: buildWaiterProvider.serviceToken,
      properties: {
        BuildId: triggerExplorerBuild.getResponseField('build.id'),
        Timestamp: Date.now().toString(),
      },
    });
    explorerBuildWaiter.node.addDependency(triggerExplorerBuild);

    const discoveryBuildWaiter = new cdk.CustomResource(this, 'DiscoveryBuildWaiter', {
      serviceToken: buildWaiterProvider.serviceToken,
      properties: {
        BuildId: triggerDiscoveryBuild.getResponseField('build.id'),
        Timestamp: Date.now().toString(),
      },
    });
    discoveryBuildWaiter.node.addDependency(triggerDiscoveryBuild);

    // ========================================================================
    // EXPLORER RUNTIME ROLE (Req 10.7: read-only DynamoDB + Bedrock/Memory/KB/Guardrail)
    // ========================================================================

    const explorerRuntimeRole = new iam.Role(this, 'ExplorerRuntimeRole', {
      roleName: `${config.appName}-explorer-runtime-role-${this.region}`,
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
        new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      ),
      description: 'IAM role for Explorer AgentCore Runtime with read-only DynamoDB, Bedrock, Memory, KB, and Guardrail permissions',
    });

    // ECR permissions
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ECRAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'ecr:GetDownloadUrlForLayer',
          'ecr:BatchGetImage',
          'ecr:BatchCheckLayerAvailability',
          'ecr:GetAuthorizationToken',
        ],
        resources: ['*'],
      })
    );

    // CloudWatch Logs permissions
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CloudWatchLogsAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'logs:CreateLogGroup',
          'logs:CreateLogStream',
          'logs:PutLogEvents',
          'logs:DescribeLogStreams',
        ],
        resources: [
          `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`,
        ],
      })
    );

    // X-Ray tracing permissions
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'XRayAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'xray:PutTraceSegments',
          'xray:PutTelemetryRecords',
          'xray:GetSamplingRules',
          'xray:GetSamplingTargets',
          'xray:GetSamplingStatisticSummaries',
        ],
        resources: ['*'],
      })
    );

    // Read-only DynamoDB access to registry table + GSIs (Req 10.7)
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'RegistryReadOnly',
        effect: iam.Effect.ALLOW,
        actions: [
          'dynamodb:GetItem',
          'dynamodb:Query',
          'dynamodb:Scan',
        ],
        resources: [
          registryTableArn,
          `${registryTableArn}/index/*`,
        ],
      })
    );

    // Bedrock model invocation
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockModelAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
          'bedrock:Converse',
          'bedrock:ConverseStream',
        ],
        resources: [
          'arn:aws:bedrock:*::foundation-model/*',
          'arn:aws:bedrock:*:*:inference-profile/*',
        ],
      })
    );

    // Bedrock Guardrails
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockGuardrailAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:ApplyGuardrail',
          'bedrock:GetGuardrail',
        ],
        resources: [
          `arn:aws:bedrock:${this.region}:${this.account}:guardrail/*`,
        ],
      })
    );

    // Bedrock Knowledge Base
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockKnowledgeBaseAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:Retrieve',
          'bedrock:RetrieveAndGenerate',
        ],
        resources: [
          `arn:aws:bedrock:${this.region}:${this.account}:knowledge-base/*`,
        ],
      })
    );

    // AgentCore Memory
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AgentCoreMemoryAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:GetMemory',
          'bedrock-agentcore:CreateMemory',
          'bedrock-agentcore:DeleteMemory',
          'bedrock-agentcore:ListMemories',
          'bedrock-agentcore:CreateEvent',
          'bedrock-agentcore:GetEvent',
          'bedrock-agentcore:ListEvents',
          'bedrock-agentcore:DeleteEvent',
          'bedrock-agentcore:CreateMemoryRecord',
          'bedrock-agentcore:GetMemoryRecord',
          'bedrock-agentcore:ListMemoryRecords',
          'bedrock-agentcore:DeleteMemoryRecord',
          'bedrock-agentcore:SearchMemoryRecords',
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`,
        ],
      })
    );

    // Athena query permissions for query_system tool
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AthenaQueryAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'athena:StartQueryExecution',
          'athena:GetQueryExecution',
          'athena:GetQueryResults',
          'athena:StopQueryExecution',
        ],
        resources: ['*'],
      })
    );

    // Glue metadata for Athena catalog resolution
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'GlueMetadataAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'glue:GetDatabase',
          'glue:GetDatabases',
          'glue:GetTable',
          'glue:GetTables',
          'glue:GetCatalog',
          'glue:GetCatalogs',
        ],
        resources: ['*'],
      })
    );

    // S3 Tables read access for query_system
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'S3TablesReadAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          's3tables:GetTableBucket',
          's3tables:ListTableBuckets',
          's3tables:GetTable',
          's3tables:GetTableData',
          's3tables:GetTableMetadataLocation',
          's3tables:UpdateTableMetadataLocation',
          's3tables:ListTables',
          's3tables:ListNamespaces',
          's3tables:GetNamespace',
          's3tables:GetTableBucketPolicy',
        ],
        resources: [
          `arn:aws:s3tables:${this.region}:${this.account}:bucket/*`,
        ],
      })
    );

    // S3 access for Athena query results bucket
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AthenaResultsBucketAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          's3:PutObject',
          's3:GetObject',
          's3:GetBucketLocation',
        ],
        resources: [
          athenaResultsBucket.bucketArn,
          `${athenaResultsBucket.bucketArn}/*`,
        ],
      })
    );

    // ========================================================================
    // DISCOVERY RUNTIME ROLE (Req 10.8, 10.9: read-write DynamoDB + S3 + KB ingestion + RDS Data API)
    // ========================================================================

    const discoveryRuntimeRole = new iam.Role(this, 'DiscoveryRuntimeRole', {
      roleName: `${config.appName}-discovery-runtime-role-${this.region}`,
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
        new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      ),
      description: 'IAM role for Discovery Agent AgentCore Runtime with read-write DynamoDB, S3, KB ingestion, and RDS Data API permissions',
    });

    // ECR permissions
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ECRAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'ecr:GetDownloadUrlForLayer',
          'ecr:BatchGetImage',
          'ecr:BatchCheckLayerAvailability',
          'ecr:GetAuthorizationToken',
        ],
        resources: ['*'],
      })
    );

    // CloudWatch Logs permissions
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CloudWatchLogsAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'logs:CreateLogGroup',
          'logs:CreateLogStream',
          'logs:PutLogEvents',
          'logs:DescribeLogStreams',
        ],
        resources: [
          `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`,
        ],
      })
    );

    // X-Ray tracing permissions
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'XRayAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'xray:PutTraceSegments',
          'xray:PutTelemetryRecords',
          'xray:GetSamplingRules',
          'xray:GetSamplingTargets',
          'xray:GetSamplingStatisticSummaries',
        ],
        resources: ['*'],
      })
    );

    // Read-write DynamoDB access to registry table (Req 10.8)
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'RegistryReadWrite',
        effect: iam.Effect.ALLOW,
        actions: [
          'dynamodb:GetItem',
          'dynamodb:Query',
          'dynamodb:Scan',
          'dynamodb:PutItem',
          'dynamodb:BatchWriteItem',
        ],
        resources: [
          registryTableArn,
          `${registryTableArn}/index/*`,
        ],
      })
    );

    // Write access to discovery history table for audit logging
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'DiscoveryHistoryWrite',
        effect: iam.Effect.ALLOW,
        actions: [
          'dynamodb:PutItem',
        ],
        resources: [
          `arn:aws:dynamodb:${this.region}:${this.account}:table/${config.discoveryHistoryTableName}`,
        ],
      })
    );

    // Bedrock model invocation (for LLM inference during discovery)
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockModelAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
          'bedrock:Converse',
          'bedrock:ConverseStream',
        ],
        resources: [
          'arn:aws:bedrock:*::foundation-model/*',
          'arn:aws:bedrock:*:*:inference-profile/*',
        ],
      })
    );

    // Bedrock Guardrails
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockGuardrailAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:ApplyGuardrail',
          'bedrock:GetGuardrail',
        ],
        resources: [
          `arn:aws:bedrock:${this.region}:${this.account}:guardrail/*`,
        ],
      })
    );

    // S3 PutObject to KB source bucket (Req 10.9)
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'KBSourceBucketWrite',
        effect: iam.Effect.ALLOW,
        actions: [
          's3:PutObject',
        ],
        resources: [
          `arn:aws:s3:::${config.appName}-kb-${this.account}-${this.region}/*`,
        ],
      })
    );

    // S3 write access for Athena query results
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AthenaResultsBucketWrite',
        effect: iam.Effect.ALLOW,
        actions: [
          's3:PutObject',
          's3:GetObject',
          's3:GetBucketLocation',
        ],
        resources: [
          athenaResultsBucket.bucketArn,
          `${athenaResultsBucket.bucketArn}/*`,
        ],
      })
    );

    // Bedrock KB retrieval access (agent never starts ingestion jobs directly;
    // the scheduled tick Lambda in the Bedrock stack debounces that).
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockKBRetrieve',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:Retrieve',
          'bedrock:RetrieveAndGenerate',
        ],
        resources: [
          `arn:aws:bedrock:${this.region}:${this.account}:knowledge-base/*`,
        ],
      })
    );

    // Flip the dirty flag after writing learned memories to the KB source bucket.
    // The table is created in the Bedrock stack; we import its name above.
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'KbSyncStateWrite',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:UpdateItem', 'dynamodb:PutItem', 'dynamodb:GetItem'],
        resources: [
          `arn:aws:dynamodb:${this.region}:${this.account}:table/${config.kbSyncStateTableName}`,
        ],
      })
    );

    // RDS Data API access for schema inspection
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'RDSDataAPIAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'rds-data:ExecuteStatement',
          'rds-data:BatchExecuteStatement',
        ],
        resources: [
          `arn:aws:rds:${this.region}:${this.account}:cluster:*`,
        ],
      })
    );

    // Secrets Manager access for RDS credentials during inspection
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'SecretsManagerAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'secretsmanager:GetSecretValue',
        ],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:*`,
        ],
      })
    );

    // AgentCore Memory (for Discovery Agent session persistence)
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AgentCoreMemoryAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:GetMemory',
          'bedrock-agentcore:CreateMemory',
          'bedrock-agentcore:DeleteMemory',
          'bedrock-agentcore:ListMemories',
          'bedrock-agentcore:CreateEvent',
          'bedrock-agentcore:GetEvent',
          'bedrock-agentcore:ListEvents',
          'bedrock-agentcore:DeleteEvent',
          'bedrock-agentcore:CreateMemoryRecord',
          'bedrock-agentcore:GetMemoryRecord',
          'bedrock-agentcore:ListMemoryRecords',
          'bedrock-agentcore:DeleteMemoryRecord',
          'bedrock-agentcore:SearchMemoryRecords',
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`,
        ],
      })
    );

    // Import Gateway ID from Gateway stack (UUID — used for IAM reference only)
    const registryGatewayId = cdk.Fn.importValue(exportNames.registryGatewayId);

    // The gateway URL uses the name-based ID, not the UUID from create_gateway.
    // Build it from the deterministic gateway name instead of the export.
    const gatewayName = `${config.appName}-registry-gateway`;
    
    // AgentCore Gateway permissions (shared registry tools)
    explorerRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AgentCoreGatewayAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:InvokeGateway',
          'bedrock-agentcore:GetGateway',
          'bedrock-agentcore:ListGateways',
          'bedrock-agentcore:ListGatewayTargets',
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/*`,
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:*`,
        ],
      })
    );

    // ========================================================================
    // EVALUATION EXECUTION IAM ROLE (Req 3.1, 3.2, 3.3, 3.4, 5.1, 5.2)
    // Dedicated role for AgentCore Online Evaluation execution
    // ========================================================================

    const evalExecutionRole = new iam.Role(this, 'EvalExecutionRole', {
      roleName: `${config.appName}-eval-execution-role-${this.region}`,
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'IAM role for AgentCore Online Evaluation execution with evaluation APIs and CloudWatch Logs read access',
    });

    cdk.Tags.of(evalExecutionRole).add('Application', config.appName);
    cdk.Tags.of(evalExecutionRole).add('ManagedBy', 'CDK');

    // Online Evaluation API permissions (Req 3.2)
    evalExecutionRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'OnlineEvaluationAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:*OnlineEvaluation*',
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:online-evaluation-config/*`,
        ],
      })
    );

    // CloudWatch Logs read permissions for runtime log groups (Req 3.3)
    evalExecutionRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CloudWatchLogsReadAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'logs:GetLogEvents',
          'logs:FilterLogEvents',
          'logs:DescribeLogGroups',
          'logs:DescribeLogStreams',
          'logs:StartQuery',
          'logs:StopQuery',
          'logs:GetQueryResults',
          'logs:CreateLogGroup',
          'logs:CreateLogStream',
          'logs:PutLogEvents',
        ],
        resources: [
          `arn:aws:logs:${this.region}:${this.account}:log-group:*`,
          `arn:aws:logs:${this.region}:${this.account}:log-group:*:*`,
        ],
      })
    );

    // Bedrock model invocation for LLM-based evaluators (Req 3.4)
    evalExecutionRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockModelAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock:InvokeModel',
        ],
        resources: [
          'arn:aws:bedrock:*::foundation-model/*',
        ],
      })
    );

    // ========================================================================
    // EXPLORER CfnRuntime (Req 10.6, 12.3: PUBLIC network mode, no VPC)
    // ========================================================================

    const explorerRuntime = new bedrockagentcore.CfnRuntime(this, 'ExplorerRuntime', {
      agentRuntimeName: config.explorerRuntimeName,
      description: `Explorer AgentCore Runtime for ${config.appName}`,
      agentRuntimeArtifact: {
        containerConfiguration: {
          containerUri: `${explorerRepository.repositoryUri}:latest`,
        },
      },
      networkConfiguration: {
        networkMode: 'PUBLIC',
      },
      roleArn: explorerRuntimeRole.roleArn,
      protocolConfiguration: 'HTTP',
      environmentVariables: {
        REGISTRY_TABLE_NAME: registryTableName,
        REGISTRY_GATEWAY_ID: registryGatewayId,
        REGISTRY_GATEWAY_NAME: gatewayName,
        AWS_REGION: this.region,
        MEMORY_ID: memoryId,
        GUARDRAIL_ID: guardrailId,
        KB_ID: knowledgeBaseId,
        LOG_LEVEL: 'INFO',
        DEFAULT_MODEL_ID: config.defaultModelId,
      },
      tags: {
        Application: config.appName,
        ManagedBy: 'CDK',
        AgentType: 'explorer',
        DeployTimestamp: deployTimestamp,
      },
    });
    explorerRuntime.node.addDependency(explorerBuildWaiter);

    // The explorer is now the primary agent runtime (replaces V1 agent)
    this.agentRuntime = explorerRuntime;

    // Glue and Athena permissions for Discovery Agent (inspect_athena_source)
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'GlueMetadataAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'glue:GetDatabase',
          'glue:GetDatabases',
          'glue:GetTable',
          'glue:GetTables',
          'glue:GetPartition',
          'glue:GetPartitions',
        ],
        resources: ['*'],
      })
    );

    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AthenaQueryAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'athena:StartQueryExecution',
          'athena:GetQueryExecution',
          'athena:GetQueryResults',
          'athena:StopQueryExecution',
        ],
        resources: ['*'],
      })
    );

    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'S3TablesAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          's3tables:GetTableBucket',
          's3tables:ListTableBuckets',
          's3tables:GetTable',
          's3tables:GetTableData',
          's3tables:GetTableMetadataLocation',
          's3tables:UpdateTableMetadataLocation',
          's3tables:ListTables',
          's3tables:ListNamespaces',
          's3tables:GetNamespace',
          's3tables:GetTableBucketPolicy',
        ],
        resources: [
          `arn:aws:s3tables:${this.region}:${this.account}:bucket/*`,
        ],
      })
    );

    // AgentCore Gateway permissions (shared registry tools)
    discoveryRuntimeRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'AgentCoreGatewayAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:InvokeGateway',
          'bedrock-agentcore:GetGateway',
          'bedrock-agentcore:ListGateways',
          'bedrock-agentcore:ListGatewayTargets',
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/*`,
          `arn:aws:bedrock-agentcore:${this.region}:${this.account}:*`,
        ],
      })
    );

    // ========================================================================
    // DISCOVERY CfnRuntime (Req 10.6, 12.3: PUBLIC network mode, no VPC)
    // ========================================================================

    const discoveryRuntime = new bedrockagentcore.CfnRuntime(this, 'DiscoveryRuntime', {
      agentRuntimeName: config.discoveryRuntimeName,
      description: `Discovery Agent AgentCore Runtime for ${config.appName}`,
      agentRuntimeArtifact: {
        containerConfiguration: {
          containerUri: `${discoveryRepository.repositoryUri}:latest`,
        },
      },
      networkConfiguration: {
        networkMode: 'PUBLIC',
      },
      roleArn: discoveryRuntimeRole.roleArn,
      protocolConfiguration: 'HTTP',
      environmentVariables: {
        REGISTRY_TABLE_NAME: registryTableName,
        REGISTRY_GATEWAY_ID: registryGatewayId,
        REGISTRY_GATEWAY_NAME: gatewayName,
        DISCOVERY_HISTORY_TABLE_NAME: `${config.appName}-discovery-history`,
        AWS_REGION: this.region,
        MEMORY_ID: discoveryMemoryId,
        KB_ID: knowledgeBaseId,
        KB_SOURCE_BUCKET: `${config.appName}-kb-${this.account}-${this.region}`,
        KB_SYNC_STATE_TABLE: kbSyncStateTableName,
        LOG_LEVEL: 'INFO',
        DEFAULT_MODEL_ID: config.defaultModelId,
      },
      tags: {
        Application: config.appName,
        ManagedBy: 'CDK',
        AgentType: 'discovery-v2',
        DeployTimestamp: deployTimestamp,
      },
    });
    discoveryRuntime.node.addDependency(discoveryBuildWaiter);

    // ========================================================================
    // OBSERVABILITY SECTION
    // Requirements: 1.4, 2.1
    // ========================================================================

    // NOTE: X-Ray trace segment destination must be enabled for CloudWatch Logs
    // before deploying this stack in a new region. The deploy-all.sh script
    // handles this automatically via: aws xray update-trace-segment-destination

    // Use deterministic names based on the app name
    const runtimeId = `${config.appName}-runtime`;
    const memoryIdName = `${config.appName}-memory`;

    // --- CloudWatch Log Group for Runtime ---
    this.runtimeLogGroup = new logs.LogGroup(this, 'RuntimeLogGroup', {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/runtime/${runtimeId}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- Delivery Source for Application Logs ---
    const logsDeliverySource = new logs.CfnDeliverySource(this, 'ExplorerLogsDeliverySource', {
      name: `${runtimeId}-v2-logs-source`,
      logType: 'APPLICATION_LOGS',
      resourceArn: this.agentRuntime.attrAgentRuntimeArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery Source for Usage Logs ---
    const usageLogsDeliverySource = new logs.CfnDeliverySource(this, 'ExplorerUsageLogsDeliverySource', {
      name: `${runtimeId}-v2-usage-logs-source`,
      logType: 'USAGE_LOGS',
      resourceArn: this.agentRuntime.attrAgentRuntimeArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // ========================================================================
    // USAGE LOGS FIREHOSE PIPELINE
    // Delivers usage metrics to DynamoDB for cost tracking
    // ========================================================================

    // Import runtime usage table from Foundation stack
    const runtimeUsageTableArn = cdk.Fn.importValue(exportNames.runtimeUsageTableArn);
    const runtimeUsageTable = dynamodb.Table.fromTableArn(this, 'ImportedComputeUsageTable', runtimeUsageTableArn);

    // Lambda function to transform usage logs and write to DynamoDB
    const usageLogsTransformFunction = new lambda.Function(this, 'UsageLogsTransformFunction', {
      functionName: `${config.appName}-usage-logs-transform`,
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(1),
      memorySize: 256,
      environment: {
        RUNTIME_USAGE_TABLE: config.runtimeUsageTableName,
      },
      code: lambda.Code.fromInline(`
import boto3
import json
import base64
import os
from datetime import datetime, timedelta, timezone

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['RUNTIME_USAGE_TABLE'])

def handler(event, context):
    """
    Transform Firehose records from AgentCore usage logs and write to DynamoDB.
    Returns transformed records for Firehose (even though we write directly to DDB).
    
    USAGE_LOGS schema per AWS docs:
    - event_timestamp: timestamp of the log entry
    - resource_arn: ARN of the resource
    - service.name: service name
    - cloud.provider: cloud provider
    - cloud.region: cloud region
    - account.id: AWS account ID
    - region: region
    - resource.id: resource ID
    - session.id: session ID (TOP LEVEL, not in attributes)
    - agent.name: agent name
    - elapsed_time_seconds: elapsed time
    - agent.runtime.vcpu.hours.used: vCPU hours used
    - agent.runtime.memory.gb_hours.used: memory GB-hours used
    """
    output = []
    
    for record in event['records']:
        try:
            # Decode the base64 encoded data
            payload = base64.b64decode(record['data']).decode('utf-8')
            
            # Parse the JSON log entry
            log_entry = json.loads(payload)
            
            # Extract session_id from attributes (where it actually is in USAGE_LOGS)
            attributes = log_entry.get('attributes', {})
            session_id = attributes.get('session.id')
            
            if not session_id:
                # Log skipped records for debugging
                print(f"Skipping record without session_id. Log entry keys: {list(log_entry.keys())}, attributes keys: {list(attributes.keys())}")
                output.append({
                    'recordId': record['recordId'],
                    'result': 'Dropped',
                    'data': record['data']
                })
                continue
            
            # Extract timestamp (in milliseconds)
            timestamp = log_entry.get('event_timestamp')
            if not timestamp:
                timestamp = int(datetime.now().timestamp() * 1000)
            else:
                # Ensure it's in milliseconds
                if timestamp < 10000000000:  # If less than year 2286 in seconds, convert to ms
                    timestamp = int(timestamp * 1000)
            
            # Create date partition for GSI (YYYY-MM-DD)
            date_partition = datetime.fromtimestamp(timestamp / 1000).strftime('%Y-%m-%d')
            
            # Extract metrics from metrics dict (where they actually are)
            metrics = log_entry.get('metrics', {})
            vcpu_hours = metrics.get('agent.runtime.vcpu.hours.used', 0)
            memory_gb_hours = metrics.get('agent.runtime.memory.gb_hours.used', 0)
            elapsed_time = attributes.get('time_elapsed_seconds', 0)
            agent_name = attributes.get('agent.name', '')
            region = attributes.get('region') or log_entry.get('resource', {}).get('cloud.region', '')
            
            # Convert timestamp to ISO format with timezone
            dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
            iso_timestamp = dt.isoformat()
            
            # Write to DynamoDB
            item = {
                'session_id': session_id,
                'timestamp': timestamp,
                'timestamp_iso': iso_timestamp,  # ISO format: 2025-12-29T20:48:57.302658+00:00
                'date_partition': date_partition,
                'vcpu_hours': str(vcpu_hours),
                'memory_gb_hours': str(memory_gb_hours),
                'time_elapsed_seconds': str(elapsed_time),
                'agent_name': agent_name,
                'region': region,
                'resource_arn': log_entry.get('resource_arn') or log_entry.get('resource.arn', ''),
            }
            
            table.put_item(Item=item)
            
            print(f"Successfully wrote record for session {session_id}")
            
            # Return success - data is already in DDB, Firehose doesn't need to store it
            output.append({
                'recordId': record['recordId'],
                'result': 'Ok',
                'data': base64.b64encode(json.dumps(item).encode('utf-8')).decode('utf-8')
            })
            
        except Exception as e:
            print(f"Error processing record: {str(e)}")
            import traceback
            traceback.print_exc()
            output.append({
                'recordId': record['recordId'],
                'result': 'ProcessingFailed',
                'data': record['data']
            })
    
    return {'records': output}
`),
    });

    // Grant DynamoDB write permissions to Lambda
    runtimeUsageTable.grantWriteData(usageLogsTransformFunction);

    // Firehose IAM role
    const firehoseRole = new iam.Role(this, 'UsageLogsFirehoseRole', {
      roleName: `${config.appName}-usage-firehose-role-${this.region}`,
      assumedBy: new iam.ServicePrincipal('firehose.amazonaws.com'),
      description: 'IAM role for Usage Logs Firehose delivery stream',
    });

    // Grant Firehose permission to invoke Lambda
    usageLogsTransformFunction.grantInvoke(firehoseRole);

    // S3 bucket for Firehose backup/errors (required by Firehose)
    const firehoseBackupBucket = new s3.Bucket(this, 'UsageLogsFirehoseBackupBucket', {
      bucketName: `${config.appName}-usage-firehose-backup-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      lifecycleRules: [
        {
          id: 'ExpireOldBackups',
          enabled: true,
          expiration: cdk.Duration.days(7),
        },
      ],
    });

    firehoseBackupBucket.grantReadWrite(firehoseRole);

    // CloudWatch Logs for Firehose errors
    const firehoseLogGroup = new logs.LogGroup(this, 'UsageLogsFirehoseLogGroup', {
      logGroupName: `/aws/kinesisfirehose/${config.appName}-usage-logs`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const firehoseLogStream = new logs.LogStream(this, 'UsageLogsFirehoseLogStream', {
      logGroup: firehoseLogGroup,
      logStreamName: 'delivery-errors',
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    firehoseLogGroup.grantWrite(firehoseRole);

    // Firehose delivery stream with Lambda transform
    const usageLogsFirehose = new firehose.CfnDeliveryStream(this, 'UsageLogsFirehose', {
      deliveryStreamName: `${config.appName}-usage-logs-stream`,
      deliveryStreamType: 'DirectPut',
      extendedS3DestinationConfiguration: {
        bucketArn: firehoseBackupBucket.bucketArn,
        roleArn: firehoseRole.roleArn,
        bufferingHints: {
          intervalInSeconds: 60,
          sizeInMBs: 1,
        },
        compressionFormat: 'GZIP',
        prefix: 'usage-logs/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/',
        errorOutputPrefix: 'errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/',
        processingConfiguration: {
          enabled: true,
          processors: [
            {
              type: 'Lambda',
              parameters: [
                {
                  parameterName: 'LambdaArn',
                  parameterValue: usageLogsTransformFunction.functionArn,
                },
                {
                  parameterName: 'BufferSizeInMBs',
                  parameterValue: '1',
                },
                {
                  parameterName: 'BufferIntervalInSeconds',
                  parameterValue: '60',
                },
              ],
            },
          ],
        },
        cloudWatchLoggingOptions: {
          enabled: true,
          logGroupName: firehoseLogGroup.logGroupName,
          logStreamName: firehoseLogStream.logStreamName,
        },
      },
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // Delivery Destination for Usage Logs (Firehose)
    const usageLogsDeliveryDestination = new logs.CfnDeliveryDestination(this, 'ExplorerUsageLogsDeliveryDestination', {
      name: `${runtimeId}-v2-usage-firehose-destination`,
      deliveryDestinationType: 'FH',
      destinationResourceArn: usageLogsFirehose.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // Delivery: Connect Usage Logs Source to Firehose Destination
    const usageLogsDelivery = new logs.CfnDelivery(this, 'ExplorerUsageLogsDelivery', {
      deliverySourceName: usageLogsDeliverySource.name,
      deliveryDestinationArn: usageLogsDeliveryDestination.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });
    usageLogsDelivery.addDependency(usageLogsDeliverySource);
    usageLogsDelivery.addDependency(usageLogsDeliveryDestination);

    // --- Delivery Source for Traces ---
    const tracesDeliverySource = new logs.CfnDeliverySource(this, 'ExplorerTracesDeliverySource', {
      name: `${runtimeId}-v2-traces-source`,
      logType: 'TRACES',
      resourceArn: this.agentRuntime.attrAgentRuntimeArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery Destination for CloudWatch Logs ---
    const logsDeliveryDestination = new logs.CfnDeliveryDestination(this, 'ExplorerLogsDeliveryDestination', {
      name: `${runtimeId}-v2-logs-destination`,
      deliveryDestinationType: 'CWL',
      destinationResourceArn: this.runtimeLogGroup.logGroupArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery Destination for X-Ray Traces ---
    const tracesDeliveryDestination = new logs.CfnDeliveryDestination(this, 'ExplorerTracesDeliveryDestination', {
      name: `${runtimeId}-v2-traces-destination`,
      deliveryDestinationType: 'XRAY',
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery: Connect Logs Source to CloudWatch Logs Destination ---
    const logsDelivery = new logs.CfnDelivery(this, 'ExplorerLogsDelivery', {
      deliverySourceName: logsDeliverySource.name,
      deliveryDestinationArn: logsDeliveryDestination.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });
    logsDelivery.addDependency(logsDeliverySource);
    logsDelivery.addDependency(logsDeliveryDestination);

    // --- Delivery: Connect Traces Source to X-Ray Destination ---
    const tracesDelivery = new logs.CfnDelivery(this, 'ExplorerTracesDelivery', {
      deliverySourceName: tracesDeliverySource.name,
      deliveryDestinationArn: tracesDeliveryDestination.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });
    tracesDelivery.addDependency(tracesDeliverySource);
    tracesDelivery.addDependency(tracesDeliveryDestination);

    // ========================================================================
    // DISCOVERY RUNTIME OBSERVABILITY
    // Mirrors explorer observability for the discovery agent
    // ========================================================================

    const discoveryRuntimeId = `${config.appName}-discovery-runtime`;

    // --- CloudWatch Log Group for Discovery Runtime ---
    const discoveryRuntimeLogGroup = new logs.LogGroup(this, 'DiscoveryRuntimeLogGroup', {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/runtime/${discoveryRuntimeId}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- Delivery Source for Discovery Application Logs ---
    const discoveryLogsDeliverySource = new logs.CfnDeliverySource(this, 'DiscoveryLogsDeliverySource', {
      name: `${discoveryRuntimeId}-logs-source`,
      logType: 'APPLICATION_LOGS',
      resourceArn: discoveryRuntime.attrAgentRuntimeArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery Source for Discovery Traces ---
    const discoveryTracesDeliverySource = new logs.CfnDeliverySource(this, 'DiscoveryTracesDeliverySource', {
      name: `${discoveryRuntimeId}-traces-source`,
      logType: 'TRACES',
      resourceArn: discoveryRuntime.attrAgentRuntimeArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery Source for Discovery Usage Logs ---
    const discoveryUsageLogsDeliverySource = new logs.CfnDeliverySource(this, 'DiscoveryUsageLogsDeliverySource', {
      name: `${discoveryRuntimeId}-usage-logs-source`,
      logType: 'USAGE_LOGS',
      resourceArn: discoveryRuntime.attrAgentRuntimeArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery Destination for Discovery CloudWatch Logs ---
    const discoveryLogsDeliveryDestination = new logs.CfnDeliveryDestination(this, 'DiscoveryLogsDeliveryDestination', {
      name: `${discoveryRuntimeId}-logs-destination`,
      deliveryDestinationType: 'CWL',
      destinationResourceArn: discoveryRuntimeLogGroup.logGroupArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery Destination for Discovery X-Ray Traces ---
    const discoveryTracesDeliveryDestination = new logs.CfnDeliveryDestination(this, 'DiscoveryTracesDeliveryDestination', {
      name: `${discoveryRuntimeId}-traces-destination`,
      deliveryDestinationType: 'XRAY',
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery Destination for Discovery Usage Logs (reuse existing Firehose) ---
    const discoveryUsageLogsDeliveryDestination = new logs.CfnDeliveryDestination(this, 'DiscoveryUsageLogsDeliveryDestination', {
      name: `${discoveryRuntimeId}-usage-firehose-destination`,
      deliveryDestinationType: 'FH',
      destinationResourceArn: usageLogsFirehose.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // --- Delivery: Connect Discovery Logs Source to CloudWatch Logs ---
    const discoveryLogsDelivery = new logs.CfnDelivery(this, 'DiscoveryLogsDelivery', {
      deliverySourceName: discoveryLogsDeliverySource.name,
      deliveryDestinationArn: discoveryLogsDeliveryDestination.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });
    discoveryLogsDelivery.addDependency(discoveryLogsDeliverySource);
    discoveryLogsDelivery.addDependency(discoveryLogsDeliveryDestination);

    // --- Delivery: Connect Discovery Traces Source to X-Ray ---
    const discoveryTracesDelivery = new logs.CfnDelivery(this, 'DiscoveryTracesDelivery', {
      deliverySourceName: discoveryTracesDeliverySource.name,
      deliveryDestinationArn: discoveryTracesDeliveryDestination.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });
    discoveryTracesDelivery.addDependency(discoveryTracesDeliverySource);
    discoveryTracesDelivery.addDependency(discoveryTracesDeliveryDestination);

    // --- Delivery: Connect Discovery Usage Logs Source to Firehose ---
    const discoveryUsageLogsDelivery = new logs.CfnDelivery(this, 'DiscoveryUsageLogsDelivery', {
      deliverySourceName: discoveryUsageLogsDeliverySource.name,
      deliveryDestinationArn: discoveryUsageLogsDeliveryDestination.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });
    discoveryUsageLogsDelivery.addDependency(discoveryUsageLogsDeliverySource);
    discoveryUsageLogsDelivery.addDependency(discoveryUsageLogsDeliveryDestination);

    // ========================================================================
    // ONLINE EVALUATION CONFIGS (Req 1.1–1.6, 2.1–2.6, 4.1–4.4, 5.1, 5.3)
    // Automated quality evaluation for Explorer and Discovery runtimes
    // ========================================================================

    // --- Explorer Online Evaluation Config ---
    const evalConfigPrefix = config.appName.replace(/-/g, '_');
    // The evaluator reads from the runtime's auto-created log group, not the vended logs group.
    // Pattern: /aws/bedrock-agentcore/runtimes/<runtimeId>-DEFAULT
    const explorerAutoLogGroup = `/aws/bedrock-agentcore/runtimes/${explorerRuntime.attrAgentRuntimeId}-DEFAULT`;
    const discoveryAutoLogGroup = `/aws/bedrock-agentcore/runtimes/${discoveryRuntime.attrAgentRuntimeId}-DEFAULT`;

    const explorerEvalConfig = new cdk.CfnResource(this, 'ExplorerOnlineEvalConfig', {
      type: 'AWS::BedrockAgentCore::OnlineEvaluationConfig',
      properties: {
        OnlineEvaluationConfigName: `${evalConfigPrefix}_explorer_eval`,
        EvaluationExecutionRoleArn: evalExecutionRole.roleArn,
        ExecutionStatus: 'ENABLED',
        DataSourceConfig: {
          CloudWatchLogs: {
            LogGroupNames: [explorerAutoLogGroup],
            ServiceNames: [`${config.explorerRuntimeName}.DEFAULT`],
          },
        },
        Evaluators: [
          { EvaluatorId: 'Builtin.GoalSuccessRate' },
          { EvaluatorId: 'Builtin.Coherence' },
          { EvaluatorId: 'Builtin.Conciseness' },
          { EvaluatorId: 'Builtin.Correctness' },
          { EvaluatorId: 'Builtin.Helpfulness' },
          { EvaluatorId: 'Builtin.InstructionFollowing' },
          { EvaluatorId: 'Builtin.ToolParameterAccuracy' },
          { EvaluatorId: 'Builtin.ToolSelectionAccuracy' },
        ],
        Rule: {
          SamplingConfig: { SamplingPercentage: 100 },
        },
        Tags: [
          { Key: 'Application', Value: config.appName },
          { Key: 'ManagedBy', Value: 'CDK' },
        ],
      },
    });
    explorerEvalConfig.node.addDependency(explorerRuntime);
    explorerEvalConfig.node.addDependency(this.runtimeLogGroup);
    explorerEvalConfig.node.addDependency(evalExecutionRole);

    // --- Discovery Online Evaluation Config ---
    const discoveryEvalConfig = new cdk.CfnResource(this, 'DiscoveryOnlineEvalConfig', {
      type: 'AWS::BedrockAgentCore::OnlineEvaluationConfig',
      properties: {
        OnlineEvaluationConfigName: `${evalConfigPrefix}_discovery_eval`,
        EvaluationExecutionRoleArn: evalExecutionRole.roleArn,
        ExecutionStatus: 'ENABLED',
        DataSourceConfig: {
          CloudWatchLogs: {
            LogGroupNames: [discoveryAutoLogGroup],
            ServiceNames: [`${config.discoveryRuntimeName}.DEFAULT`],
          },
        },
        Evaluators: [
          { EvaluatorId: 'Builtin.GoalSuccessRate' },
          { EvaluatorId: 'Builtin.Coherence' },
          { EvaluatorId: 'Builtin.Conciseness' },
          { EvaluatorId: 'Builtin.Correctness' },
          { EvaluatorId: 'Builtin.Helpfulness' },
          { EvaluatorId: 'Builtin.InstructionFollowing' },
          { EvaluatorId: 'Builtin.ToolParameterAccuracy' },
          { EvaluatorId: 'Builtin.ToolSelectionAccuracy' },
        ],
        Rule: {
          SamplingConfig: { SamplingPercentage: 100 },
        },
        Tags: [
          { Key: 'Application', Value: config.appName },
          { Key: 'ManagedBy', Value: 'CDK' },
        ],
      },
    });
    discoveryEvalConfig.node.addDependency(discoveryRuntime);
    discoveryEvalConfig.node.addDependency(discoveryRuntimeLogGroup);
    discoveryEvalConfig.node.addDependency(evalExecutionRole);

    // ========================================================================
    // MEMORY OBSERVABILITY
    // ========================================================================

    // CloudWatch Log Group for Memory vended logs
    this.memoryLogGroup = new logs.LogGroup(this, 'MemoryLogGroup', {
      logGroupName: `/aws/vendedlogs/bedrock-agentcore/memory/${memoryIdName}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Delivery Source for Memory Application Logs
    const memoryLogsDeliverySource = new logs.CfnDeliverySource(this, 'MemoryLogsDeliverySource', {
      name: `${memoryIdName}-logs-source`,
      logType: 'APPLICATION_LOGS',
      resourceArn: memoryArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // Delivery Source for Memory Traces
    const memoryTracesDeliverySource = new logs.CfnDeliverySource(this, 'MemoryTracesDeliverySource', {
      name: `${memoryIdName}-traces-source`,
      logType: 'TRACES',
      resourceArn: memoryArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // Delivery Destination for Memory CloudWatch Logs
    const memoryLogsDeliveryDestination = new logs.CfnDeliveryDestination(this, 'MemoryLogsDeliveryDestination', {
      name: `${memoryIdName}-logs-destination`,
      deliveryDestinationType: 'CWL',
      destinationResourceArn: this.memoryLogGroup.logGroupArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // Delivery Destination for Memory X-Ray Traces
    const memoryTracesDeliveryDestination = new logs.CfnDeliveryDestination(this, 'MemoryTracesDeliveryDestination', {
      name: `${memoryIdName}-traces-destination`,
      deliveryDestinationType: 'XRAY',
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });

    // Delivery: Connect Memory Logs Source to CloudWatch Logs Destination
    const memoryLogsDelivery = new logs.CfnDelivery(this, 'MemoryLogsDelivery', {
      deliverySourceName: memoryLogsDeliverySource.name,
      deliveryDestinationArn: memoryLogsDeliveryDestination.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });
    memoryLogsDelivery.addDependency(memoryLogsDeliverySource);
    memoryLogsDelivery.addDependency(memoryLogsDeliveryDestination);

    // Delivery: Connect Memory Traces Source to X-Ray Destination
    const memoryTracesDelivery = new logs.CfnDelivery(this, 'MemoryTracesDelivery', {
      deliverySourceName: memoryTracesDeliverySource.name,
      deliveryDestinationArn: memoryTracesDeliveryDestination.attrArn,
      tags: [
        { key: 'Application', value: config.appName },
        { key: 'ManagedBy', value: 'CDK' },
      ],
    });
    memoryTracesDelivery.addDependency(memoryTracesDeliverySource);
    memoryTracesDelivery.addDependency(memoryTracesDeliveryDestination);

    // ========================================================================
    // Resource Policy for X-Ray Transaction Search
    // ========================================================================

    new logs.CfnResourcePolicy(this, 'XRayTracingPolicy', {
      policyName: 'AgentCoreTracingPolicy',
      policyDocument: JSON.stringify({
        Version: '2012-10-17',
        Statement: [
          {
            Sid: 'TransactionSearchXRayAccess',
            Effect: 'Allow',
            Principal: {
              Service: 'xray.amazonaws.com',
            },
            Action: 'logs:PutLogEvents',
            Resource: [
              `arn:aws:logs:${this.region}:${this.account}:log-group:aws/spans:*`,
              `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/application-signals/data:*`,
            ],
            Condition: {
              ArnLike: {
                'aws:SourceArn': `arn:aws:xray:${this.region}:${this.account}:*`,
              },
              StringEquals: {
                'aws:SourceAccount': this.account,
              },
            },
          },
        ],
      }),
    });

    // ========================================================================
    // Enable X-Ray Transaction Search and Sampling (Lambda-backed custom resource)
    // ========================================================================

    const xrayConfigFunction = new lambda.Function(this, 'XRayConfigFunction', {
      functionName: `${config.appName}-xray-config`,
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(2),
      memorySize: 128,
      code: lambda.Code.fromInline(`
import boto3
import json
import time
import cfnresponse

def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    if event['RequestType'] == 'Delete':
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return
    
    try:
        xray = boto3.client('xray')
        results = {}
        
        try:
            xray.update_trace_segment_destination(Destination='CloudWatchLogs')
            results['TransactionSearch'] = 'Enabled'
        except Exception as e:
            if 'already' in str(e).lower():
                results['TransactionSearch'] = 'Already enabled'
            else:
                raise e
        
        # Verify the destination is actually set (eventual consistency)
        for attempt in range(6):
            resp = xray.get_trace_segment_destination()
            dest_info = resp.get('Destination', {})
            # API returns either a dict with Status or a string
            if isinstance(dest_info, dict):
                status = dest_info.get('Status', '')
            else:
                status = str(dest_info)
            print(f"Verify attempt {attempt+1}: destination={dest_info}, status={status}")
            if status in ('ACTIVE', 'CloudWatchLogs'):
                results['Verified'] = True
                break
            time.sleep(10)
        else:
            results['Verified'] = False
            print("WARNING: Destination not yet ACTIVE after 60s, proceeding anyway")
        
        try:
            xray.update_indexing_rule(
                Name='Default',
                Rule={'Probabilistic': {'DesiredSamplingPercentage': 100}}
            )
            results['Sampling'] = 'Set to 100%'
        except Exception as e:
            results['Sampling'] = f'Warning: {str(e)}'
        
        print(f"Results: {json.dumps(results)}")
        cfnresponse.send(event, context, cfnresponse.SUCCESS, results)
        
    except Exception as e:
        print(f"Error: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=str(e))
`),
    });

    // X-Ray permissions
    xrayConfigFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TransactionSearchXRayPermissions',
        effect: iam.Effect.ALLOW,
        actions: [
          'xray:GetTraceSegmentDestination',
          'xray:UpdateTraceSegmentDestination',
          'xray:GetIndexingRules',
          'xray:UpdateIndexingRule',
        ],
        resources: ['*'],
      })
    );

    // CloudWatch Logs permissions
    xrayConfigFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TransactionSearchLogGroupPermissions',
        effect: iam.Effect.ALLOW,
        actions: [
          'logs:CreateLogGroup',
          'logs:CreateLogStream',
          'logs:PutRetentionPolicy',
        ],
        resources: [
          `arn:aws:logs:${this.region}:${this.account}:log-group:/aws/application-signals/data:*`,
          `arn:aws:logs:${this.region}:${this.account}:log-group:aws/spans:*`,
        ],
      })
    );

    // Resource policy permissions
    xrayConfigFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TransactionSearchLogsPermissions',
        effect: iam.Effect.ALLOW,
        actions: [
          'logs:PutResourcePolicy',
          'logs:DescribeResourcePolicies',
        ],
        resources: ['*'],
      })
    );

    // Application Signals permissions
    xrayConfigFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'TransactionSearchApplicationSignalsPermissions',
        effect: iam.Effect.ALLOW,
        actions: ['application-signals:StartDiscovery'],
        resources: ['*'],
      })
    );

    // Service-linked role permissions
    xrayConfigFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'CloudWatchApplicationSignalsCreateServiceLinkedRolePermissions',
        effect: iam.Effect.ALLOW,
        actions: ['iam:CreateServiceLinkedRole'],
        resources: [
          `arn:aws:iam::${this.account}:role/aws-service-role/application-signals.cloudwatch.amazonaws.com/AWSServiceRoleForCloudWatchApplicationSignals`,
        ],
        conditions: {
          StringLike: {
            'iam:AWSServiceName': 'application-signals.cloudwatch.amazonaws.com',
          },
        },
      })
    );

    xrayConfigFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'CloudWatchApplicationSignalsGetRolePermissions',
        effect: iam.Effect.ALLOW,
        actions: ['iam:GetRole'],
        resources: [
          `arn:aws:iam::${this.account}:role/aws-service-role/application-signals.cloudwatch.amazonaws.com/AWSServiceRoleForCloudWatchApplicationSignals`,
        ],
      })
    );

    xrayConfigFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'CloudWatchApplicationSignalsCloudTrailPermissions',
        effect: iam.Effect.ALLOW,
        actions: ['cloudtrail:CreateServiceLinkedChannel'],
        resources: [
          `arn:aws:cloudtrail:${this.region}:${this.account}:channel/aws-service-channel/application-signals/*`,
        ],
      })
    );

    const xrayConfigProvider = new cr.Provider(this, 'XRayConfigProvider', {
      onEventHandler: xrayConfigFunction,
      logGroup: new logs.LogGroup(this, 'XRayConfigLogs', {
        retention: logs.RetentionDays.ONE_DAY,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    const xrayConfig = new cdk.CustomResource(this, 'XRayConfig', {
      serviceToken: xrayConfigProvider.serviceToken,
      properties: {
        Timestamp: Date.now().toString(),
      },
    });

    // Ensure X-Ray trace destination is configured before any XRAY delivery resources
    tracesDelivery.node.addDependency(xrayConfig);
    discoveryTracesDelivery.node.addDependency(xrayConfig);
    memoryTracesDelivery.node.addDependency(xrayConfig);

    // ========================================================================
    // UPDATE SECRETS MANAGER WITH AGENT RUNTIME ARN
    // Requirements: 2.1, 2.3
    // ========================================================================
    
    // Import secret ARN from Foundation stack
    const secretArn = cdk.Fn.importValue(exportNames.secretArn);
    
    // Lambda function to merge values into existing secret
    const updateSecretFunction = new lambda.Function(this, 'UpdateSecretFunction', {
      functionName: `${config.appName}-update-secret-agent`,
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(1),
      memorySize: 128,
      code: lambda.Code.fromInline(`
import boto3
import json
import cfnresponse

def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    if event['RequestType'] == 'Delete':
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return
    
    try:
        secret_id = event['ResourceProperties']['SecretId']
        new_values = json.loads(event['ResourceProperties']['NewValues'])
        
        client = boto3.client('secretsmanager')
        
        # Get existing secret
        response = client.get_secret_value(SecretId=secret_id)
        existing = json.loads(response['SecretString'])
        
        # Merge new values
        existing.update(new_values)
        
        # Update secret
        client.put_secret_value(
            SecretId=secret_id,
            SecretString=json.dumps(existing)
        )
        
        print(f"Updated secret with keys: {list(new_values.keys())}")
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {'Updated': list(new_values.keys())})
        
    except Exception as e:
        print(f"Error: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=str(e))
`),
    });

    updateSecretFunction.addToRolePolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: [
          'secretsmanager:GetSecretValue',
          'secretsmanager:PutSecretValue',
        ],
        resources: [
          `arn:aws:secretsmanager:${this.region}:${this.account}:secret:${config.secretName}*`,
        ],
      })
    );

    const updateSecretProviderLogGroup = new logs.LogGroup(this, 'UpdateSecretProviderLogs', {
      retention: logs.RetentionDays.ONE_DAY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const updateSecretProvider = new cr.Provider(this, 'UpdateSecretProvider', {
      onEventHandler: updateSecretFunction,
      logGroup: updateSecretProviderLogGroup,
    });

    const updateSecretWithAgentRuntime = new cdk.CustomResource(this, 'UpdateSecretWithAgentRuntime', {
      serviceToken: updateSecretProvider.serviceToken,
      properties: {
        SecretId: secretArn,
        NewValues: JSON.stringify({
          explorer_runtime_arn: explorerRuntime.attrAgentRuntimeArn,
          discovery_runtime_arn: discoveryRuntime.attrAgentRuntimeArn,
          registry_gateway_id: registryGatewayId,
          explorer_eval_config_id: explorerEvalConfig.getAtt('OnlineEvaluationConfigId').toString(),
          discovery_eval_config_id: discoveryEvalConfig.getAtt('OnlineEvaluationConfigId').toString(),
        }),
        Timestamp: Date.now().toString(),
      },
    });

    // Ensure secret update happens after all runtimes are created
    updateSecretWithAgentRuntime.node.addDependency(this.agentRuntime);
    updateSecretWithAgentRuntime.node.addDependency(explorerRuntime);
    updateSecretWithAgentRuntime.node.addDependency(discoveryRuntime);

    // ========================================================================
    // STACK OUTPUTS AND EXPORTS
    // Requirements: 2.3
    // ========================================================================

    // --- Agent Runtime Export (for ChatApp stack) ---
    new cdk.CfnOutput(this, 'AgentRuntimeArn', {
      value: this.agentRuntime.attrAgentRuntimeArn,
      description: 'AgentCore Runtime ARN',
      exportName: exportNames.agentRuntimeArn,
    });

    // --- Additional outputs (not exported) ---
    new cdk.CfnOutput(this, 'AgentRuntimeId', {
      value: this.agentRuntime.attrAgentRuntimeId,
      description: 'AgentCore Runtime ID',
    });

    new cdk.CfnOutput(this, 'AgentRuntimeVersion', {
      value: this.agentRuntime.attrAgentRuntimeVersion,
      description: 'AgentCore Runtime Version',
    });

    new cdk.CfnOutput(this, 'BuildSourceBucketName', {
      value: this.sourceBucket.bucketName,
      description: 'S3 bucket name for CodeBuild source files',
    });

    new cdk.CfnOutput(this, 'RuntimeLogGroupArn', {
      value: this.runtimeLogGroup.logGroupArn,
      description: 'CloudWatch Log Group ARN for Runtime logs',
    });

    new cdk.CfnOutput(this, 'RuntimeLogGroupName', {
      value: this.runtimeLogGroup.logGroupName!,
      description: 'CloudWatch Log Group name for Runtime logs',
    });

    new cdk.CfnOutput(this, 'MemoryLogGroupArn', {
      value: this.memoryLogGroup.logGroupArn,
      description: 'CloudWatch Log Group ARN for Memory logs',
    });

    new cdk.CfnOutput(this, 'MemoryLogGroupName', {
      value: this.memoryLogGroup.logGroupName!,
      description: 'CloudWatch Log Group name for Memory logs',
    });

    new cdk.CfnOutput(this, 'GenAIDashboardUrl', {
      value: `https://${this.region}.console.aws.amazon.com/cloudwatch/home?region=${this.region}#gen-ai-observability/agent-core/agents`,
      description: 'GenAI Observability Dashboard URL',
    });

    new cdk.CfnOutput(this, 'XRayTracesUrl', {
      value: `https://${this.region}.console.aws.amazon.com/cloudwatch/home?region=${this.region}#xray:service-map`,
      description: 'X-Ray Service Map URL',
    });

    // --- V2 Agent Runtime Exports ---
    new cdk.CfnOutput(this, 'ExplorerRuntimeArn', {
      value: explorerRuntime.attrAgentRuntimeArn,
      description: 'Explorer AgentCore Runtime ARN',
      exportName: exportNames.explorerRuntimeArn,
    });

    new cdk.CfnOutput(this, 'DiscoveryRuntimeArn', {
      value: discoveryRuntime.attrAgentRuntimeArn,
      description: 'Discovery Agent AgentCore Runtime ARN',
      exportName: exportNames.discoveryRuntimeArn,
    });

    new cdk.CfnOutput(this, 'ExplorerRepositoryUri', {
      value: explorerRepository.repositoryUri,
      description: 'ECR repository URI for Explorer container images',
    });

    new cdk.CfnOutput(this, 'DiscoveryRepositoryUri', {
      value: discoveryRepository.repositoryUri,
      description: 'ECR repository URI for Discovery Agent container images',
    });

    // --- Evaluation Config ID Exports (Req 7.5) ---
    new cdk.CfnOutput(this, 'ExplorerEvalConfigId', {
      value: explorerEvalConfig.getAtt('OnlineEvaluationConfigId').toString(),
      description: 'Online Evaluation Config ID for Explorer runtime',
      exportName: `${config.appName}-ExplorerEvalConfigId`,
    });

    new cdk.CfnOutput(this, 'DiscoveryEvalConfigId', {
      value: discoveryEvalConfig.getAtt('OnlineEvaluationConfigId').toString(),
      description: 'Online Evaluation Config ID for Discovery runtime',
      exportName: `${config.appName}-DiscoveryEvalConfigId`,
    });

    // ========================================================================
    // CDK-NAG SUPPRESSIONS
    // ========================================================================
    
    applyCommonSuppressions(this);
    applyBucketDeploymentSuppressions(this);
    applyCodeBuildSuppressions(this);
    applyBedrockSuppressions(this);

    // Suppress ECR authorization token wildcard (required by ECR)
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/AgentRuntimeRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'ECR GetAuthorizationToken requires Resource::* as it is account-level, not repository-specific.',
          appliesTo: ['Resource::*'],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Runtime logs require wildcard for dynamic log group names.',
          appliesTo: [`Resource::arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Bedrock Guardrail ID is dynamic. Scoped to guardrail resources only.',
          appliesTo: [`Resource::arn:aws:bedrock:${this.region}:${this.account}:guardrail/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Bedrock Knowledge Base ID is dynamic. Scoped to knowledge-base resources only.',
          appliesTo: [`Resource::arn:aws:bedrock:${this.region}:${this.account}:knowledge-base/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Memory ID is dynamic. Scoped to memory resources only.',
          appliesTo: [`Resource::arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`],
        },
      ]
    );

    // Suppress CodeBuild role wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/CodeBuildRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'CodeBuild log groups include build number. Scoped to specific project prefix.',
          appliesTo: [
            `Resource::arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/${config.explorerBuildProjectName}*`,
            `Resource::arn:aws:logs:${this.region}:${this.account}:log-group:/aws/codebuild/${config.discoveryBuildProjectName}*`,
          ],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'CodeBuild needs access to all objects in source bucket.',
          appliesTo: ['Resource::<BuildSourceBucketB61842F6.Arn>/*'],
        },
      ]
    );

    // Suppress BucketDeployment wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/Custom::CDKBucketDeployment8693BB64968944B69AAFB0CC9EB8756C512MiB/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'BucketDeployment needs access to CDK assets bucket for deployment.',
          appliesTo: [`Resource::arn:aws:s3:::cdk-hnb659fds-assets-${this.account}-${this.region}/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'BucketDeployment needs access to all objects in destination bucket.',
          appliesTo: ['Resource::<BuildSourceBucketB61842F6.Arn>/*'],
        },
      ]
    );

    // Suppress provider framework wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/BuildWaiterProvider/framework-onEvent/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'CDK Provider framework requires lambda:InvokeFunction with wildcard for versioned invocations.',
          appliesTo: ['Resource::<BuildWaiterFunction2EBEED87.Arn>:*'],
        },
      ]
    );

    // Suppress XRay config function wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/XRayConfigFunction/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'X-Ray configuration requires account-level permissions for trace settings.',
          appliesTo: ['Resource::*'],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Application Signals log groups are AWS-managed with fixed names.',
          appliesTo: [
            `Resource::arn:aws:logs:${this.region}:${this.account}:log-group:/aws/application-signals/data:*`,
            `Resource::arn:aws:logs:${this.region}:${this.account}:log-group:aws/spans:*`,
          ],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'CloudTrail channel for Application Signals requires wildcard.',
          appliesTo: [`Resource::arn:aws:cloudtrail:${this.region}:${this.account}:channel/aws-service-channel/application-signals/*`],
        },
      ]
    );

    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/XRayConfigProvider/framework-onEvent/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'CDK Provider framework requires lambda:InvokeFunction with wildcard for versioned invocations.',
          appliesTo: ['Resource::<XRayConfigFunctionCF1D2705.Arn>:*'],
        },
      ]
    );

    // Suppress update secret function wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/UpdateSecretFunction/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Secret ARN includes random suffix. Scoped to specific secret name prefix.',
          appliesTo: [`Resource::arn:aws:secretsmanager:${this.region}:${this.account}:secret:${config.secretName}*`],
        },
      ]
    );

    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/UpdateSecretProvider/framework-onEvent/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'CDK Provider framework requires lambda:InvokeFunction with wildcard for versioned invocations.',
          appliesTo: ['Resource::<UpdateSecretFunction83556651.Arn>:*'],
        },
      ]
    );

    // Suppress Usage Logs Transform Lambda wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/UsageLogsTransformFunction/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'DynamoDB table ARN imported from Foundation stack requires index/* pattern for GSI access.',
          appliesTo: ['Resource::<ImportedComputeUsageTable.Arn>/index/*'],
        },
      ]
    );

    // Suppress Firehose role wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/UsageLogsFirehoseRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Firehose needs access to all objects in backup bucket for error handling.',
          appliesTo: ['Resource::<UsageLogsFirehoseBackupBucket2A1E4868.Arn>/*'],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Lambda invoke permission requires wildcard for versioned function invocations.',
          appliesTo: ['Resource::<UsageLogsTransformFunctionCDE17FC9.Arn>:*'],
        },
      ]
    );

    // Suppress Firehose backup bucket - acceptable for starter kit
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/UsageLogsFirehoseBackupBucket/Resource`,
      [
        {
          id: 'AwsSolutions-S1',
          reason: 'Firehose backup bucket does not require access logging for starter kit. Contains only error records.',
        },
      ]
    );

    // Suppress Firehose encryption - uses S3 managed encryption
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/UsageLogsFirehose`,
      [
        {
          id: 'AwsSolutions-KDF1',
          reason: 'Firehose uses S3 managed encryption for backup bucket. Server-side encryption enabled on destination.',
        },
      ]
    );

    // Suppress ExplorerRuntimeRole wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/ExplorerRuntimeRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'ECR GetAuthorizationToken requires Resource::* as it is account-level, not repository-specific.',
          appliesTo: ['Resource::*'],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Runtime logs require wildcard for dynamic log group names created by the service.',
          appliesTo: [`Resource::arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'DynamoDB GSI access requires index/* pattern. Table ARN is imported from Foundation stack via CloudFormation export.',
          appliesTo: ['Resource::mfg-ukg-SystemRegistryTableArn/index/*'],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Bedrock Guardrail ID is dynamic (created in Bedrock stack). Scoped to guardrail resources only.',
          appliesTo: [`Resource::arn:aws:bedrock:${this.region}:${this.account}:guardrail/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Bedrock Knowledge Base ID is dynamic (created in Bedrock stack). Scoped to knowledge-base resources only.',
          appliesTo: [`Resource::arn:aws:bedrock:${this.region}:${this.account}:knowledge-base/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Memory ID is dynamic (created in Bedrock stack). Scoped to memory resources only.',
          appliesTo: [`Resource::arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'S3 Tables bucket names are dynamic (user-provisioned). Scoped to s3tables bucket resources only.',
          appliesTo: [`Resource::arn:aws:s3tables:${this.region}:${this.account}:bucket/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Gateway and resource IDs are dynamic. Scoped to bedrock-agentcore resources in this account/region.',
          appliesTo: [
            `Resource::arn:aws:bedrock-agentcore:${this.region}:${this.account}:*`,
            `Resource::arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/*`,
          ],
        },
      ]
    );

    // Suppress DiscoveryRuntimeRole wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/DiscoveryRuntimeRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'ECR GetAuthorizationToken requires Resource::* as it is account-level, not repository-specific.',
          appliesTo: ['Resource::*'],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Runtime logs require wildcard for dynamic log group names created by the service.',
          appliesTo: [`Resource::arn:aws:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'DynamoDB GSI access requires index/* pattern. Table ARN is imported from Foundation stack via CloudFormation export.',
          appliesTo: ['Resource::mfg-ukg-SystemRegistryTableArn/index/*'],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Bedrock Guardrail ID is dynamic (created in Bedrock stack). Scoped to guardrail resources only.',
          appliesTo: [`Resource::arn:aws:bedrock:${this.region}:${this.account}:guardrail/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Knowledge Base S3 source bucket needs object-level access for document upload during discovery.',
          appliesTo: [`Resource::arn:aws:s3:::${config.appName}-kb-${this.account}-${this.region}/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Bedrock Knowledge Base ID is dynamic (created in Bedrock stack). Scoped to knowledge-base resources only.',
          appliesTo: [`Resource::arn:aws:bedrock:${this.region}:${this.account}:knowledge-base/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'RDS Data API requires cluster-level wildcard as cluster ARNs are user-provided at runtime for schema inspection.',
          appliesTo: [`Resource::arn:aws:rds:${this.region}:${this.account}:cluster:*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Secrets Manager access requires wildcard as RDS credential secret ARNs are user-provided at runtime.',
          appliesTo: [`Resource::arn:aws:secretsmanager:${this.region}:${this.account}:secret:*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Memory ID is dynamic (created in Bedrock stack). Scoped to memory resources only.',
          appliesTo: [`Resource::arn:aws:bedrock-agentcore:${this.region}:${this.account}:memory/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'S3 Tables bucket names are dynamic (user-provisioned). Scoped to s3tables bucket resources only.',
          appliesTo: [`Resource::arn:aws:s3tables:${this.region}:${this.account}:bucket/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Gateway and resource IDs are dynamic. Scoped to bedrock-agentcore resources in this account/region.',
          appliesTo: [
            `Resource::arn:aws:bedrock-agentcore:${this.region}:${this.account}:*`,
            `Resource::arn:aws:bedrock-agentcore:${this.region}:${this.account}:gateway/*`,
          ],
        },
      ]
    );

    // Suppress EvalExecutionRole wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${config.appName}-Agent/EvalExecutionRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Online Evaluation API uses action wildcards (*OnlineEvaluation*) as the service defines multiple evaluation actions. Scoped to evaluation config resources.',
          appliesTo: ['Action::bedrock-agentcore:*OnlineEvaluation*'],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Online Evaluation config IDs are dynamic. Scoped to online-evaluation-config resources only.',
          appliesTo: [`Resource::arn:aws:bedrock-agentcore:${this.region}:${this.account}:online-evaluation-config/*`],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Evaluation service needs to read logs from dynamically-named runtime log groups for quality assessment.',
          appliesTo: [
            `Resource::arn:aws:logs:${this.region}:${this.account}:log-group:*`,
            `Resource::arn:aws:logs:${this.region}:${this.account}:log-group:*:*`,
          ],
        },
      ]
    );
  }
}
