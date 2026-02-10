#!/bin/bash
# Post-deployment manual steps for Q in Connect
# All infrastructure is now created by CDK

echo "Stack deployed successfully!"
echo ""
echo "Manual steps required in AWS Console:"
echo ""
echo "1. Amazon Connect Console > Amazon Q > Enable Q in Connect"
echo "2. Amazon Q > AI agents > Self-Service > Configure prompts"
echo "3. Channels > Communications widget > Add > Select TaxRefundFlow > Enable Chat + Voice"
echo ""
echo "Stack outputs:"
aws cloudformation describe-stacks --stack-name riverside-tax-refund --region us-west-2 \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" --output table
