import json
import logging
import os

import boto3
import jellyfish
from bs4 import BeautifulSoup

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
BUCKET = os.environ.get('S3_BUCKET')
FILE_KEY = os.environ.get('DATA_FILE', 'UnclaimedRefunds.xls')
CLAIM_URL = os.environ.get('CLAIM_URL', 'https://apps.auditorcontroller.org/unclaimedrefund/refundform.aspx')
FUZZY_THRESHOLD = float(os.environ.get('FUZZY_THRESHOLD', '0.8'))

_records_cache = None


def load_records():
    global _records_cache
    if _records_cache is not None:
        return _records_cache
    response = s3.get_object(Bucket=BUCKET, Key=FILE_KEY)
    html = response['Body'].read().decode('utf-8')
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for row in soup.find_all('tr')[1:]:
        cells = row.find_all('td')
        if len(cells) >= 5:
            amount_str = cells[3].get_text(strip=True)
            amount = float(amount_str.replace('$', '').replace(',', '')) if amount_str else 0.0
            records.append({'name': cells[0].get_text(strip=True), 'amount': amount})
    _records_cache = records
    return records


def find_best_match(query):
    if not query:
        return None, []

    q = query.lower().strip()
    records = load_records()

    scored = [
        (jellyfish.jaro_winkler_similarity(q, r['name'].lower()), r)
        for r in records
    ]
    scored.sort(key=lambda x: -x[0])

    top5 = [(round(s, 3), r['name']) for s, r in scored[:5]]
    logger.info("Query: '%s' | Top 5: %s", query, top5)

    best_score, best_record = scored[0]
    if best_score < FUZZY_THRESHOLD:
        logger.info("Best score %.3f below threshold %.2f — no match", best_score, FUZZY_THRESHOLD)
        return None, []

    best_name = best_record['name']
    matching_records = [r for r in records if r['name'] == best_name]

    total = sum(r['amount'] for r in matching_records)
    logger.info("Matched: '%s' (score=%.3f) | %d record(s) | Total: $%.2f",
                best_name, best_score, len(matching_records), total)

    return best_name, matching_records


def lookup(name):
    best_name, records = find_best_match(name)
    if not best_name:
        return f"No refunds found for {name}."

    total = sum(r['amount'] for r in records)
    return (
        f"Match: {best_name} | "
        f"Refunds: {len(records)} | "
        f"Total: ${total:,.2f} | "
        f"Claim URL: {CLAIM_URL}"
    )


def lambda_handler(event, context):
    logger.info("Event: %s", json.dumps(event, default=str))

    # MCP Gateway tool call
    if 'customer_name' in event:
        return {'result': lookup(event['customer_name'])}

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

    return {'result': 'Unknown invocation type.'}
