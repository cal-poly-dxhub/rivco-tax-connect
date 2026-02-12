# Riverside County Tax Refund Lookup - Amazon Nova Sonic

Self-service AI agent for tax refund lookup using Amazon Connect with Nova Sonic speech-to-speech.

## Architecture

- Amazon Connect instance with Conversational AI bot
- Amazon Nova Sonic for speech-to-speech voice interactions
- Lambda function for tax refund data lookup
- S3 bucket for refund data storage
- Wisdom (Q in Connect) Assistant

## Prerequisites

- Docker (for Lambda bundling)
- AWS CDK
- Python 3.12+

## Deployment

```bash
# 1. Install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Deploy CDK stack
cdk deploy

# 3. Upload refund data
aws s3 cp UnclaimedRefunds.xls s3://$(aws cloudformation describe-stacks \
  --stack-name riverside-tax-refund --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)/
```

## Manual Steps

### In The AWS Console

### 0. Enable Lex Bot Management (AWS Console)

1. Go to **Amazon Connect** in AWS Console
2. Select your instance **Flows** → **Amazon Lex**
3. Enable both checkboxes:
   - **Enable Lex Bot Management in Amazon Connect**
   - **Enable Bot Analytics and Transcripts in Amazon Connect**
4. Save

### 1. Create Connect AI Agent Domain (AWS Console)

1. In the left navigation, choose **AI agents** → **Add domain**
2. Create a new domain with a friendly name (e.g., "riverside-tax")
3. Use default encryption or create a KMS key
4. Click **Add domain**

### 2. Register AgentCore Gateway as MCP Server

1. Go to **Third-party applications** → **Add application**
2. Configure:
   - **Display name**: `Tax Lookup Gateway`
   - **Application type**: Select **MCP server**
3. In **Instance association**:
   - Select your Connect instance
4. Click **Add application**

> **Note:** This step must be done after `cdk deploy` completes. The CDK stack creates the gateway and sets the correct `allowedAudience` automatically, but registering it as a third-party application in Connect has no CloudFormation resource and must be done manually.

### Amazon Connect Admin Website

### 3. Add Tool to AI Agent

1. Go to **AI agent designer** → **AI Agents** → **Create AI Agent**
2. Select **Orchestration** type
3. In the **Tools** section, click **Add tool** → **MCP tool**
4. Select `Tax Lookup Gateway` and add the `tax_lookup` tool
5. Save and Publish the AI agent

### 4. Configure Nova Sonic Speech-to-Speech

1. Go to **Routing** → **Flows** → **Conversational AI** tab
3. Select your bot name
4. Go to **Configuration** tab, select your locale (e.g., en-US)
5. In **Speech model** section → **Edit**
6. Set **Model type** to **Speech-to-Speech**
7. Set **Voice provider** to **Amazon Nova Sonic**
8. Click **Confirm**, then **Build language**

### 5. Configure Contact Flow Voice

1. Open your contact flow in Flow designer
2. Add/edit a **Set voice** block
3. In **Other settings**, set **Override speaking style** → **Generative**
4. Select a Nova Sonic compatible voice:
   - Matthew (en-US, Masculine)
   - Amy (en-GB, Feminine)
   - Olivia (en-AU, Feminine)
   - Lupe (es-US, Feminine)
5. **Save** and **Publish** the flow

### 6. Enable Communications Widget

1. Go to **Channels** → **Communications widget** → **Add widget**
2. Select Add chat and Add web calling
3. Select your contact flow under Chat contact flow and Web calling contact flow
4. Customize the widget's appearance to your liking
5. Add the URL which the bot will be hosted on to required domains
6. Copy and paste the connect widget script onto your website

## Local Testing

Ensure that `http://localhost:8000/test-widget.html` is an accepted domain.
```bash
python3 -m http.server 8000
# Visit http://localhost:8000/test-widget.html
```
