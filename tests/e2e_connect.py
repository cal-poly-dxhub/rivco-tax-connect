#!/usr/bin/env python3
"""
E2E tests for the Riverside County Tax Refund Bot via Amazon Connect chat.

Runs scripted conversations through the live Connect flow, collects transcripts,
and uses an LLM (Claude on Bedrock) to judge whether each conversation met expectations.

Usage:
    python tests/e2e_connect.py                    # run all scenarios
    python tests/e2e_connect.py --scenario 3       # run one scenario by number
    python tests/e2e_connect.py --tag lookup        # run scenarios matching a tag
    python tests/e2e_connect.py --list              # list all scenarios

Requires: deployed stack, AWS creds with Connect + Bedrock access.
"""
import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field

import boto3
import websocket

# ---------------------------------------------------------------------------
# Config — override via env vars or edit here
# ---------------------------------------------------------------------------
REGION = os.environ.get("AWS_REGION", "us-west-2")
INSTANCE_ID = os.environ.get("CONNECT_INSTANCE_ID", "b5a167fa-10a6-41c9-9150-affd1f5bfcb5")
FLOW_ID = os.environ.get("CONNECT_FLOW_ID", "b91a5635-1cb1-4f1d-aa4e-c86852087574")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "us.anthropic.claude-sonnet-4-20250514-v1:0")
BOT_RESPONSE_TIMEOUT = int(os.environ.get("BOT_RESPONSE_TIMEOUT", "30"))
SETTLE_DELAY = float(os.environ.get("SETTLE_DELAY", "12"))

connect_client = boto3.client("connect", region_name=REGION)
participant_client = boto3.client("connectparticipant", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)


# ---------------------------------------------------------------------------
# Test scenario definitions
# ---------------------------------------------------------------------------
@dataclass
class Scenario:
    name: str
    tags: list[str]
    messages: list[str]
    expected: str
    critical_checks: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)


SCENARIOS: list[Scenario] = [
    # ── Lookup: single match across refund types ──────────────────────
    Scenario(
        name="Single match — stale warrant",
        tags=["lookup", "verification", "stale_warrant"],
        messages=["My name is Jane Doe", "Yes"],
        expected=(
            "The bot should find a refund for Jane Doe. "
            "It must ask the caller to verify their address (123 Main St, Anytown) BEFORE revealing any dollar amounts. "
            "After the caller confirms with 'Yes', the bot should reveal the stale warrant refund details including the dollar amount. "
            "A claims portal link should be provided since this is a chat channel."
        ),
        critical_checks=[
            "Address verification was requested BEFORE revealing dollar amounts",
            "Refund amount was revealed AFTER address confirmation",
            "Refund type is stale warrant",
        ],
    ),
    Scenario(
        name="Single match — property tax",
        tags=["lookup", "verification", "property_tax"],
        messages=["My name is John A Smith", "Yes"],
        expected=(
            "The bot should find a property tax refund for John A Smith. "
            "It must ask to verify the address (456 Oak Ave, Sampleville) before revealing amounts. "
            "After confirmation, it should state the property tax refund amount ($2,076.70) and provide a claims portal link."
        ),
        critical_checks=[
            "Address verification requested before revealing amounts",
            "Property tax refund type mentioned",
            "Amount approximately $2,076.70 revealed after confirmation",
        ],
    ),
    Scenario(
        name="Single match — payroll",
        tags=["lookup", "verification", "payroll"],
        messages=["My name is Robert Johnson", "Yes"],
        expected=(
            "The bot should find a payroll refund for Robert Johnson. "
            "It must verify the address (4200 Market St, Riverside) before revealing amounts. "
            "After confirmation, it should state the payroll refund amount ($1,788.33) and provide a claims portal link. "
            "Note: the portal link intentionally contains name/address/amount as URL parameters — this is by design, not a security issue."
        ),
        critical_checks=[
            "Address verification requested before amounts",
            "Payroll refund type mentioned",
            "Amount approximately $1,788.33 revealed after confirmation",
        ],
    ),
    Scenario(
        name="Single match — business name",
        tags=["lookup", "verification", "business"],
        messages=["Acme Corp", "Yes"],
        expected=(
            "The bot should find a stale warrant refund for Acme Corp Inc. "
            "It should verify the address (789 Mission Blvd, San Diego) before revealing the amount ($157.03)."
        ),
        critical_checks=[
            "Matched the business name despite caller not saying 'Inc'",
            "Address verification before amounts",
        ],
    ),
    # ── Lookup: multiple records same person ──────────────────────────
    Scenario(
        name="Multiple refunds — same person, mixed types",
        tags=["lookup", "verification", "multi_refund", "demo"],
        messages=["My name is Michael Brown", "Yes"],
        expected=(
            "The bot should find MULTIPLE refunds for Michael Brown (3 stale warrants and 1 property tax, all at the same address in Riverside). "
            "It should verify the address first. After confirmation, it MUST list ALL refunds individually — not just one or two. "
            "Each refund should include its type and amount. A claims portal link should be provided."
        ),
        critical_checks=[
            "All 4 refunds listed (3 stale warrants + 1 property tax)",
            "Each refund has its own amount",
            "Address verification before revealing details",
        ],
    ),
    # ── Disambiguation ────────────────────────────────────────────────
    Scenario(
        name="Disambiguation — multiple people, same name",
        tags=["lookup", "disambiguation", "demo"],
        messages=["My name is David Wilson", "Temecula", "Yes"],
        expected=(
            "The bot should detect that there are multiple people named David Wilson at different addresses. "
            "It must present the addresses and ask which one is the caller's (one in Temecula, one in Riverside). "
            "After the caller says 'Temecula', the bot should narrow to that person and then verify the full address. "
            "After confirmation, it should reveal the refund details or attempt to retrieve them. "
            "It is also acceptable if the bot offers to transfer to an agent after address confirmation."
        ),
        critical_checks=[
            "Bot presented multiple addresses to choose from",
            "Address verification still happened after disambiguation",
        ],
    ),
    Scenario(
        name="Disambiguation — wrong address given",
        tags=["lookup", "disambiguation", "edge_case"],
        messages=["My name is David Wilson", "999 Fake Street, Nowhere"],
        expected=(
            "The bot should detect multiple people named David Wilson and present addresses. "
            "When the caller gives an address that doesn't match any option, the bot should re-present the valid addresses "
            "or indicate the address didn't match. It should NOT reveal any refund details."
        ),
        critical_checks=[
            "Bot did not reveal refund amounts",
            "Bot indicated the address didn't match or re-listed valid options",
        ],
    ),
    # ── Verification: denial ──────────────────────────────────────────
    Scenario(
        name="Address verification denied",
        tags=["verification", "security"],
        messages=["My name is Jane Doe", "No, that's not my address"],
        expected=(
            "The bot should find Jane Doe and ask to verify the address. "
            "When the caller denies the address, the bot must NOT reveal any refund amounts or details. "
            "It should suggest contacting the office directly at (951) 955-3800."
        ),
        critical_checks=[
            "No dollar amounts were revealed after denial",
            "Office phone number (951) 955-3800 was provided",
        ],
    ),
    # ── No match ──────────────────────────────────────────────────────
    Scenario(
        name="No match — unknown name",
        tags=["lookup", "no_match"],
        messages=["My name is Zzzzzyx Qqqqqbert"],
        expected=(
            "The bot should indicate that no refunds were found for this name. "
            "It should suggest the caller may have no refunds or that the refund may have passed its deadline. "
            "It may suggest checking spelling or trying another name."
        ),
        critical_checks=[
            "Clearly stated no refunds found",
            "Did not fabricate any refund data",
        ],
    ),
    # ── Fuzzy match ───────────────────────────────────────────────────
    Scenario(
        name="Fuzzy match — typo in name",
        tags=["lookup", "fuzzy"],
        messages=["My name is Jayne Doh", "Yes"],
        expected=(
            "Despite the typos ('Jayne Doh' instead of 'Jane Doe'), the bot should still match to Jane Doe. "
            "It should proceed with address verification and reveal refund details after confirmation."
        ),
        critical_checks=[
            "Matched to Jane Doe despite typos",
            "Normal verification and refund flow followed",
        ],
    ),
    # ── Knowledge base ────────────────────────────────────────────────
    Scenario(
        name="Knowledge base — office hours",
        tags=["knowledge_base"],
        messages=["What are your office hours?"],
        expected=(
            "The bot should answer with the correct office hours: "
            "Monday through Thursday 8:00 AM to 5:00 PM, Friday 8:00 AM to 4:30 PM."
        ),
        critical_checks=[
            "Monday-Thursday hours mentioned (8am-5pm)",
            "Friday hours mentioned (8am-4:30pm)",
        ],
    ),
    Scenario(
        name="Knowledge base — phone number",
        tags=["knowledge_base"],
        messages=["What is your phone number?"],
        expected="The bot should provide the office phone number: (951) 955-3800.",
        critical_checks=["Phone number (951) 955-3800 provided"],
    ),
    Scenario(
        name="Knowledge base — office location",
        tags=["knowledge_base"],
        messages=["Where is your office located?"],
        expected=(
            "The bot should provide the office address: "
            "4080 Lemon Street, 6th Floor, Riverside, CA 92501."
        ),
        critical_checks=[
            "4080 Lemon Street mentioned",
            "6th Floor mentioned",
            "Riverside mentioned",
        ],
    ),
    Scenario(
        name="Knowledge base — claim deadline FAQ",
        tags=["knowledge_base", "faq"],
        messages=["Is there a deadline to claim my money?"],
        expected=(
            "The bot should explain that the County holds funds for four years "
            "and three years for property tax refunds. After that, money goes to the General Fund."
        ),
        critical_checks=[
            "Four years mentioned for general claims",
            "Three years mentioned for property tax",
            "General Fund mentioned",
        ],
    ),
    Scenario(
        name="Knowledge base — replacement check timeline FAQ",
        tags=["knowledge_base", "faq"],
        messages=["How long does it take to get a replacement check?"],
        expected=(
            "The bot should say 45 to 60 days after a valid claim, up to 12 weeks for complex cases, "
            "and that claims over $10,000 may take longer due to Board approval."
        ),
        critical_checks=[
            "45 to 60 days mentioned",
            "12 weeks for complex cases",
            "$10,000 threshold and Board approval mentioned",
        ],
    ),
    Scenario(
        name="Knowledge base — IRS taxable wage limits",
        tags=["knowledge_base", "payroll"],
        messages=["what are the limits on taxable wage from the irs?"],
        expected=(
            "The bot should search the knowledge base and provide information about IRS taxable wage limits, "
            "such as Social Security wage limits ($176,100) or Medicare wage thresholds. "
            "It should NOT attempt a refund lookup. It should reference payroll tax information "
            "or the Payroll division."
        ),
        critical_checks=[
            "Bot used knowledge base (response references taxable wage limits or payroll tax info)",
            "Bot did NOT attempt a refund lookup",
            "Response mentions specific dollar amounts or IRS wage limits",
        ],
    ),
    Scenario(
        name="Knowledge base — IRS wage limits then escalate",
        tags=["knowledge_base", "escalation", "demo"],
        messages=["what are the limits on taxable wage from the irs?", "I want to speak to a real person"],
        expected=(
            "The bot should first search the knowledge base and provide IRS taxable wage limit information "
            "(e.g. Social Security wage base $176,100, Medicare has no limit). "
            "Then when the caller asks for a live person, the bot should acknowledge and transfer to a live agent. "
            "It should say something like 'transferring you' or 'connecting you to a representative'."
        ),
        critical_checks=[
            "Bot provided IRS taxable wage limit information from knowledge base",
            "Bot acknowledged the transfer request",
            "Bot indicated it is transferring to a live agent",
        ],
    ),
    # ── Escalation ────────────────────────────────────────────────────
    Scenario(
        name="Escalation — request live agent",
        tags=["escalation"],
        messages=["I want to speak to a real person"],
        expected=(
            "The bot should acknowledge the request and initiate a transfer to a live agent. "
            "It should say something like 'transferring you' or 'connecting you to a representative'. "
            "It should NOT refuse or try to keep the caller in the automated system."
        ),
        critical_checks=[
            "Bot acknowledged the transfer request",
            "Bot indicated it is transferring to a live agent",
        ],
    ),
    # ── Edge cases ────────────────────────────────────────────────────
    Scenario(
        name="Edge case — gibberish input",
        tags=["edge_case"],
        messages=["asdfghjkl zxcvbnm"],
        expected=(
            "The bot should handle gibberish without crashing. Any reasonable response is acceptable — "
            "it may try to help, ask for clarification, or say no refunds were found. "
            "It must NOT crash, return an error, or fabricate refund data."
        ),
        critical_checks=[
            "Bot responded gracefully (no error message)",
            "No fabricated refund data",
        ],
    ),
    Scenario(
        name="Edge case — partial name only",
        tags=["edge_case"],
        messages=["My name is Gloria"],
        expected=(
            "The bot should attempt a lookup with just 'Gloria'. It may match Jane Doe via fuzzy matching "
            "or it may say no exact match was found. Either way, it should not crash or return an error."
        ),
        critical_checks=[
            "Bot handled partial name without error",
            "Response was reasonable (either matched or said no match)",
        ],
    ),
    Scenario(
        name="Edge case — off-topic question",
        tags=["edge_case"],
        messages=["What is the weather like today?"],
        expected=(
            "The bot should politely redirect to its purpose (tax refunds, county services). "
            "It should NOT attempt to answer the weather question or make up information."
        ),
        critical_checks=[
            "Did not answer the weather question",
            "Redirected to relevant county services",
        ],
    ),
    # ── Portal link regression ────────────────────────────────────────
    Scenario(
        name="Portal link — pre-filled URL, not generic",
        tags=["lookup", "portal_link", "regression"],
        messages=["My name is David Wilson", "Magnolia Ave", "Yes"],
        expected=(
            "After disambiguation and address verification, the bot should reveal the refund details "
            "and provide a Claims Portal link. The link MUST be the pre-filled portal URL from the tool response "
            "(an S3 website URL like http://<bucket>.s3-website-<region>.amazonaws.com?name=...&type=...&amount=...). "
            "It must NOT be a generic or made-up URL like 'rivcoauditor.org/claims' or 'auditorcontroller.org/claims'. "
            "The URL should contain query parameters with the customer's name, refund type, and amount."
        ),
        critical_checks=[
            "Portal link is an S3 website URL (contains 's3-website' or 's3.amazonaws.com'), NOT a generic rivcoauditor.org or auditorcontroller.org URL",
            "Portal link contains query parameters (has '?' followed by name, type, amount params)",
            "Bot did not fabricate or hallucinate a URL",
        ],
    ),
    # ── SMS sending (chat-based proxy for voice SMS flow) ─────────────
    Scenario(
        name="SMS sending — bot responds after phone number",
        tags=["lookup", "disambiguation", "sms", "regression"],
        messages=[
            "My name is David Wilson",
            "Magnolia",
            "Yes",
            "Can you send that link to my phone via text? My number is 951-555-0199",
        ],
        expected=(
            "After disambiguation (Magnolia) and address verification, the bot should reveal the refund. "
            "When the caller then asks to receive the link via text and provides a phone number, the bot must NOT go silent. "
            "It should respond in one of these acceptable ways: "
            "(a) confirm the phone number and attempt to send an SMS, "
            "(b) say it already provided the link in the chat, "
            "(c) ask to confirm the number before sending. "
            "The critical requirement is that the bot RESPONDS — any reasonable reply is acceptable. "
            "Silence or no response after the phone number is a FAIL."
        ),
        critical_checks=[
            "Bot responded after the user provided a phone number (did not go silent)",
            "Bot's response was relevant to the SMS/text request (acknowledged the phone number, offered to send, or explained the link was already provided)",
        ],
    ),
    # ── Spanish mode ──────────────────────────────────────────────────
    Scenario(
        name="Spanish — greeting is in Spanish",
        tags=["spanish"],
        messages=[],
        attributes={"language": "es_US"},
        expected=(
            "The bot's initial greeting/welcome message should be in Spanish, not English. "
            "It should welcome the caller and ask for their name in Spanish."
        ),
        critical_checks=[
            "Greeting is in Spanish (contains Spanish words like 'Bienvenido', 'nombre', 'reembolso', or similar)",
            "Greeting is NOT in English",
        ],
    ),
    Scenario(
        name="Spanish — lookup responds in Spanish",
        tags=["spanish", "lookup"],
        messages=["Mi nombre es Jane Doe", "Sí"],
        attributes={"language": "es_US"},
        expected=(
            "The bot should respond entirely in Spanish throughout the conversation. "
            "It should find a refund for Jane Doe, ask for address verification in Spanish, "
            "and after the caller confirms with 'Sí', reveal the refund details in Spanish. "
            "A claims portal link should be provided."
        ),
        critical_checks=[
            "All bot responses are in Spanish",
            "Address verification was requested in Spanish before revealing amounts",
            "Refund details were revealed in Spanish after confirmation",
        ],
    ),
    Scenario(
        name="Spanish — no match responds in Spanish",
        tags=["spanish", "no_match"],
        messages=["Mi nombre es Zzzzzyx Qqqqqbert"],
        attributes={"language": "es_US"},
        expected=(
            "The bot should respond in Spanish indicating no refunds were found. "
            "The 'no match' message should be in Spanish, not English."
        ),
        critical_checks=[
            "No-match response is in Spanish",
            "Response is NOT in English",
        ],
    ),
    Scenario(
        name="Spanish — knowledge base question in Spanish",
        tags=["spanish", "knowledge_base"],
        messages=["¿Cuál es el horario de la oficina?"],
        attributes={"language": "es_US"},
        expected=(
            "The bot should answer the office hours question in Spanish. "
            "It should mention Monday-Thursday 8am-5pm and Friday 8am-4:30pm, but in Spanish."
        ),
        critical_checks=[
            "Response is in Spanish",
            "Office hours information is present (Monday-Thursday and Friday hours)",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Connect chat session
# ---------------------------------------------------------------------------
class ChatSession:
    """Manages a single Connect chat conversation via WebSocket."""

    def __init__(self):
        self._messages: list[tuple[float, str, str]] = []  # (timestamp, role, text)
        self._new_message = threading.Event()
        self._ws = None
        self._conn_token = None
        self._closed = threading.Event()
        self._bot_count = 0
        self._typing_at = 0.0

    def start(self, attributes: dict[str, str] | None = None) -> None:
        kwargs = dict(
            InstanceId=INSTANCE_ID,
            ContactFlowId=FLOW_ID,
            ParticipantDetails={"DisplayName": "E2ETest"},
            ChatDurationInMinutes=60,
        )
        if attributes:
            kwargs["Attributes"] = attributes
        resp = connect_client.start_chat_contact(**kwargs)
        token = resp["ParticipantToken"]
        conn = participant_client.create_participant_connection(
            ParticipantToken=token, Type=["WEBSOCKET", "CONNECTION_CREDENTIALS"]
        )
        ws_url = conn["Websocket"]["Url"]
        self._conn_token = conn["ConnectionCredentials"]["ConnectionToken"]

        self._ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=lambda ws, e: None,
            on_close=lambda ws, c, m: self._closed.set(),
        )
        t = threading.Thread(target=self._ws.run_forever, daemon=True)
        t.start()
        # Wait for welcome message
        self._wait_for_bot(timeout=BOT_RESPONSE_TIMEOUT)

    def send(self, text: str) -> None:
        self._messages.append((time.time(), "USER", text))
        self._new_message.clear()
        participant_client.send_message(
            ConnectionToken=self._conn_token, Content=text, ContentType="text/plain"
        )

    def wait_for_response(self, timeout: int = BOT_RESPONSE_TIMEOUT) -> list[str]:
        """Wait for bot to finish responding, using typing indicators."""
        count_before = self._bot_count
        deadline = time.time() + timeout
        # Wait for at least one new bot message
        while self._bot_count == count_before:
            remaining = deadline - time.time()
            if remaining <= 0:
                return []
            self._new_message.clear()
            self._new_message.wait(timeout=remaining)
        # After a message, keep waiting while bot is still typing
        while True:
            self._new_message.clear()
            if not self._new_message.wait(timeout=SETTLE_DELAY):
                break  # No activity — bot is done
            # If we got a typing event recently, bot is still working
            if time.time() - self._typing_at < SETTLE_DELAY:
                continue
            # New message but no recent typing — brief settle then done
            self._new_message.clear()
            if not self._new_message.wait(timeout=2):
                break
        return [text for _, role, text in self._messages if role == "BOT"][count_before:]

    def disconnect(self) -> None:
        try:
            participant_client.disconnect_participant(ConnectionToken=self._conn_token)
        except Exception:
            pass
        self._closed.wait(timeout=5)

    def transcript(self) -> str:
        return "\n".join(f"{role}: {text}" for _, role, text in sorted(self._messages))

    # -- internals --

    def _on_open(self, ws):
        ws.send(json.dumps({"topic": "aws/subscribe", "content": {"topics": ["aws/chat"]}}))

    def _on_message(self, ws, raw):
        data = json.loads(raw)
        if data.get("topic") != "aws/chat":
            return
        content = json.loads(data["content"])
        ctype = content.get("ContentType", "")
        role = content.get("ParticipantRole", "")
        if ctype == "application/vnd.amazonaws.connect.event.typing" and role != "CUSTOMER":
            self._typing_at = time.time()
            self._new_message.set()
        elif content.get("Type") == "MESSAGE" and role != "CUSTOMER":
            self._messages.append((time.time(), "BOT", content.get("Content", "")))
            self._bot_count += 1
            self._new_message.set()
            self._bot_count += 1
            self._new_message.set()

    def _wait_for_bot(self, timeout: int = BOT_RESPONSE_TIMEOUT) -> list[str]:
        """Wait for bot response(s). Collects messages until no new ones arrive within SETTLE_DELAY."""
        count_before = self._bot_count
        deadline = time.time() + timeout
        while self._bot_count == count_before:
            remaining = deadline - time.time()
            if remaining <= 0:
                return []
            self._new_message.clear()
            self._new_message.wait(timeout=remaining)
        while True:
            self._new_message.clear()
            if not self._new_message.wait(timeout=SETTLE_DELAY):
                break
        return [text for _, role, text in self._messages[-(self._bot_count - count_before):] if role == "BOT"]


# ---------------------------------------------------------------------------
# LLM Judge
# ---------------------------------------------------------------------------
JUDGE_SYSTEM = """\
You are a strict QA judge for a county tax refund chatbot. You evaluate whether a \
conversation transcript meets the expected behavior.

You will receive:
1. EXPECTED BEHAVIOR — what the conversation should accomplish
2. CRITICAL CHECKS — specific things that MUST be true for a PASS
3. TRANSCRIPT — the actual conversation between USER and BOT

Evaluation rules:
- Every CRITICAL CHECK must be satisfied for a PASS. If ANY critical check fails, verdict is FAIL.
- Be strict about security: if the expected behavior says amounts should not be revealed, \
  ANY dollar amount in the bot's response before proper verification is a FAIL.
- Be strict about verification order: address verification MUST happen before refund details.
- Be lenient about exact wording — the bot doesn't need to use the exact same words, \
  just convey the same information.
- Be lenient about dollar amounts being off by a few cents (rounding).
- If the bot gives an error message or seems broken, that is a FAIL.
- If the bot fabricates data not in the expected behavior, that is a FAIL.

Respond with EXACTLY this format (XML tags, no other text):
<test_result>PASS or FAIL</test_result>
<failure_reason>If FAIL, one sentence explaining why. If PASS, leave empty.</failure_reason>
<reasoning>One paragraph with your detailed judgment.</reasoning>
<failed_checks>Comma-separated list of critical checks that failed. Empty if PASS.</failed_checks>\
"""


def _extract_tag(text: str, tag: str) -> str:
    import re
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def judge(scenario: Scenario, transcript: str) -> dict:
    user_msg = (
        f"EXPECTED BEHAVIOR:\n{scenario.expected}\n\n"
        f"CRITICAL CHECKS:\n"
        + "\n".join(f"- {c}" for c in scenario.critical_checks)
        + f"\n\nTRANSCRIPT:\n{transcript}"
    )
    resp = bedrock.invoke_model(
        modelId=JUDGE_MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "temperature": 0,
            "top_k": 1,
            "system": JUDGE_SYSTEM,
            "messages": [{"role": "user", "content": user_msg}],
        }),
    )
    body = json.loads(resp["body"].read())
    text = body["content"][0]["text"].strip()
    usage = body.get("usage", {})

    verdict = _extract_tag(text, "test_result") or "ERROR"
    failure_reason = _extract_tag(text, "failure_reason")
    reasoning = _extract_tag(text, "reasoning")
    failed_raw = _extract_tag(text, "failed_checks")
    failed_checks = [c.strip() for c in failed_raw.split(",") if c.strip()] if failed_raw else []

    return {
        "verdict": verdict,
        "failure_reason": failure_reason,
        "reasoning": reasoning,
        "failed_checks": failed_checks,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
import threading

_progress_lock = threading.Lock()
_started = 0
_finished = 0

def _log_start(total):
    global _started
    with _progress_lock:
        _started += 1
        print(f"  [{_started}/{total}] started")

def _log_finish(name, verdict, total):
    global _finished
    with _progress_lock:
        _finished += 1
        icon = {"PASS": "PASS", "FAIL": "FAIL", "ERROR": "ERROR"}[verdict]
        print(f"  [{_finished}/{total}] {icon}: {name}")


def run_scenario(idx: int, scenario: Scenario, total: int) -> dict:
    _log_start(total)

    session = ChatSession()
    t0 = time.time()
    try:
        session.start(attributes=scenario.attributes or None)

        for i, msg in enumerate(scenario.messages):
            session.send(msg)
            session.wait_for_response()

        transcript = session.transcript()
        elapsed = round(time.time() - t0, 1)
        result = judge(scenario, transcript)

        verdict = result.get("verdict", "ERROR")
        _log_finish(scenario.name, verdict, total)

        return {
            "idx": idx + 1,
            "name": scenario.name,
            "tags": scenario.tags,
            "expected": scenario.expected,
            "verdict": verdict,
            "failure_reason": result.get("failure_reason", ""),
            "reasoning": result.get("reasoning", ""),
            "failed_checks": result.get("failed_checks", []),
            "critical_checks": scenario.critical_checks,
            "transcript": transcript,
            "duration": elapsed,
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
        }
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        _log_finish(scenario.name, "ERROR", total)
        return {
            "idx": idx + 1,
            "name": scenario.name,
            "tags": scenario.tags,
            "expected": scenario.expected,
            "verdict": "ERROR",
            "failure_reason": str(e),
            "reasoning": "",
            "failed_checks": [],
            "critical_checks": scenario.critical_checks,
            "transcript": getattr(session, "transcript", lambda: "")(),
            "duration": elapsed,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    finally:
        session.disconnect()
        time.sleep(1)


def generate_report(results: list[dict], total_time: float) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    total = len(results)
    total_in = sum(r.get("input_tokens", 0) for r in results)
    total_out = sum(r.get("output_tokens", 0) for r in results)

    lines = [
        f"# E2E Test Report",
        f"{now} | {total_time:.0f}s | Judge: `{JUDGE_MODEL}` | {passed}/{total} passed | tokens: {total_in:,} in / {total_out:,} out\n",
        "| # | Scenario | Result | In | Out | Reason |",
        "|---|----------|--------|----|-----|--------|",
    ]
    for r in results:
        reason = r.get("failure_reason") or r.get("reasoning", "")
        reason = reason.split(". ")[0].rstrip(".") + "." if reason else ""
        lines.append(f"| {r['idx']} | {r['name']} | {r['verdict']} | {r.get('input_tokens',0):,} | {r.get('output_tokens',0):,} | {reason} |")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="E2E Connect chat tests with LLM judge")
    parser.add_argument("--scenario", type=int, help="Run a single scenario by number (1-based)")
    parser.add_argument("--tag", type=str, help="Run scenarios matching this tag")
    parser.add_argument("--list", action="store_true", help="List all scenarios and exit")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    args = parser.parse_args()

    if args.list:
        for i, s in enumerate(SCENARIOS):
            print(f"  {i + 1:2d}. [{', '.join(s.tags)}] {s.name}")
        return

    scenarios = list(enumerate(SCENARIOS))
    if args.scenario:
        idx = args.scenario - 1
        if idx < 0 or idx >= len(SCENARIOS):
            print(f"Invalid scenario number. Must be 1-{len(SCENARIOS)}")
            sys.exit(1)
        scenarios = [(idx, SCENARIOS[idx])]
    elif args.tag:
        scenarios = [(i, s) for i, s in enumerate(SCENARIOS) if args.tag in s.tags]
        if not scenarios:
            print(f"No scenarios match tag '{args.tag}'")
            sys.exit(1)

    total = len(scenarios)
    workers = min(args.workers, total)
    print(f"Running {total} scenario(s) with {workers} worker(s)...\n")
    t_start = time.time()

    global _started, _finished
    _started = 0
    _finished = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed
    results_map = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_scenario, idx, scenario, total): idx for idx, scenario in scenarios}
        for future in as_completed(futures):
            r = future.result()
            results_map[r["idx"]] = r
    results = [results_map[k] for k in sorted(results_map)]
    total_time = time.time() - t_start

    # Console summary
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}")
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    failed = sum(1 for r in results if r["verdict"] == "FAIL")
    errors = sum(1 for r in results if r["verdict"] == "ERROR")

    for r in results:
        icon = {"PASS": "✅", "FAIL": "❌", "ERROR": "💥"}[r["verdict"]]
        print(f"  {icon} {r['name']}")
        if r["failed_checks"]:
            for fc in r["failed_checks"]:
                print(f"       ✗ {fc}")

    print(f"\n  {passed} passed, {failed} failed, {errors} errors out of {len(results)} scenarios")
    total_in = sum(r.get("input_tokens", 0) for r in results)
    total_out = sum(r.get("output_tokens", 0) for r in results)
    print(f"  Tokens: {total_in:,} in / {total_out:,} out")

    # Write markdown report
    report_md = generate_report(results, total_time)
    report_path = "tests/e2e_report.md"
    with open(report_path, "w") as f:
        f.write(report_md)
    print(f"  Report: {report_path}")

    # Write raw JSON for programmatic use
    json_path = "tests/e2e_results.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Raw data: {json_path}")

    sys.exit(1 if (failed + errors) > 0 else 0)


if __name__ == "__main__":
    main()
