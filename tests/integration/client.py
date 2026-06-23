"""HTTP + WebSocket clients for the Riverside refund-bot stack.

These are intentionally thin — just enough to drive the same endpoints the
claimant portal and admin dashboard call. Tests assert on the response shape;
fancy retry / pagination logic is out of scope.

Endpoints (all on the upload REST API except WebSocket):
  POST /claimant/reserve            mint submission id + token
  GET  /claimant/quiz?id=           street options for the address quiz
  POST /claimant/verify             street + house number, returns token
  GET  /claimant/status?id=         submission state (token-auth)
  POST /claimant/save-draft         persist partial form fields + filenames
  POST /claimant/continue           presigned PUT URLs for upload-more flow
  POST /upload                      presigned PUT URLs for initial submit
  POST /upload-complete             merge filenames + flip status

WebSocket frames the chat handler emits:
  {type: delta, text: ...}              token stream
  {type: tool_use, name: ...}           bot called a tool
  {type: street_options, options: []}   address-quiz buttons
  {type: number_input}                  ask-for-house-number form
  {type: handoff, reference: ...}       live-agent ref number
  {type: done}                          turn complete
  {type: error, message: ...}           handler error
"""

from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover - import-time guard
    websocket = None  # type: ignore


# ── HTTP client ───────────────────────────────────────────────────────


class HttpError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


def http_request(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """One-shot HTTP call returning parsed JSON. Raises HttpError on non-2xx."""
    body_bytes = None
    final_headers: dict[str, str] = {"Accept": "application/json"}
    if json_body is not None:
        body_bytes = json.dumps(json_body).encode()
        final_headers["Content-Type"] = "application/json"
    if headers:
        final_headers.update(headers)
    req = urllib.request.Request(url, data=body_bytes, method=method, headers=final_headers)
    # Default Python SSL context lacks the system CA bundle on macOS Python.org
    # builds. certifi covers that; fall back gracefully if certifi isn't there.
    try:
        import certifi  # type: ignore
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise HttpError(e.code, body) from e


# ── REST helpers tailored to our endpoints ────────────────────────────


@dataclass
class StackEndpoints:
    """The two URLs every test needs. Populate from cdk outputs / env / CLI."""

    api_url: str  # e.g. https://owago2nrwc.execute-api.us-west-2.amazonaws.com/prod/
    ws_url: str   # e.g. wss://2tfmq7uxcj.execute-api.us-west-2.amazonaws.com/prod

    def api(self, path: str) -> str:
        base = self.api_url.rstrip("/")
        if not path.startswith("/"):
            path = "/" + path
        return base + path


def reserve(eps: StackEndpoints, *, name: str, refund_type: str, address: str) -> dict[str, Any]:
    return http_request(
        "POST",
        eps.api("/claimant/reserve"),
        json_body={"name": name, "refundType": refund_type, "address": address},
    )


def quiz(eps: StackEndpoints, submission_id: str) -> dict[str, Any]:
    return http_request("GET", eps.api(f"/claimant/quiz?id={urllib.parse.quote(submission_id)}"))


def verify(
    eps: StackEndpoints, submission_id: str, *, street: str, number: str
) -> dict[str, Any]:
    return http_request(
        "POST",
        eps.api("/claimant/verify"),
        json_body={"submissionId": submission_id, "street": street, "number": number},
    )


def status(eps: StackEndpoints, submission_id: str, token: str) -> dict[str, Any]:
    return http_request(
        "GET",
        eps.api(f"/claimant/status?id={urllib.parse.quote(submission_id)}"),
        headers={"X-Claimant-Token": token},
    )


def save_draft(
    eps: StackEndpoints,
    submission_id: str,
    token: str,
    *,
    form_data: dict[str, Any],
    filenames: list[str] | None = None,
    original_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    return http_request(
        "POST",
        eps.api("/claimant/save-draft"),
        json_body={
            "submissionId": submission_id,
            "formData": form_data,
            "filenames": filenames or [],
            "originalNames": original_names or {},
        },
        headers={"X-Claimant-Token": token},
    )


def upload(eps: StackEndpoints, *, name: str, refund_type: str, address: str,
           files: list[dict[str, str]], submission_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "refundType": refund_type,
        "address": address,
        "files": files,
    }
    if submission_id:
        body["submissionId"] = submission_id
    return http_request("POST", eps.api("/upload"), json_body=body)


def upload_complete(eps: StackEndpoints, submission_id: str, filenames: list[str]) -> dict[str, Any]:
    return http_request(
        "POST",
        eps.api("/upload-complete"),
        json_body={"submissionId": submission_id, "filenames": filenames},
    )


def s3_put(presigned_url: str, data: bytes, content_type: str) -> None:
    """Upload bytes to a presigned URL. Returns nothing; raises on non-2xx."""
    req = urllib.request.Request(
        presigned_url,
        data=data,
        method="PUT",
        headers={"Content-Type": content_type},
    )
    try:
        import certifi  # type: ignore
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            if resp.status >= 300:
                raise HttpError(resp.status, "PUT failed")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise HttpError(e.code, body) from e


# ── WebSocket helper for the chat agent ───────────────────────────────


@dataclass
class ChatTurn:
    """Everything the chat handler emitted in response to one sendMessage."""

    text: str = ""
    tool_uses: list[str] = field(default_factory=list)
    street_options: list[str] | None = None
    asked_for_number: bool = False
    handoff_ref: str | None = None
    error: str | None = None
    done: bool = False
    raw_frames: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ChatSession:
    """One WebSocket conversation; reusable across multiple turns."""

    session_id: str
    ws_url: str
    _ws: Any | None = None
    _open_event: threading.Event = field(default_factory=threading.Event)

    def __post_init__(self) -> None:
        if websocket is None:
            raise RuntimeError(
                "websocket-client is required for chat tests. `pip install websocket-client`."
            )

    def open(self) -> None:
        url = f"{self.ws_url}?session={self.session_id}"
        ws = websocket.WebSocketApp(  # type: ignore[attr-defined]
            url,
            on_open=lambda _w: self._open_event.set(),
        )
        self._ws = ws
        # Run the loop in a daemon thread so test scripts can ctrl-c out.
        threading.Thread(
            target=lambda: ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}),
            daemon=True,
        ).start()
        if not self._open_event.wait(timeout=10):
            raise RuntimeError("WebSocket failed to open within 10s")

    def send(self, text: str, *, timeout: float = 60.0) -> ChatTurn:
        """Send one user message; collect frames until {type: done} arrives."""
        if self._ws is None:
            self.open()
        turn = ChatTurn()
        finished = threading.Event()

        def on_msg(_ws: Any, message: str) -> None:
            try:
                frame = json.loads(message)
            except Exception:  # noqa: BLE001
                return
            turn.raw_frames.append(frame)
            t = frame.get("type")
            if t == "delta":
                turn.text += str(frame.get("text", ""))
            elif t == "tool_use":
                turn.tool_uses.append(str(frame.get("name", "")))
            elif t == "street_options":
                opts = frame.get("options")
                if isinstance(opts, list):
                    turn.street_options = [str(o) for o in opts]
            elif t == "number_input":
                turn.asked_for_number = True
            elif t == "handoff":
                ref = frame.get("reference")
                if isinstance(ref, str):
                    turn.handoff_ref = ref
            elif t == "error":
                turn.error = str(frame.get("message", ""))
                finished.set()
            elif t == "done":
                turn.done = True
                finished.set()

        # websocket-client lets you swap on_message mid-flight by reassigning
        # the attribute directly.
        self._ws.on_message = on_msg  # type: ignore[union-attr]
        self._ws.send(  # type: ignore[union-attr]
            json.dumps({"action": "sendMessage", "session": self.session_id, "text": text})
        )
        finished.wait(timeout=timeout)
        return turn

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
            self._ws = None


def fresh_session_id() -> str:
    """12 alnum chars — matches the chat handler's $connect validation."""
    import secrets
    return secrets.token_hex(6)


def wait(seconds: float) -> None:
    time.sleep(seconds)
