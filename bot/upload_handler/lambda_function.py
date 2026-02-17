import json
import os
import uuid
import boto3

s3 = boto3.client("s3")
BUCKET = os.environ["UPLOAD_BUCKET"]
ALLOWED_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/heic",
}
MAX_SIZE = 10 * 1024 * 1024  # 10 MB


def lambda_handler(event, context):
    origin = os.environ.get("ALLOWED_ORIGIN", "*")
    cors_headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST,OPTIONS",
    }

    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": cors_headers, "body": ""}

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON", cors_headers)

    name = (body.get("name") or "").strip()
    refund_type = (body.get("refundType") or "").strip()
    files = body.get("files") or []

    if not name or not refund_type:
        return _err(400, "name and refundType are required", cors_headers)
    if not files or len(files) > 5:
        return _err(400, "Provide 1-5 files", cors_headers)

    urls = []
    submission_id = uuid.uuid4().hex[:12]

    for f in files:
        content_type = f.get("contentType", "")
        filename = f.get("filename", "file")
        if content_type not in ALLOWED_TYPES:
            return _err(400, f"Unsupported file type: {content_type}", cors_headers)

        key = f"{submission_id}/{filename}"
        presigned = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": BUCKET,
                "Key": key,
                "ContentType": content_type,
                "Metadata": {"name": name, "refund-type": refund_type},
            },
            ExpiresIn=900,
        )
        urls.append({"filename": filename, "uploadUrl": presigned, "key": key})

    return {
        "statusCode": 200,
        "headers": cors_headers,
        "body": json.dumps({"submissionId": submission_id, "uploads": urls}),
    }


def _err(code, msg, headers):
    return {
        "statusCode": code,
        "headers": headers,
        "body": json.dumps({"error": msg}),
    }
