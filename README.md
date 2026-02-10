# Riverside County Tax Refund Lookup - Amazon Q in Connect

Self-service AI agent for tax refund lookup using Amazon Q in Connect.

## Architecture

- Amazon Connect instance (created by CDK)
- Amazon Connect contact flow with Lex integration
- Lex V2 bot with `AMAZON.QInConnectIntent` for voice/chat
- Lambda function for tax refund data lookup
- S3 bucket for refund data storage
- Wisdom (Q in Connect) Assistant

## Project Structure

```
.
├── app.py                    # CDK entry point
├── config.yaml               # All configuration
├── bot/
│   ├── infrastructure.py     # CDK stack (Connect, S3, Lambda, Lex, Wisdom)
│   └── runtime/
│       ├── lambda_function.py
│       └── requirements.txt
├── scripts/
│   └── setup-connect.sh      # Post-deploy info
└── test-widget.html
```

## Prerequisites

- Docker (for Lambda bundling)
- AWS CDK
- Python 3.12+

## Deployment

```bash
# 1. Install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Deploy CDK stack (creates Connect instance, Lex bot, Lambda, S3, Wisdom)
# Lambda dependencies are bundled via Docker at deploy time
cdk deploy

# 3. Upload refund data
aws s3 cp UnclaimedRefunds.xls s3://$(aws cloudformation describe-stacks \
  --stack-name riverside-tax-refund --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)/

# 4. View stack outputs
./scripts/setup-connect.sh
```

## Manual Steps (Console)

1. **Enable Q in Connect**: Amazon Connect console → Amazon Q → Enable
2. **Configure Self-Service AI**: Amazon Q → AI agents → Self-Service → Configure prompts
3. **Widget**: Channels → Communications widget → Add → Select TaxRefundFlow → Enable Chat + Voice

## Testing

```bash
python3 -m http.server 8000
# Visit http://localhost:8000/test-widget.html
```
