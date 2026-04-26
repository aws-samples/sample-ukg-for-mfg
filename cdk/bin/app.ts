#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { Aspects } from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { config, validateConfig, setDeploymentMode } from '../lib/config';
import { FoundationStack } from '../lib/foundation-stack';
import { BedrockStack } from '../lib/bedrock-stack';

import { AgentStack } from '../lib/agent-stack';
import { ChatAppStack } from '../lib/chatapp-stack';
import { GatewayStack } from '../lib/gateway-stack';
import { WorkflowSchedulerStack } from '../lib/workflow-scheduler-stack';

validateConfig();
const app = new cdk.App();
Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }));
const ingressMode = app.node.tryGetContext('ingress') || 'ecs';
setDeploymentMode(ingressMode);
const env: cdk.Environment = {
  account: config.account || process.env.CDK_DEFAULT_ACCOUNT,
  region: config.region || process.env.CDK_DEFAULT_REGION || 'us-east-1',
};

const foundationStack = new FoundationStack(app, `${config.appName}-Foundation`, { env, description: 'Universal Knowledge Graph: Cognito, DynamoDB, IAM, Secrets', stackName: `${config.appName}-foundation` });
const bedrockStack = new BedrockStack(app, `${config.appName}-Bedrock`, { env, description: 'Universal Knowledge Graph: Bedrock Guardrail, Knowledge Base, Memory', stackName: `${config.appName}-bedrock` });
bedrockStack.addDependency(foundationStack);
const gatewayStack = new GatewayStack(app, `${config.appName}-Gateway`, { env, description: 'Universal Knowledge Graph: AgentCore Gateway with shared registry tools', stackName: `${config.appName}-gateway` });
gatewayStack.addDependency(foundationStack);
const agentStack = new AgentStack(app, `${config.appName}-Agent`, { env, description: 'Universal Knowledge Graph: Agent infrastructure and runtime', stackName: `${config.appName}-agent` });
agentStack.addDependency(bedrockStack);
agentStack.addDependency(foundationStack);
agentStack.addDependency(gatewayStack);
const chatAppStack = new ChatAppStack(app, `${config.appName}-ChatApp`, { env, description: 'Universal Knowledge Graph: ECS Express Mode chat application', stackName: `${config.appName}-chatapp` });
chatAppStack.addDependency(foundationStack);
chatAppStack.addDependency(agentStack);
const workflowSchedulerStack = new WorkflowSchedulerStack(app, `${config.appName}-WorkflowScheduler`, { env, description: 'Universal Knowledge Graph: Workflow scheduler Lambda + EventBridge', stackName: `${config.appName}-workflow-scheduler` });
workflowSchedulerStack.addDependency(foundationStack);
workflowSchedulerStack.addDependency(agentStack);

[foundationStack, bedrockStack, agentStack, gatewayStack, chatAppStack, workflowSchedulerStack].forEach((s) => {
  cdk.Tags.of(s).add('Application', config.appName);
  cdk.Tags.of(s).add('ManagedBy', 'CDK');
});
app.synth();
