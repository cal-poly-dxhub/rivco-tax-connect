"""DynamoDB-streams notification Lambda.

Fires on every write to the ClaimSubmissions table. Sends emails to admins in
departments matching the submission's refund types. Two event kinds:
  - INSERT: new submission started → subject "New submission started"
  - MODIFY (status partial → complete): ready for review → subject
    "Submission ready for review"

Super-admins are excluded from notifications per spec.
"""

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ses = boto3.client("ses")
dynamodb = boto3.resource("dynamodb")
cognito = boto3.client("cognito-idp")

ADMIN_CONFIG_TABLE = os.environ["ADMIN_CONFIG_TABLE"]
USER_POOL_ID = os.environ["USER_POOL_ID"]
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "")
SENDER = os.environ["SES_SENDER"]
MODE = os.environ.get("NOTIFICATIONS_MODE", "ses")

admin_table = dynamodb.Table(ADMIN_CONFIG_TABLE)


def _unwrap(attr: dict[str, Any]) -> Any:
    """Minimal DynamoDB Stream type unwrapper."""
    if "S" in attr: return attr["S"]
    if "N" in attr: return attr["N"]
    if "BOOL" in attr: return attr["BOOL"]
    if "L" in attr: return [_unwrap(v) for v in attr["L"]]
    if "M" in attr: return {k: _unwrap(v) for k, v in attr["M"].items()}
    if "NULL" in attr: return None
    if "SS" in attr: return list(attr["SS"])
    return None


def _unwrap_image(image: dict[str, Any]) -> dict[str, Any]:
    return {k: _unwrap(v) for k, v in image.items()}


def _load_departments() -> list[dict[str, Any]]:
    resp = admin_table.scan(
        FilterExpression="begins_with(pk, :p)",
        ExpressionAttributeValues={":p": "DEPT#"},
    )
    return resp.get("Items", [])


def _departments_for_types(refund_types: list[str]) -> list[str]:
    depts = set()
    for d in _load_departments():
        if any(rt in (d.get("refund_types") or []) for rt in refund_types):
            depts.add(d["key"])
    return sorted(depts)


def _recipients_for_departments(dept_keys: list[str]) -> list[str]:
    emails: set[str] = set()
    for key in dept_keys:
        group = f"admin-{key}"
        try:
            users = cognito.list_users_in_group(UserPoolId=USER_POOL_ID, GroupName=group)
        except cognito.exceptions.ResourceNotFoundException:
            continue
        for u in users.get("Users", []):
            attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
            email = attrs.get("email")
            if email:
                emails.add(email)
    return sorted(emails)


def _send(recipients: list[str], subject: str, body_text: str):
    if not recipients:
        logger.info("No recipients — skipping.")
        return
    if MODE != "ses":
        logger.info("LOG MODE: subject=%s recipients=%s body=%s", subject, recipients, body_text)
        return
    for to in recipients:
        try:
            ses.send_email(
                Source=SENDER,
                Destination={"ToAddresses": [to]},
                Message={
                    "Subject": {"Data": subject},
                    "Body": {"Text": {"Data": body_text}},
                },
            )
            logger.info("Sent '%s' to %s", subject, to)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            # Fall back to logging when the sender isn't verified yet.
            if code in ("MessageRejected", "MailFromDomainNotVerifiedException"):
                logger.warning("SES rejected (likely unverified sender). Logging instead. to=%s subject=%s body=%s",
                               to, subject, body_text)
            else:
                raise


def _format_email(submission: dict[str, Any], kind: str) -> tuple[str, str]:
    name = submission.get("name", "(no name)")
    sid = submission.get("submissionId", "")
    types = submission.get("refundType", "")
    dashboard = DASHBOARD_URL or "(dashboard URL not configured)"
    if kind == "new":
        subject = f"New submission started — {name}"
        body = (
            f"A new unclaimed refund submission has been started.\n\n"
            f"Claimant: {name}\n"
            f"Refund type(s): {types}\n"
            f"Submission ID: {sid}\n\n"
            f"Documents may still be uploading. Check the dashboard for status:\n"
            f"{dashboard}\n"
        )
    else:  # ready
        subject = f"Submission ready for review — {name}"
        body = (
            f"A submission has completed document upload and is ready for review.\n\n"
            f"Claimant: {name}\n"
            f"Refund type(s): {types}\n"
            f"Submission ID: {sid}\n\n"
            f"Review on the dashboard:\n"
            f"{dashboard}\n"
        )
    return subject, body


def lambda_handler(event: dict[str, Any], context: Any) -> None:
    for record in event.get("Records", []):
        event_name = record.get("eventName")
        dynamo = record.get("dynamodb", {})
        new_image = _unwrap_image(dynamo.get("NewImage") or {})
        old_image = _unwrap_image(dynamo.get("OldImage") or {})

        kind = None
        if event_name == "INSERT":
            kind = "new"
        elif event_name == "MODIFY":
            old_status = old_image.get("status")
            new_status = new_image.get("status")
            if old_status == "partial" and new_status == "complete":
                kind = "ready"

        if not kind or not new_image:
            continue

        refund_types = [
            t.strip() for t in (new_image.get("refundType") or "").split(",") if t.strip()
        ]
        dept_keys = new_image.get("departments") or _departments_for_types(refund_types)
        if not dept_keys:
            logger.info("No departments map to %s; skipping notification.", refund_types)
            continue

        recipients = _recipients_for_departments(dept_keys)
        subject, body = _format_email(new_image, kind)
        _send(recipients, subject, body)
