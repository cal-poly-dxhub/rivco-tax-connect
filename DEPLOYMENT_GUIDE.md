# Deployment Guide

This guide provides detailed deployment instructions, troubleshooting steps, and recovery procedures for the Riverside County Tax Refund Lookup system.

## Pre-Deployment Checklist

Before running the deployment scripts, ensure:

1. **AWS Credentials**: Configure AWS CLI with appropriate credentials
   ```bash
   aws configure
   ```

2. **Python Environment**: Python 3.12+ with required dependencies
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **AWS Permissions**: Your IAM user/role has permissions for:
   - CDK deployment (CloudFormation, IAM, S3, Lambda, Connect, Lex, Q in Connect, Bedrock)
   - S3 bucket creation and management
   - Lambda function creation and updates
   - Amazon Connect instance access
   - Q in Connect knowledge base management

4. **Configuration**: Update `config.yaml` with your environment-specific values:
   - AWS region
   - Lambda timeout and memory settings
   - Environment variables (URLs, thresholds)
   - Connect flow name and voice IDs
   - Wisdom assistant name and seed URLs

## Deployment Steps

### Step 1: Deploy Infrastructure (CDK)

```bash
cdk deploy --require-approval=never
```

This creates:
- S3 buckets (data, uploads, portal)
- Lambda functions (main lookup, upload handler)
- IAM roles and policies
- Amazon Connect integration
- Q in Connect knowledge base

**Expected output**: CloudFormation stack name and resource ARNs

### Step 2: Run Post-Deploy Script

```bash
python3 post_deploy.py
```

This script:
1. Uploads refund data (`refunds_demo_balanced.jsonl`) to S3
2. Creates/updates the AI orchestration prompt in Q in Connect
3. Syncs knowledge base content from seed URLs
4. Configures Amazon Connect contact flow
5. Registers MCP Gateway tools

**Expected output**: Success messages for each step

## Post-Deployment Configuration (Manual)

The following steps require AWS Console access and cannot be automated:

### 1. Amazon Connect Instance Setup

1. Navigate to Amazon Connect in AWS Console
2. Select your instance
3. Go to **Contact Flows** → **Manage phone numbers**
4. Claim a phone number (or use existing)
5. Set the contact flow to `TaxRefundFlow`

### 2. SMS Channel Setup (Optional)

To enable SMS:

1. Go to **Channels** → **Phone Numbers**
2. Request a toll-free number for SMS
3. AWS will verify your use case (typically 1-2 business days)
4. Once approved, configure the number in the contact flow

### 3. Chat Channel Setup (Optional)

1. Go to **Channels** → **Chat**
2. Create a chat widget
3. Embed the widget code in your website
4. Configure the contact flow for chat

### 4. Live Agent Queue Setup (Optional)

1. Go to **Routing** → **Queues**
2. Create a queue for live agents
3. Create a routing profile for agents
4. Add agents to the queue
5. Update the contact flow to route to this queue

## Troubleshooting

### Issue: CDK Deployment Fails

**Symptom**: CloudFormation stack creation fails

**Solutions**:
1. Check IAM permissions: `aws iam get-user`
2. Verify region is correct: `aws configure get region`
3. Check for existing resources with same name: `aws s3 ls | grep riverside`
4. Review CloudFormation events: `aws cloudformation describe-stack-events --stack-name <stack-name>`

### Issue: Post-Deploy Script Fails at "Uploading Refund Data"

**Symptom**: `refunds_demo_balanced.jsonl not found`

**Solutions**:
1. Verify file exists: `ls -la refunds_demo_balanced.jsonl`
2. If missing, create demo data: See [Data Format Documentation](README.md#data-format)
3. Ensure file is in the same directory as `post_deploy.py`

### Issue: Post-Deploy Script Fails at "Creating AI Prompt"

**Symptom**: Q in Connect API error

**Solutions**:
1. Verify Q in Connect is enabled in your region
2. Check IAM permissions for `qconnect:*` actions
3. Verify `config.yaml` has valid YAML syntax: `python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"`
4. Check CloudWatch logs: `aws logs tail /aws/lambda/post-deploy --follow`

### Issue: Post-Deploy Script Fails at "Syncing Knowledge Base"

**Symptom**: Playwright timeout or URL fetch error

**Solutions**:
1. Verify seed URLs are accessible: `curl -I https://auditorcontroller.org/`
2. Check network connectivity from Lambda environment
3. Increase timeout in `post_deploy.py` (line 476): `timeout=30000` → `timeout=60000`
4. Reduce number of seed URLs in `config.yaml` to test with fewer pages
5. Check CloudWatch logs for detailed error: `aws logs tail /aws/lambda/post-deploy --follow`

### Issue: Lambda Function Returns "No Refunds Found"

**Symptom**: Tax lookup always returns no matches

**Solutions**:
1. Verify refund data was uploaded: `aws s3 ls s3://<bucket>/refunds_demo_balanced.jsonl`
2. Check data file format: `aws s3 cp s3://<bucket>/refunds_demo_balanced.jsonl - | head -1 | jq .`
3. Verify claim deadlines in data are in the future: `aws s3 cp s3://<bucket>/refunds_demo_balanced.jsonl - | jq '.claim_deadline' | head -5`
4. Check Lambda logs: `aws logs tail /aws/lambda/riverside-tax-lookup --follow`

### Issue: Amazon Connect Contact Flow Fails

**Symptom**: Calls drop or flow doesn't execute

**Solutions**:
1. Verify contact flow exists: `aws connect list-contact-flows --instance-id <instance-id>`
2. Check flow configuration: `aws connect describe-contact-flow --instance-id <instance-id> --contact-flow-id <flow-id>`
3. Verify Lambda permissions: `aws lambda get-policy --function-name riverside-tax-lookup`
4. Check CloudWatch logs: `aws logs tail /aws/connect/TaxRefundFlow --follow`

### Issue: SMS Not Sending

**Symptom**: SMS tool returns error

**Solutions**:
1. Verify SNS topic exists: `aws sns list-topics | grep riverside`
2. Check SNS permissions: `aws sns get-topic-attributes --topic-arn <topic-arn> --attribute-name Policy`
3. Verify phone number format is E.164: `+1` followed by 10 digits
4. Check CloudWatch logs: `aws logs tail /aws/lambda/riverside-tax-lookup --follow`
5. Verify toll-free number is registered and active in Amazon Connect

## Recovery Procedures

### Rollback to Previous Version

If deployment fails and you need to rollback:

```bash
# List previous stack versions
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE

# Rollback to previous version
aws cloudformation cancel-update-stack --stack-name riverside-tax-refund
```

### Reset Knowledge Base

To clear and rebuild the knowledge base:

```bash
# Delete existing knowledge base
aws qconnect delete-knowledge-base --knowledge-base-id <kb-id>

# Re-run post-deploy script
python3 post_deploy.py
```

### Update Refund Data

To upload new refund data without redeploying:

```bash
# Upload new data file
aws s3 cp refunds_demo_balanced.jsonl s3://<bucket>/refunds_demo_balanced.jsonl

# Lambda will automatically use new data on next invocation (cache expires)
```

### Update AI Prompt

To update the AI orchestration prompt without redeploying:

1. Edit `config.yaml` with new prompt text
2. Run: `python3 post_deploy.py`
3. The script will update the prompt in Q in Connect

## Monitoring

### CloudWatch Logs

Monitor Lambda execution:

```bash
# Main lookup function
aws logs tail /aws/lambda/riverside-tax-lookup --follow

# Upload handler
aws logs tail /aws/lambda/upload-handler --follow

# Post-deploy script
aws logs tail /aws/lambda/post-deploy --follow
```

### CloudWatch Metrics

Monitor key metrics:

```bash
# Lambda invocations
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=riverside-tax-lookup \
  --start-time 2024-01-01T00:00:00Z \
  --end-time 2024-01-02T00:00:00Z \
  --period 3600 \
  --statistics Sum
```

### Amazon Connect Metrics

Monitor contact center performance:

1. Go to Amazon Connect Console
2. Select your instance
3. Go to **Metrics and quality** → **Real-time metrics**
4. Monitor: Contacts in queue, Average handle time, Abandonment rate

## Support

For issues not covered in this guide:

1. Check CloudWatch Logs for detailed error messages
2. Review AWS service quotas: `aws service-quotas list-service-quotas --service-code connect`
3. Contact AWS Support or your AWS account team
4. Review the main [README.md](README.md) for additional context
