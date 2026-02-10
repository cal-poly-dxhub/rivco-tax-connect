#!/bin/bash
set -e

CONFIG_FILE="config.yaml"
REGION=$(yq -r '.aws.region' $CONFIG_FILE)
INSTANCE_ID=$(yq -r '.aws.connect_instance_id' $CONFIG_FILE)
FLOW_NAME=$(yq -r '.connect.flow_name' $CONFIG_FILE)
VOICE_ID=$(yq -r '.connect.voice_id' $CONFIG_FILE)
VOICE_ENGINE=$(yq -r '.connect.voice_engine' $CONFIG_FILE)
BOT_NAME=$(yq -r '.lex.bot_name' $CONFIG_FILE)
ALIAS_NAME=$(yq -r '.lex.alias_name' $CONFIG_FILE)
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

BOT_ID=$(aws lexv2-models list-bots --region $REGION \
  --query "botSummaries[?botName=='$BOT_NAME'].botId" --output text)
ALIAS_ID=$(aws lexv2-models list-bot-aliases --region $REGION --bot-id $BOT_ID \
  --query "botAliasSummaries[?botAliasName=='$ALIAS_NAME'].botAliasId" --output text)
ALIAS_ARN="arn:aws:lex:${REGION}:${ACCOUNT}:bot-alias/${BOT_ID}/${ALIAS_ID}"

echo "Bot: $BOT_ID, Alias: $ALIAS_ID"

echo "Associating bot..."
aws connect associate-bot --region $REGION --instance-id $INSTANCE_ID \
  --lex-v2-bot "AliasArn=$ALIAS_ARN" 2>/dev/null || true

FLOW_CONTENT=$(cat <<EOF
{"Version":"2019-10-30","StartAction":"set-voice","Actions":[
  {"Identifier":"set-voice","Type":"UpdateContactTextToSpeechVoice",
   "Parameters":{"TextToSpeechVoice":"$VOICE_ID","TextToSpeechEngine":"$VOICE_ENGINE"},
   "Transitions":{"NextAction":"bot"}},
  {"Identifier":"bot","Type":"ConnectParticipantWithLexBot",
   "Parameters":{"Text":" ","LexV2Bot":{"AliasArn":"$ALIAS_ARN"}},
   "Transitions":{"NextAction":"bot","Errors":[{"NextAction":"bot","ErrorType":"NoMatchingCondition"},{"NextAction":"bot","ErrorType":"NoMatchingError"}]}},
  {"Identifier":"disconnect","Type":"DisconnectParticipant","Parameters":{},"Transitions":{}}
]}
EOF
)

EXISTING=$(aws connect list-contact-flows --region $REGION --instance-id $INSTANCE_ID \
  --query "ContactFlowSummaryList[?Name=='$FLOW_NAME'].Id" --output text)

if [ -n "$EXISTING" ]; then
  echo "Updating flow $EXISTING..."
  aws connect update-contact-flow-content --region $REGION --instance-id $INSTANCE_ID \
    --contact-flow-id $EXISTING --content "$FLOW_CONTENT"
else
  echo "Creating flow..."
  aws connect create-contact-flow --region $REGION --instance-id $INSTANCE_ID \
    --name "$FLOW_NAME" --type CONTACT_FLOW --content "$FLOW_CONTENT"
fi

echo "Done. Manual steps:"
echo "1. Connect console > Bots > $BOT_NAME > Speech model > Speech-to-Speech > Nova Sonic"
echo "2. Channels > Widget > Add > Select $FLOW_NAME > Enable Chat+Voice"
