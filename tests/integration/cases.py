"""Test cases for the Riverside refund-bot stack.

Each case is a function `case_<slug>(eps) -> None` that exercises one
end-to-end flow and raises AssertionError if the contract is violated.
The runner discovers them by inspecting this module.

Cases are deliberately small. When something breaks in production we'd
rather have 20 small assertions than 5 big "did everything work" ones,
because the small ones tell us *which* step regressed.
"""

from __future__ import annotations

import re
from typing import Any

from tests.integration import client as c


# A claimant from refunds_demo_balanced.jsonl. If the dataset is replaced
# the real thing should be fine, since the bot fuzzy-matches on first name.
HAPPY_NAME = "Carey Ministries"
HAPPY_ADDRESS = "789 MISSION BLVD, SAN DIEGO, CA 92154"
HAPPY_STREET = "MISSION BLVD"
HAPPY_NUMBER = "789"

NO_MATCH_NAME = "Bartholomew Cubbins"


# ── REST: reserve → quiz → verify → status round-trip ──────────


def case_reserve_returns_token(eps: c.StackEndpoints) -> None:
    """Bot-handoff path: /claimant/reserve mints a session token along with
    the submission id. Without this, save-draft fails with 'session expired'
    on the first click."""
    res = c.reserve(
        eps,
        name=HAPPY_NAME,
        refund_type="STALE_WARRANT",
        address=HAPPY_ADDRESS,
    )
    assert "submissionId" in res, f"missing submissionId: {res}"
    assert res.get("token"), f"reserve must return a token; got {res}"
    assert "expiresAt" in res, f"reserve must return expiresAt; got {res}"


def case_quiz_decoys_are_stable(eps: c.StackEndpoints) -> None:
    """Two reserves for the same claimant should yield the same 4 quiz
    options in the same order. Lets a claimant who restarts the flow see an
    identical 4-button quiz instead of fresh randomization."""
    a = c.reserve(eps, name=HAPPY_NAME, refund_type="STALE_WARRANT", address=HAPPY_ADDRESS)
    b = c.reserve(eps, name=HAPPY_NAME, refund_type="STALE_WARRANT", address=HAPPY_ADDRESS)
    quiz_a = c.quiz(eps, a["submissionId"])
    quiz_b = c.quiz(eps, b["submissionId"])
    assert quiz_a.get("street_options") == quiz_b.get("street_options"), (
        f"quiz options drifted between reserves: {quiz_a} vs {quiz_b}"
    )
    assert HAPPY_STREET in quiz_a["street_options"], (
        f"claimant's real street should be one of the options: {quiz_a['street_options']}"
    )


def case_verify_round_trip(eps: c.StackEndpoints) -> None:
    """Reserve → verify with right answer → status returns the draft."""
    res = c.reserve(eps, name=HAPPY_NAME, refund_type="STALE_WARRANT", address=HAPPY_ADDRESS)
    sid = res["submissionId"]
    verified = c.verify(eps, sid, street=HAPPY_STREET, number=HAPPY_NUMBER)
    assert verified.get("token"), f"verify must mint a token: {verified}"
    s = c.status(eps, sid, verified["token"])
    assert s["submissionId"] == sid, f"status echoes submission id: {s}"
    assert s["overallStatus"] == "draft", (
        f"new submission should still be draft until upload: {s['overallStatus']}"
    )
    assert "draftFormData" in s, f"status response shape includes draftFormData: {s}"


def case_save_draft_persists_form_fields(eps: c.StackEndpoints) -> None:
    """save-draft → status → expect draftFormData echoed back."""
    res = c.reserve(eps, name=HAPPY_NAME, refund_type="STALE_WARRANT", address=HAPPY_ADDRESS)
    sid = res["submissionId"]
    token = res["token"]
    payload = {"name": HAPPY_NAME, "phone": "555-555-1212", "warrant_number": "W123"}
    c.save_draft(eps, sid, token, form_data=payload)
    s = c.status(eps, sid, token)
    saved = s.get("draftFormData") or {}
    for k, v in payload.items():
        assert saved.get(k) == v, f"draftFormData should round-trip {k}: got {saved}"


def case_save_draft_rejects_after_submit(eps: c.StackEndpoints) -> None:
    """Once /upload-complete has flipped status off draft, save-draft must
    409 — otherwise an autosave on tab close could clobber a real submission.
    """
    res = c.reserve(eps, name=HAPPY_NAME, refund_type="STALE_WARRANT", address=HAPPY_ADDRESS)
    sid = res["submissionId"]
    token = res["token"]

    # Mint presigned URLs. We use /upload (not /claimant/continue) so the
    # status row gets recomputed off draft.
    up = c.upload(
        eps,
        name=HAPPY_NAME,
        refund_type="STALE_WARRANT",
        address=HAPPY_ADDRESS,
        files=[
            {"filename": "unified-form.json", "contentType": "application/json"},
            {"filename": "government-id.pdf", "contentType": "application/pdf"},
        ],
        submission_id=sid,
    )
    # PUT placeholder bytes so the row's documents list catches up.
    for slot in up["uploads"]:
        body = b'{"placeholder": true}' if slot["filename"].endswith(".json") else b"%PDF-1.4\n%test\n"
        ct = "application/json" if slot["filename"].endswith(".json") else "application/pdf"
        c.s3_put(slot["uploadUrl"], body, ct)
    c.upload_complete(eps, sid, [s["filename"] for s in up["uploads"]])

    # Now save-draft must 409.
    try:
        c.save_draft(eps, sid, token, form_data={"name": HAPPY_NAME})
    except c.HttpError as e:
        assert e.status == 409, f"expected 409 after submit, got {e.status}: {e.body}"
        return
    raise AssertionError("save-draft should have refused after submit")


# ── WebSocket: chat-bot lookup flows ───────────────────────────


def case_chat_smoke(eps: c.StackEndpoints) -> None:
    """A simple "hi" should produce a non-empty welcome reply with no errors."""
    sess = c.ChatSession(session_id=c.fresh_session_id(), ws_url=eps.ws_url)
    try:
        turn = sess.send("hi")
        assert turn.done, f"chat turn never completed: {turn.raw_frames}"
        assert turn.error is None, f"chat returned error: {turn.error}"
        assert len(turn.text.strip()) > 20, f"welcome reply too short: {turn.text!r}"
    finally:
        sess.close()


def case_chat_no_match(eps: c.StackEndpoints) -> None:
    """Lookup of a name not in the dataset should deflect cleanly without
    pretending it found a match."""
    sess = c.ChatSession(session_id=c.fresh_session_id(), ws_url=eps.ws_url)
    try:
        turn = sess.send(f"refunds for {NO_MATCH_NAME}")
        assert turn.done and turn.error is None
        # Bot should NOT have called tax_lookup with positive results, and the
        # text should mention "no refunds" or a similar deflection.
        text = turn.text.lower()
        assert (
            "no refunds" in text
            or "couldn't find" in text
            or "could not find" in text
            or "not find" in text
        ), f"no-match reply should deflect; got {turn.text!r}"
    finally:
        sess.close()


def case_chat_address_quiz_starts(eps: c.StackEndpoints) -> None:
    """Giving a known full name should trigger tax_lookup and produce a
    street_options frame with the claimant's real street present."""
    sess = c.ChatSession(session_id=c.fresh_session_id(), ws_url=eps.ws_url)
    try:
        turn = sess.send(f"My name is {HAPPY_NAME}")
        assert turn.done and turn.error is None
        assert "tax_lookup" in turn.tool_uses, f"expected tax_lookup tool call: {turn.tool_uses}"
        assert turn.street_options, f"expected street_options frame: {turn.raw_frames[-3:]}"
        assert HAPPY_STREET in turn.street_options, (
            f"real street missing from quiz: {turn.street_options}"
        )
        # And critically, the bot must NOT have leaked refund details.
        assert "$" not in turn.text, f"bot leaked dollar amounts pre-verification: {turn.text!r}"
    finally:
        sess.close()


def case_chat_handoff_returns_reference(eps: c.StackEndpoints) -> None:
    """Asking for a person should produce a handoff frame with a REF-XXXXX."""
    sess = c.ChatSession(session_id=c.fresh_session_id(), ws_url=eps.ws_url)
    try:
        turn = sess.send("I want to talk to a person")
        assert turn.done and turn.error is None
        assert "request_agent" in turn.tool_uses, (
            f"expected request_agent tool call: {turn.tool_uses}"
        )
        assert turn.handoff_ref, f"expected handoff frame with reference: {turn.raw_frames}"
        assert re.match(r"^REF-[A-Z0-9]{3,12}$", turn.handoff_ref), (
            f"reference should match REF-XXXXX shape: {turn.handoff_ref}"
        )
    finally:
        sess.close()


def case_chat_first_name_only_asks_for_full(eps: c.StackEndpoints) -> None:
    """A single-word first name should NOT immediately call tax_lookup; the
    bot is supposed to ask for the full name first."""
    sess = c.ChatSession(session_id=c.fresh_session_id(), ws_url=eps.ws_url)
    try:
        turn = sess.send("Chris")
        assert turn.done and turn.error is None
        assert "tax_lookup" not in turn.tool_uses, (
            f"first-name-only should not trigger tax_lookup; got {turn.tool_uses}"
        )
        text = turn.text.lower()
        assert (
            "last name" in text or "full name" in text
        ), f"expected request for full/last name; got {turn.text!r}"
    finally:
        sess.close()


# ── Discovery ──────────────────────────────────────────────────


def all_cases() -> list[tuple[str, Any]]:
    """Return [(name, fn)] for every case_* defined here, preserving file
    order so the runner can show a stable progression."""
    out: list[tuple[str, Any]] = []
    for name, value in globals().items():
        if name.startswith("case_") and callable(value):
            out.append((name[5:].replace("_", "-"), value))
    return out
