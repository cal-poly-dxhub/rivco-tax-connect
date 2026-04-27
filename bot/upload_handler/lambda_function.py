import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")
BUCKET = os.environ["UPLOAD_BUCKET"]
TABLE_NAME = os.environ.get("TABLE_NAME", "")
ADMIN_CONFIG_TABLE = os.environ.get("ADMIN_CONFIG_TABLE", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
table = dynamodb.Table(TABLE_NAME) if TABLE_NAME else None
admin_table = dynamodb.Table(ADMIN_CONFIG_TABLE) if ADMIN_CONFIG_TABLE else None
ALLOWED_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
    "application/json",
}
MAX_SIZE = 10 * 1024 * 1024  # 10 MB
_SAFE_FILENAME = re.compile(r'[^\w.\-]')
PACKAGE_EXPIRY = 7 * 24 * 3600  # 7 days

# Expected document types per refund category
_EXPECTED_DOCS = {
    "STALE_WARRANT": {"photo-id", "proof-of-address", "ap13-affidavit"},
    "PAYROLL": {"photo-id", "proof-of-address", "ap13-affidavit"},
    "PROPERTY_TAX": {"photo-id", "property-tax-claim"},
}
# Property tax also requires proof-of-payment OR proof-of-ownership (either satisfies)
_PT_EITHER = {"proof-of-payment", "proof-of-ownership"}


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
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "POST,GET,PATCH,PUT,DELETE,OPTIONS",
        "Vary": "Origin",
    }


def _err(code: int, msg: str, headers: dict[str, str]) -> dict[str, Any]:
    return {"statusCode": code, "headers": headers, "body": json.dumps({"error": msg})}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_departments() -> list[dict[str, Any]]:
    """Return all department records from the admin-config table."""
    if not admin_table:
        return []
    resp = admin_table.scan(
        FilterExpression="begins_with(pk, :p)",
        ExpressionAttributeValues={":p": "DEPT#"},
    )
    return resp.get("Items", [])


def _derive_departments(refund_types: list[str]) -> list[str]:
    """Map refund types to department keys using the admin-config table."""
    depts = set()
    for d in _load_departments():
        if any(rt in (d.get("refund_types") or []) for rt in refund_types):
            depts.add(d["key"])
    return sorted(depts)


def _derive_tasks(status: str, refund_types: list[str], documents: list[str]) -> list[dict[str, str]]:
    """Build a list of task dicts {label, done} for a submission based on its state."""
    tasks: list[dict[str, str]] = []
    expected = set()
    for rt in refund_types:
        expected |= _EXPECTED_DOCS.get(rt, set())
    uploaded_prefixes = {f.split("_", 1)[0].rsplit(".", 1)[0] for f in documents}
    for doc in sorted(expected):
        tasks.append({"label": f"Claimant uploads {doc.replace('-', ' ')}", "done": doc in uploaded_prefixes})
    if "PROPERTY_TAX" in refund_types:
        pt_ok = bool(_PT_EITHER & uploaded_prefixes)
        tasks.append({"label": "Claimant uploads proof of payment or ownership", "done": pt_ok})
    tasks.append({"label": "Admin reviews documents", "done": status in {"under-review", "approved", "denied"}})
    tasks.append({"label": "Admin approves or denies claim", "done": status in {"approved", "denied"}})
    return tasks


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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    headers = _cors_headers(event)

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    resource = event.get("resource", "")
    if resource.startswith("/admin/"):
        return _handle_admin(event, headers)
    if resource == "/package" and event.get("httpMethod") == "GET":
        return _handle_package(event, headers)
    if resource == "/status" and event.get("httpMethod") == "GET":
        return _handle_status(event, headers)
    if resource == "/upload-complete" and event.get("httpMethod") == "POST":
        return _handle_upload_complete(event, headers)
    if resource == "/update-status" and event.get("httpMethod") == "POST":
        return _handle_update_status(event, headers)
    if resource == "/delete-submission" and event.get("httpMethod") == "POST":
        return _handle_delete_submission(event, headers)

    return _handle_upload(event, headers)


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

    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{submission_id}/")
    files = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        filename = key.split("/", 1)[1]
        if filename.startswith("_"):
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
    """Return 'complete' or 'partial' based on expected vs actual docs."""
    expected = set()
    for rt in refund_types:
        expected |= _EXPECTED_DOCS.get(rt, set())
    uploaded_prefixes = {f.split("_", 1)[0].rsplit(".", 1)[0] for f in filenames}
    if not expected <= uploaded_prefixes:
        return "partial"
    if "PROPERTY_TAX" in refund_types and not (_PT_EITHER & uploaded_prefixes):
        return "partial"
    return "complete"


def _handle_status(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """List submissions the caller is allowed to see."""
    dept_keys, is_super = _auth(event)
    if table:
        resp = table.scan()
        submissions = []
        for item in resp.get("Items", []):
            refund_types = [t.strip() for t in item.get("refundType", "").split(",") if t.strip()]
            depts = item.get("departments") or _derive_departments(refund_types)
            if not is_super and not (dept_keys & set(depts)):
                continue
            docs = item.get("documents", [])
            status = item.get("status", "partial")
            submissions.append({
                "submissionId": item["submissionId"],
                "name": item.get("name", ""),
                "refundType": item.get("refundType", ""),
                "status": status,
                "documents": docs,
                "submittedAt": item.get("submittedAt", ""),
                "departments": depts,
                "tasks": _derive_tasks(status, refund_types, docs),
            })
        submissions.sort(key=lambda s: (0 if s["status"] == "complete" else 1, s["submittedAt"]))
        permissions = {
            "isSuperAdmin": is_super,
            "canDelete": is_super,
            "departments": sorted(dept_keys) if not is_super else None,
        }
        return {"statusCode": 200, "headers": headers,
                "body": json.dumps({"submissions": submissions, "permissions": permissions})}

    # Fallback: S3 scan (unauthenticated legacy, kept for local dev)
    return _handle_status_s3(headers)


def _handle_status_s3(headers: dict[str, str]) -> dict[str, Any]:
    """Original S3-based status listing."""
    paginator = s3.get_paginator("list_objects_v2")
    manifests = []
    for page in paginator.paginate(Bucket=BUCKET, Delimiter="/"):
        for prefix in page.get("CommonPrefixes", []):
            manifests.append(prefix["Prefix"])

    submissions = []
    for prefix in manifests:
        sid = prefix.rstrip("/")
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=f"{sid}/_manifest.json")
            manifest = json.loads(obj["Body"].read())
        except Exception:
            continue

        resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=f"{sid}/")
        docs = []
        latest = ""
        for o in resp.get("Contents", []):
            fname = o["Key"].split("/", 1)[1]
            if fname.startswith("_"):
                continue
            docs.append(fname)
            ts = o["LastModified"].isoformat()
            if ts > latest:
                latest = ts

        refund_types = [t.strip() for t in manifest.get("refundType", "").split(",") if t.strip()]
        status = _classify_status(refund_types, docs)
        submissions.append({
            "submissionId": sid,
            "name": manifest.get("name", ""),
            "refundType": manifest.get("refundType", ""),
            "status": status,
            "documents": docs,
            "submittedAt": latest,
        })

    submissions.sort(key=lambda s: (0 if s["status"] == "complete" else 1, s["submittedAt"]))
    return {"statusCode": 200, "headers": headers, "body": json.dumps(submissions)}


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

    # Get the item to read refundType
    resp = table.get_item(Key={"submissionId": submission_id})
    item = resp.get("Item")
    if not item:
        return _err(404, "Submission not found", headers)

    refund_types = [t.strip() for t in item.get("refundType", "").split(",") if t.strip()]
    status = _classify_status(refund_types, filenames)

    table.update_item(
        Key={"submissionId": submission_id},
        UpdateExpression="SET #s = :s, documents = :d, updatedAt = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": status,
            ":d": filenames,
            ":u": _now_iso(),
        },
    )

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"submissionId": submission_id, "status": status}),
    }


_VALID_STATUSES = {"partial", "complete", "under-review", "approved", "denied"}


def _handle_update_status(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    """Admin endpoint to manually change a submission's status."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    submission_id = (body.get("submissionId") or "").strip()
    new_status = (body.get("status") or "").strip()

    if not submission_id or not new_status:
        return _err(400, "submissionId and status are required", headers)
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)
    if new_status not in _VALID_STATUSES:
        return _err(400, f"Invalid status. Must be one of: {', '.join(sorted(_VALID_STATUSES))}", headers)
    if not table:
        return _err(500, "DynamoDB not configured", headers)

    resp = table.get_item(Key={"submissionId": submission_id})
    item = resp.get("Item")
    if not item:
        return _err(404, "Submission not found", headers)

    dept_keys, is_super = _auth(event)
    submission_depts = set(item.get("departments") or _derive_departments(
        [t.strip() for t in item.get("refundType", "").split(",") if t.strip()]))
    if not is_super and not (dept_keys & submission_depts):
        return _err(403, "Not authorized for this submission", headers)

    table.update_item(
        Key={"submissionId": submission_id},
        UpdateExpression="SET #s = :s, updatedAt = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": new_status, ":u": _now_iso()},
    )

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"submissionId": submission_id, "status": new_status}),
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

    # Delete from DynamoDB
    if table:
        table.delete_item(Key={"submissionId": submission_id})

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"submissionId": submission_id, "deleted": True}),
    }


def _handle_upload(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    name = (body.get("name") or "").strip()
    refund_type = (body.get("refundType") or "").strip()
    files = body.get("files") or []

    if not name or not refund_type:
        return _err(400, "name and refundType are required", headers)
    if not files or len(files) > 5:
        return _err(400, "Provide 1-5 files", headers)

    urls = []
    submission_id = uuid.uuid4().hex[:12]
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
        table.put_item(Item={
            "submissionId": submission_id,
            "name": name,
            "refundType": refund_type,
            "departments": _derive_departments(refund_types),
            "status": "partial",
            "documents": [],
            "submittedAt": now,
            "updatedAt": now,
        })

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

    return _err(404, f"Unknown admin route: {method} {path}", headers)


def _admin_get_config(headers: dict[str, str]) -> dict[str, Any]:
    resp = admin_table.scan()
    departments = []
    users = []
    refund_type_labels = {}
    for item in resp.get("Items", []):
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
                "createdAt": item.get("createdAt", ""),
            })
        elif pk.startswith("TYPELABEL#"):
            refund_type_labels[item["refund_type"]] = item.get("label", "")

    # Merge in Cognito users that aren't in admin-config yet (e.g. bootstrap super-admin)
    known = {u["username"] for u in users}
    cognito_users = cognito.list_users(UserPoolId=USER_POOL_ID).get("Users", [])
    for cu in cognito_users:
        uname = cu.get("Username")
        if not uname or uname in known:
            continue
        attrs = {a["Name"]: a["Value"] for a in cu.get("Attributes", [])}
        groups_resp = cognito.admin_list_groups_for_user(UserPoolId=USER_POOL_ID, Username=uname)
        users.append({
            "username": uname, "email": attrs.get("email", ""),
            "groups": [g["GroupName"] for g in groups_resp.get("Groups", [])],
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
    return {"statusCode": 200, "headers": headers, "body": json.dumps({"key": key, "deleted": True})}


def _admin_create_user(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)
    email = (body.get("email") or "").strip().lower()
    groups = list(body.get("groups") or [])
    if "@" not in email:
        return _err(400, "valid email required", headers)
    username = _username_from_email(email)
    if not username:
        return _err(400, "Cannot derive username from email", headers)
    # Collision check
    if admin_table.get_item(Key={"pk": f"USER#{username}"}).get("Item"):
        return _err(409, f"Username '{username}' already exists (email prefix collision)", headers)
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
        try:
            cognito.admin_add_user_to_group(
                UserPoolId=USER_POOL_ID, Username=username, GroupName=group,
            )
        except cognito.exceptions.ResourceNotFoundException:
            return _err(400, f"Group not found: {group}", headers)
    admin_table.put_item(Item={
        "pk": f"USER#{username}", "username": username, "email": email,
        "groups": groups, "createdAt": _now_iso(),
    })
    return {"statusCode": 201, "headers": headers,
            "body": json.dumps({"username": username, "email": email, "groups": groups})}


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
        cognito.admin_update_user_attributes(
            UserPoolId=USER_POOL_ID, Username=username,
            UserAttributes=[
                {"Name": "email", "Value": new_email},
                {"Name": "email_verified", "Value": "true"},
            ],
        )

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

    updates = {"groups": sorted(desired)}
    if new_email:
        updates["email"] = new_email
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
