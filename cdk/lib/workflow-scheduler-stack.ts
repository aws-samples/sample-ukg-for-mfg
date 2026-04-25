/**
 * Workflow Scheduler Stack
 *
 * Provisions:
 * - Lambda function that reads workflows from DynamoDB and invokes AgentCore
 * - EventBridge Scheduler group for workflow schedules
 * - IAM role for EventBridge Scheduler to invoke the Lambda
 *
 * Individual schedules are created/updated/deleted at runtime by the chatapp
 * when users manage workflows.
 */
import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as path from 'path';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import { config, exportNames } from './config';
import { applyCommonSuppressions, applyBedrockSuppressions } from './nag-suppressions';

export class WorkflowSchedulerStack extends cdk.Stack {
  public readonly executorFunction: lambda.Function;
  public readonly schedulerGroup: scheduler.CfnScheduleGroup;
  public readonly schedulerRole: iam.Role;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Import cross-stack values
    const secretArn = cdk.Fn.importValue(exportNames.secretArn);
    const orchestratorRuntimeArn = cdk.Fn.importValue(exportNames.orchestratorRuntimeArn);

    // ── Lambda Function ──────────────────────────────────────────────
    this.executorFunction = new lambda.Function(this, 'WorkflowExecutor', {
      functionName: `${config.appName}-workflow-executor`,
      runtime: lambda.Runtime.PYTHON_3_13,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda/workflow-executor')),
      timeout: cdk.Duration.minutes(10),
      memorySize: 256,
      environment: {
        WORKFLOWS_TABLE_NAME: config.workflowsTableName,
        WORKFLOW_RESULTS_TABLE_NAME: config.workflowResultsTableName,
        ORCHESTRATOR_RUNTIME_ARN: orchestratorRuntimeArn,
        AWS_REGION_OVERRIDE: config.region,
      },
      description: 'Executes scheduled workflows by invoking AgentCore Runtime',
    });

    // DynamoDB permissions — read workflows, write results
    this.executorFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'dynamodb:GetItem',
        'dynamodb:PutItem',
        'dynamodb:Query',
        'dynamodb:Scan',
      ],
      resources: [
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${config.workflowsTableName}`,
        `arn:aws:dynamodb:${this.region}:${this.account}:table/${config.workflowResultsTableName}`,
      ],
    }));

    // AgentCore Runtime permissions — need wildcard suffix for /runtime-endpoint/DEFAULT
    this.executorFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'bedrock-agentcore:InvokeAgentRuntime',
      ],
      resources: [
        `arn:aws:bedrock-agentcore:${this.region}:${this.account}:runtime/*`,
      ],
    }));

    // Bedrock model invocation (the agent runtime needs this transitively)
    this.executorFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
      ],
      resources: [
        'arn:aws:bedrock:*::foundation-model/*',
        'arn:aws:bedrock:*:*:inference-profile/*',
      ],
    }));

    // ── EventBridge Scheduler Group ──────────────────────────────────
    this.schedulerGroup = new scheduler.CfnScheduleGroup(this, 'WorkflowScheduleGroup', {
      name: `${config.appName}-workflows`,
    });

    // ── IAM Role for EventBridge Scheduler → Lambda ──────────────────
    this.schedulerRole = new iam.Role(this, 'SchedulerRole', {
      roleName: `${config.appName}-workflow-scheduler-role-${this.region}`,
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      description: 'Allows EventBridge Scheduler to invoke the workflow executor Lambda',
    });

    this.executorFunction.grantInvoke(this.schedulerRole);

    // ── Exports ──────────────────────────────────────────────────────
    new cdk.CfnOutput(this, 'ExecutorFunctionArn', {
      value: this.executorFunction.functionArn,
      description: 'Workflow executor Lambda ARN',
      exportName: `${config.appName}-WorkflowExecutorArn`,
    });

    new cdk.CfnOutput(this, 'SchedulerGroupName', {
      value: this.schedulerGroup.name!,
      description: 'EventBridge Scheduler group for workflows',
      exportName: `${config.appName}-WorkflowSchedulerGroup`,
    });

    new cdk.CfnOutput(this, 'SchedulerRoleArn', {
      value: this.schedulerRole.roleArn,
      description: 'IAM role ARN for EventBridge Scheduler',
      exportName: `${config.appName}-WorkflowSchedulerRoleArn`,
    });

    // ========================================================================
    // CDK-NAG SUPPRESSIONS
    // ========================================================================

    applyCommonSuppressions(this);
    applyBedrockSuppressions(this);

    // Suppress scheduler role Lambda invoke wildcard (CDK grantInvoke adds :* for version/alias)
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${id}/SchedulerRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'CDK grantInvoke() adds :* suffix for Lambda version/alias invocations. Scoped to specific workflow executor function.',
          appliesTo: [`Resource::<WorkflowExecutorB757A2C7.Arn>:*`],
        },
      ]
    );

    // Suppress workflow executor Lambda role wildcards
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${id}/WorkflowExecutor/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore Runtime ARN is dynamic (created in Agent stack). Scoped to runtime resources only.',
          appliesTo: [`Resource::arn:aws:bedrock-agentcore:${this.region}:${this.account}:runtime/*`],
        },
      ]
    );
  }
}
