#!/usr/bin/env node
/**
 * Standalone CDK app for test/demo data infrastructure.
 *
 * Deploys the S3 Tables stack independently from the main application.
 * Requires the main app (Foundation stack) to be deployed first so that
 * the task role and secret already exist.
 *
 * Usage:
 *   npx cdk --app "npx ts-node bin/testdata-app.ts" deploy
 *   npx cdk --app "npx ts-node bin/testdata-app.ts" destroy
 */
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { config, validateConfig } from '../lib/config';
import { S3TablesStack } from '../lib/s3tables-stack';

validateConfig();
const app = new cdk.App();

const env: cdk.Environment = {
  account: config.account || process.env.CDK_DEFAULT_ACCOUNT,
  region: config.region || process.env.CDK_DEFAULT_REGION || 'us-east-1',
};

// These ARNs come from the already-deployed Foundation stack.
// Resolve via CloudFormation exports so no hard-coded values are needed.
const taskRoleArn = cdk.Fn.importValue(`${config.appName}-TaskRoleArn`);
const secretArn = cdk.Fn.importValue(`${config.appName}-SecretArn`);

const s3TablesStack = new S3TablesStack(app, `${config.appName}-S3Tables`, {
  env,
  description: 'Test/demo data: S3 Tables Iceberg storage with simulated manufacturing data. Deploy separately from the main app.',
  stackName: `${config.appName}-s3tables`,
  taskRoleArn: taskRoleArn.toString(),
  secretArn: secretArn.toString(),
});

cdk.Tags.of(s3TablesStack).add('Application', config.appName);
cdk.Tags.of(s3TablesStack).add('ManagedBy', 'CDK');
cdk.Tags.of(s3TablesStack).add('Purpose', 'test-data');

app.synth();
