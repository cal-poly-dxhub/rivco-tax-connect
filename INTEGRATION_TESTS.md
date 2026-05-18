# Integration Tests

Manual test scenarios for verifying the deployed stack. Run these against the live chat widget after each deploy. Names below come from `refunds_demo_balanced.jsonl`.

## Smoke test

Open the chat widget on the upload portal (`UploadPortalUrl` stack output). Send "hi". Expect: bot greets, mentions the four topic areas (refunds / stale dated warrants / payroll / property tax), no errors.

## Refund lookup — happy path

**Send:** `My name is Carey Ministries`

**Expect:**
1. `tool_use` indicator appears
2. Bot returns numbered list of 4 streets (decoy quiz)
3. Real address is `789 MISSION BLVD` — but the bot must *not* reveal that

**Send:** `I have lived at 789 Mission Blvd`

**Expect:**
4. Bot lists every refund individually with type, dollar amount, and deadline
5. Bot includes a portal URL with `?name=...&type=...&amount=...&id=...` query params (this is the personalized claim link)
6. Bot mentions the 45-60 day processing time

## Refund lookup — failed quiz

**Send:** `My name is Carey Ministries`

(Wait for street options.)

**Send:** `I lived at 100 Fake Street`

**Expect:**
- Bot says identity could not be verified, suggests calling the office directly
- Does NOT retry the quiz, does NOT reveal which street was correct
- Does NOT reveal any refund details

## Refund lookup — no match

**Send:** `Refunds for Bartholomew Cubbins`

**Expect:**
- Bot returns "We found no refunds for Bartholomew Cubbins. You may have no refunds or your refund may have passed its claim deadline."
- Suggests checking spelling or trying another name

## Multiple refunds — same person

**Send:** `Michael Brown`

**Expect:**
- After address verification: bot lists 4 refunds (3 stale warrants + 1 property tax) individually
- One portal URL with all 4 refund types encoded

## Disambiguation

**Send:** `My name is John Smith`

**Expect (if dataset has multiple John Smiths):**
- Bot returns the address quiz with options spanning multiple addresses
- Picking any address narrows to that person's records

## Live agent handoff

**Send:** `I want to talk to a real person`

**Expect:**
1. Bot calls `request_agent`
2. Browser shows a banner: "Reference number: REF-XXXXX. Call (951) 955-3800..."
3. Bot's spoken response includes the same REF-XXXXX
4. Verify in DynamoDB:

```bash
aws dynamodb query --profile <profile> --region us-west-2 \
  --table-name riverside-tax-refund-v2-chat-sessions \
  --index-name handoffIx \
  --key-condition-expression "gsi1pk = :p" \
  --expression-attribute-values '{":p":{"S":"HANDOFF_PENDING"}}'
```

The query should return a row matching the REF-XXXXX, with the user's session ID and a `reason` field.

## Admin handoff queue

1. Sign in to the admin dashboard as super-admin
2. Click "Chat handoffs" in the top-right nav
3. The handoff from the previous test should appear
4. Click into it — full transcript visible
5. Click "Mark resolved" — handoff disappears from the pending list
6. Toggle the filter to "All" — handoff reappears with `Resolved` status

## URL fabrication safety

**Send:** `My name is Carey Ministries`, then verify at `789 Mission Blvd`.

**Expect:**
- The portal URL the bot returns matches `^http://riverside-tax-refund-v2-portal-[0-9]+\.s3-website-[^/]+/\?name=` (or the deployed hostname). The agent must NEVER make up `https://example.com/` or shorten/modify the URL.

**Send:** `Can you give me a different shorter link?`

**Expect:**
- Bot declines or repeats the same URL. Does NOT generate an alternate URL.

## Multi-turn replay

This catches the `parsed_output` bug we hit during the initial deploy.

**Send:** `My name is Carey Ministries`

(Wait for street options.)

**Send:** `789 Mission Blvd`

**Expect:**
- Bot proceeds normally to refund details. No `Sorry, something went wrong.` error.

If you see the error, check chat-handler CloudWatch logs for `Extra inputs are not permitted` — that's the response-only-fields-leaking-into-input bug.

## Spanish

**Send:** `Tengo refunds para Carey Ministries`

**Expect:**
- Bot responds entirely in Spanish, including the address quiz, FAQ answers, and refund details. No mixed English.

## Upload portal end-to-end

1. From a chat where the bot returned a portal URL, click the link (or copy/paste)
2. The portal loads with the unified claim form pre-filled (name, refund type, amount from query params)
3. Required documents are listed based on the refund type
4. Upload sample docs (any PDFs/images)
5. Submit
6. Admin dashboard `/dashboard` should show the submission with status `uploaded`
7. Click into it — see the AP-13 PDF preview filled with the claim data

## Stack hygiene

```bash
# Confirm no Connect/Lex/Wisdom resources remain in the account
aws connect list-instances --profile <profile> --region us-west-2 --query "InstanceSummaryList[?contains(InstanceAlias, 'riverside')]"
aws lexv2-models list-bots --profile <profile> --region us-west-2 --query "botSummaries[?contains(botName, 'TaxRefund')]"
aws wisdom list-assistants --profile <profile> --region us-west-2 --query "assistantSummaries[?contains(name, 'riverside')]"
```

All three should return empty arrays.

## Tearing down a test session

```bash
# Delete a single test chat session by ID (admin-only API):
curl -X DELETE "<UploadApiUrl>admin/chat-sessions/<session_id>" \
  -H "Authorization: Bearer <cognito-id-token>"
```

Or just let the TTL expire; sessions auto-delete after 7 days.
