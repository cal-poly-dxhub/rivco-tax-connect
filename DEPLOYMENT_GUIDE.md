# Deployment Guide & Learnings

## Architecture Overview

```
Customer → Connect Chat → Lex Bot (QInConnectIntent)
                              ↓
                        Q in Connect (ORCHESTRATION AI Agent)
                              ↓
                        MCP Gateway (AgentCore)
                              ↓
                        Lambda (tax_lookup) → S3 (refund data)
```

## Key Learnings

### 1. AI Agent Type Must Be ORCHESTRATION

The AI agent **must** be `ORCHESTRATION` type to use MCP tools. `SELF_SERVICE` agents do not support MCP tool configuration. This is a Q in Connect limitation — the console only allows adding MCP tools to ORCHESTRATION agents.

The AWS CLI `create-ai-agent` command does **not** support creating ORCHESTRATION agents (only `SELF_SERVICE`, `ANSWER_RECOMMENDATION`, `MANUAL_SEARCH`). The agent must be created in the Q in Connect console.

However, boto3 **can** read and update an existing ORCHESTRATION agent's configuration (including the prompt reference), and the CLI can version it via `create-ai-agent-version`. The `post_deploy.py` script uses boto3 for the update and versioning.

### 2. MCP Gateway Registration Is Manual

The AgentCore MCP Gateway must be registered as a third-party application in the Connect console. There is no API or CloudFormation resource for this. The CDK stack creates the gateway, but the Connect registration is a manual step.

### 3. Agent Prompt Updates Are Automated

The `post_deploy.py` script uses boto3 to update the agent's `orchestrationAIAgentConfiguration` with the latest prompt version. The AWS CLI cannot do this — it shows the orchestration config as `SDK_UNKNOWN_MEMBER` — but boto3 handles it natively. This eliminates the need to manually update the prompt in the console after each change.

### 4. CreateWisdomSession Block Configuration

The contact flow's `CreateWisdomSession` block must include:

```json
{
  "Parameters": {
    "WisdomAssistantArn": "<assistant-arn>",
    "OrchestrationAIAgentConfiguration": {
      "AgentAssistanceAgentVersionArn": "<agent-version-arn>"
    }
  },
  "Type": "CreateWisdomSession"
}
```

Using a `SELF_SERVICE` agent ARN in `AgentAssistanceAgentVersionArn` causes an error. Only `ORCHESTRATION` agent ARNs work here.

### 5. Lex Session Attribute

The Lex bot block must also pass the agent ARN via session attribute:

```json
"LexSessionAttributes": {
  "x-amz-lex:q-in-connect:ai-agent-arn": "<agent-version-arn>"
}
```

Both the CreateWisdomSession config AND the Lex session attribute are required for the ORCHESTRATION agent to invoke MCP tools.

### 6. Agent Versioning Matters

After configuring tools on the agent in the console, you must create a new version. The `post_deploy.py` script handles this automatically — it updates the prompt reference and creates a new version each run.

### 7. MCP Gateway Tool Naming

The gateway exposes tools with a prefixed name: `<target-name>___<tool-name>` (e.g., `tax-lookup-target___tax_lookup`). Q in Connect handles this mapping automatically when the tool is added to the agent via the console.

## Deployment Steps

### Automated (CDK + post_deploy.py)

1. `CDK_DOCKER=finch cdk deploy` — Creates:
   - Connect instance, Lex bot, Lambda, S3 bucket
   - Q in Connect assistant + knowledge base
   - AgentCore MCP Gateway + Lambda target
   - IAM roles and permissions

2. `python3 post_deploy.py` — Handles:
   - Upload refund data to S3
   - Create/update AI prompt from config.yaml
   - Update agent to use latest prompt version
   - Version the AI agent
   - Create/update contact flow with resolved ARNs
   - Claim and associate phone number
   - Set up SMS channel

### Manual (Console — required before first `post_deploy.py` run)

See README.md for step-by-step console instructions.

**Order matters:**
1. Register MCP gateway as third-party app in Connect console
2. Create ORCHESTRATION AI agent in Q in Connect console
3. Add MCP tool to the agent
4. Save and publish the agent
5. Run `python3 post_deploy.py` (updates agent prompt, versions the agent, and wires it into the flow)

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `CreateWisdomSession` errors | Wrong agent type in `AgentAssistanceAgentVersionArn` | Use ORCHESTRATION agent ARN, not SELF_SERVICE |
| `GetUserInput` errors with "Amazon Lex could not access your Q In Connect Assistant" | SELF_SERVICE agent with empty config | Use ORCHESTRATION agent instead |
| Bot says "I don't have an answer" | Agent not configured with MCP tool | Add tool in Q in Connect console, create new version |
| Bot says "I'll look up..." but returns no results | Agent hallucinating — tool not actually invoked | Check Lambda logs for MCP invocations; ensure agent version has tool configured |
| No welcome message | `CreateWisdomSession` block failing | Check flow logs in CloudWatch `/aws/connect/<instance-name>` |
