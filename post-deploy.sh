#!/bin/bash
# post-deploy.sh — Run after `cdk deploy` and console setup complete.
#
# Prerequisites (manual, one-time):
#   1. Register MCP gateway as third-party app in Connect console
#   2. Create ORCHESTRATION AI agent in Q in Connect console with MCP tool
#   3. Save and publish the agent
#
# This script handles:
#   1. Upload refund data to S3
#   2. Create/update the AI orchestration prompt from config.yaml
#   3. Version the existing AI agent
#   4. Create/update the contact flow with resolved ARNs
#   5. Claim a phone number and associate it with the flow

set -euo pipefail

REGION=us-west-2
STACK_NAME=riverside-tax-refund
AGENT_NAME=tax-refund-agent
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "============================================"
echo "  Post-Deploy Setup for $STACK_NAME"
echo "============================================"
echo ""

# ── Read stack outputs ──────────────────────────────────────
echo "▶ Reading stack outputs..."
OUTPUTS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs' --output json)

get_output() {
  echo "$OUTPUTS" | python3 -c "
import sys, json
for o in json.load(sys.stdin):
    if o['OutputKey'] == '$1': print(o['OutputValue']); break
"
}

BUCKET=$(get_output BucketName)
INSTANCE_ID=$(get_output ConnectInstanceId)
INSTANCE_ARN=$(get_output ConnectInstanceArn)
QUEUE_ARN=$(get_output QueueArn)
ASSISTANT_ARN=$(get_output AssistantArn)
BOT_ALIAS_ARN=$(get_output BotAliasArn)
KB_ID=$(get_output KnowledgeBaseId)
GATEWAY_ID=$(get_output GatewayId)
GATEWAY_URL=$(get_output GatewayUrl)
LAMBDA_ARN=$(get_output LambdaArn)
ASSISTANT_ID=$(echo "$ASSISTANT_ARN" | awk -F/ '{print $NF}')

echo "  Instance:  $INSTANCE_ID"
echo "  Assistant: $ASSISTANT_ID"
echo "  Gateway:   $GATEWAY_ID"
echo ""

# ── Step 1: Upload refund data ──────────────────────────────
echo "▶ Uploading refund data to S3..."
if [ -f "$SCRIPT_DIR/refunds_demo_balanced.jsonl" ]; then
  aws s3 cp "$SCRIPT_DIR/refunds_demo_balanced.jsonl" "s3://${BUCKET}/" --region "$REGION"
  echo "  ✅ Done"
else
  echo "  ⚠️  refunds_demo_balanced.jsonl not found"
fi
echo ""

# ── Step 2: Create/update AI Prompt ──────────────────────────
echo "▶ Creating AI orchestration prompt..."

PROMPT_NAME="tax-refund-orchestration-prompt"
PROMPT_MODEL="us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Extract prompt text from config.yaml
PROMPT_TEMPLATE_JSON=$(source "$SCRIPT_DIR/.venv/bin/activate" && python3 -c "
import yaml, json
with open('$SCRIPT_DIR/config.yaml') as f:
    config = yaml.safe_load(f)
text = config['prompts']['ai_orchestration']
print(json.dumps({'textFullAIPromptEditTemplateConfiguration': {'text': text}}))
")

# Check if prompt exists
EXISTING_PROMPT_ID=$(aws qconnect list-ai-prompts \
  --assistant-id "$ASSISTANT_ID" \
  --region "$REGION" \
  --query "aiPromptSummaries[?name==\`$PROMPT_NAME\`].aiPromptId" \
  --output text 2>/dev/null || true)

if [ -n "$EXISTING_PROMPT_ID" ] && [ "$EXISTING_PROMPT_ID" != "None" ]; then
  echo "  Updating existing prompt: $EXISTING_PROMPT_ID"
  aws qconnect update-ai-prompt \
    --assistant-id "$ASSISTANT_ID" \
    --ai-prompt-id "$EXISTING_PROMPT_ID" \
    --visibility-status PUBLISHED \
    --template-configuration "$PROMPT_TEMPLATE_JSON" \
    --region "$REGION" >/dev/null 2>&1 && \
    echo "  ✅ Updated" || echo "  ⚠️  Update failed (prompt may be unchanged)"
  AI_PROMPT_ID="$EXISTING_PROMPT_ID"
else
  PROMPT_RESULT=$(aws qconnect create-ai-prompt \
    --assistant-id "$ASSISTANT_ID" \
    --name "$PROMPT_NAME" \
    --type "ORCHESTRATION" \
    --api-format "MESSAGES" \
    --template-type "TEXT" \
    --model-id "$PROMPT_MODEL" \
    --visibility-status "PUBLISHED" \
    --template-configuration "$PROMPT_TEMPLATE_JSON" \
    --region "$REGION" 2>&1) && \
    AI_PROMPT_ID=$(echo "$PROMPT_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['aiPrompt']['aiPromptId'])") && \
    echo "  ✅ Created prompt: $AI_PROMPT_ID" || \
    { echo "  ❌ Failed: $PROMPT_RESULT"; AI_PROMPT_ID=""; }
fi

if [ -n "$AI_PROMPT_ID" ]; then
  PROMPT_VERSION_ARN=$(aws qconnect create-ai-prompt-version \
    --assistant-id "$ASSISTANT_ID" \
    --ai-prompt-id "$AI_PROMPT_ID" \
    --region "$REGION" \
    --query 'aiPrompt.aiPromptArn' --output text 2>&1) && \
    echo "  ✅ Prompt version: $PROMPT_VERSION_ARN" || \
    echo "  ⚠️  Prompt versioning failed"
fi
echo ""

# ── Step 3: Find and version AI Agent ───────────────────────
echo "▶ Looking for ORCHESTRATION AI Agent '$AGENT_NAME'..."

AI_AGENT_ID=$(aws qconnect list-ai-agents \
  --assistant-id "$ASSISTANT_ID" \
  --region "$REGION" \
  --query "aiAgentSummaries[?name==\`$AGENT_NAME\` && type==\`ORCHESTRATION\`].aiAgentId" \
  --output text 2>/dev/null || true)

if [ -z "$AI_AGENT_ID" ] || [ "$AI_AGENT_ID" = "None" ]; then
  echo "  ⚠️  No ORCHESTRATION agent named '$AGENT_NAME' found."
  echo ""
  echo "  Create it in the Q in Connect console:"
  echo "    1. AI agent designer → Create AI Agent → Orchestration type"
  echo "    2. Name it '$AGENT_NAME'"
  echo "    3. Add MCP tools from the gateway: tax_lookup, send_sms"
  echo "    4. Set prompt to '$PROMPT_NAME'"
  echo "    5. Save and publish, then re-run this script"
  echo ""
  echo "  Skipping agent versioning and flow update."
  echo ""
  echo "============================================"
  echo "  ⚠️  Partial Deploy (prompt created, agent pending)"
  echo "============================================"
  echo ""
  echo "  Prompt ARN: ${PROMPT_VERSION_ARN:-N/A}"
  echo "  Gateway:    $GATEWAY_ID"
  echo ""
  exit 0
fi

echo "  Found: $AI_AGENT_ID"

# Create a new version (publishes any pending changes)
VERSION_RESULT=$(aws qconnect create-ai-agent-version \
  --assistant-id "$ASSISTANT_ID" \
  --ai-agent-id "$AI_AGENT_ID" \
  --region "$REGION" 2>&1) && \
  VERSIONED_ARN=$(echo "$VERSION_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['aiAgent']['aiAgentArn'])") && \
  echo "  ✅ Published version: $VERSIONED_ARN" || \
  { echo "  ❌ Versioning failed: $VERSION_RESULT"; exit 1; }

# Use $LATEST qualifier so the flow always resolves to the latest published version
AI_AGENT_ARN=$(echo "$VERSIONED_ARN" | sed 's/:[0-9]*$/:\$LATEST/')
echo ""

# ── Step 4: Create/update contact flow ──────────────────────
echo "▶ Updating contact flow..."
FLOW_CONTENT=$(python3 << PYEOF
import json
with open('$SCRIPT_DIR/TaxRefundFlow.json') as f:
    content = f.read()
content = content.replace('\${AssistantArn}', '$ASSISTANT_ARN')
content = content.replace('\${AIAgentArn}', '$AI_AGENT_ARN')
content = content.replace('\${BotAliasArn}', '$BOT_ALIAS_ARN')
content = content.replace('\${QueueArn}', '$QUEUE_ARN')
content = content.replace('\${LambdaArn}', '$LAMBDA_ARN')
json.loads(content)  # validate
print(content)
PYEOF
)

EXISTING_FLOW_ID=$(aws connect list-contact-flows \
  --instance-id "$INSTANCE_ID" \
  --contact-flow-types "CONTACT_FLOW" \
  --region "$REGION" \
  --query "ContactFlowSummaryList[?Name=='TaxRefundFlow'].Id" --output text 2>/dev/null || true)

if [ -n "$EXISTING_FLOW_ID" ] && [ "$EXISTING_FLOW_ID" != "None" ]; then
  aws connect update-contact-flow-content \
    --instance-id "$INSTANCE_ID" \
    --contact-flow-id "$EXISTING_FLOW_ID" \
    --content "$FLOW_CONTENT" \
    --region "$REGION" 2>&1 && \
    echo "  ✅ Updated flow: $EXISTING_FLOW_ID" || \
    { echo "  ❌ Failed to update flow"; exit 1; }
  CONTACT_FLOW_ID="$EXISTING_FLOW_ID"
else
  FLOW_RESULT=$(aws connect create-contact-flow \
    --instance-id "$INSTANCE_ID" \
    --name "TaxRefundFlow" \
    --type "CONTACT_FLOW" \
    --content "$FLOW_CONTENT" \
    --region "$REGION" 2>&1) && \
    CONTACT_FLOW_ID=$(echo "$FLOW_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['ContactFlowId'])") && \
    echo "  ✅ Created flow: $CONTACT_FLOW_ID" || \
    { echo "  ❌ Failed: $FLOW_RESULT"; exit 1; }
fi
echo ""

# ── Step 5: Claim phone number ──────────────────────────────
echo "▶ Checking phone number..."
EXISTING_PHONE=$(aws connect list-phone-numbers-v2 \
  --target-arn "$INSTANCE_ARN" \
  --region "$REGION" \
  --query 'ListPhoneNumbersSummaryList[0].PhoneNumberId' --output text 2>/dev/null || true)

if [ -n "$EXISTING_PHONE" ] && [ "$EXISTING_PHONE" != "None" ]; then
  echo "  Phone already claimed: $EXISTING_PHONE"
  PHONE_NUMBER_ID="$EXISTING_PHONE"
else
  PHONE_NUMBER=$(aws connect search-available-phone-numbers \
    --target-arn "$INSTANCE_ARN" \
    --phone-number-country-code US \
    --phone-number-type TOLL_FREE \
    --max-results 1 \
    --region "$REGION" \
    --query 'AvailableNumbersList[0].PhoneNumber' --output text 2>/dev/null || true)

  if [ -n "$PHONE_NUMBER" ] && [ "$PHONE_NUMBER" != "None" ]; then
    PHONE_NUMBER_ID=$(aws connect claim-phone-number \
      --phone-number "$PHONE_NUMBER" \
      --target-arn "$INSTANCE_ARN" \
      --region "$REGION" \
      --query 'PhoneNumberId' --output text)
    echo "  ✅ Claimed: $PHONE_NUMBER (ID: $PHONE_NUMBER_ID)"
  else
    echo "  ⚠️  No toll-free numbers available"
    PHONE_NUMBER_ID=""
  fi
fi

if [ -n "${PHONE_NUMBER_ID:-}" ] && [ -n "$CONTACT_FLOW_ID" ]; then
  sleep 5
  aws connect associate-phone-number-contact-flow \
    --phone-number-id "$PHONE_NUMBER_ID" \
    --instance-id "$INSTANCE_ID" \
    --contact-flow-id "$CONTACT_FLOW_ID" \
    --region "$REGION" 2>&1 && \
    echo "  ✅ Associated with TaxRefundFlow" || \
    echo "  ⚠️  Association failed — do it in the Connect console"
fi
echo ""

# ── Step 6: SMS Channel Setup ────────────────────────────────
echo "▶ Setting up SMS channel..."

# Find the SMS-capable toll-free number in End User Messaging
SMS_PHONE_ID=$(aws pinpoint-sms-voice-v2 describe-phone-numbers \
  --region "$REGION" \
  --query "PhoneNumbers[?NumberType=='TOLL_FREE' && contains(NumberCapabilities, 'SMS')].PhoneNumberId" \
  --output text 2>/dev/null || true)

if [ -z "$SMS_PHONE_ID" ] || [ "$SMS_PHONE_ID" = "None" ]; then
  echo "  ⚠️  No SMS-capable toll-free number found in End User Messaging."
  echo "     Request one in the End User Messaging SMS console first."
  SMS_STATUS="NO_NUMBER"
else
  echo "  Found SMS number: $SMS_PHONE_ID"

  # Check toll-free registration status
  REG_ID=$(aws pinpoint-sms-voice-v2 describe-phone-numbers \
    --phone-number-ids "$SMS_PHONE_ID" \
    --region "$REGION" \
    --query 'PhoneNumbers[0].RegistrationId' --output text 2>/dev/null || true)

  REG_STATUS="UNKNOWN"
  if [ -n "$REG_ID" ] && [ "$REG_ID" != "None" ]; then
    REG_STATUS=$(aws pinpoint-sms-voice-v2 describe-registrations \
      --registration-ids "$REG_ID" \
      --region "$REGION" \
      --query 'Registrations[0].RegistrationStatus' --output text 2>/dev/null || true)
    echo "  Registration status: $REG_STATUS"
  fi

  SMS_PHONE_NUMBER=$(aws pinpoint-sms-voice-v2 describe-phone-numbers \
    --phone-number-ids "$SMS_PHONE_ID" \
    --region "$REGION" \
    --query 'PhoneNumbers[0].PhoneNumber' --output text)

  if [ "$REG_STATUS" != "COMPLETE" ] && [ "$REG_STATUS" != "APPROVED" ]; then
    echo "  ⚠️  Toll-free registration not yet approved ($REG_STATUS)."
    echo "     SMS import into Connect will be attempted but may fail until approved."
    SMS_STATUS="REG_PENDING"
  else
    SMS_STATUS="READY"
  fi

  # Check if SMS number is already imported into Connect
  EXISTING_SMS_IN_CONNECT=$(aws connect list-phone-numbers-v2 \
    --target-arn "$INSTANCE_ARN" \
    --phone-number-types "TOLL_FREE" \
    --region "$REGION" \
    --query "ListPhoneNumbersSummaryList[?PhoneNumber=='$SMS_PHONE_NUMBER'].PhoneNumberId" \
    --output text 2>/dev/null || true)

  if [ -n "$EXISTING_SMS_IN_CONNECT" ] && [ "$EXISTING_SMS_IN_CONNECT" != "None" ] && [ "$EXISTING_SMS_IN_CONNECT" != "$PHONE_NUMBER_ID" ]; then
    echo "  SMS number already imported into Connect: $EXISTING_SMS_IN_CONNECT"
    SMS_CONNECT_ID="$EXISTING_SMS_IN_CONNECT"
  else
    # Import the SMS number into Connect
    echo "  Importing SMS number into Connect..."
    IMPORT_RESULT=$(aws connect import-phone-number \
      --instance-id "$INSTANCE_ID" \
      --source-phone-number-arn "arn:aws:sms-voice:${REGION}:$(aws sts get-caller-identity --query Account --output text):phone-number/${SMS_PHONE_ID}" \
      --phone-number-description "SMS channel for tax refund bot" \
      --region "$REGION" 2>&1) && \
      SMS_CONNECT_ID=$(echo "$IMPORT_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['PhoneNumberId'])") && \
      echo "  ✅ Imported: $SMS_CONNECT_ID" || \
      { echo "  ⚠️  Import failed (registration may still be pending): $IMPORT_RESULT"; SMS_CONNECT_ID=""; }
  fi

  # Associate SMS number with the contact flow
  if [ -n "${SMS_CONNECT_ID:-}" ] && [ -n "$CONTACT_FLOW_ID" ]; then
    sleep 3
    aws connect associate-phone-number-contact-flow \
      --phone-number-id "$SMS_CONNECT_ID" \
      --instance-id "$INSTANCE_ID" \
      --contact-flow-id "$CONTACT_FLOW_ID" \
      --region "$REGION" 2>&1 && \
      echo "  ✅ SMS number associated with TaxRefundFlow" || \
      echo "  ⚠️  Flow association failed — retry after registration is approved"
  fi
fi
echo ""

# ── Done ────────────────────────────────────────────────────
echo "============================================"
echo "  ✅ Post-Deploy Complete"
echo "============================================"
echo ""
echo "  AI Agent ARN: $AI_AGENT_ARN (latest version)"
echo "  Prompt ARN:   ${PROMPT_VERSION_ARN:-N/A}"
echo "  Flow:         $CONTACT_FLOW_ID"
echo "  Gateway:      $GATEWAY_ID"
echo "  SMS Status:   ${SMS_STATUS:-N/A}"
echo ""
if [ -n "${PROMPT_VERSION_ARN:-}" ]; then
  echo "  ⚠️  Manual console steps (CLI cannot manage ORCHESTRATION agent config):"
  echo "     Q in Connect console → AI Agents → $AGENT_NAME → Edit"
  echo "     • Tools: add tax_lookup and send_sms from Tax Lookup Gateway"
  echo "     • Prompt: set to $PROMPT_NAME"
  echo "     Save and publish"
  echo ""
fi
if [ "${SMS_STATUS:-}" = "REG_PENDING" ]; then
  echo "  ⚠️  SMS: Toll-free registration is pending. Re-run this script after approval"
  echo "     to complete the SMS import and flow association."
  echo ""
fi
