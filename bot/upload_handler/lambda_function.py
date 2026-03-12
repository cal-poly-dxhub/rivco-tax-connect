import json
import os
import re
import uuid
import boto3
from typing import Any

s3 = boto3.client("s3")
BUCKET = os.environ["UPLOAD_BUCKET"]
UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "")
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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    headers = _cors_headers()

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    resource = event.get("resource", "")
    if resource == "/package" and event.get("httpMethod") == "GET":
        return _handle_package(event, headers)

    return _handle_upload(event, headers)


def _handle_package(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    qs = event.get("queryStringParameters") or {}
    submission_id = (qs.get("id") or "").strip()
    password = (qs.get("password") or "").strip()

    if not submission_id:
        return _err(400, "id is required", headers)
    if UPLOAD_PASSWORD and password != UPLOAD_PASSWORD:
        return _err(403, "Invalid password", headers)
    # Sanitize: submission_id should be hex only
    if not re.fullmatch(r'[0-9a-f]{12}', submission_id):
        return _err(400, "Invalid submission id", headers)

    # Load manifest
    try:
        manifest_obj = s3.get_object(Bucket=BUCKET, Key=f"{submission_id}/_manifest.json")
        manifest = json.loads(manifest_obj["Body"].read())
    except s3.exceptions.NoSuchKey:
        return _err(404, "Submission not found", headers)

    # List all files in the submission prefix
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


def _handle_upload(event: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", headers)

    name = (body.get("name") or "").strip()
    refund_type = (body.get("refundType") or "").strip()
    password = (body.get("password") or "").strip()
    files = body.get("files") or []

    if not name or not refund_type:
        return _err(400, "name and refundType are required", headers)
    if UPLOAD_PASSWORD and password != UPLOAD_PASSWORD:
        return _err(403, "Invalid password", headers)
    if not files or len(files) > 5:
        return _err(400, "Provide 1-5 files", headers)

    urls = []
    submission_id = uuid.uuid4().hex[:12]

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

    # Write submission manifest so metadata is preserved alongside uploads
    s3.put_object(
        Bucket=BUCKET,
        Key=f"{submission_id}/_manifest.json",
        Body=json.dumps({"name": name, "refundType": refund_type}),
        ContentType="application/json",
    )

    return {
        "statusCode": 200,
        "headers": headers,
        "body": json.dumps({"submissionId": submission_id, "uploads": urls}),
    }
