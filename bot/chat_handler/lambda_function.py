"""WebSocket-driven chat handler — Bedrock Claude with tool use.

Wired to API Gateway WebSocket routes:
  $connect      — register session, write SESSION#<id> META row
  $disconnect   — clear connection, leave transcript intact
  sendMessage   — append user msg, run Claude turn, stream deltas back

Uses raw boto3 `bedrock-runtime.invoke_model_with_response_stream` (no Anthropic
SDK) so the Lambda only carries boto3 and we keep the Bedrock-specific request
shape — including `amazon-bedrock-guardrailConfig` — wide open for future use.

The agent has three tools:
  tax_lookup     — fuzzy name match against the refund S3 dataset (existing Lambda)
  request_agent  — generate a 5-char reference number, write a HANDOFF row;
                   the user is told to call the office and quote the reference
  send_email     — SES send (used for portal-link delivery to web visitors)
"""
import json
import logging
import os
import secrets
import string
import time
from datetime import datetime, timezone
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CHAT_TABLE = os.environ["CHAT_TABLE"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
TAX_LOOKUP_FN = os.environ["TAX_LOOKUP_FN"]
WS_ENDPOINT = os.environ["WS_ENDPOINT"]
SES_SENDER = os.environ.get("SES_SENDER", "")
AI_PROMPT_PARAM = os.environ["AI_PROMPT_PARAM"]
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "")
REGION = os.environ.get("AWS_REGION", "us-west-2")
SESSION_TTL_DAYS = 7
OFFICE_PHONE = os.environ.get("OFFICE_PHONE", "(951) 955-3800")

ANTHROPIC_VERSION = "bedrock-2023-05-31"

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(CHAT_TABLE)
lambda_client = boto3.client("lambda")
ses = boto3.client("ses")
ssm = boto3.client("ssm")
ws_client = boto3.client("apigatewaymanagementapi", endpoint_url=WS_ENDPOINT)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)

_prompt_cache: str | None = None


def system_prompt() -> str:
    """Fetch the system prompt from SSM. Cached on the Lambda container."""
    global _prompt_cache
    if _prompt_cache is None:
        resp = ssm.get_parameter(Name=AI_PROMPT_PARAM)
        _prompt_cache = str(resp["Parameter"]["Value"])
    return _prompt_cache


TOOLS = [
    {
        "name": "tax_lookup",
        "description": (
            "Look up tax refunds for a customer. Two-step identity verification. "
            "Step 1: pass customer_name only — returns address_verification:'street' "
            "with a list of street_options to ask the user. "
            "Step 2: after the user picks a street, pass customer_name + customer_street — "
            "returns address_verification:'number' asking for the house number on that street. "
            "Step 3: pass customer_name + customer_street + customer_number — returns refunds "
            "and portal_url on success, or verification_failed on a wrong answer. The tool never "
            "returns the actual address. After verification_failed, do NOT retry; tell the user "
            "to contact the office."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Full name, first name, or business name to search for.",
                },
                "customer_street": {
                    "type": "string",
                    "description": "The street name the user picked from the verification quiz (e.g. 'Mission Blvd'). Step 2+.",
                },
                "customer_number": {
                    "type": "string",
                    "description": "The house number the user provided (e.g. '789'). Step 3 only.",
                },
            },
            "required": ["customer_name"],
        },
    },
    {
        "name": "request_agent",
        "description": (
            "Generate a reference number when the user asks for a live agent or you cannot resolve "
            "their issue. The reference is saved with the conversation transcript. Tell the user to "
            "call (951) 955-3800 and quote the reference."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief reason for the handoff so the agent knows what to expect.",
                },
            },
            "required": ["reason"],
        },
    },
    {
        "name": "send_email",
        "description": (
            "Send an email to the user. Useful for delivering the Claims Portal link if the user "
            "prefers email over an in-chat link."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain text body."},
            },
            "required": ["to", "subject", "body"],
        },
    },
]


# ── DynamoDB helpers ───────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ttl_epoch() -> int:
    return int(time.time()) + SESSION_TTL_DAYS * 24 * 3600


def _put_session(session_id: str, connection_id: str) -> None:
    table.put_item(Item={
        "pk": f"SESSION#{session_id}",
        "sk": "META",
        "sessionId": session_id,
        "connectionId": connection_id,
        "startedAt": _now_iso(),
        "status": "active",
        "ttl": _ttl_epoch(),
    })


def _disconnect_session(connection_id: str) -> None:
    """Find the session by connection ID via scan and clear the connection."""
    # In high-volume systems we'd add a GSI on connectionId. For a county-scale
    # demo a scan over active sessions is fine — the table TTLs old rows.
    resp = table.scan(
        FilterExpression="sk = :meta AND connectionId = :cid",
        ExpressionAttributeValues={":meta": "META", ":cid": connection_id},
    )
    for item in resp.get("Items", []):
        table.update_item(
            Key={"pk": item["pk"], "sk": "META"},
            UpdateExpression="SET disconnectedAt = :d, #s = :s REMOVE connectionId",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":d": _now_iso(), ":s": "disconnected"},
        )


def _append_message(session_id: str, role: str, content: Any) -> None:
    table.put_item(Item={
        "pk": f"SESSION#{session_id}",
        "sk": f"MSG#{_now_iso()}",
        "role": role,
        "content": content if isinstance(content, str) else json.dumps(content),
        "ttl": _ttl_epoch(),
    })


def _load_history(session_id: str) -> list[dict]:
    """Return Claude-shaped messages array from the DynamoDB transcript.

    Content is stored as a plain string for simple text turns or as a
    JSON-encoded list for content-block turns. Only json.loads when the value
    looks like a JSON array — naively json.loads-ing every value would parse
    a user message of "789" as the int 789 and the API rejects that.
    """
    resp = table.query(
        KeyConditionExpression="pk = :p AND begins_with(sk, :s)",
        ExpressionAttributeValues={":p": f"SESSION#{session_id}", ":s": "MSG#"},
    )
    messages = []
    for item in resp.get("Items", []):
        content = item["content"]
        parsed: Any = content
        if isinstance(content, str) and content.startswith("["):
            try:
                parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                parsed = content
        messages.append({"role": item["role"], "content": parsed})
    return messages


def _create_handoff(session_id: str, reason: str) -> str:
    ref = "REF-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))
    now = _now_iso()
    table.put_item(Item={
        "pk": f"SESSION#{session_id}",
        "sk": "HANDOFF",
        "refNumber": ref,
        "reason": reason,
        "requestedAt": now,
        "gsi1pk": "HANDOFF_PENDING",
        "gsi1sk": now,
        "ttl": _ttl_epoch(),
    })
    return ref


# ── Verification lockout (per-name attempt counter) ────────────
# Keyed on a normalized version of the looked-up customer name so a new
# session does not reset the counter.

MAX_VERIFICATION_FAILURES = 5
LOCKOUT_TTL_SECONDS = 60 * 60  # 1 hour


def _lockout_pk(customer_name: str) -> str:
    return f"LOCKOUT#{customer_name.lower().strip()}"


def _is_locked(customer_name: str) -> bool:
    resp = table.get_item(Key={"pk": _lockout_pk(customer_name), "sk": "META"})
    item = resp.get("Item") or {}
    locked_until = int(item.get("verificationLockedUntil") or 0)
    return locked_until > int(time.time())


def _record_verification_failure(customer_name: str) -> tuple[int, bool]:
    """Atomically increment the failure counter; lock when the threshold is reached.

    Uses ADD (atomic counter) for the increment so concurrent requests can't
    double-count. The lock timestamp is set in a second conditional write that
    is idempotent — concurrent callers racing to set it are both fine.

    Returns `(failures_after_increment, is_now_locked)`.
    """
    now = int(time.time())
    resp = table.update_item(
        Key={"pk": _lockout_pk(customer_name), "sk": "META"},
        UpdateExpression="ADD verificationFailures :one SET lastFailureAt = :ts",
        ExpressionAttributeValues={":one": 1, ":ts": _now_iso()},
        ReturnValues="UPDATED_NEW",
    )
    failures = int(resp.get("Attributes", {}).get("verificationFailures", 0))
    is_locked = failures >= MAX_VERIFICATION_FAILURES
    if is_locked:
        try:
            table.update_item(
                Key={"pk": _lockout_pk(customer_name), "sk": "META"},
                UpdateExpression="SET verificationLockedUntil = :u",
                ConditionExpression=(
                    "attribute_not_exists(verificationLockedUntil) OR verificationLockedUntil <= :now"
                ),
                ExpressionAttributeValues={":u": now + LOCKOUT_TTL_SECONDS, ":now": now},
            )
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            pass  # already locked by a concurrent request — fine
    return failures, is_locked


def _record_verification_success(customer_name: str) -> None:
    """Reset failure counter on a clean verification."""
    table.update_item(
        Key={"pk": _lockout_pk(customer_name), "sk": "META"},
        UpdateExpression="SET verificationFailures = :z REMOVE verificationLockedUntil",
        ExpressionAttributeValues={":z": 0},
    )


# ── Tool dispatch ──────────────────────────────────────────────

def _tool_tax_lookup(_session_id: str, input_: dict) -> str:
    """Two-step verified lookup with per-name lockout after 5 failures."""
    customer_name = input_["customer_name"]

    if _is_locked(customer_name):
        return json.dumps({
            "locked": True,
            "message": (
                "This lookup has been locked after too many failed verification "
                f"attempts. Please contact the Auditor-Controller's office at "
                f"{OFFICE_PHONE} to continue."
            ),
        })

    payload = {"customer_name": customer_name}
    if input_.get("customer_street"):
        payload["customer_street"] = input_["customer_street"]
    if input_.get("customer_number"):
        payload["customer_number"] = input_["customer_number"]
    resp = lambda_client.invoke(
        FunctionName=TAX_LOOKUP_FN,
        Payload=json.dumps(payload).encode("utf-8"),
    )
    body = json.loads(resp["Payload"].read())
    result_str = body.get("result", json.dumps({"error": "Lookup failed."}))

    # Count a failure only when an answer was actually provided (street or number).
    # The initial name-only call that returns the quiz does not count.
    try:
        result = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str

    is_attempt = bool(input_.get("customer_street") or input_.get("customer_number"))
    if is_attempt and result.get("verification_failed"):
        failures, locked = _record_verification_failure(customer_name)
        result["attempts_remaining"] = max(0, MAX_VERIFICATION_FAILURES - failures)
        if locked:
            result["locked"] = True
            result["message"] = (
                "Too many failed verification attempts. Further lookups for this name "
                f"are locked for an hour. Please contact the Auditor-Controller's office at "
                f"{OFFICE_PHONE} to continue."
            )
        return json.dumps(result)

    if result.get("refunds"):
        _record_verification_success(customer_name)

    return result_str


def _tool_request_agent(session_id: str, input_: dict) -> str:
    ref = _create_handoff(session_id, input_.get("reason", ""))
    return json.dumps({"reference_number": ref})


def _tool_send_email(input_: dict) -> str:
    if not SES_SENDER:
        return "Email is not configured for this environment."
    to = input_["to"].strip()
    if "@" not in to or len(to) > 254:
        return "Invalid email address."
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": input_["subject"][:200]},
            "Body": {"Text": {"Data": input_["body"][:8000]}},
        },
    )
    return f"Email sent to {to}."


def _dispatch_tool(session_id: str, name: str, input_: dict) -> str:
    if name == "tax_lookup":
        return _tool_tax_lookup(session_id, input_)
    if name == "request_agent":
        return _tool_request_agent(session_id, input_)
    if name == "send_email":
        return _tool_send_email(input_)
    return f"Unknown tool: {name}"


# ── WebSocket helpers ──────────────────────────────────────────

def _ws_send(connection_id: str, data: dict) -> None:
    try:
        ws_client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(data).encode("utf-8"),
        )
    except ws_client.exceptions.GoneException:
        logger.warning("Connection %s gone — dropping frame", connection_id)


# ── Main handler ───────────────────────────────────────────────

def lambda_handler(event: dict, context: Any) -> dict:
    route = (event.get("requestContext") or {}).get("routeKey", "")
    connection_id = (event.get("requestContext") or {}).get("connectionId", "")
    logger.info("WS event route=%s connection=%s", route, connection_id)

    if route == "$connect":
        return _handle_connect(event, connection_id)
    if route == "$disconnect":
        _disconnect_session(connection_id)
        return {"statusCode": 200}
    if route in ("sendMessage", "$default"):
        return _handle_message(event, connection_id)
    return {"statusCode": 400, "body": f"Unknown route: {route}"}


def _handle_connect(event: dict, connection_id: str) -> dict:
    qs = event.get("queryStringParameters") or {}
    session_id = (qs.get("session") or "").strip()
    if not session_id or not session_id.isalnum() or not (8 <= len(session_id) <= 32):
        return {"statusCode": 400, "body": "session query param required (alnum, 8-32 chars)"}
    _put_session(session_id, connection_id)
    return {"statusCode": 200}


def _handle_message(event: dict, connection_id: str) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        _ws_send(connection_id, {"type": "error", "message": "Invalid JSON"})
        return {"statusCode": 400}

    session_id = (body.get("session") or "").strip()
    text = (body.get("text") or "").strip()
    if not session_id or not text:
        _ws_send(connection_id, {"type": "error", "message": "session and text required"})
        return {"statusCode": 400}

    _append_message(session_id, "user", text)
    messages = _load_history(session_id)

    try:
        _run_claude_loop(session_id, connection_id, messages)
    except Exception:
        logger.exception("Claude loop failed for session %s", session_id)
        _ws_send(connection_id, {"type": "error", "message": "Sorry, something went wrong."})
    return {"statusCode": 200}


# ── Bedrock streaming ──────────────────────────────────────────

def _stream_turn(connection_id: str, messages: list[dict]) -> tuple[list[dict], str]:
    """Invoke Bedrock with streaming, forward text deltas, return (content, stop_reason).

    Parses the Bedrock Anthropic Messages event stream into reassembled content
    blocks. Text is streamed to the client as it arrives; tool_use input JSON
    is accumulated across `input_json_delta` events and parsed at block close.
    """
    body = {
        "anthropic_version": ANTHROPIC_VERSION,
        "max_tokens": 2048,
        "system": system_prompt(),
        "tools": TOOLS,
        "messages": messages,
    }

    invoke_kwargs: dict = {
        "modelId": MODEL_ID,
        "body": json.dumps(body),
        "contentType": "application/json",
        "accept": "application/json",
    }
    # Bedrock applies the guardrail to both the input messages and the model
    # output stream. Blocked content surfaces as a `BLOCKED` stop_reason and a
    # synthesized assistant message in the stream — same handling as a normal
    # turn, no extra branch needed here.
    if GUARDRAIL_ID:
        invoke_kwargs["guardrailIdentifier"] = GUARDRAIL_ID
        if GUARDRAIL_VERSION:
            invoke_kwargs["guardrailVersion"] = GUARDRAIL_VERSION

    response = bedrock.invoke_model_with_response_stream(**invoke_kwargs)

    blocks: dict[int, dict] = {}
    tool_input_buffers: dict[int, str] = {}
    stop_reason = "end_turn"

    for event in response.get("body", []):
        chunk = event.get("chunk")
        if not chunk:
            continue
        try:
            payload = json.loads(chunk["bytes"])
        except (json.JSONDecodeError, KeyError):
            continue
        etype = payload.get("type")

        if etype == "content_block_start":
            idx = payload["index"]
            cb = payload.get("content_block") or {}
            cb_type = cb.get("type")
            if cb_type == "text":
                blocks[idx] = {"type": "text", "text": ""}
            elif cb_type == "tool_use":
                blocks[idx] = {
                    "type": "tool_use",
                    "id": cb.get("id", ""),
                    "name": cb.get("name", ""),
                    "input": {},
                }
                tool_input_buffers[idx] = ""
            elif cb_type == "thinking":
                blocks[idx] = {"type": "thinking", "thinking": "", "signature": ""}
            else:
                blocks[idx] = {"type": cb_type or "unknown"}

        elif etype == "content_block_delta":
            idx = payload["index"]
            delta = payload.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                text = delta.get("text", "")
                if idx in blocks and blocks[idx].get("type") == "text":
                    blocks[idx]["text"] += text
                if text:
                    _ws_send(connection_id, {"type": "delta", "text": text})
            elif dtype == "input_json_delta":
                tool_input_buffers[idx] = tool_input_buffers.get(idx, "") + delta.get("partial_json", "")
            elif dtype == "thinking_delta":
                if idx in blocks and blocks[idx].get("type") == "thinking":
                    blocks[idx]["thinking"] += delta.get("thinking", "")
            elif dtype == "signature_delta":
                if idx in blocks and blocks[idx].get("type") == "thinking":
                    blocks[idx]["signature"] += delta.get("signature", "")

        elif etype == "content_block_stop":
            idx = payload["index"]
            if idx in tool_input_buffers:
                raw = tool_input_buffers.pop(idx)
                try:
                    blocks[idx]["input"] = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    blocks[idx]["input"] = {}

        elif etype == "message_delta":
            stop_reason = (payload.get("delta") or {}).get("stop_reason") or stop_reason

    ordered = [blocks[i] for i in sorted(blocks.keys())]
    return ordered, stop_reason


def _run_claude_loop(session_id: str, connection_id: str, messages: list[dict]) -> None:
    """Manual agent loop — stream text deltas, dispatch tools, persist final assistant turn."""
    max_iterations = 6  # safety cap; real conversations end well before this

    for _ in range(max_iterations):
        full_content, stop_reason = _stream_turn(connection_id, messages)

        _append_message(session_id, "assistant", full_content)
        messages.append({"role": "assistant", "content": full_content})

        if stop_reason != "tool_use":
            _ws_send(connection_id, {"type": "done"})
            return

        # Dispatch every tool call in this assistant turn, send results back as one user turn.
        tool_results = []
        for block in full_content:
            if block.get("type") != "tool_use":
                continue
            _ws_send(connection_id, {"type": "tool_use", "name": block["name"]})
            try:
                result = _dispatch_tool(session_id, block["name"], block.get("input", {}))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result,
                })
                # Surface handoff to the client so it can render a banner.
                if block["name"] == "request_agent":
                    try:
                        ref = json.loads(result).get("reference_number", "")
                    except json.JSONDecodeError:
                        ref = ""
                    if ref:
                        _ws_send(connection_id, {"type": "handoff", "reference": ref})
            except Exception as e:  # noqa: BLE001
                logger.exception("Tool %s failed", block.get("name"))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": f"Tool error: {e}",
                    "is_error": True,
                })

        _append_message(session_id, "user", tool_results)
        messages.append({"role": "user", "content": tool_results})

    _ws_send(connection_id, {"type": "error", "message": "Hit iteration limit. Please try again."})
