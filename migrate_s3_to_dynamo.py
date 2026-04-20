#!/usr/bin/env python3
"""One-time migration: backfill DynamoDB from existing S3 submissions."""

import json
import os
import re
import sys

import boto3

REGION = "us-west-2"

EXPECTED_DOCS = {
    "STALE_WARRANT": {"photo-id", "proof-of-address", "ap13-affidavit"},
    "PAYROLL": {"photo-id", "proof-of-address", "ap13-affidavit"},
    "PROPERTY_TAX": {"photo-id", "property-tax-claim"},
}
PT_EITHER = {"proof-of-payment", "proof-of-ownership"}


def classify_status(refund_types, filenames):
    expected = set()
    for rt in refund_types:
        expected |= EXPECTED_DOCS.get(rt, set())
    uploaded_prefixes = {f.split("_", 1)[0].rsplit(".", 1)[0] for f in filenames}
    if not expected <= uploaded_prefixes:
        return "partial"
    if "PROPERTY_TAX" in refund_types and not (PT_EITHER & uploaded_prefixes):
        return "partial"
    return "complete"


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <uploads-bucket> <table-name>")
        sys.exit(1)

    bucket = sys.argv[1]
    table_name = sys.argv[2]

    s3 = boto3.client("s3", region_name=REGION)
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(table_name)

    paginator = s3.get_paginator("list_objects_v2")
    prefixes = []
    for page in paginator.paginate(Bucket=bucket, Delimiter="/"):
        for p in page.get("CommonPrefixes", []):
            prefixes.append(p["Prefix"])

    print(f"Found {len(prefixes)} submissions in S3")

    for prefix in prefixes:
        sid = prefix.rstrip("/")
        try:
            obj = s3.get_object(Bucket=bucket, Key=f"{sid}/_manifest.json")
            manifest = json.loads(obj["Body"].read())
        except Exception as e:
            print(f"  ⚠️  {sid}: no manifest ({e})")
            continue

        resp = s3.list_objects_v2(Bucket=bucket, Prefix=f"{sid}/")
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
        status = classify_status(refund_types, docs)

        table.put_item(Item={
            "submissionId": sid,
            "name": manifest.get("name", ""),
            "refundType": manifest.get("refundType", ""),
            "status": status,
            "documents": docs,
            "submittedAt": latest,
            "updatedAt": latest,
        })
        print(f"  ✅ {sid} — {manifest.get('name', '?')} — {status}")

    print(f"\nMigrated {len(prefixes)} submissions to {table_name}")


if __name__ == "__main__":
    main()
