/**
 * Gateway Stack — AgentCore Gateway with shared Lambda-backed registry tools.
 *
 * Creates:
 * - Lambda function implementing the 4 registry read tools
 * - AgentCore Gateway with the Lambda target
 * - IAM permissions for Gateway → Lambda invocation
 *
 * Both the Explorer and Discovery agents consume these tools via
 * the Gateway MCP protocol, eliminating duplicated tool code.
 *
 * Exports:
 * - GatewayId (for agents to reference)
 */

import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import { config, exportNames } from './config';
import { applyCommonSuppressions } from './nag-suppressions';
import * as path from 'path';
import * as fs from 'fs';

export class GatewayStack extends cdk.Stack {
  public readonly gatewayId: string;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Import registry table from Foundation stack
    const registryTableArn = cdk.Fn.importValue(`${config.appName}-SystemRegistryTableArn`);
    const registryTableName = cdk.Fn.importValue(`${config.appName}-SystemRegistryTableName`);

    // ========================================================================
    // REGISTRY GATEWAY LAMBDA
    // ========================================================================

    const registryLambda = new lambda.Function(this, 'RegistryGatewayFunction', {
      functionName: `${config.appName}-registry-gateway`,
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'handler.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda/registry-gateway')),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        REGISTRY_TABLE_NAME: registryTableName,
        CONCEPTS_TABLE_NAME: config.conceptsTableName,
        AWS_REGION_OVERRIDE: this.region,
        LOG_LEVEL: 'INFO',
      },
      logRetention: logs.RetentionDays.ONE_MONTH,
    });

    // Read-only DynamoDB access to registry table + GSIs
    registryLambda.addToRolePolicy(
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

    // Read-only access to the concepts vocabulary table so both agents can
    // resolve canonical concept IDs via the shared gateway. The table name is
    // deterministic from config (Foundation stack owns + seeds it), so we build
    // the ARN directly rather than adding a new cross-stack export. A plain
    // Scan needs only the base table — no index/* wildcard.
    const conceptsTableArn = `arn:${cdk.Aws.PARTITION}:dynamodb:${this.region}:${this.account}:table/${config.conceptsTableName}`;

    registryLambda.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'ConceptsReadOnly',
        effect: iam.Effect.ALLOW,
        actions: [
          'dynamodb:GetItem',
          'dynamodb:Query',
          'dynamodb:Scan',
        ],
        resources: [conceptsTableArn],
      })
    );

    // ========================================================================
    // AGENTCORE GATEWAY
    // ========================================================================

    // Load tool schema from file
    const toolSchemaPath = path.join(__dirname, '../../lambda/registry-gateway/tool_schema.json');
    const toolDefinitions = JSON.parse(fs.readFileSync(toolSchemaPath, 'utf-8'));

    // Gateway IAM role — allows AgentCore to invoke the Lambda
    const gatewayRole = new iam.Role(this, 'GatewayRole', {
      roleName: `${config.appName}-gateway-role-${this.region}`,
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'IAM role for AgentCore Gateway to invoke registry Lambda target',
    });

    registryLambda.grantInvoke(gatewayRole);

    // Gateway needs WorkloadIdentity permissions for its internal setup
    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'WorkloadIdentityAccess',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:CreateWorkloadIdentity',
          'bedrock-agentcore:GetWorkloadIdentity',
          'bedrock-agentcore:DeleteWorkloadIdentity',
          'bedrock-agentcore:ListWorkloadIdentities',
        ],
        resources: ['*'],
      })
    );

    // The Gateway itself is created via a custom resource since CfnGateway
    // may not yet be in the CDK L1 constructs. We use the AWS SDK directly.
    const createGatewayFunction = new lambda.Function(this, 'CreateGatewayFunction', {
      functionName: `${config.appName}-create-gateway`,
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(5),
      memorySize: 256,
      code: lambda.Code.fromInline(`
import boto3
import json
import cfnresponse
import time

def handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    props = event['ResourceProperties']
    gateway_name = props['GatewayName']
    role_arn = props['RoleArn']
    lambda_arn = props['LambdaArn']
    tool_definitions = json.loads(props['ToolDefinitions'])
    target_name = props['TargetName']
    
    from botocore.config import Config as BotoConfig
    client = boto3.client('bedrock-agentcore-control', config=BotoConfig(parameter_validation=False))
    
    # Build the correct targetConfiguration per boto3 docs:
    # targetConfiguration.mcp.lambda.lambdaArn + toolSchema.inlinePayload
    target_config = {
        'mcp': {
            'lambda': {
                'lambdaArn': lambda_arn,
                'toolSchema': {
                    'inlinePayload': tool_definitions,
                },
            },
        },
    }
    
    # Lambda targets use the gateway's IAM role for invocation
    cred_config = [
        {
            'credentialProviderType': 'GATEWAY_IAM_ROLE',
        }
    ]
    
    if event['RequestType'] == 'Delete':
        try:
            gateway_id = event.get('PhysicalResourceId', '')
            if gateway_id:
                # Delete targets first
                try:
                    targets = client.list_gateway_targets(gatewayIdentifier=gateway_id)
                    for target in targets.get('items', []):
                        client.delete_gateway_target(
                            gatewayIdentifier=gateway_id,
                            targetId=target['targetId'],
                        )
                        print(f"Deleted target {target['targetId']}")
                except Exception as e:
                    print(f"Could not list/delete targets (non-fatal): {e}")
                
                client.delete_gateway(gatewayIdentifier=gateway_id)
                print(f"Deleted gateway {gateway_id}")
        except Exception as e:
            print(f"Delete error (non-fatal): {e}")
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return
    
    try:
        if event['RequestType'] == 'Create':
            # Create the gateway
            gw_response = client.create_gateway(
                name=gateway_name,
                roleArn=role_arn,
                protocolType='MCP',
                authorizerType='AWS_IAM',
                description='Shared registry tools for Explorer and Discovery agents',
            )
            gateway_id = gw_response['gatewayId']
            print(f"Created gateway (initial ID): {gateway_id}")
            
            # Wait for gateway to be READY
            for attempt in range(30):
                gw = client.get_gateway(gatewayIdentifier=gateway_id)
                status = gw.get('status', 'UNKNOWN')
                print(f"Gateway status (attempt {attempt+1}): {status}")
                if status == 'READY':
                    break
                if status == 'FAILED':
                    reasons = gw.get('statusReasons', [])
                    raise Exception(f"Gateway creation failed: {reasons}")
                time.sleep(5)
            
            # The create_gateway response returns a UUID, but the actual
            # usable gateway ID (for URLs) is the name-based one.
            # get_gateway with UUID still returns UUID — must use list_gateways
            # and match by name to get the real ID.
            resolved_id = gateway_id
            gateways = client.list_gateways()
            for g in gateways.get('items', []):
                if g['name'] == gateway_name:
                    resolved_id = g['gatewayId']
                    break
            print(f"Resolved gateway ID: {resolved_id} (was {gateway_id})")
            gateway_id = resolved_id
            
            # Add Lambda target with tool definitions
            target_response = client.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name=target_name,
                targetConfiguration=target_config,
                credentialProviderConfigurations=cred_config,
                description='DynamoDB System Registry read tools',
            )
            target_id = target_response['targetId']
            print(f"Created target: {target_id}")
            
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'GatewayId': gateway_id,
                'TargetId': target_id,
            }, physicalResourceId=gateway_id)
        
        elif event['RequestType'] == 'Update':
            # Always resolve the gateway ID by name via list_gateways.
            # The PhysicalResourceId may be a UUID from create_gateway,
            # but the URL-usable ID is the name-based one from list_gateways.
            gateway_id = None
            gateways = client.list_gateways()
            for gw in gateways.get('items', []):
                if gw['name'] == gateway_name:
                    gateway_id = gw['gatewayId']
                    print(f"Found gateway by name: {gateway_id}")
                    break
            if not gateway_id:
                # Fallback to PhysicalResourceId
                gateway_id = event.get('PhysicalResourceId', '')
                print(f"Fallback to PhysicalResourceId: {gateway_id}")
            
            # Update gateway
            try:
                client.update_gateway(
                    gatewayIdentifier=gateway_id,
                    name=gateway_name,
                    roleArn=role_arn,
                    description='Shared registry tools for Explorer and Discovery agents',
                )
            except Exception as e:
                print(f"Gateway update warning: {e}")
            
            # Find and update existing target (or create new)
            target_id = None
            try:
                targets = client.list_gateway_targets(gatewayIdentifier=gateway_id)
                for target in targets.get('items', []):
                    if target.get('name') == target_name:
                        target_id = target['targetId']
                        break
            except Exception as e:
                print(f"list_gateway_targets error, will create target: {e}")
            
            if target_id:
                client.update_gateway_target(
                    gatewayIdentifier=gateway_id,
                    targetId=target_id,
                    name=target_name,
                    targetConfiguration=target_config,
                    credentialProviderConfigurations=cred_config,
                    description='DynamoDB System Registry read tools',
                )
                print(f"Updated target: {target_id}")
            else:
                target_response = client.create_gateway_target(
                    gatewayIdentifier=gateway_id,
                    name=target_name,
                    targetConfiguration=target_config,
                    credentialProviderConfigurations=cred_config,
                    description='DynamoDB System Registry read tools',
                )
                target_id = target_response['targetId']
                print(f"Created new target: {target_id}")
            
            # IMPORTANT: Always return the existing PhysicalResourceId on Update.
            # If we return a different ID (e.g. name-based vs UUID), CFN treats
            # it as a resource replacement, which tries to change the exported
            # RegistryGatewayId value — blocked when agent-stack is importing it.
            existing_physical_id = event.get('PhysicalResourceId', gateway_id)
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {
                'GatewayId': gateway_id,
                'TargetId': target_id,
            }, physicalResourceId=existing_physical_id)
    
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=str(e))
`),
    });

    // Permissions for the gateway management Lambda
    // Use explicit actions — wildcard patterns like *Gateway* don't resolve
    // consistently across all accounts for newer services.
    createGatewayFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'AgentCoreGatewayManagement',
        effect: iam.Effect.ALLOW,
        actions: [
          'bedrock-agentcore:CreateGateway',
          'bedrock-agentcore:GetGateway',
          'bedrock-agentcore:UpdateGateway',
          'bedrock-agentcore:DeleteGateway',
          'bedrock-agentcore:ListGateways',
          'bedrock-agentcore:CreateGatewayTarget',
          'bedrock-agentcore:GetGatewayTarget',
          'bedrock-agentcore:UpdateGatewayTarget',
          'bedrock-agentcore:DeleteGatewayTarget',
          'bedrock-agentcore:ListGatewayTargets',
          'bedrock-agentcore:CreateWorkloadIdentity',
          'bedrock-agentcore:GetWorkloadIdentity',
          'bedrock-agentcore:DeleteWorkloadIdentity',
          'bedrock-agentcore:ListWorkloadIdentities',
        ],
        resources: ['*'],
      })
    );

    // Allow passing the gateway role
    createGatewayFunction.addToRolePolicy(
      new iam.PolicyStatement({
        sid: 'PassGatewayRole',
        effect: iam.Effect.ALLOW,
        actions: ['iam:PassRole'],
        resources: [gatewayRole.roleArn],
      })
    );

    const createGatewayProviderLogGroup = new logs.LogGroup(this, 'CreateGatewayProviderLogs', {
      retention: logs.RetentionDays.ONE_DAY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const createGatewayProvider = new cdk.custom_resources.Provider(this, 'CreateGatewayProvider', {
      onEventHandler: createGatewayFunction,
      logGroup: createGatewayProviderLogGroup,
    });

    const gateway = new cdk.CustomResource(this, 'RegistryGateway', {
      serviceToken: createGatewayProvider.serviceToken,
      properties: {
        GatewayName: `${config.appName}-registry-gateway`,
        RoleArn: gatewayRole.roleArn,
        LambdaArn: registryLambda.functionArn,
        ToolDefinitions: JSON.stringify(toolDefinitions),
        TargetName: 'registry',
        // NOTE: No Timestamp here — adding one forces an Update on every deploy,
        // which causes the Lambda to re-resolve the gateway ID by name. If that
        // resolved ID differs from the stored PhysicalResourceId, CFN treats it
        // as a resource replacement and tries to change the exported value, which
        // fails when mfg-ukg-agent is importing mfg-ukg-RegistryGatewayId.
      },
    });

    // Ensure the Lambda's IAM policy is fully applied before the custom resource runs
    gateway.node.addDependency(createGatewayFunction);

    // ========================================================================
    // GATEWAY OBSERVABILITY — REMOVED
    // Vended log delivery for Gateway requires AllowVendedLogDeliveryForResource
    // which is not available on fresh accounts. Re-add when service support improves.
    // ========================================================================

    // ========================================================================
    // OUTPUTS & EXPORTS
    // ========================================================================

    // The PhysicalResourceId returned by our Lambda IS the gateway ID
    new cdk.CfnOutput(this, 'RegistryGatewayId', {
      value: gateway.ref,
      description: 'AgentCore Gateway ID for registry tools',
      exportName: exportNames.registryGatewayId,
    });

    new cdk.CfnOutput(this, 'RegistryLambdaArn', {
      value: registryLambda.functionArn,
      description: 'Registry Gateway Lambda function ARN',
    });

    // ========================================================================
    // CDK-NAG SUPPRESSIONS
    // ========================================================================

    applyCommonSuppressions(this);

    // Suppress GatewayRole Lambda invoke wildcard (CDK grantInvoke adds :* for version/alias)
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${id}/GatewayRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'CDK grantInvoke() adds :* suffix for Lambda version/alias invocations. Scoped to specific registry gateway function.',
          appliesTo: ['Resource::<RegistryGatewayFunction49876594.Arn>:*'],
        },
        {
          id: 'AwsSolutions-IAM5',
          reason: 'AgentCore WorkloadIdentity operations require account-level permissions as identity names are dynamic.',
          appliesTo: ['Resource::*'],
        },
      ]
    );

    // Suppress RegistryGatewayFunction DynamoDB index wildcard
    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${id}/RegistryGatewayFunction/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'DynamoDB GSI access requires index/* pattern. Table ARN is imported from Foundation stack via CloudFormation export.',
          appliesTo: ['Resource::mfg-ukg-SystemRegistryTableArn/index/*'],
        },
      ]
    );

    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${id}/CreateGatewayFunction/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'Gateway management requires account-level permissions for create/update/delete operations on dynamically-named gateways.',
          appliesTo: ['Resource::*'],
        },
      ]
    );

    NagSuppressions.addResourceSuppressionsByPath(
      this,
      `/${id}/CreateGatewayProvider/framework-onEvent/ServiceRole/DefaultPolicy/Resource`,
      [
        {
          id: 'AwsSolutions-IAM5',
          reason: 'CDK Provider framework requires lambda:InvokeFunction with wildcard for versioned invocations.',
          appliesTo: ['Resource::<CreateGatewayFunction4EB55EAB.Arn>:*'],
        },
      ]
    );
  }
}
