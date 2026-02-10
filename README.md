# Nova Sonic Connect Bot

Amazon Connect chatbot + voice assistant using Nova Sonic speech-to-speech.

## Configuration

Edit `config.yaml` to customize project name, prompts, AWS settings.

## Project Structure

```
.
├── app.py                    # CDK entry point
├── cdk.json
├── config.yaml               # All configuration
├── requirements.txt
├── bot/
│   ├── infrastructure.py     # CDK stack (S3, Lambda, Lex)
│   └── runtime/
│       ├── lambda_function.py
│       └── requirements.txt
├── scripts/
│   └── setup-connect.sh      # Connect flow + bot association
└── test-widget.html
```

## Deployment

```bash
# 1. Install CDK dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Install Lambda dependencies
pip install -r bot/runtime/requirements.txt -t bot/runtime/

# 3. Deploy
cdk deploy

# 4. Setup Connect resources
./scripts/setup-connect.sh
```

## Manual Steps (Console)

1. **Nova Sonic**: Connect console → Bots → [bot_name] → Configuration → Speech model → Speech-to-Speech → Amazon Nova Sonic → Build
2. **Widget**: Channels → Communications widget → Add → Select flow → Enable Chat + Voice → Add allowed domains

## Testing

```bash
python3 -m http.server 8000
# Visit http://localhost:8000/test-widget.html
```
