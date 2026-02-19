# Integration Test Cases

Manual test cases for verifying feature implementation against the live bot (voice or chat).

## Test 1: Basic Lookup — Single Stale Warrant
**Say/type:** "My name is Jane Doe"
**Expected:**
- Bot finds 1 refund
- Asks address verification: "Do you currently or have you previously lived at 123 Main St, Anytown, CA 92241?"
- After confirming → reveals: Stale Warrant, $75.00, deadline 02/08/2030
- Provides AP13 PDF link
- Mentions upload portal for supporting docs

## Test 2: Multiple Refunds — Same Person
**Say/type:** "Michael Brown"
**Expected:**
- Bot finds 4 refunds: 3 Stale Warrant ($879.59, $754.75, $1,959.14) + 1 Property Tax ($1,245.80)
- Asks address verification (654 Pine Rd, Riverside)
- After confirming → lists ALL 4 refunds individually with type, amount, deadline
- Provides a single Claims Portal link
- Portal shows tabs: "Stale Warrant — AP13 Affidavit" and "Property Tax Claim" with pre-filled iframes

## Test 3: Property Tax Refund — Different Form URL
**Say/type:** "John A Smith"
**Expected:**
- Bot finds 1 Property Tax refund, $2,076.70, deadline 08/01/2027
- Address verification: 456 Oak Ave, Sampleville
- Provides a single Claims Portal link (not the raw county form URL)
- Portal shows the property tax web form embedded in an iframe with pre-filled fields

## Test 4: Payroll Refund
**Say/type:** "Robert Johnson"
**Expected:**
- Bot finds 1 Payroll refund, $1,788.33, deadline 01/25/2027
- Address verification: 4200 Market St, Riverside
- Links to AP13 PDF
- Mentions upload portal for supporting docs

## Test 5: Business Name Lookup
**Say/type:** "Corona-Norco Unified School District"
**Expected:**
- Bot finds 1 Stale Warrant, $3,636.63, deadline 02/04/2029
- Address verification: 2820 Clark Ave, Norco

## Test 6: No Match Found
**Say/type:** "Zzzfakename Doesnotexist"
**Expected:**
- "We found no refunds for Zzzfakename Doesnotexist. You may have no refunds or your refund may have passed its claim deadline."
- Should NOT mention expired refunds or deadlines specifically

## Test 7: Fuzzy Name Matching
**Say/type:** "chris ryan" (lowercase, partial)
**Expected:**
- Should still match MICHAEL BROWN (fuzzy threshold 0.8)
- Returns the 3 refunds

## Test 8: Address Verification — Denied
**Say/type:** "Jane Doe" → then when asked about address, say "No, I've never lived there"
**Expected:**
- Bot should NOT reveal refund details
- Says it cannot verify identity, suggests contacting the office directly

## Test 9: SMS Link Delivery (Voice Channel)
**Call the bot** and say: "John A Smith" → confirm address → then when given the claim link...
**Expected:**
- Bot should NOT read the full URL aloud
- Should ask: "Would you like me to send the claim form link to your phone via text message?"
- If you give a number like "+12125551234" → bot calls `send_sms` tool

## Test 10: SMS Link Delivery (Chat Channel)
**Chat** and type: "John A Smith" → confirm address
**Expected:**
- Bot includes the claim URL directly inline in the chat message (no offer to text it)

## Test 11: Live Agent Handoff
**Say/type:** "I want to talk to a real person"
**Expected:**
- Bot offers to transfer: "Let me transfer you to a live representative..."
- Contact flow routes to TaxRefundLiveAgents queue

## Test 12: Frustration Detection
**Say/type:** Something like "this isn't working, I keep getting the wrong answer, this is useless"
**Expected:**
- Bot proactively offers to transfer to a live representative

## Test 13: Spanish — Voice
**Call the bot** → when prompted "For English press 1, para español presione 2" → press 2
**Expected:**
- Voice switches to Lupe (Spanish)
- Bot responds in Spanish throughout the conversation

## Test 14: Spanish — Chat
**Type:** "Mi nombre es Maria Johnson"
**Expected:**
- Bot responds in Spanish
- Finds Payroll refund, $2,109.51

## Test 15: Dollar Formatting
**Any lookup that returns results**
**Expected:**
- Amounts shown as `$75.00`, `$3,593.48`, etc.
- Never spelled out as "seventy-five dollars" in chat

## Test 16: Website Q&A (Knowledge Base)
**Say/type:** "What does the Auditor-Controller office do?" or "How do I report fraud?"
**Expected:**
- Bot answers from the crawled auditorcontroller.org content
- Does NOT make up information or say "I don't know" if the info is on the website

## Test 17: Multiple Refund Types — Charles Deluna
**Say/type:** "Charles Deluna"
**Expected:**
- 2 Stale Warrant refunds ($1,798.32 + $605.33)
- Both link to AP13 PDF
- Address verification: 3850 Jurupa Ave, Riverside
