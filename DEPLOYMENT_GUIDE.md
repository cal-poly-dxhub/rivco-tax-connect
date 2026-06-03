# Deployment Guide

End-to-end deploy is a single `cdk deploy`. No console clicks, no post-deploy script.

## Prerequisites

| Requirement | How to check / get |
|---|---|
| AWS profile with admin access to a sandbox/dev account | `aws sts get-caller-identity --profile <name>` should resolve to a role with `AdministratorAccess` |
| CDK bootstrapped in your target region | `aws cloudformation describe-stacks --stack-name CDKToolkit` returns `CREATE_COMPLETE`. If not, run `cdk bootstrap aws://<account>/<region>` |
| Bedrock model access enabled | Console → Bedrock → Model access (in your deploy region). Enable `anthropic.claude-haiku-4-5`. Approval is instant |
| Python 3.12+ + Node 20+ + AWS CDK v2 | `python --version`, `node --version`, `cdk --version` |
| `pip install -r requirements.txt` done | CDK + bundler dependencies |
| Demo refund dataset present locally | `refunds_demo_balanced.jsonl` in repo root. It's `.gitignore`d, so on a fresh clone you'll need to copy it in |
| Branch pushed to GitHub | The admin dashboard CodeBuild clones from GitHub. The branch named in `config.yaml`'s `admin_dashboard.github_branch` must exist on the remote |

## Configure `config.yaml`

Two values must change before the first deploy:

```yaml
super_admin:
  email: you@example.com         # Cognito user gets created with this email; you receive the temp password as a stack output

notifications:
  sender: you@example.com         # SES sender for notification emails. Click the verification link AWS sends after first deploy

admin_dashboard:
  github_branch: feat/your-branch # The branch CodeBuild clones; must already exist on the remote
```

Optional knobs:

```yaml
bedrock:
  model_id: us.anthropic.claude-haiku-4-5-20251001-v1:0   # cross-region inference profile

prompts:
  ai_orchestration: |          # System prompt; lands in SSM (Advanced tier — up to 8KB)
    You are a Riverside County ...
```

## Deploy

```bash
AWS_PROFILE=<your-profile> AWS_DEFAULT_REGION=us-west-2 cdk deploy --require-approval never
```

First deploy takes ~10 min. Subsequent deploys without code changes are ~2 min.

What happens:

1. S3 buckets, DynamoDB tables, Cognito user pool, IAM roles
2. Two Python Lambdas bundle locally — `bot/runtime` (the tax-lookup tool with jellyfish + decoy quiz) and `bot/chat_handler` (the WebSocket handler with `anthropic[bedrock]`). Local bundling means no Docker dependency
3. WebSocket API Gateway + REST API Gateway routes get wired
4. SSM parameter is created with the system prompt from `config.yaml`
5. `BucketDeployment` uploads `refunds_demo_balanced.jsonl` to the data bucket
6. `BucketDeployment` ships the upload portal (`bot/upload_portal/` including `index.html`, the chat widget, the unified-form JS) to the portal S3 site
7. CodeBuild starts and builds the Next.js admin dashboard from your GitHub branch
8. Stack outputs print: dashboard URL, chat WebSocket URL, super-admin temp password, etc.

## After the first deploy

**Verify the SES sender email.** AWS sends a verification link to whatever address you put in `notifications.sender`. Click it. Until you do, notification emails won't deliver (CloudFormation won't fail; you'll just silently miss email).

**Sign in to the admin dashboard.** The stack output `SuperAdminBootstrapPassword` is your one-time password. Username is `sa-<sanitized-email>`. Cognito forces you to change the password on first login.

**Wait for CodeBuild.** Stack output `AdminDashboardUrl` works the moment the first build finishes. If `AdminBuildProjectName` shows `IN_PROGRESS`, give it 3-5 min. Watch builds at:

```
https://<region>.console.aws.amazon.com/codesuite/codebuild/projects/<AdminBuildProjectName>/history
```

**Embed the chat widget on a real page.** The stack outputs `UploadPortalUrl` (e.g., `http://riverside-tax-refund-v2-portal-<account>.s3-website-<region>.amazonaws.com`). Drop these tags into any HTML page that should host the chat:

```html
<link rel="stylesheet" href="<UploadPortalUrl>/chat-widget.css">
<script src="<UploadPortalUrl>/config.js"></script>
<script src="<UploadPortalUrl>/chat-widget.js" async></script>
```

`config.js` sets `window.WS_ENDPOINT` for the widget. The widget reads that, opens the WebSocket, renders the bottom-right chat bubble.

## URL handoff from the auditor-controller website

The county's existing per-division lookup pages (auditorcontroller.org) can deep-link a claimant straight into the upload portal with the form pre-filled. Useful when the claimant has already identified their refund through the existing site and just needs to submit documents.

### URL format

```
<UploadPortalUrl>/?name=<NAME>&type=<TYPES>&amount=<AMOUNTS>&id=<IDS>&address=<ADDRESS>&confidence=<CONFIDENCE>
```

| Param | Required | Notes |
|---|---|---|
| `name` | yes | Claimant's full name (URL-encoded). Pre-fills the name field across all sections of the unified form. |
| `type` | yes | Comma-separated list of refund types: `STALE_WARRANT`, `PAYROLL`, `PROPERTY_TAX`. Drives which form sections render and which documents are required. |
| `amount` | yes | Comma-separated dollar amounts, one per type, in the same order. Used for notarization-threshold logic. |
| `id` | recommended | Comma-separated identifiers, one per type. For stale warrants this is the warrant number; for property tax it's the assessment index. |
| `address` | recommended | Pre-fills the address field. URL-encoded full address. |
| `assessment` | property tax only | Property tax assessment number. |
| `taxyear` | property tax only | Property tax year. |
| `confidence` | optional | `high` or `low`. If absent, defaults to `high`. The bot's `tax_lookup` tool sets this; the county-side handoff should usually omit it. |

### Example: stale warrant handoff

```
http://riverside-tax-refund-v2-portal-779377649677.s3-website-us-west-2.amazonaws.com/
  ?name=Jane%20Doe
  &type=STALE_WARRANT
  &amount=475.00
  &id=W123456
  &address=123%20Main%20St%2C%20Riverside%2C%20CA%2092501
```

### Example: combined refunds

```
?name=Jane%20Doe
  &type=STALE_WARRANT,PROPERTY_TAX
  &amount=475.00,1245.80
  &id=W123456,2023-456789
  &address=123%20Main%20St%2C%20Riverside%2C%20CA%2092501
  &assessment=2023-456789
  &taxyear=2023
```

### Embedding on auditorcontroller.org

The simplest integration is a single anchor tag on each lookup result page:

```html
<a href="<UploadPortalUrl>/?name=...&type=...&amount=...&id=..." class="claim-button">
  Continue to Claims Portal →
</a>
```

If the existing lookup pages are server-rendered (PHP, .NET, etc.), construct the URL server-side from the matched record and link directly. If they're JS-driven, build the URL in the click handler before navigating.

For the chat widget itself (the bot), use the embed snippet from the section above (`chat-widget.css` + `config.js` + `chat-widget.js`). The bot then drives the same handoff URL automatically when claimants chat their way to a match.

### Validation

Test the handoff before going live:

1. Construct a URL with a known claimant from the demo dataset
2. Open in a fresh browser tab
3. Confirm the unified form loads with the name, refund type(s), amount(s), and documents-required section pre-populated
4. Confirm the form validates and submits successfully
5. Confirm the submission appears in the admin dashboard with the right department tags

## Smoke tests

```bash
# 1. WebSocket round-trip — should stream tokens back and end with {type:"done"}
python3 - <<'PY'
import json, ssl, threading, websocket
URL = "wss://<from stack output ChatWebSocketUrl>?session=smoke12345abc"
done = threading.Event()
def on_msg(ws, m):
    f = json.loads(m)
    if f.get("type") == "delta": print(f["text"], end="", flush=True)
    elif f.get("type") == "done": print("\n[done]"); done.set()
def on_open(ws):
    ws.send(json.dumps({"action":"sendMessage","session":"smoke12345abc","text":"What services do you offer?"}))
ws = websocket.WebSocketApp(URL, on_message=on_msg, on_open=on_open)
threading.Thread(target=lambda: ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}), daemon=True).start()
done.wait(timeout=60); ws.close()
PY

# 2. Tax lookup tool — should hit the decoy quiz
# Send: "Do you have refunds for Carey Ministries?"
# Expect: tool_use frame, then 4 streets in a numbered list

# 3. Handoff queue
# Send: "I want to talk to a person"
# Expect: tool_use {request_agent}, handoff frame with REF-XXXXX, then verify in DynamoDB:
aws dynamodb query \
  --profile <your-profile> --region us-west-2 \
  --table-name riverside-tax-refund-v2-chat-sessions \
  --index-name handoffIx \
  --key-condition-expression "gsi1pk = :p" \
  --expression-attribute-values '{":p":{"S":"HANDOFF_PENDING"}}'
```

## Troubleshooting

**`SSM PutParameter failed` during deploy.** The system prompt is over 4 KB and the parameter tier is set to `Standard`. Either trim the prompt below 4096 characters or change `bot/infrastructure.py`'s `prompt_param` tier to `ssm.ParameterTier.ADVANCED` (already the default).

**`pip install ... returned non-zero exit status 1` during synth.** A package in `bot/runtime/requirements.txt` or `bot/chat_handler/requirements.txt` doesn't have a manylinux wheel for the version pinned. Bump the package version. The `_LocalBundling` class enforces `--platform manylinux2014_x86_64 --only-binary=:all:` so the bundle works on Lambda.

**`Bedrock 403 AccessDeniedException`.** Bedrock model access not enabled in your account/region. Console → Bedrock → Model access → request access to `anthropic.claude-haiku-4-5`. Approval is instant.

**WebSocket connects but `sendMessage` errors with `Sorry, something went wrong`.** Check `aws logs tail /aws/lambda/<project>-chat-handler --follow`. Most common cause: the Lambda IAM role lacks `bedrock:InvokeModel*` for the inference profile, or the SSM parameter is missing.

**Admin dashboard 404s.** CodeBuild build failed. Check `https://<region>.console.aws.amazon.com/codesuite/codebuild/projects/<AdminBuildProjectName>`. Most common cause: `admin_dashboard.github_branch` in `config.yaml` doesn't exist on the remote, or the GitHub repo is private and CodeBuild lacks access.

**Stack stuck in `UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS`.** A `BucketDeployment` resource can't delete its bucket contents on rollback (typically because the source-asset includes large directories like `.venv`). Manually empty the bucket via the console, then retry `cdk deploy`.

## Tearing down

```bash
cdk destroy --require-approval never
```

S3 buckets with `auto_delete_objects=True` empty themselves. DynamoDB tables and CloudFront distributions delete in the foreground. The Cognito user pool is destroyed. Bedrock model access in the account is unaffected — that's an account-level setting.

The data bucket's `refunds_demo_balanced.jsonl` is removed; the SES verified identity remains until manually deleted.
