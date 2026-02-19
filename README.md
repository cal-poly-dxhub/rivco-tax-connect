# Riverside County Tax Refund Lookup - Amazon Nova Sonic

Self-service AI agent for tax refund lookup using Amazon Connect with Nova Sonic speech-to-speech.

## Architecture

```
Customer → Connect Chat/Voice → Lex Bot (QInConnectIntent)
                                    ↓
                              Q in Connect (ORCHESTRATION AI Agent)
                                    ↓
                              MCP Gateway (AgentCore)
                                    ↓
                              Lambda (tax_lookup) → S3 (refund data)
```

## Prerequisites

- Finch or Docker (for Lambda bundling)
- AWS CDK
- Python 3.12+

## Deployment

### Step 1: Deploy Infrastructure (CDK)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CDK_DOCKER=finch cdk deploy --require-approval never
```

This creates: Connect instance, Lex bot, Lambda, S3 bucket, Q in Connect assistant, knowledge base, AgentCore MCP Gateway with Lambda target.

### Step 2: Console Setup (One-Time)

These steps **must** be completed before running `post-deploy.sh`. They cannot be automated via CLI.

#### 2a. Register MCP Gateway as Third-Party App

1. Go to **Amazon Connect** → **Third-party applications** → **Add application**
2. Set **Display name** to `Tax Lookup Gateway`
3. Set **Application type** to **MCP server**
4. Select the AgentCore gateway created by CDK (name starts with `riverside-tax-refund-mcp-gateway-`)
5. Associate with your Connect instance
6. Click **Add application**

#### 2b. Create ORCHESTRATION AI Agent

> **Important:** The agent must be ORCHESTRATION type. SELF_SERVICE agents do not support MCP tools. The CLI cannot create ORCHESTRATION agents — this must be done in the console.

1. In the Connect admin site, go to **AI agent designer** → **AI Agents** → **Create AI Agent**
2. Select **Orchestration** type
3. Name it `tax-refund-agent`
4. In the **Tools** section, click **Add tool** → **MCP tool**
5. Select `Tax Lookup Gateway` and add the `tax_lookup` tool
6. Click **Add tool** → **MCP tool** again, and add the `send_sms` tool
7. In the **Prompt** section, select `tax-refund-orchestration-prompt` (created by `post-deploy.sh`)
8. **Save** and **Publish** the agent

> **Adding new tools later:** The CLI cannot add tools to ORCHESTRATION agents. Any time a new MCP tool is added to the gateway (via CDK), you must also add it to the agent in the console, then Save and Publish.

#### 2c. Add Third-Party App to Security Profile

The MCP gateway app must be enabled in the security profile assigned to your agents:

1. In the Connect admin site, go to **Users** → **Security profiles**
2. Select the security profile used by your agents (e.g., `Admin` or `Agent`)
3. Under **Agent Applications** → **Third-party applications**, enable `Tax Lookup Gateway`
4. **Save**

> **Note:** Run `post-deploy.sh` once before this step so the prompt exists. Then after creating the agent, run `post-deploy.sh` again to version the agent and wire it into the flow.

#### 2d. Enable Lex Bot Management

1. Go to **Amazon Connect** in AWS Console → select your instance
2. Go to **Flows** → **Amazon Lex**
3. Enable both checkboxes:
   - Enable Lex Bot Management in Amazon Connect
   - Enable Bot Analytics and Transcripts in Amazon Connect
4. Save

### Step 3: Run Post-Deploy Script

```bash
bash post-deploy.sh
```

This script:
- Uploads refund data to S3
- Creates/updates the AI orchestration prompt from `config.yaml` (prompt text for the agent)
- Finds the `tax-refund-agent` ORCHESTRATION agent and creates a new version
- Creates/updates the contact flow with resolved ARNs
- Claims a phone number and associates it with the flow

**First-time deployment order:**
1. Run `post-deploy.sh` → creates the prompt (agent step will fail — that's OK)
2. Complete Step 2b in the console (create agent, select prompt and tool)
3. Run `post-deploy.sh` again → versions the agent and wires it into the flow

**Subsequent runs:** Just run `post-deploy.sh` — it updates the prompt, versions the agent, and updates the flow.

### Step 4: Configure Nova Sonic (Optional, for Voice)

1. In the Connect admin site, go to **Routing** → **Flows** → **Conversational AI**
2. Select your bot, go to **Configuration** → select locale (e.g., en-US)
3. In **Speech model** → **Edit** → set **Model type** to **Speech-to-Speech**
4. Set **Voice provider** to **Amazon Nova Sonic**
5. **Confirm**, then **Build language**

## Testing

### Chat Test (Programmatic)

```bash
python3 test_chat.py
```

### Web Widget

Ensure `http://localhost:8000` is an accepted domain in Connect.

```bash
python3 -m http.server 8000
# Visit http://localhost:8000/test-widget.html
```

### Verify Tool Invocation

Check Lambda logs for MCP gateway calls (not just Lex calls):

```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/riverside-tax-lookup" \
  --region us-west-2 --start-time $(date -v-5M +%s000) \
  --query 'events[?contains(message, `customer_name`)].message' --output text
```

If this returns results, the MCP gateway → Lambda path is working. If empty, only Lex DialogCodeHook calls are happening (tool not invoked).

## Troubleshooting

See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for detailed learnings and a troubleshooting table.

Common issues:
- **`CreateWisdomSession` errors**: Agent ARN in flow must be an ORCHESTRATION agent, not SELF_SERVICE
- **Bot says "I don't have an answer"**: MCP tool not configured on agent, or gateway not registered
- **Bot hallucinates lookup results**: Agent version doesn't have tool — create new version after configuring tool in console, re-run `post-deploy.sh`
- **`post-deploy.sh` exits with "No ORCHESTRATION agent found"**: Complete Step 2b first
