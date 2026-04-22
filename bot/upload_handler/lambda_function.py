import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
BUCKET = os.environ["UPLOAD_BUCKET"]
TABLE_NAME = os.environ.get("TABLE_NAME", "")
table = dynamodb.Table(TABLE_NAME) if TABLE_NAME else None
# Department config passed as JSON env var from CDK. Shape:
#   [{"key": "general", "refund_types": ["STALE_WARRANT"]}, ...]
DEPARTMENTS = json.loads(os.environ.get("DEPARTMENTS", "[]"))
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


def _cors_headers() -> dict[str, str]:
    origin = os.environ.get("ALLOWED_ORIGIN", "*")
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
    }


def _err(code: int, msg: str, headers: dict[str, str]) -> dict[str, Any]:
    return {"statusCode": code, "headers": headers, "body": json.dumps({"error": msg})}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_departments(refund_types: list[str]) -> list[str]:
    """Map refund types to department keys using DEPARTMENTS config."""
    depts = set()
    for d in DEPARTMENTS:
        if any(rt in d.get("refund_types", []) for rt in refund_types):
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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    headers = _cors_headers()

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    resource = event.get("resource", "")
    if resource == "/package" and event.get("httpMethod") == "GET":
        return _handle_package(event, headers)
    if resource == "/status" and event.get("httpMethod") == "GET":
        return _handle_status(headers)
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


def _handle_status(headers: dict[str, str]) -> dict[str, Any]:
    """List all submissions — from DynamoDB if available, else fall back to S3."""
    if table:
        resp = table.scan()
        submissions = []
        for item in resp.get("Items", []):
            refund_types = [t.strip() for t in item.get("refundType", "").split(",") if t.strip()]
            docs = item.get("documents", [])
            status = item.get("status", "partial")
            submissions.append({
                "submissionId": item["submissionId"],
                "name": item.get("name", ""),
                "refundType": item.get("refundType", ""),
                "status": status,
                "documents": docs,
                "submittedAt": item.get("submittedAt", ""),
                "departments": item.get("departments") or _derive_departments(refund_types),
                "tasks": _derive_tasks(status, refund_types, docs),
            })
        submissions.sort(key=lambda s: (0 if s["status"] == "complete" else 1, s["submittedAt"]))
        return {"statusCode": 200, "headers": headers, "body": json.dumps(submissions)}

    # Fallback: S3 scan (original behavior)
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
    if not resp.get("Item"):
        return _err(404, "Submission not found", headers)

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
    """Delete a submission from DynamoDB and S3."""
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
