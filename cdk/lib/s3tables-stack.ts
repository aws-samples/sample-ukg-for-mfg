/**
 * S3 Tables Stack - Test/demo Iceberg table storage for simulated manufacturing data.
 *
 * This stack is fully decoupled from the main application stacks and is
 * intended for test/demo purposes only. It accepts all cross-stack
 * dependencies as props (taskRoleArn, secretArn) and is deployed via
 * its own CDK app entry point (bin/testdata-app.ts).
 *
 * Creates one S3 Table Bucket with namespaces per domain and tables per system:
 *
 * Namespace: erp
 *   - sap_indianapolis   (work orders, equipment, materials)
 *   - oracle_pune        (work orders)
 *   - custom_monterrey   (work orders)
 *
 * Namespace: mes
 *   - ignition_indianapolis  (production runs, OEE, machine status)
 *
 * Namespace: cmms
 *   - maximo_indianapolis    (work orders, assets)
 *
 * Namespace: plm
 *   - teamcenter_global      (BOM, part specs, design revisions)
 *
 * Namespace: iot
 *   - sitewise_indianapolis  (sensor readings - vibration, temperature)
 *
 * The awslabs.s3-tables-mcp-server queries these tables with SQL.
 * Each specialist agent gets an MCPClient pointed at its namespace/table.
 *
 * Exports:
 * - S3TablesBucketArn, S3TablesBucketName
 */

import * as cdk from 'aws-cdk-lib';
import * as s3tables from 'aws-cdk-lib/aws-s3tables';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cr from 'aws-cdk-lib/custom-resources';
import { NagSuppressions } from 'cdk-nag';
import { Construct } from 'constructs';
import { config, exportNames } from './config';

/**
 * Props for S3TablesStack.
 * All cross-stack references are passed explicitly so the stack
 * can be deployed standalone or wired into the main CDK app.
 */
export interface S3TablesStackProps extends cdk.StackProps {
  /** ARN of the IAM role that needs S3 Tables query permissions (e.g. ECS task role). */
  taskRoleArn: string;
  /** ARN of the Secrets Manager secret to update with bucket info. */
  secretArn: string;
}

export class S3TablesStack extends cdk.Stack {
  public readonly tableBucket: s3tables.CfnTableBucket;

  constructor(scope: Construct, id: string, props: S3TablesStackProps) {
    super(scope, id, props);

    // ========================================================================
    // S3 Table Bucket
    // ========================================================================

    this.tableBucket = new s3tables.CfnTableBucket(this, 'ManufacturingTableBucket', {
      tableBucketName: `${config.appName}-manufacturing-${this.account}-${this.region}`,
      unreferencedFileRemoval: {
        status: 'Enabled',
        noncurrentDays: 7,
        unreferencedDays: 7,
      },
    });

    // ========================================================================
    // IAM permissions for the agent runtime role to query S3 Tables
    // ========================================================================

    const taskRole = iam.Role.fromRoleArn(this, 'ImportedTaskRole', props.taskRoleArn);

    // S3 Tables requires s3tables:* + s3:* on the table bucket ARN
    new iam.Policy(this, 'S3TablesAccessPolicy', {
      policyName: `${config.appName}-s3tables-access`,
      roles: [taskRole],
      statements: [
        new iam.PolicyStatement({
          sid: 'S3TablesDataAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            's3tables:GetTableBucket',
            's3tables:ListTableBuckets',
            's3tables:CreateNamespace',
            's3tables:GetNamespace',
            's3tables:ListNamespaces',
            's3tables:DeleteNamespace',
            's3tables:CreateTable',
            's3tables:GetTable',
            's3tables:ListTables',
            's3tables:DeleteTable',
            's3tables:RenameTable',
            's3tables:GetTableData',
            's3tables:PutTableData',
            's3tables:DeleteTableData',
            's3tables:GetTableMaintenanceConfiguration',
            's3tables:PutTableMaintenanceConfiguration',
            's3tables:GetTableBucketMaintenanceConfiguration',
            's3tables:PutTableBucketMaintenanceConfiguration',
            's3tables:GetTablePolicy',
            's3tables:PutTablePolicy',
            's3tables:DeleteTablePolicy',
            's3tables:GetTableBucketPolicy',
            's3tables:PutTableBucketPolicy',
            's3tables:DeleteTableBucketPolicy',
          ],
          resources: [
            `arn:aws:s3tables:${this.region}:${this.account}:bucket/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: 'AthenaQueryAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'athena:StartQueryExecution',
            'athena:GetQueryExecution',
            'athena:GetQueryResults',
            'athena:StopQueryExecution',
            'athena:ListQueryExecutions',
          ],
          resources: ['*'],
        }),
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
            'glue:CreateDatabase',
            'glue:CreateTable',
            'glue:UpdateTable',
            'glue:DeleteTable',
          ],
          resources: ['*'],
        }),
      ],
    });

    // ========================================================================
    // Update Secrets Manager with S3 Tables bucket info
    // ========================================================================

    const updateSecretFunction = new lambda.Function(this, 'UpdateSecretFunction', {
      functionName: `${config.appName}-update-secret-s3tables`,
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      timeout: cdk.Duration.minutes(1),
      memorySize: 128,
      code: lambda.Code.fromInline(`
import boto3
import json
import cfnresponse

def handler(event, context):
    if event['RequestType'] == 'Delete':
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return
    try:
        secret_id = event['ResourceProperties']['SecretId']
        new_values = json.loads(event['ResourceProperties']['NewValues'])
        client = boto3.client('secretsmanager')
        response = client.get_secret_value(SecretId=secret_id)
        existing = json.loads(response['SecretString'])
        existing.update(new_values)
        client.put_secret_value(SecretId=secret_id, SecretString=json.dumps(existing))
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {'Updated': list(new_values.keys())})
    except Exception as e:
        cfnresponse.send(event, context, cfnresponse.FAILED, {}, reason=str(e))
`),
    });

    updateSecretFunction.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['secretsmanager:GetSecretValue', 'secretsmanager:PutSecretValue'],
      resources: [
        `arn:aws:secretsmanager:${this.region}:${this.account}:secret:${config.secretName}*`,
      ],
    }));

    const updateSecretProvider = new cr.Provider(this, 'UpdateSecretProvider', {
      onEventHandler: updateSecretFunction,
      logGroup: new logs.LogGroup(this, 'UpdateSecretProviderLogs', {
        retention: logs.RetentionDays.ONE_DAY,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    new cdk.CustomResource(this, 'UpdateSecretWithS3TablesValues', {
      serviceToken: updateSecretProvider.serviceToken,
      properties: {
        SecretId: props.secretArn,
        NewValues: JSON.stringify({
          s3tables_bucket_name: `${config.appName}-manufacturing-${this.account}-${this.region}`,
          s3tables_bucket_arn: this.tableBucket.attrTableBucketArn,
          aws_region: this.region,
        }),
        Timestamp: Date.now().toString(),
      },
    });

    // ========================================================================
    // Outputs
    // ========================================================================

    new cdk.CfnOutput(this, 'S3TablesBucketArn', {
      value: this.tableBucket.attrTableBucketArn,
      description: 'S3 Tables bucket ARN for manufacturing data',
      exportName: exportNames.s3TablesBucketArn,
    });

    new cdk.CfnOutput(this, 'S3TablesBucketName', {
      value: `${config.appName}-manufacturing-${this.account}-${this.region}`,
      description: 'S3 Tables bucket name',
      exportName: exportNames.s3TablesBucketName,
    });

    // ========================================================================
    // CDK-NAG Suppressions
    // ========================================================================

    NagSuppressions.addStackSuppressions(this, [
      { id: 'AwsSolutions-IAM5', reason: 'S3 Tables and Athena require broad resource access for query execution.' },
      { id: 'AwsSolutions-IAM4', reason: 'Lambda basic execution role acceptable for custom resource.' },
      { id: 'AwsSolutions-L1', reason: 'Python 3.11 is current supported runtime.' },
    ]);
  }
}
