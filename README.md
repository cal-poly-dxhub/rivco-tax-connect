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

### 0. Enable Lex Bot Management (AWS Console)

1. Go to **Amazon Connect** in AWS Console
2. Select your instance → **Lex bots**
3. Enable both checkboxes:
   - **Enable Lex Bot Management in Amazon Connect**
   - **Enable Bot Analytics and Transcripts in Amazon Connect**
4. Save

### Amazon Connect Admin Website

### 1. Create Connect AI Agent Domain (AWS Console)

1. Go to **AWS Console** → **Amazon Connect** → select your instance
2. In the left navigation, choose **Amazon Q** → **Add domain**
3. Create a new domain with a friendly name
4. Use default encryption or create a KMS key
5. Click **Add domain**

### 2. Configure AI Prompt with TAX_LOOKUP Action (Admin Website)

1. Go to **Amazon Q** → **AI prompts** → **Create prompt**
3. Add the following **System prompt**:
   ```
   You are a helpful assistant for Riverside County tax refund lookups.
   When a customer provides their name, use the TAX_LOOKUP tool to search for unclaimed refunds.
   Be friendly and helpful. If the customer wants to end the conversation, use COMPLETE.
   ```
4. Add these **Custom tools**:
   ```yaml
   tools:
   - name: TAX_LOOKUP
     description: Look up tax refunds for a customer by their name. Use this when the customer wants to check if they have any unclaimed tax refunds.
     input_schema:
       type: object
       properties:
         customer_name:
           type: string
           description: The customer's full name to search for refunds
       required:
       - customer_name
   - name: CONVERSATION
     description: Continue holding a casual conversation with the customer.
     input_schema:
       type: object
       properties:
         message:
           type: string
           description: The message to send to the customer
       required:
       - message
   - name: COMPLETE
     description: End the conversation when the customer is done.
     input_schema:
       type: object
       properties:
         message:
           type: string
           description: A goodbye message
       required:
       - message
   ```
5. Save the prompt

### 3. Configure Nova Sonic Speech-to-Speech

1. Go to **Routing** → **Flows** → **Conversational AI** tab
3. Select your bot name
4. Go to **Configuration** tab, select your locale (e.g., en-US)
5. In **Speech model** section → **Edit**
6. Set **Model type** to **Speech-to-Speech**
7. Set **Voice provider** to **Amazon Nova Sonic**
8. Click **Confirm**, then **Build language**

### 4. Configure Contact Flow Voice

1. Open your contact flow in Flow designer
2. Add/edit a **Set voice** block
3. In **Other settings**, set **Override speaking style** → **Generative**
4. Select a Nova Sonic compatible voice:
   - Matthew (en-US, Masculine)
   - Amy (en-GB, Feminine)
   - Olivia (en-AU, Feminine)
   - Lupe (es-US, Feminine)
5. **Save** and **Publish** the flow

### 5. Enable Communications Widget

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
