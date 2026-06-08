import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")
BUCKET = os.environ["UPLOAD_BUCKET"]
TABLE_NAME = os.environ.get("TABLE_NAME", "")
ADMIN_CONFIG_TABLE = os.environ.get("ADMIN_CONFIG_TABLE", "")
CHAT_TABLE_NAME = os.environ.get("CHAT_TABLE", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
table = dynamodb.Table(TABLE_NAME) if TABLE_NAME else None
admin_table = dynamodb.Table(ADMIN_CONFIG_TABLE) if ADMIN_CONFIG_TABLE else None
chat_table = dynamodb.Table(CHAT_TABLE_NAME) if CHAT_TABLE_NAME else None
ALLOWED_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
    "application/json",
}
_SAFE_FILENAME = re.compile(r'[^\w.\-]')
PACKAGE_EXPIRY = 60 * 60  # 1 hour — short-lived; admin dashboard re-fetches on demand

# Default document requirements per refund type. Used as the seed when the
# admin-config table has no DOCREQ#<type> entry yet. Super-admin can override
# via the dashboard.
#   required  — counts toward `complete` status
#   internal  — hidden from non-super-admins (e.g. signed affidavit JSON)
#   either_of — at least one of the listed doc ids must be present (OR group)
#
# The unified portal collects identity / address details inside the form JSON
# itself and submits a single `unified-form.json`. The seeded defaults below
# therefore require only that signed-form artifact; admins can re-introduce
# photo-id / proof-of-address upload requirements via the doc-requirements UI.
_DEFAULT_DOC_REQS: dict[str, dict[str, Any]] = {
    "STALE_WARRANT": {
        "docs": [
            {"id": "ap13-affidavit", "label": "Signed AP13 affidavit", "required": True, "internal": True},
            {"id": "government-id", "label": "Government-issued photo ID (driver's license, passport, or state ID)", "required": True},
            {"id": "proof-of-entitlement", "label": "Proof of entitlement (original warrant, bank statement, or documentation showing you are the payee)", "required": True},
        ],
    },
    "PAYROLL": {
        "docs": [
            {"id": "ap13-affidavit", "label": "Signed AP13 affidavit", "required": True, "internal": True},
            {"id": "government-id", "label": "Government-issued photo ID (driver's license, passport, or state ID)", "required": True},
        ],
    },
    "PROPERTY_TAX": {
        "docs": [
            {"id": "property-tax-claim", "label": "Signed property tax claim", "required": True, "internal": True},
            {"id": "government-id", "label": "Government-issued photo ID (driver's license, passport, or state ID)", "required": True},
            {"id": "proof-of-ownership", "label": "Proof of property ownership (deed, title, or current tax bill)", "required": True},
        ],
    },
}

# The unified portal posts a single `unified-form.json` covering every refund
# type in the claim. Map it to each internal "signed" doc id so that file
# satisfies the affidavit / property-tax-claim requirement for any refund type
# present on the submission.
_UNIFIED_FORM_FILENAME = "unified-form.json"
_UNIFIED_FORM_FULFILLS = {"ap13-affidavit", "property-tax-claim"}


def _get_doc_req(refund_type: str) -> dict[str, Any]:
    """Return the doc requirement spec for a refund type, seeding from defaults if absent."""
    default = _DEFAULT_DOC_REQS.get(refund_type, {"docs": [], "either_of": []})
    if not admin_table:
        return default
    try:
        resp = admin_table.get_item(Key={"pk": f"DOCREQ#{refund_type}"})
    except Exception:  # noqa: BLE001
        return default
    item = resp.get("Item")
    if not item:
        return default
    return {
        "docs": item.get("docs") or default.get("docs") or [],
        "either_of": item.get("either_of") or default.get("either_of") or [],
    }


def _required_doc_ids(refund_types: list[str]) -> set[str]:
    ids = set()
    for rt in refund_types:
        for d in _get_doc_req(rt).get("docs", []):
            if d.get("required"):
                ids.add(d["id"])
    return ids


def _either_of_groups(refund_types: list[str]) -> list[list[str]]:
    groups = []
    for rt in refund_types:
        groups.extend(_get_doc_req(rt).get("either_of", []) or [])
    return groups


def _internal_doc_ids(refund_types: list[str]) -> set[str]:
    ids = set()
    for rt in refund_types:
        for d in _get_doc_req(rt).get("docs", []):
            if d.get("internal"):
                ids.add(d["id"])
    return ids


def _doc_prefix(filename: str) -> str:
    """Map 'photo-id_passport.pdf' → 'photo-id' and 'ap13-affidavit.json' → 'ap13-affidavit'."""
    return filename.split("_", 1)[0].rsplit(".", 1)[0]


def _fulfilled_doc_ids(filenames: list[str]) -> set[str]:
    """Return the set of doc-requirement ids satisfied by the uploaded filenames.

    Most files map 1:1 via `_doc_prefix`. The unified portal additionally posts
    a single `unified-form.json` that contains the signed claim payload, which
    fulfills the per-refund-type affidavit/claim doc ids in `_UNIFIED_FORM_FULFILLS`.
    """
    ids = {_doc_prefix(f) for f in filenames}
    if _UNIFIED_FORM_FILENAME in filenames:
        ids |= _UNIFIED_FORM_FULFILLS
    return ids


def _sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    name = _SAFE_FILENAME.sub('_', name)
    return name[:100] or 'file'


def _cors_headers(event: dict[str, Any] | None = None) -> dict[str, str]:
    # Echo the request Origin if it matches an allowed one; fall back to ALLOWED_ORIGIN env.
    default_origin = os.environ.get("ALLOWED_ORIGIN", "*")
    allowed = set(filter(None, (os.environ.get("ALLOWED_ORIGINS", "") or default_origin).split(",")))
    origin = default_origin
    if event is not None:
        req_origin = (event.get("headers") or {}).get("Origin") or (event.get("headers") or {}).get("origin")
        if req_origin and (req_origin in allowed or "*" in allowed):
            origin = req_origin
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Claimant-Token",
        "Access-Control-Allow-Methods": "POST,GET,PATCH,PUT,DELETE,OPTIONS",
        "Vary": "Origin",
    }


def _err(code: int, msg: str, headers: dict[str, str]) -> dict[str, Any]:
    return {"statusCode": code, "headers": headers, "body": json.dumps({"error": msg})}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scan_all(table_resource, **scan_kwargs) -> list[dict[str, Any]]:
    """Scan a DynamoDB table with pagination, returning all items."""
    items: list[dict[str, Any]] = []
    resp = table_resource.scan(**scan_kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table_resource.scan(
            **scan_kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items", []))
    return items


def _load_departments() -> list[dict[str, Any]]:
    """Return all department records from the admin-config table.

    Cached on the function object so repeat calls within one invocation (or
    within a warm-reused Lambda container) don't re-scan DynamoDB. Bust the
    cache by clearing ``_load_departments.cache`` if you write a department.
    """
    cache = getattr(_load_departments, "cache", None)
    if cache is not None:
        return cache
    if not admin_table:
        _load_departments.cache = []  # type: ignore[attr-defined]
        return _load_departments.cache
    items = _scan_all(
        admin_table,
        FilterExpression="begins_with(pk, :p)",
        ExpressionAttributeValues={":p": "DEPT#"},
    )
    _load_departments.cache = items  # type: ignore[attr-defined]
    return items


def _bust_department_cache() -> None:
    """Call after any write that changes department rows."""
    if hasattr(_load_departments, "cache"):
        delattr(_load_departments, "cache")


def _derive_departments(refund_types: list[str]) -> list[str]:
    """Map refund types to department keys using the admin-config table."""
    depts = set()
    for d in _load_departments():
        if any(rt in (d.get("refund_types") or []) for rt in refund_types):
            depts.add(d["key"])
    return sorted(depts)


def _derive_tasks(status: str, refund_types: list[str], documents: list[str]) -> list[dict[str, Any]]:
    """Build a list of task dicts {label, done} for a submission's refund type subset.

    Callers typically pass the refund types relevant to ONE department and that
    dept's status; the returned task list then represents that dept's checklist.
    """
    tasks: list[dict[str, Any]] = []
    uploaded_prefixes = _fulfilled_doc_ids(documents)

    # Per-refund-type required docs. Use a dict keyed by id so we dedupe across
    # overlapping refund types.
    seen: dict[str, str] = {}
    for rt in refund_types:
        for d in _get_doc_req(rt).get("docs", []):
            if d.get("required"):
                seen.setdefault(d["id"], d.get("label") or d["id"].replace("-", " "))
    for doc_id, label in sorted(seen.items()):
        tasks.append({"label": f"Claimant uploads {label}", "done": doc_id in uploaded_prefixes})

    # Either-of groups (e.g. proof-of-payment OR proof-of-ownership for property tax)
    for group in _either_of_groups(refund_types):
        if not group:
            continue
        done = any(doc_id in uploaded_prefixes for doc_id in group)
        pretty = " or ".join(doc_id.replace("-", " ") for doc_id in group)
        tasks.append({"label": f"Claimant uploads {pretty}", "done": done})

    tasks.append({"label": "Admin reviews documents", "done": status in {"under-review", "approved", "denied"}})
    tasks.append({"label": "Admin approves or denies claim", "done": status in {"approved", "denied"}})
    return tasks


def _tasks_by_department(
    statuses: dict[str, str], departments: list[str],
    all_refund_types: list[str], documents: list[str],
) -> dict[str, list[dict[str, str]]]:
    """Return {dept_key: tasks[]} — one task list per department."""
    out: dict[str, list[dict[str, str]]] = {}
    for dept in departments:
        sub_types = _refund_types_for_department(dept, all_refund_types)
        if not sub_types:
            continue
        out[dept] = _derive_tasks(statuses.get(dept, "partial"), sub_types, documents)
    return out


def _auth(event: dict[str, Any]) -> tuple[set[str], bool]:
    """Return (allowed_department_keys, is_super_admin) from the JWT claims.

    Super-admin sees everything and gets all department keys. Non-admins get the
    set of departments derived from their admin-<key> group memberships.
    """
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    groups_raw = claims.get("cognito:groups") or ""
    # API Gateway serializes the groups list as "[a b c]" or a comma-separated string.
    groups = set(re.split(r"[,\s\[\]]+", groups_raw)) - {""}
    is_super = "super-admin" in groups
    dept_keys = {g[len("admin-"):] for g in groups if g.startswith("admin-")}
    return dept_keys, is_super


def _actor(event: dict[str, Any]) -> str:
    """Return the Cognito username of the caller, or 'unknown' if missing."""
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    return claims.get("cognito:username") or claims.get("username") or "unknown"


def _audit(submission_id: str, actor: str, action: str, details: dict[str, Any]) -> None:
    """Record a change to a submission. Non-fatal on failure."""
    if not table:
        return
    try:
        ts = _now_iso()
        table.put_item(Item={
            "pk": f"SUBMISSION#{submission_id}",
            "sk": f"AUDIT#{ts}",
            "submissionId": submission_id,
            "timestamp": ts,
            "actor": actor,
            "action": action,
            "details": details,
        })
    except Exception:  # noqa: BLE001
        # Audit is best-effort; don't fail the user's request if the log write errors.
        pass


# ── Single-table helpers ───────────────────────────────────

def _sub_key(submission_id: str) -> dict[str, str]:
    return {"pk": f"SUBMISSION#{submission_id}", "sk": "META"}


def _get_submission(submission_id: str) -> dict[str, Any] | None:
    if not table:
        return None
    return table.get_item(Key=_sub_key(submission_id)).get("Item")


def _put_submission(
    item: dict[str, Any],
    address: str = "",
    initial_status: str = "partial",
) -> None:
    """Write a submission record. Attaches pk/sk/gsi1 keys from submissionId.

    Optional `address` stores the claimant mailing address on the META row
    (used for the address-quiz verification flow).
    Optional `initial_status` overrides per-dept status; defaults to "partial".
    """
    if not table:
        return
    sid = item["submissionId"]
    extra: dict[str, Any] = {}
    if address:
        extra["address"] = address
    item = {
        **item,
        **extra,
        "pk": f"SUBMISSION#{sid}",
        "sk": "META",
        "gsi1pk": "SUBMISSION_LIST",
        "gsi1sk": item.get("submittedAt") or _now_iso(),
    }
    table.put_item(Item=item)


def _list_submissions() -> list[dict[str, Any]]:
    """Query the listIx GSI to return all submissions, newest first."""
    if not table:
        return []
    resp = table.query(
        IndexName="listIx",
        KeyConditionExpression="gsi1pk = :p",
        ExpressionAttributeValues={":p": "SUBMISSION_LIST"},
        ScanIndexForward=False,
    )
    return resp.get("Items", [])


def _list_audit(submission_id: str) -> list[dict[str, Any]]:
    if not table:
        return []
    resp = table.query(
        KeyConditionExpression="pk = :p AND begins_with(sk, :s)",
        ExpressionAttributeValues={":p": f"SUBMISSION#{submission_id}", ":s": "AUDIT#"},
        ScanIndexForward=False,
    )
    return resp.get("Items", [])


def _delete_submission_data(submission_id: str) -> None:
    """Delete the META row and all audit rows for a submission."""
    if not table:
        return
    # Query all items under this submission's partition
    resp = table.query(
        KeyConditionExpression="pk = :p",
        ExpressionAttributeValues={":p": f"SUBMISSION#{submission_id}"},
    )
    with table.batch_writer() as batch:
        for it in resp.get("Items", []):
            batch.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    headers = _cors_headers(event)

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    resource = event.get("resource", "")
    method = event.get("httpMethod", "")
    if resource.startswith("/admin/"):
        return _handle_admin(event, headers)
    if resource == "/audit/{submissionId}" and method == "GET":
        return _handle_audit(event, headers)
    if resource == "/package" and method == "GET":
        return _handle_package(event, headers)
    if resource == "/form-schemas" and method == "GET":
        return _handle_public_form_schemas(event, headers)
    if resource == "/doc-requirements" and method == "GET":
        return _handle_public_doc_requirements(event, headers)
    if resource == "/status" and method == "GET":
        return _handle_status(event, headers)
    if resource == "/upload" and method == "POST":
        return _handle_upload(event, headers)
    if resource == "/upload-complete" and method == "POST":
        return _handle_upload_complete(event, headers)
    if resource == "/update-status" and method == "POST":
        return _handle_update_status(event, headers)
    if resource == "/delete-submission" and method == "POST":
        return _handle_delete_submission(event, headers)
    if resource.startswith("/claimant/") or resource == "/claimant/{proxy+}":
        return _handle_claimant(event, headers)

    return _err(404, f"No handler for {method} {resource}", headers)


def _handle_package(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    qs = event.get("queryStringParameters") or {}
    submission_id = (qs.get("id") or "").strip()

    if not submission_id:
        return _err(400, "id is required", headers)
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)

    try:
        manifest_obj = s3.get_object(Bucket=BUCKET, Key=f"{submission_id}/_manifest.json")
        manifest = json.loads(manifest_obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return _err(404, "Submission not found", headers)

    dept_keys, is_super = _auth(event)
    refund_types = [t.strip() for t in manifest.get("refundType", "").split(",") if t.strip()]
    submission_depts = set(_derive_departments(refund_types))
    if not is_super and not (dept_keys & submission_depts):
        return _err(403, "Not authorized for this submission", headers)

    internal_ids = _internal_doc_ids(refund_types) if not is_super else set()

    paginator = s3.get_paginator("list_objects_v2")
    files = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"{submission_id}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.split("/", 1)[1]
            if filename.startswith("_"):
                continue
            if _doc_prefix(filename) in internal_ids:
                # Hide super-admin-only docs from regular admins.
                continue
            download_url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": BUCKET, "Key": key},
                ExpiresIn=PACKAGE_EXPIRY,
            )
            files.append({"filename": filename, "downloadUrl": download_url, "size": obj["Size"]})

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({
            "submissionId": submission_id,
            "name": manifest.get("name", ""),
            "refundType": manifest.get("refundType", ""),
            "files": files,
        }),
    }


def _classify_status(refund_types: list[str], filenames: list[str]) -> str:
    """Return 'uploaded' or 'partial' based on expected vs actual docs for the given refund types."""
    expected = _required_doc_ids(refund_types)
    uploaded_prefixes = _fulfilled_doc_ids(filenames)
    if not expected <= uploaded_prefixes:
        return "partial"
    for group in _either_of_groups(refund_types):
        if not (set(group) & uploaded_prefixes):
            return "partial"
    return "uploaded"


def _refund_types_for_department(dept_key: str, all_refund_types: list[str]) -> list[str]:
    """Return the subset of a submission's refund types that belong to this dept."""
    if not admin_table:
        return []
    resp = admin_table.get_item(Key={"pk": f"DEPT#{dept_key}"}).get("Item")
    if not resp:
        return []
    dept_types = set(resp.get("refund_types") or [])
    return [rt for rt in all_refund_types if rt in dept_types]


def _compute_statuses(departments: list[str], refund_type_csv: str, filenames: list[str]) -> dict[str, str]:
    """Build the per-department statuses map by classifying each dept's required docs."""
    all_types = [t.strip() for t in refund_type_csv.split(",") if t.strip()]
    statuses: dict[str, str] = {}
    for dept in departments:
        sub_types = _refund_types_for_department(dept, all_types)
        if not sub_types:
            continue
        statuses[dept] = _classify_status(sub_types, filenames)
    return statuses


def _handle_status(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """List submissions the caller is allowed to see."""
    dept_keys, is_super = _auth(event)
    if table:
        submissions = []
        for item in _list_submissions():
            refund_types = [t.strip() for t in item.get("refundType", "").split(",") if t.strip()]
            depts = item.get("departments") or _derive_departments(refund_types)
            if not is_super and not (dept_keys & set(depts)):
                continue
            docs = item.get("documents", [])

            # Build or recover per-dept statuses. Legacy rows may still have a
            # single `status` — if `statuses` isn't present, compute from docs.
            statuses = item.get("statuses")
            if not isinstance(statuses, dict):
                statuses = _compute_statuses(depts, item.get("refundType", ""), docs)

            tasks_by_dept = _tasks_by_department(statuses, depts, refund_types, docs)

            # Filter the statuses/tasks maps to the caller's visible scope.
            if not is_super:
                visible_depts = [d for d in depts if d in dept_keys]
                statuses = {d: statuses.get(d, "partial") for d in visible_depts if d in statuses}
                tasks_by_dept = {d: tasks_by_dept[d] for d in visible_depts if d in tasks_by_dept}
                depts_out = visible_depts
            else:
                depts_out = depts

            submissions.append({
                "submissionId": item["submissionId"],
                "name": item.get("name", ""),
                "refundType": item.get("refundType", ""),
                "statuses": statuses,
                "documents": docs,
                "confidence": item.get("confidence", "high"),
                "submittedAt": item.get("submittedAt", ""),
                "departments": depts_out,
                "tasksByDepartment": tasks_by_dept,
            })

        def sort_key(s: dict[str, Any]) -> tuple[int, str]:
            # Any non-terminal status (partial/uploaded/under-review) = high priority
            terminal = {"approved", "denied"}
            any_pending = any(v not in terminal for v in s["statuses"].values())
            return (0 if any_pending else 1, s["submittedAt"])
        submissions.sort(key=sort_key)

        permissions = {
            "isSuperAdmin": is_super,
            "canDelete": is_super,
            "departments": sorted(dept_keys) if not is_super else None,
        }
        return {"statusCode": 200, "headers": headers,
                "body": json.dumps({"submissions": submissions, "permissions": permissions})}

    return _err(500, "DynamoDB table not configured", headers)


def _handle_upload_complete(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Called by portal after all files are uploaded. Computes status and updates DynamoDB."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    submission_id = (body.get("submissionId") or "").strip()
    filenames = body.get("filenames") or []

    if not submission_id:
        return _err(400, "submissionId is required", headers)
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)

    if not table:
        return _err(500, "DynamoDB not configured", headers)

    # Get the item to read refundType + departments
    item = _get_submission(submission_id)
    if not item:
        return _err(404, "Submission not found", headers)

    refund_type_csv = item.get("refundType", "")
    departments = item.get("departments") or _derive_departments(
        [t.strip() for t in refund_type_csv.split(",") if t.strip()])

    # Compute only partial→uploaded transitions. Don't stomp on a dept that's
    # already moved past `uploaded` (e.g. an admin already approved).
    new_statuses = _compute_statuses(departments, refund_type_csv, filenames)
    existing = item.get("statuses") or {}
    merged: dict[str, str] = {}
    for dept in departments:
        prev = existing.get(dept, "partial")
        # Only let the doc-driven classifier set partial/uploaded. If the dept
        # is already under-review/approved/denied, preserve it.
        if prev in {"partial", "uploaded"}:
            merged[dept] = new_statuses.get(dept, "partial")
        else:
            merged[dept] = prev

    table.update_item(
        Key=_sub_key(submission_id),
        UpdateExpression="SET statuses = :s, documents = :d, updatedAt = :u",
        ExpressionAttributeValues={
            ":s": merged,
            ":d": filenames,
            ":u": _now_iso(),
        },
    )

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"submissionId": submission_id, "statuses": merged}),
    }


_VALID_STATUSES = {"partial", "uploaded", "under-review", "approved", "denied"}


def _handle_update_status(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Admin endpoint to manually change a submission's status."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    submission_id = (body.get("submissionId") or "").strip()
    new_status = (body.get("status") or "").strip()
    department = (body.get("department") or "").strip()

    if not submission_id or not new_status or not department:
        return _err(400, "submissionId, status, and department are required", headers)
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)
    if new_status not in _VALID_STATUSES:
        return _err(400, f"Invalid status. Must be one of: {', '.join(sorted(_VALID_STATUSES))}", headers)
    if not table:
        return _err(500, "DynamoDB not configured", headers)

    item = _get_submission(submission_id)
    if not item:
        return _err(404, "Submission not found", headers)

    dept_keys, is_super = _auth(event)
    submission_depts = set(item.get("departments") or _derive_departments(
        [t.strip() for t in item.get("refundType", "").split(",") if t.strip()]))
    if department not in submission_depts:
        return _err(400, f"Submission is not tagged for department '{department}'", headers)
    if not is_super and department not in dept_keys:
        return _err(403, "Not authorized for this department", headers)

    statuses = dict(item.get("statuses") or {})
    prev_status = statuses.get(department, "")
    statuses[department] = new_status

    table.update_item(
        Key=_sub_key(submission_id),
        UpdateExpression="SET statuses = :s, updatedAt = :u",
        ExpressionAttributeValues={":s": statuses, ":u": _now_iso()},
    )
    _audit(submission_id, _actor(event), "status_change",
           {"department": department, "from": prev_status, "to": new_status})

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"submissionId": submission_id, "department": department, "status": new_status}),
    }


def _handle_delete_submission(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Delete a submission from DynamoDB and S3. Super-admin only."""
    _, is_super = _auth(event)
    if not is_super:
        return _err(403, "Only super-admin can delete submissions", headers)

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    submission_id = (body.get("submissionId") or "").strip()
    if not submission_id:
        return _err(400, "submissionId is required", headers)
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)

    # Delete all S3 objects under this submission prefix
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"{submission_id}/"):
        for obj in page.get("Contents", []):
            keys.append({"Key": obj["Key"]})
    if keys:
        # delete_objects supports max 1000 keys per call
        for i in range(0, len(keys), 1000):
            s3.delete_objects(Bucket=BUCKET, Delete={"Objects": keys[i:i + 1000]})

    # Delete from DynamoDB (META row + all audit rows under this submission)
    if table:
        _delete_submission_data(submission_id)
    # Note: delete wipes the whole partition, so there's no useful place to
    # write a final "deleted by X" audit row that would survive. If forensics
    # are needed, consult CloudWatch Lambda logs instead.

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"submissionId": submission_id, "deleted": True}),
    }


def _handle_audit(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Return audit entries for a submission, scoped by caller permissions."""
    submission_id = ((event.get("pathParameters") or {}).get("submissionId") or "").strip()
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)
    if not table:
        return _err(500, "Audit backend not configured", headers)

    item = _get_submission(submission_id)
    if not item:
        return _err(404, "Submission not found", headers)

    dept_keys, is_super = _auth(event)
    submission_depts = set(item.get("departments") or _derive_departments(
        [t.strip() for t in item.get("refundType", "").split(",") if t.strip()]))
    if not is_super and not (dept_keys & submission_depts):
        return _err(403, "Not authorized for this submission", headers)

    entries = _list_audit(submission_id)
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps({"submissionId": submission_id, "entries": entries})}


def _handle_upload(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    name = (body.get("name") or "").strip()
    refund_type = (body.get("refundType") or "").strip()
    address = (body.get("address") or "").strip()
    files = body.get("files") or []
    # Confidence is the name-match strength from tax_lookup. Defaults to "high"
    # (claimant arrived via portal directly without going through chat) so the
    # admin queue isn't flooded with low-confidence flags from non-bot traffic.
    confidence = (body.get("confidence") or "high").strip().lower()
    if confidence not in ("high", "low"):
        confidence = "high"
    # Accept a pre-reserved submissionId from the claimant portal so the ID
    # shown to the user before submit matches the one stored after submit.
    submission_id = (body.get("submissionId") or "").strip()
    if not submission_id:
        submission_id = uuid.uuid4().hex[:12]

    if not name or not refund_type:
        return _err(400, "name and refundType are required", headers)
    if not files or len(files) > 15:
        return _err(400, "Provide 1-15 files", headers)

    urls = []
    now = _now_iso()

    for f in files:
        content_type = f.get("contentType", "")
        filename = _sanitize_filename(f.get("filename") or "file")
        if content_type not in ALLOWED_TYPES:
            return _err(400, f"Unsupported file type: {content_type}", headers)

        key = f"{submission_id}/{filename}"
        presigned = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": BUCKET, "Key": key, "ContentType": content_type},
            ExpiresIn=900,
        )
        urls.append({"filename": filename, "uploadUrl": presigned, "key": key})

    s3.put_object(
        Bucket=BUCKET,
        Key=f"{submission_id}/_manifest.json",
        Body=json.dumps({"name": name, "refundType": refund_type}),
        ContentType="application/json",
    )

    # Write initial record to DynamoDB
    if table:
        refund_types = [t.strip() for t in refund_type.split(",") if t.strip()]
        departments = _derive_departments(refund_types)
        item = {
            "submissionId": submission_id,
            "name": name,
            "refundType": refund_type,
            "departments": departments,
            "statuses": {d: "partial" for d in departments},
            "documents": [],
            "confidence": confidence,
            "submittedAt": now,
            "updatedAt": now,
        }
        if address:
            item["address"] = address
        _put_submission(item)

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"submissionId": submission_id, "uploads": urls}),
    }


# ── Super-admin config CRUD ────────────────────────────────

_VALID_KEY = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')


def _username_from_email(email: str) -> str:
    """Local-part of the email as username. Reject unsafe chars."""
    local = email.split("@", 1)[0].lower()
    local = re.sub(r'[^a-z0-9._-]', '', local)
    return local or ""


def _handle_admin(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    _, is_super = _auth(event)
    if not is_super:
        return _err(403, "Super-admin only", headers)
    if not admin_table or not USER_POOL_ID:
        return _err(500, "Admin backend not configured", headers)

    path = (event.get("pathParameters") or {}).get("proxy", "")
    method = event.get("httpMethod", "GET")
    parts = path.split("/") if path else []

    # /admin/config (GET)
    if path == "config" and method == "GET":
        return _admin_get_config(headers)
    # /admin/departments
    if parts[:1] == ["departments"]:
        if len(parts) == 1 and method == "POST":
            return _admin_create_department(event, headers)
        if len(parts) == 2 and method == "PATCH":
            return _admin_update_department(event, parts[1], headers)
        if len(parts) == 2 and method == "DELETE":
            return _admin_delete_department(parts[1], headers)
    # /admin/users
    if parts[:1] == ["users"]:
        if len(parts) == 1 and method == "POST":
            return _admin_create_user(event, headers)
        if len(parts) == 2 and method == "PATCH":
            return _admin_update_user(event, parts[1], headers)
        if len(parts) == 2 and method == "DELETE":
            return _admin_delete_user(parts[1], headers)
    # /admin/refund-types/<TYPE> (PUT to set label)
    if parts[:1] == ["refund-types"] and len(parts) == 2 and method == "PUT":
        return _admin_set_refund_type_label(event, parts[1], headers)
    # /admin/doc-requirements
    if parts[:1] == ["doc-requirements"]:
        if len(parts) == 1 and method == "GET":
            return _admin_list_doc_requirements(headers)
        if len(parts) == 2 and method == "PUT":
            return _admin_put_doc_requirements(event, parts[1], headers)
    # /admin/form-schemas
    if parts[:1] == ["form-schemas"]:
        if len(parts) == 1 and method == "GET":
            return _admin_list_form_schemas(headers)
        if len(parts) == 2 and method == "GET":
            return _admin_get_form_schema(parts[1], headers)
        if len(parts) == 2 and method == "PUT":
            return _admin_put_form_schema(event, parts[1], headers)
    # /admin/chat-sessions
    if parts[:1] == ["chat-sessions"]:
        if len(parts) == 1 and method == "GET":
            return _admin_list_chat_sessions(event, headers)
        if len(parts) == 2 and method == "GET":
            return _admin_get_chat_session(parts[1], headers)
        if len(parts) == 3 and parts[2] == "resolve" and method == "POST":
            return _admin_resolve_chat_handoff(parts[1], headers)
        if len(parts) == 2 and method == "DELETE":
            return _admin_delete_chat_session(parts[1], headers)

    return _err(404, f"Unknown admin route: {method} {path}", headers)


def _admin_get_config(headers: dict[str, str]) -> dict[str, Any]:
    items = _scan_all(admin_table)
    departments = []
    users = []
    refund_type_labels = {}
    for item in items:
        pk = item.get("pk", "")
        if pk.startswith("DEPT#"):
            departments.append({
                "key": item["key"], "label": item.get("label", item["key"]),
                "refund_types": item.get("refund_types") or [],
            })
        elif pk.startswith("USER#"):
            users.append({
                "username": item["username"], "email": item.get("email", ""),
                "groups": item.get("groups") or [],
                "notifyEmail": item.get("notifyEmail", True),
                "createdAt": item.get("createdAt", ""),
            })
        elif pk.startswith("TYPELABEL#"):
            refund_type_labels[item["refund_type"]] = item.get("label", "")

    # Merge in Cognito users that aren't in admin-config yet (e.g. bootstrap super-admin)
    known = {u["username"] for u in users}
    cognito_users: list[dict[str, Any]] = []
    pagination_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"UserPoolId": USER_POOL_ID, "Limit": 60}
        if pagination_token:
            kwargs["PaginationToken"] = pagination_token
        resp = cognito.list_users(**kwargs)
        cognito_users.extend(resp.get("Users", []))
        pagination_token = resp.get("PaginationToken")
        if not pagination_token:
            break
    for cu in cognito_users:
        uname = cu.get("Username")
        if not uname or uname in known:
            continue
        attrs = {a["Name"]: a["Value"] for a in cu.get("Attributes", [])}
        groups_resp = cognito.admin_list_groups_for_user(UserPoolId=USER_POOL_ID, Username=uname)
        users.append({
            "username": uname, "email": attrs.get("email", ""),
            "groups": [g["GroupName"] for g in groups_resp.get("Groups", [])],
            "notifyEmail": True,
            "createdAt": cu.get("UserCreateDate").isoformat() if cu.get("UserCreateDate") else "",
        })

    return {"statusCode": 200, "headers": headers, "body": json.dumps({
        "departments": sorted(departments, key=lambda d: d["key"]),
        "users": sorted(users, key=lambda u: u["username"]),
        "refundTypeLabels": refund_type_labels,
    })}


def _admin_create_department(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)
    key = (body.get("key") or "").strip().lower()
    label = (body.get("label") or "").strip()
    refund_types = body.get("refund_types") or []
    if not _VALID_KEY.match(key):
        return _err(400, "key must be lowercase alphanumeric/dashes, 1-32 chars", headers)
    if not label:
        return _err(400, "label is required", headers)
    if admin_table.get_item(Key={"pk": f"DEPT#{key}"}).get("Item"):
        return _err(409, "Department already exists", headers)
    group_name = f"admin-{key}"
    try:
        cognito.create_group(UserPoolId=USER_POOL_ID, GroupName=group_name, Description=label)
    except cognito.exceptions.GroupExistsException:
        pass
    admin_table.put_item(Item={
        "pk": f"DEPT#{key}", "key": key, "label": label,
        "refund_types": refund_types, "createdAt": _now_iso(),
    })
    _bust_department_cache()
    return {"statusCode": 201, "headers": headers,
            "body": json.dumps({"key": key, "label": label, "refund_types": refund_types})}


def _admin_update_department(event: dict[str, Any], key: str, headers: dict[str, str]) -> dict[str, Any]:
    if not _VALID_KEY.match(key):
        return _err(400, "invalid key", headers)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)
    resp = admin_table.get_item(Key={"pk": f"DEPT#{key}"})
    if not resp.get("Item"):
        return _err(404, "Department not found", headers)
    updates = {}
    if "label" in body:
        updates["label"] = str(body["label"]).strip() or resp["Item"]["label"]
    if "refund_types" in body:
        updates["refund_types"] = list(body["refund_types"] or [])
    if not updates:
        return _err(400, "No updates", headers)
    expr = ", ".join(f"#{k} = :{k}" for k in updates)
    admin_table.update_item(
        Key={"pk": f"DEPT#{key}"},
        UpdateExpression=f"SET {expr}, updatedAt = :u",
        ExpressionAttributeNames={f"#{k}": k for k in updates},
        ExpressionAttributeValues={**{f":{k}": v for k, v in updates.items()}, ":u": _now_iso()},
    )
    _bust_department_cache()
    return {"statusCode": 200, "headers": headers, "body": json.dumps({"key": key, **updates})}


def _admin_delete_department(key: str, headers: dict[str, str]) -> dict[str, Any]:
    if not _VALID_KEY.match(key):
        return _err(400, "invalid key", headers)
    if not admin_table.get_item(Key={"pk": f"DEPT#{key}"}).get("Item"):
        return _err(404, "Department not found", headers)
    group_name = f"admin-{key}"
    try:
        cognito.delete_group(UserPoolId=USER_POOL_ID, GroupName=group_name)
    except cognito.exceptions.ResourceNotFoundException:
        pass
    admin_table.delete_item(Key={"pk": f"DEPT#{key}"})
    _bust_department_cache()
    return {"statusCode": 200, "headers": headers, "body": json.dumps({"key": key, "deleted": True})}


def _admin_create_user(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)
    email = (body.get("email") or "").strip().lower()
    groups = list(body.get("groups") or [])
    notify_email = bool(body.get("notifyEmail", True))
    if "@" not in email:
        return _err(400, "valid email required", headers)
    username = _username_from_email(email)
    if not username:
        return _err(400, "Cannot derive username from email", headers)
    # Collision check
    if admin_table.get_item(Key={"pk": f"USER#{username}"}).get("Item"):
        return _err(409, f"Username '{username}' already exists (email prefix collision)", headers)
    # Validate every requested group exists *before* creating the user, so we
    # don't leave an orphaned Cognito user if a group name is wrong.
    for group in groups:
        try:
            cognito.get_group(UserPoolId=USER_POOL_ID, GroupName=group)
        except cognito.exceptions.ResourceNotFoundException:
            return _err(400, f"Group not found: {group}", headers)

    try:
        cognito.admin_create_user(
            UserPoolId=USER_POOL_ID,
            Username=username,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except cognito.exceptions.UsernameExistsException:
        return _err(409, "Username already exists in Cognito", headers)
    for group in groups:
        cognito.admin_add_user_to_group(
            UserPoolId=USER_POOL_ID, Username=username, GroupName=group,
        )
    admin_table.put_item(Item={
        "pk": f"USER#{username}", "username": username, "email": email,
        "groups": groups, "notifyEmail": notify_email, "createdAt": _now_iso(),
    })
    return {"statusCode": 201, "headers": headers,
            "body": json.dumps({"username": username, "email": email, "groups": groups,
                                "notifyEmail": notify_email})}


def _get_or_materialize_user(username: str) -> dict[str, Any] | None:
    """Return the user's admin-config record, creating it from Cognito if missing."""
    existing = admin_table.get_item(Key={"pk": f"USER#{username}"}).get("Item")
    if existing:
        return existing
    # Fallback: materialize from Cognito (handles bootstrap super-admin)
    try:
        cu = cognito.admin_get_user(UserPoolId=USER_POOL_ID, Username=username)
    except cognito.exceptions.UserNotFoundException:
        return None
    attrs = {a["Name"]: a["Value"] for a in cu.get("UserAttributes", [])}
    groups_resp = cognito.admin_list_groups_for_user(UserPoolId=USER_POOL_ID, Username=username)
    item = {
        "pk": f"USER#{username}", "username": username, "email": attrs.get("email", ""),
        "groups": [g["GroupName"] for g in groups_resp.get("Groups", [])],
        "createdAt": cu.get("UserCreateDate").isoformat() if cu.get("UserCreateDate") else _now_iso(),
    }
    admin_table.put_item(Item=item)
    return item


def _admin_update_user(event: dict[str, Any], username: str, headers: dict[str, str]) -> dict[str, Any]:
    if not re.fullmatch(r'[a-z0-9._-]+', username):
        return _err(400, "invalid username", headers)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)
    existing = _get_or_materialize_user(username)
    if not existing:
        return _err(404, "User not found", headers)

    # Email update
    new_email = (body.get("email") or "").strip().lower()
    if new_email and new_email != existing.get("email"):
        try:
            cognito.admin_update_user_attributes(
                UserPoolId=USER_POOL_ID, Username=username,
                UserAttributes=[
                    {"Name": "email", "Value": new_email},
                    {"Name": "email_verified", "Value": "true"},
                ],
            )
        except cognito.exceptions.UserNotFoundException:
            return _err(404, "Cognito user no longer exists", headers)

    # Groups update (diff add/remove)
    if "groups" in body:
        desired = set(body["groups"] or [])
        current = set(existing.get("groups") or [])
        # Safety: don't remove last super-admin
        if "super-admin" in current and "super-admin" not in desired:
            remaining = cognito.list_users_in_group(UserPoolId=USER_POOL_ID, GroupName="super-admin")
            if len([u for u in remaining.get("Users", []) if u.get("Username") != username]) == 0:
                return _err(400, "Cannot remove the last super-admin", headers)
        for g in desired - current:
            cognito.admin_add_user_to_group(UserPoolId=USER_POOL_ID, Username=username, GroupName=g)
        for g in current - desired:
            cognito.admin_remove_user_from_group(UserPoolId=USER_POOL_ID, Username=username, GroupName=g)
    else:
        desired = set(existing.get("groups") or [])

    updates: dict[str, Any] = {"groups": sorted(desired)}
    if new_email:
        updates["email"] = new_email
    if "notifyEmail" in body:
        updates["notifyEmail"] = bool(body["notifyEmail"])
    expr = ", ".join(f"#{k} = :{k}" for k in updates)
    admin_table.update_item(
        Key={"pk": f"USER#{username}"},
        UpdateExpression=f"SET {expr}, updatedAt = :u",
        ExpressionAttributeNames={f"#{k}": k for k in updates},
        ExpressionAttributeValues={**{f":{k}": v for k, v in updates.items()}, ":u": _now_iso()},
    )
    return {"statusCode": 200, "headers": headers, "body": json.dumps({"username": username, **updates})}


def _admin_delete_user(username: str, headers: dict[str, str]) -> dict[str, Any]:
    if not re.fullmatch(r'[a-z0-9._-]+', username):
        return _err(400, "invalid username", headers)
    existing = _get_or_materialize_user(username)
    if not existing:
        return _err(404, "User not found", headers)
    # Safety: don't delete last super-admin
    if "super-admin" in (existing.get("groups") or []):
        remaining = cognito.list_users_in_group(UserPoolId=USER_POOL_ID, GroupName="super-admin")
        if len([u for u in remaining.get("Users", []) if u.get("Username") != username]) == 0:
            return _err(400, "Cannot delete the last super-admin", headers)
    try:
        cognito.admin_delete_user(UserPoolId=USER_POOL_ID, Username=username)
    except cognito.exceptions.UserNotFoundException:
        pass
    admin_table.delete_item(Key={"pk": f"USER#{username}"})
    return {"statusCode": 200, "headers": headers, "body": json.dumps({"username": username, "deleted": True})}


def _admin_set_refund_type_label(event: dict[str, Any], refund_type: str, headers: dict[str, str]) -> dict[str, Any]:
    refund_type = refund_type.upper()
    if refund_type not in {"STALE_WARRANT", "PAYROLL", "PROPERTY_TAX"}:
        return _err(400, "Unknown refund type", headers)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)
    label = (body.get("label") or "").strip()
    if not label:
        return _err(400, "label is required", headers)
    admin_table.put_item(Item={
        "pk": f"TYPELABEL#{refund_type}", "refund_type": refund_type,
        "label": label, "updatedAt": _now_iso(),
    })
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps({"refund_type": refund_type, "label": label})}


def _admin_list_doc_requirements(headers: dict[str, str]) -> dict[str, Any]:
    """Return doc requirements for each known refund type, seeded from defaults."""
    out = {}
    for rt in ("STALE_WARRANT", "PAYROLL", "PROPERTY_TAX"):
        out[rt] = _get_doc_req(rt)
    return {"statusCode": 200, "headers": headers, "body": json.dumps(out)}


def _admin_put_doc_requirements(event: dict[str, Any], refund_type: str, headers: dict[str, str]) -> dict[str, Any]:
    refund_type = refund_type.upper()
    if refund_type not in {"STALE_WARRANT", "PAYROLL", "PROPERTY_TAX"}:
        return _err(400, "Unknown refund type", headers)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    docs = body.get("docs")
    if not isinstance(docs, list):
        return _err(400, "docs must be a list", headers)
    normalized_docs = []
    for d in docs:
        if not isinstance(d, dict) or "id" not in d or not d["id"]:
            return _err(400, "each doc requires an id", headers)
        normalized_docs.append({
            "id": str(d["id"]),
            "label": str(d.get("label") or d["id"]),
            "required": bool(d.get("required", True)),
            "internal": bool(d.get("internal", False)),
        })

    either_of = body.get("either_of") or []
    if not isinstance(either_of, list) or any(not isinstance(g, list) for g in either_of):
        return _err(400, "either_of must be a list of lists", headers)

    admin_table.put_item(Item={
        "pk": f"DOCREQ#{refund_type}",
        "refund_type": refund_type,
        "docs": normalized_docs,
        "either_of": either_of,
        "updatedAt": _now_iso(),
    })
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps({"refund_type": refund_type, "docs": normalized_docs, "either_of": either_of})}

# ── Form schemas (unified claim form) ─────────────────────

# Default field schemas per refund type. Used as the seed when the
# admin-config table has no FORMSCHEMA#<type> entry yet. Super-admin can
# override via the dashboard.
#
# Field shape:
#   id         — stable identifier; shared across refund types by matching id
#   label      — display label
#   type       — text | email | tel | date | number | address | textarea | checkbox
#   required   — whether the unified form must collect it
#   section    — "common" (always shown) or a refund-type-specific section key
#
# The unified form dedupes by `id`: if two refund types both define
# {"id": "name", ...}, the claimant only fills it once.
_DEFAULT_FORM_SCHEMAS: dict[str, dict[str, Any]] = {
    "STALE_WARRANT": {
        "title": "Affidavit for the Replacement of Stale-Dated Warrant (AP-13)",
        "fields": [
            {"id": "name", "label": "Claimant Name", "type": "text", "required": True, "section": "common"},
            {"id": "address", "label": "Mailing Address", "type": "address", "required": True, "section": "common"},
            {"id": "email", "label": "Email Address", "type": "email", "required": True, "section": "common"},
            {"id": "phone", "label": "Phone Number", "type": "tel", "required": True, "section": "common"},
            {"id": "warrant_number", "label": "Warrant Number", "type": "text", "required": True, "section": "STALE_WARRANT"},
            {"id": "warrant_amount", "label": "Warrant Amount", "type": "number", "required": True, "section": "STALE_WARRANT"},
            {"id": "warrant_date", "label": "Warrant Date", "type": "date", "required": False, "section": "STALE_WARRANT"},
            {"id": "business_name", "label": "Business Claimant Name & Title", "type": "text", "required": False, "section": "STALE_WARRANT"},
            {"id": "business_unit", "label": "Business Unit", "type": "text", "required": False, "section": "STALE_WARRANT"},
            {"id": "is_incorporated", "label": "Is the claimant an incorporated entity?", "type": "checkbox", "required": False, "section": "STALE_WARRANT"},
            {"id": "is_owner", "label": "Is the claimant the original payee?", "type": "checkbox", "required": False, "section": "STALE_WARRANT"},
        ],
    },
    "PAYROLL": {
        "title": "Affidavit for the Replacement of Stale-Dated Payroll Warrant (AP-13)",
        "fields": [
            {"id": "name", "label": "Claimant Name", "type": "text", "required": True, "section": "common"},
            {"id": "address", "label": "Mailing Address", "type": "address", "required": True, "section": "common"},
            {"id": "email", "label": "Email Address", "type": "email", "required": True, "section": "common"},
            {"id": "phone", "label": "Phone Number", "type": "tel", "required": True, "section": "common"},
            {"id": "warrant_number", "label": "Warrant Number", "type": "text", "required": True, "section": "PAYROLL"},
            {"id": "warrant_amount", "label": "Warrant Amount", "type": "number", "required": True, "section": "PAYROLL"},
            {"id": "warrant_date", "label": "Warrant Date", "type": "date", "required": False, "section": "PAYROLL"},
            {"id": "business_unit", "label": "Business Unit", "type": "text", "required": False, "section": "PAYROLL"},
        ],
    },
    "PROPERTY_TAX": {
        "title": "Property Tax Unclaimed Refund Claim",
        "fields": [
            {"id": "name", "label": "Claimant Name", "type": "text", "required": True, "section": "common"},
            {"id": "address", "label": "Mailing Address", "type": "address", "required": True, "section": "common"},
            {"id": "email", "label": "Email Address", "type": "email", "required": True, "section": "common"},
            {"id": "phone", "label": "Phone Number", "type": "tel", "required": True, "section": "common"},
            {"id": "company", "label": "Company (if applicable)", "type": "text", "required": False, "section": "PROPERTY_TAX"},
            {"id": "title", "label": "Title (if representing a business)", "type": "text", "required": False, "section": "PROPERTY_TAX"},
            {"id": "assessment_number", "label": "Assessment Number", "type": "text", "required": True, "section": "PROPERTY_TAX"},
            {"id": "tax_year", "label": "Tax Year", "type": "text", "required": True, "section": "PROPERTY_TAX"},
            {"id": "refund_amount", "label": "Refund Amount", "type": "number", "required": True, "section": "PROPERTY_TAX"},
            {"id": "how_heard_contact", "label": "Heard via direct contact", "type": "checkbox", "required": False, "section": "PROPERTY_TAX"},
            {"id": "how_heard_website", "label": "Heard via county website", "type": "checkbox", "required": False, "section": "PROPERTY_TAX"},
            {"id": "how_heard_newspaper", "label": "Heard via newspaper", "type": "checkbox", "required": False, "section": "PROPERTY_TAX"},
            {"id": "how_heard_other", "label": "Heard via other source", "type": "checkbox", "required": False, "section": "PROPERTY_TAX"},
            {"id": "how_heard_other_text", "label": "Other source (describe)", "type": "text", "required": False, "section": "PROPERTY_TAX"},
        ],
    },
}


def _get_form_schema(refund_type: str) -> dict[str, Any]:
    """Return the form schema for a refund type, seeded from defaults if absent."""
    default = _DEFAULT_FORM_SCHEMAS.get(refund_type, {"title": refund_type, "fields": []})
    if not admin_table:
        return default
    try:
        resp = admin_table.get_item(Key={"pk": f"FORMSCHEMA#{refund_type}"})
    except Exception:  # noqa: BLE001
        return default
    item = resp.get("Item")
    if not item:
        return default
    return {
        "title": item.get("title") or default.get("title") or refund_type,
        "fields": item.get("fields") or default.get("fields") or [],
    }


def _admin_list_form_schemas(headers: dict[str, str]) -> dict[str, Any]:
    """Return schemas for every known refund type."""
    out = {rt: _get_form_schema(rt) for rt in _DEFAULT_FORM_SCHEMAS.keys()}
    return {"statusCode": 200, "headers": headers, "body": json.dumps(out)}


def _admin_get_form_schema(refund_type: str, headers: dict[str, str]) -> dict[str, Any]:
    refund_type = refund_type.upper()
    if refund_type not in _DEFAULT_FORM_SCHEMAS:
        return _err(400, "Unknown refund type", headers)
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps(_get_form_schema(refund_type))}


def _admin_put_form_schema(event: dict[str, Any], refund_type: str, headers: dict[str, str]) -> dict[str, Any]:
    refund_type = refund_type.upper()
    if refund_type not in _DEFAULT_FORM_SCHEMAS:
        return _err(400, "Unknown refund type", headers)
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    fields = body.get("fields")
    if not isinstance(fields, list):
        return _err(400, "fields must be a list", headers)
    valid_field_types = {"text", "email", "tel", "date", "number", "address", "textarea", "checkbox"}
    # Field IDs are interpolated into HTML id/name/data-field-id attributes by
    # the public portal, so they must be safe identifier strings.
    valid_field_id = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$')
    normalized = []
    seen_ids: set[str] = set()
    for f in fields:
        if not isinstance(f, dict) or not f.get("id"):
            return _err(400, "each field requires an id", headers)
        fid = str(f["id"]).strip()
        if not valid_field_id.match(fid):
            return _err(400, f"invalid field id '{fid}': must start with a letter or underscore and contain only letters, digits, underscores, or hyphens", headers)
        if fid in seen_ids:
            return _err(400, f"duplicate field id: {fid}", headers)
        seen_ids.add(fid)
        field_type = str(f.get("type") or "text")
        if field_type not in valid_field_types:
            return _err(400, f"invalid field type '{field_type}' for field '{fid}'. Must be one of: {', '.join(sorted(valid_field_types))}", headers)
        normalized.append({
            "id": fid,
            "label": str(f.get("label") or fid),
            "type": field_type,
            "required": bool(f.get("required", False)),
            "section": str(f.get("section") or "common"),
        })

    raw_title = body.get("title")
    if raw_title is not None and not isinstance(raw_title, str):
        return _err(400, "title must be a string", headers)
    title = (raw_title or _DEFAULT_FORM_SCHEMAS[refund_type]["title"]).strip()

    admin_table.put_item(Item={
        "pk": f"FORMSCHEMA#{refund_type}",
        "refund_type": refund_type,
        "title": title,
        "fields": normalized,
        "updatedAt": _now_iso(),
    })
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps({"refund_type": refund_type, "title": title, "fields": normalized})}


def _handle_public_form_schemas(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Return form schemas for the requested refund types.

    Public: claimants access this before authentication. Accepts
    ?types=PAYROLL,STALE_WARRANT and returns each schema plus a merged view
    for the unified form.
    """
    qs = event.get("queryStringParameters") or {}
    raw = (qs.get("types") or "").strip()
    if not raw:
        return _err(400, "types query parameter required", headers)
    requested = [t.strip().upper() for t in raw.split(",") if t.strip()]
    invalid = [t for t in requested if t not in _DEFAULT_FORM_SCHEMAS]
    if invalid:
        return _err(400, f"Unknown refund types: {', '.join(invalid)}", headers)

    schemas: dict[str, dict[str, Any]] = {}
    merged_by_id: dict[str, dict[str, Any]] = {}
    # NOTE: Today the chat handoff carries one warrant per claim, so merging
    # by `id` is fine — a combined claim that genuinely needs *both* a stale
    # warrant and a payroll warrant would collapse the two warrant_number /
    # warrant_amount pairs into one. Revisit (namespace per refund type) if
    # multi-warrant submissions become real.
    for rt in requested:
        schema = _get_form_schema(rt)
        schemas[rt] = schema
        for f in schema.get("fields", []):
            fid = f["id"]
            if fid in merged_by_id:
                # If another refund type needed the same field, carry forward
                # `required=True` if any schema requires it.
                if f.get("required"):
                    merged_by_id[fid]["required"] = True
                continue
            merged_by_id[fid] = {**f}

    # Preserve section ordering: common first, then per-refund-type sections
    # in the order they were requested.
    section_order = ["common"] + requested
    merged_fields = sorted(
        merged_by_id.values(),
        key=lambda f: section_order.index(f.get("section", "common")) if f.get("section") in section_order else 999,
    )

    return {"statusCode": 200, "headers": headers, "body": json.dumps({
        "refund_types": requested,
        "schemas": schemas,
        "merged_fields": merged_fields,
    })}


def _evaluate_doc_condition(condition: dict[str, Any], params: dict[str, str]) -> bool:
    """Evaluate a single condition against query params."""
    field = condition.get("field", "")
    op = condition.get("operator", "eq")
    expected = condition.get("value")
    actual_raw = params.get(field, "")

    if not actual_raw:
        return False

    try:
        actual_num = float(actual_raw)
    except (ValueError, TypeError):
        actual_num = None

    try:
        expected_num = float(expected) if expected is not None else None
    except (ValueError, TypeError):
        expected_num = None

    if op == "gt" and actual_num is not None and expected_num is not None:
        return actual_num > expected_num
    if op == "gte" and actual_num is not None and expected_num is not None:
        return actual_num >= expected_num
    if op == "lt" and actual_num is not None and expected_num is not None:
        return actual_num < expected_num
    if op == "lte" and actual_num is not None and expected_num is not None:
        return actual_num <= expected_num
    if op == "eq":
        if actual_num is not None and expected_num is not None:
            return actual_num == expected_num
        return actual_raw.lower() == str(expected).lower()
    if op == "in":
        vals = [str(v).lower() for v in expected] if isinstance(expected, list) else []
        return actual_raw.lower() in vals
    if op == "not_in":
        vals = [str(v).lower() for v in expected] if isinstance(expected, list) else []
        return actual_raw.lower() not in vals
    return False


def _handle_public_doc_requirements(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Return document requirements for the requested refund types.

    Public endpoint. Accepts:
      ?types=STALE_WARRANT,PAYROLL
      &amount=1500          (optional, for condition evaluation)
      &entity_type=individual  (optional)
      &claimant_status=deceased (optional)

    Evaluates conditions on each doc to determine if it applies.
    """
    qs = event.get("queryStringParameters") or {}
    raw = (qs.get("types") or "").strip()
    if not raw:
        return _err(400, "types query parameter required", headers)
    requested = [t.strip().upper() for t in raw.split(",") if t.strip()]

    docs_by_id: dict[str, dict[str, Any]] = {}
    either_of_all: list[list[str]] = []

    for rt in requested:
        req = _get_doc_req(rt)
        for d in req.get("docs", []):
            if d.get("internal"):
                continue
            conditions = d.get("conditions")
            if conditions:
                if not all(_evaluate_doc_condition(c, qs) for c in conditions):
                    continue
            fid = d["id"]
            if fid not in docs_by_id:
                docs_by_id[fid] = {
                    "id": fid,
                    "label": d.get("label", fid),
                    "required": d.get("required", False),
                }
            elif d.get("required"):
                docs_by_id[fid]["required"] = True
        for group in req.get("either_of", []):
            if group not in either_of_all:
                either_of_all.append(group)

    return {"statusCode": 200, "headers": headers, "body": json.dumps({
        "refund_types": requested,
        "docs": list(docs_by_id.values()),
        "either_of": either_of_all,
    })}


# ── Chat sessions (super-admin: read transcripts and resolve handoffs) ──

_SAFE_SESSION_ID = re.compile(r'^[a-z0-9]{8,32}$')


def _admin_list_chat_sessions(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """List pending agent handoffs by querying the handoffIx GSI.

    Pass ?status=all to also include resolved handoffs (for audit). Default
    is pending-only — that's what the admin queue page wants.
    """
    if not chat_table:
        return _err(500, "Chat backend not configured", headers)
    qs = event.get("queryStringParameters") or {}
    status = (qs.get("status") or "pending").lower()
    out: list[dict[str, Any]] = []
    if status == "pending":
        resp = chat_table.query(
            IndexName="handoffIx",
            KeyConditionExpression="gsi1pk = :p",
            ExpressionAttributeValues={":p": "HANDOFF_PENDING"},
            ScanIndexForward=False,  # newest first
        )
        items = resp.get("Items", [])
    else:
        # Scan SK=HANDOFF rows (small volume; fine without a second GSI)
        items = _scan_all(
            chat_table,
            FilterExpression="sk = :h",
            ExpressionAttributeValues={":h": "HANDOFF"},
        )
    for it in items:
        sid = it["pk"].split("#", 1)[1]
        out.append({
            "sessionId": sid,
            "refNumber": it.get("refNumber", ""),
            "reason": it.get("reason", ""),
            "requestedAt": it.get("requestedAt", ""),
            "resolved": it.get("gsi1pk") != "HANDOFF_PENDING",
        })
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps({"sessions": out})}


def _admin_get_chat_session(session_id: str, headers: dict[str, str]) -> dict[str, Any]:
    """Return the full session: meta, every message, and the handoff row if any."""
    if not chat_table:
        return _err(500, "Chat backend not configured", headers)
    if not _SAFE_SESSION_ID.match(session_id):
        return _err(400, "Invalid session id", headers)
    resp = chat_table.query(
        KeyConditionExpression="pk = :p",
        ExpressionAttributeValues={":p": f"SESSION#{session_id}"},
    )
    meta: dict[str, Any] = {}
    handoff: dict[str, Any] = {}
    messages: list[dict[str, Any]] = []
    for it in resp.get("Items", []):
        sk = it["sk"]
        if sk == "META":
            meta = {
                "sessionId": session_id,
                "startedAt": it.get("startedAt", ""),
                "disconnectedAt": it.get("disconnectedAt", ""),
                "status": it.get("status", ""),
            }
        elif sk == "HANDOFF":
            handoff = {
                "refNumber": it.get("refNumber", ""),
                "reason": it.get("reason", ""),
                "requestedAt": it.get("requestedAt", ""),
                "resolved": it.get("gsi1pk") != "HANDOFF_PENDING",
            }
        elif sk.startswith("MSG#"):
            messages.append({
                "timestamp": sk[4:],
                "role": it.get("role", ""),
                "content": it.get("content", ""),
            })
    if not meta:
        return _err(404, "Session not found", headers)
    messages.sort(key=lambda m: m["timestamp"])
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps({"meta": meta, "handoff": handoff, "messages": messages})}


def _admin_resolve_chat_handoff(session_id: str, headers: dict[str, str]) -> dict[str, Any]:
    """Mark a handoff handled — clear the GSI keys so it drops off the pending list."""
    if not chat_table:
        return _err(500, "Chat backend not configured", headers)
    if not _SAFE_SESSION_ID.match(session_id):
        return _err(400, "Invalid session id", headers)
    try:
        chat_table.update_item(
            Key={"pk": f"SESSION#{session_id}", "sk": "HANDOFF"},
            UpdateExpression="REMOVE gsi1pk, gsi1sk SET resolvedAt = :r",
            ExpressionAttributeValues={":r": _now_iso()},
            ConditionExpression="attribute_exists(pk)",
        )
    except chat_table.meta.client.exceptions.ConditionalCheckFailedException:
        return _err(404, "Handoff not found", headers)
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps({"sessionId": session_id, "resolved": True})}


def _admin_delete_chat_session(session_id: str, headers: dict[str, str]) -> dict[str, Any]:
    """Wipe a chat session — META, MSG rows, HANDOFF row."""
    if not chat_table:
        return _err(500, "Chat backend not configured", headers)
    if not _SAFE_SESSION_ID.match(session_id):
        return _err(400, "Invalid session id", headers)
    resp = chat_table.query(
        KeyConditionExpression="pk = :p",
        ExpressionAttributeValues={":p": f"SESSION#{session_id}"},
    )
    items = resp.get("Items", [])
    if not items:
        return _err(404, "Session not found", headers)
    with chat_table.batch_writer() as batch:
        for it in items:
            batch.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})
    return {"statusCode": 200, "headers": headers,
            "body": json.dumps({"sessionId": session_id, "deleted": True})}


# ── Claimant portal (public, HMAC-token-gated for status/continue) ────────

import logging as _logging
logger = _logging.getLogger(__name__)

# Street suffix words — stripped when comparing street names for quiz matching.
_ROAD_SUFFIXES = frozenset({
    'street', 'st', 'avenue', 'ave', 'drive', 'dr', 'boulevard', 'blvd',
    'lane', 'ln', 'road', 'rd', 'way', 'court', 'ct', 'place', 'pl',
    'circle', 'cir', 'terrace', 'ter', 'trail', 'trl', 'highway', 'hwy',
})

# Fallback decoy streets used if there aren't enough real submissions to pull from.
_FALLBACK_DECOYS = [
    "Magnolia Ave",
    "Van Buren Blvd",
    "Arlington Ave",
    "Mission Inn Ave",
    "Market St",
    "University Ave",
]


def _street_name_words(street: str) -> list[str]:
    return [
        w for w in street.lower().split()
        if w not in _ROAD_SUFFIXES and not w.isdigit() and len(w) >= 2
    ]


def _extract_street(address: str) -> str:
    """Return just the street portion (first comma-delimited chunk)."""
    return address.split(",", 1)[0].strip() if "," in address else address.strip()


def _looks_like_house_num(token: str) -> bool:
    return bool(token) and any(c.isdigit() for c in token) and len(token) <= 8


def _street_name_only(street: str) -> str:
    """Strip any leading house number from a street string, e.g. '2100 E FLORIDA AVE' → 'E FLORIDA AVE'."""
    parts = street.strip().split(None, 1)
    if len(parts) >= 2 and (parts[0].isdigit() or _looks_like_house_num(parts[0])):
        return parts[1].strip()
    return street.strip()


def _generate_decoy_streets(real_address: str, all_addresses: list[str], count: int = 3) -> list[str]:
    """Pick `count` distinct street strings that differ from `real_address`."""
    import random
    real_street = _extract_street(real_address)
    seen = {real_street.lower()}
    decoys: list[str] = []
    candidates = list(all_addresses)
    random.shuffle(candidates)
    for addr in candidates:
        if addr == real_address:
            continue
        s = _extract_street(addr)
        if s.lower() not in seen:
            decoys.append(s)
            seen.add(s.lower())
        if len(decoys) >= count:
            break
    # Pad with fallbacks if needed
    for fb in _FALLBACK_DECOYS:
        if len(decoys) >= count:
            break
        if fb.lower() not in seen:
            decoys.append(fb)
            seen.add(fb.lower())
    return decoys[:count]


def _verify_claimant_token(submission_id: str, token: str) -> bool:
    """Verify HMAC-signed claimant token. Returns True if valid and not expired."""
    import base64
    import hmac
    import hashlib
    import time
    secret = os.environ.get("CLAIMANT_SECRET", "dev-secret-change-me")
    if secret == "dev-secret-change-me":
        logger.warning("CLAIMANT_SECRET not set — using insecure dev default")
    try:
        decoded = base64.urlsafe_b64decode(token + "==").decode()
        parts = decoded.split(":")
        if len(parts) != 3:
            return False
        sid, expiry_str, provided_hmac = parts
        if sid != submission_id:
            return False
        if int(expiry_str) < int(time.time()):
            return False
        expected = hmac.new(
            secret.encode(),
            f"{sid}:{expiry_str}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, provided_hmac)
    except Exception:  # noqa: BLE001
        return False


def _handle_claimant(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Route dispatcher for all /claimant/* paths."""
    resource = event.get("resource", "")
    path_params = event.get("pathParameters") or {}
    method = event.get("httpMethod", "")

    # API Gateway routes the /claimant/{proxy+} catch-all; extract sub-path.
    proxy = path_params.get("proxy", "").strip("/")
    # Also handle explicit resource strings used in unit tests / older deployments.
    if resource == "/claimant/reserve" or proxy == "reserve":
        if method == "POST":
            return _claimant_reserve(event, headers)
    elif resource == "/claimant/quiz" or proxy == "quiz":
        if method == "GET":
            return _claimant_quiz(event, headers)
    elif resource == "/claimant/verify" or proxy == "verify":
        if method == "POST":
            return _claimant_verify(event, headers)
    elif resource == "/claimant/status" or proxy == "status":
        if method == "GET":
            return _claimant_status(event, headers)
    elif resource == "/claimant/continue" or proxy == "continue":
        if method == "POST":
            return _claimant_continue(event, headers)

    return _err(404, f"No claimant handler for {method} {resource or proxy}", headers)


def _claimant_reserve(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """POST /claimant/reserve — create a submission record before form fill.

    Body: {"name": "...", "refundType": "STALE_WARRANT,PAYROLL", "address": "..."}
    Returns: {"submissionId": "abc123def456"}
    """
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    name = (body.get("name") or "").strip()
    refund_type = (body.get("refundType") or "").strip()
    address = (body.get("address") or "").strip()

    if not name:
        return _err(400, "name is required", headers)
    if not refund_type:
        return _err(400, "refundType is required", headers)
    if not address:
        return _err(400, "address is required", headers)

    submission_id = uuid.uuid4().hex[:12]
    now = _now_iso()
    refund_types = [t.strip() for t in refund_type.split(",") if t.strip()]
    departments = _derive_departments(refund_types)

    _put_submission(
        {
            "submissionId": submission_id,
            "name": name,
            "refundType": refund_type,
            "departments": departments,
            "statuses": {d: "draft" for d in departments},
            "documents": [],
            "submittedAt": now,
            "updatedAt": now,
        },
        address=address,
        initial_status="draft",
    )

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"submissionId": submission_id}),
    }


def _claimant_quiz(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """GET /claimant/quiz?id={submissionId}

    Returns shuffled street options for the address quiz.
    Rate-limited to 10 requests per hour per submission.
    """
    import random
    qs = event.get("queryStringParameters") or {}
    submission_id = (qs.get("id") or "").strip()
    if not submission_id:
        return _err(400, "id is required", headers)
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)
    if not table:
        return _err(500, "DynamoDB not configured", headers)

    item = _get_submission(submission_id)
    if not item:
        return _err(404, "Submission not found", headers)

    # ── Rate limit: max 10 quiz fetches per hour ──
    import time
    now_ts = int(time.time())
    window_start = item.get("quizFetchWindowStart") or 0
    fetch_count = int(item.get("quizFetchCount") or 0)
    if now_ts - int(window_start) > 3600:
        # New hour window — reset
        fetch_count = 0
        window_start = now_ts

    if fetch_count >= 10:
        return _err(429, "Too many quiz requests. Please try again later.", headers)

    # Atomically increment fetch counter
    try:
        table.update_item(
            Key=_sub_key(submission_id),
            UpdateExpression="SET quizFetchCount = :c, quizFetchWindowStart = :w",
            ExpressionAttributeValues={
                ":c": fetch_count + 1,
                ":w": window_start,
            },
        )
    except Exception:  # noqa: BLE001
        pass  # best-effort rate limit; don't fail the user

    address = item.get("address", "")
    if not address:
        return _err(400, "No address on file for this submission", headers)

    # Gather other addresses from the submissions table for decoys (up to 50)
    try:
        resp = table.query(
            IndexName="listIx",
            KeyConditionExpression="gsi1pk = :p",
            ExpressionAttributeValues={":p": "SUBMISSION_LIST"},
            Limit=50,
            ScanIndexForward=False,
        )
        other_addresses = [
            it.get("address", "")
            for it in resp.get("Items", [])
            if it.get("submissionId") != submission_id and it.get("address")
        ]
    except Exception:  # noqa: BLE001
        other_addresses = []

    real_street = _street_name_only(_extract_street(address))
    decoys = [_street_name_only(_extract_street(a)) for a in _generate_decoy_streets(address, other_addresses, count=3)]
    # Filter empty strings and deduplicate in case stripping left collisions
    seen: set[str] = {real_street.lower()}
    clean_decoys = []
    for d in decoys:
        if d and d.lower() not in seen:
            clean_decoys.append(d)
            seen.add(d.lower())

    options = [real_street] + clean_decoys
    random.shuffle(options)

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"street_options": options}),
    }


def _claimant_verify(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """POST /claimant/verify

    Body: {"submissionId": "...", "street": "...", "number": "..."}
    Verifies street + house number against stored address.
    Returns {"token": "...", "expiresAt": "..."} on success.
    Returns 403 with attempts_remaining on failure.
    """
    import base64
    import hmac
    import hashlib
    import time

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    submission_id = (body.get("submissionId") or "").strip()
    provided_street = (body.get("street") or "").strip()
    provided_number = (body.get("number") or "").strip()

    if not submission_id:
        return _err(400, "submissionId is required", headers)
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)
    if not provided_street or not provided_number:
        return _err(400, "street and number are required", headers)
    if not table:
        return _err(500, "DynamoDB not configured", headers)

    item = _get_submission(submission_id)
    if not item:
        return _err(404, "Submission not found", headers)

    # Check lockout
    locked_until = item.get("verificationLockedUntil") or 0
    now_ts = int(time.time())
    if int(locked_until) > now_ts:
        return _err(403, json.dumps({
            "error": "verification_locked",
            "attempts_remaining": 0,
        }), headers)

    address = item.get("address", "")
    if not address:
        return _err(400, "No address on file for this submission", headers)

    stored_street = _extract_street(address)

    # Verify street — word-overlap matching (flexible casing / suffix-stripped)
    provided_words = set(_street_name_words(provided_street))
    stored_words = set(_street_name_words(stored_street))
    street_ok = bool(provided_words & stored_words)

    # Verify house number — first numeric token of the address
    stored_parts = address.strip().split()
    stored_number = stored_parts[0] if stored_parts else ""
    number_ok = provided_number.strip() == stored_number.strip()

    if not (street_ok and number_ok):
        # Increment failure counter
        failures = int(item.get("verificationFailures") or 0) + 1
        update_expr = "SET verificationFailures = :f"
        expr_vals: dict[str, Any] = {":f": failures}
        if failures >= 5:
            lock_until = now_ts + 3600
            update_expr += ", verificationLockedUntil = :l"
            expr_vals[":l"] = lock_until
        table.update_item(
            Key=_sub_key(submission_id),
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_vals,
        )
        remaining = max(0, 5 - failures)
        return _err(403, json.dumps({
            "error": "verification_failed",
            "attempts_remaining": remaining,
        }), headers)

    # ── Verification succeeded — issue token ──
    secret = os.environ.get("CLAIMANT_SECRET", "dev-secret-change-me")
    if secret == "dev-secret-change-me":
        logger.warning("CLAIMANT_SECRET not set — using insecure dev default")

    expiry = now_ts + 3600  # 1-hour token
    msg = f"{submission_id}:{expiry}"
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    token_plain = f"{submission_id}:{expiry}:{sig}"
    token = base64.urlsafe_b64encode(token_plain.encode()).rstrip(b"=").decode()

    expiry_iso = datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat()

    # Reset failure counter
    table.update_item(
        Key=_sub_key(submission_id),
        UpdateExpression="SET verificationFailures = :z REMOVE verificationLockedUntil",
        ExpressionAttributeValues={":z": 0},
    )

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"token": token, "expiresAt": expiry_iso}),
    }


_STATUS_ORDER = ["draft", "partial", "uploaded", "under-review", "approved", "denied"]


def _worst_status(statuses: dict[str, str]) -> str:
    """Return the worst-case status across all departments."""
    if not statuses:
        return "partial"
    def rank(s: str) -> int:
        # "worse" = earlier in the pipeline = lower index (not yet processed)
        order = ["draft", "partial", "uploaded", "under-review", "approved", "denied"]
        try:
            return order.index(s)
        except ValueError:
            return 0
    return min(statuses.values(), key=rank)


def _claimant_status(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """GET /claimant/status?id={submissionId}  (requires X-Claimant-Token header)

    Returns filtered submission data safe for the claimant to see.
    """
    qs = event.get("queryStringParameters") or {}
    submission_id = (qs.get("id") or "").strip()
    token = (event.get("headers") or {}).get("X-Claimant-Token") or \
            (event.get("headers") or {}).get("x-claimant-token") or ""

    if not submission_id:
        return _err(400, "id is required", headers)
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)
    if not token:
        return _err(401, "X-Claimant-Token header required", headers)
    if not _verify_claimant_token(submission_id, token):
        return _err(403, "Invalid or expired token", headers)
    if not table:
        return _err(500, "DynamoDB not configured", headers)

    item = _get_submission(submission_id)
    if not item:
        return _err(404, "Submission not found", headers)

    refund_type = item.get("refundType", "")
    refund_types = [t.strip() for t in refund_type.split(",") if t.strip()]
    internal_ids = _internal_doc_ids(refund_types)

    statuses = item.get("statuses") or {}
    overall = _worst_status(statuses)

    # Strip internal docs from the document list
    all_docs = item.get("documents") or []
    visible_docs = [d for d in all_docs if _doc_prefix(d) not in internal_ids]

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({
            "submissionId": submission_id,
            "name": item.get("name", ""),
            "refundType": refund_type,
            "overallStatus": overall,
            "documents": visible_docs,
            "submittedAt": item.get("submittedAt", ""),
            "updatedAt": item.get("updatedAt", ""),
        }),
    }


def _presigned_put(submission_id: str, filename: str, content_type: str) -> str:
    """Generate a presigned PUT URL for a file within a submission's S3 prefix."""
    sanitized = _sanitize_filename(filename)
    key = f"{submission_id}/{sanitized}"
    return s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=900,
    )


def _claimant_continue(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """POST /claimant/continue  (requires X-Claimant-Token header)

    Body: {"submissionId": "...", "files": [{"filename": "...", "contentType": "..."}]}
    Returns: {"uploads": [{"filename": "...", "uploadUrl": "..."}]}
    """
    token = (event.get("headers") or {}).get("X-Claimant-Token") or \
            (event.get("headers") or {}).get("x-claimant-token") or ""

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    submission_id = (body.get("submissionId") or "").strip()
    files = body.get("files") or []

    if not submission_id:
        return _err(400, "submissionId is required", headers)
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)
    if not token:
        return _err(401, "X-Claimant-Token header required", headers)
    if not _verify_claimant_token(submission_id, token):
        return _err(403, "Invalid or expired token", headers)
    if not files or len(files) > 10:
        return _err(400, "Provide 1–10 files", headers)

    uploads = []
    for f in files:
        content_type = f.get("contentType", "")
        filename = _sanitize_filename(f.get("filename") or "file")
        if content_type not in ALLOWED_TYPES:
            return _err(400, f"Unsupported file type: {content_type}", headers)
        upload_url = _presigned_put(submission_id, filename, content_type)
        uploads.append({"filename": filename, "uploadUrl": upload_url})

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"uploads": uploads}),
    }
