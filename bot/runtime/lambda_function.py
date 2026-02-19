import json
import logging
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import boto3
import jellyfish

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
sns = boto3.client('sns')
connect_client = boto3.client('connect')
qconnect = boto3.client('qconnect')
BUCKET = os.environ.get('S3_BUCKET')
FILE_KEY = os.environ.get('DATA_FILE', 'refunds_demo_balanced.jsonl')
FUZZY_THRESHOLD = float(os.environ.get('FUZZY_THRESHOLD', '0.8'))

PROPERTY_TAX_URL = os.environ.get('PROPERTY_TAX_URL', 'https://apps.auditorcontroller.org/unclaimedrefund/refundform.aspx')
AP13_PDF_URL = os.environ.get('AP13_PDF_URL', 'https://auditorcontroller.org/sites/g/files/aldnop171/files/2024-10/AP13AffidavitfortheReplaceofStlDtdWarr%2010_30_24.pdf')
UPLOAD_PORTAL_URL = os.environ.get('UPLOAD_PORTAL_URL', '')

_records_cache = None


def load_records() -> list[dict]:
    global _records_cache
    if _records_cache is not None:
        return _records_cache

    response = s3.get_object(Bucket=BUCKET, Key=FILE_KEY)
    body = response['Body'].read().decode('utf-8')
    today = datetime.now()
    records = []
    for line in body.strip().splitlines():
        r = json.loads(line)
        deadline = datetime.strptime(r['claim_deadline'], '%m/%d/%Y')
        if deadline >= today:
            records.append(r)
    _records_cache = records
    return records


def build_claim_url(record: dict[str, Any]) -> str:
    if record['refund_type'] == 'PROPERTY_TAX':
        params = '&'.join(f'{k}={record[k]}' for k in ('index', 'assessment', 'taxyear'))
        return f"{PROPERTY_TAX_URL}?{params}&amount={record['amount']}"
    return AP13_PDF_URL


def build_portal_url(records: list[dict[str, Any]]) -> str:
    """Build a single claims portal URL with all refund types and amounts."""
    if not UPLOAD_PORTAL_URL or not records:
        return ''
    types = ','.join(r['refund_type'] for r in records)
    amounts = ','.join(str(r['amount']) for r in records)
    # Collect per-record identifiers (warrant_number for AP13, index for property tax)
    identifiers = ','.join(r.get('warrant_number') or r.get('index') or '' for r in records)
    params = {
        'name': records[0]['name'],
        'type': types,
        'amount': amounts,
        'address': records[0].get('address', ''),
        'id': identifiers,
    }
    # Add property tax fields if any record is PROPERTY_TAX
    pt = next((r for r in records if r['refund_type'] == 'PROPERTY_TAX'), None)
    if pt:
        params.update({k: pt[k] for k in ('assessment', 'taxyear') if k in pt})
    return f"{UPLOAD_PORTAL_URL}?{urlencode(params)}"


def find_best_match(query: str) -> tuple[str | None, list[dict[str, Any]]]:
    if not query:
        return None, []

    q = query.lower().strip()[:200]
    records = load_records()

    scored = [
        (jellyfish.jaro_winkler_similarity(q, r['name'].lower()), r)
        for r in records
    ]
    scored.sort(key=lambda x: -x[0])

    top5 = [(round(s, 3), r['name']) for s, r in scored[:5]]
    logger.info("Query: '%s' | Top 5: %s", query[:50], top5)

    best_score, best_record = scored[0]
    if best_score < FUZZY_THRESHOLD:
        logger.info("Best score %.3f below threshold %.2f — no match", best_score, FUZZY_THRESHOLD)
        return None, []

    best_name = best_record['name']
    matching_records = [r for r in records if r['name'] == best_name]
    logger.info("Matched: '%s' (score=%.3f) | %d record(s)", best_name, best_score, len(matching_records))

    return best_name, matching_records


def lookup(name: str) -> str:
    best_name, records = find_best_match(name)
    if not best_name:
        return (
            f"We found no refunds for {name}. "
            "You may have no refunds or your refund may have passed its claim deadline."
        )

    results = []
    for r in records:
        results.append({
            'name': r['name'],
            'refund_type': r['refund_type'],
            'amount': f"${r['amount']:,.2f}",
            'claim_deadline': r['claim_deadline'],
            'address': r.get('address', ''),
        })

    portal_url = build_portal_url(records)
    return json.dumps({'refunds': results, 'portal_url': portal_url})


def send_sms(phone_number: str, message: str) -> str:
    """Send SMS to a validated US phone number."""
    if not message:
        return 'Message is required.'
    if not re.match(r'^\+1\d{10}$', phone_number):
        return 'Invalid phone number. Must be a US number in E.164 format (+1XXXXXXXXXX).'
    if len(message) > 160:
        message = message[:157] + '...'
    sns.publish(
        PhoneNumber=phone_number,
        Message=message,
        MessageAttributes={
            'AWS.SNS.SMS.SMSType': {'DataType': 'String', 'StringValue': 'Transactional'},
        },
    )
    logger.info("SMS sent to %s...%s", phone_number[:5], phone_number[-2:])
    return f'SMS sent to {phone_number}.'


def inject_channel(event: dict[str, Any]) -> dict[str, str]:
    """Push the Connect channel into the Q in Connect session as custom data."""
    contact_data = event['Details']['ContactData']
    channel = contact_data.get('Channel', 'VOICE')
    contact_id = contact_data['ContactId']
    instance_arn = contact_data['InstanceARN']
    instance_id = instance_arn.split('/')[-1]
    assistant_id = os.environ.get('ASSISTANT_ID', '')

    if not assistant_id:
        logger.error("ASSISTANT_ID not set")
        return {'lambdaResult': 'Error'}

    # Get the wisdom session ARN from the contact
    contact = connect_client.describe_contact(
        ContactId=contact_id, InstanceId=instance_id
    )
    session_arn = (contact.get('Contact', {}).get('WisdomInfo') or {}).get('SessionArn')
    if not session_arn:
        logger.warning("No wisdom session on contact %s", contact_id)
        return {'lambdaResult': 'NoSession'}

    session_id = session_arn.split('/')[-1]
    qconnect.update_session_data(
        assistantId=assistant_id,
        sessionId=session_id,
        data=[{'key': 'channel', 'value': {'stringValue': channel}}],
    )
    logger.info("Injected channel=%s into session %s", channel, session_id)
    return {'lambdaResult': 'Success'}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("Event: %s", json.dumps(event, default=str))
    try:
        # Connect flow direct invocation: inject channel into session
        params = (event.get('Details') or {}).get('Parameters') or {}
        if params.get('action') == 'inject_channel':
            return inject_channel(event)

        # MCP Gateway tool calls
        if 'contact_id' in event and 'instance_id' in event:
            connect_client.update_contact_attributes(
                InstanceId=event['instance_id'],
                InitialContactId=event['contact_id'],
                Attributes={'transferToAgent': 'true'},
            )
            logger.info("Set transferToAgent=true for contact %s", event['contact_id'])
            return {'result': 'Transfer flag set. The caller will be routed to a live agent.'}

        if 'customer_name' in event:
            return {'result': lookup(event['customer_name'])}

        if 'phone_number' in event:
            return {'result': send_sms(event['phone_number'], event.get('message', ''))}

        # Lex: delegate QInConnectIntent back to Q in Connect
        intent = event.get('sessionState', {}).get('intent', {})
        session = event.get('sessionState', {}).get('sessionAttributes', {}) or {}

        if intent.get('name') == 'QInConnectIntent':
            return {
                'sessionState': {
                    'dialogAction': {'type': 'Delegate'},
                    'intent': intent,
                    'sessionAttributes': session,
                }
            }

        # Lex FallbackIntent: do tax lookup on transcript
        if 'inputTranscript' in event:
            msg = lookup(event['inputTranscript'].strip())
            return {
                'sessionState': {
                    'dialogAction': {'type': 'ElicitIntent'},
                    'intent': intent,
                    'sessionAttributes': session,
                },
                'messages': [{'contentType': 'PlainText', 'content': msg}],
            }

        logger.warning("Unrecognized invocation: %s", list(event.keys()))
        return {'result': 'Unknown invocation type.'}
    except Exception:
        logger.exception("Unhandled error")
        return {'result': 'An error occurred. Please try again.'}
