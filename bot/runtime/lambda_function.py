import json
import os
import boto3
import jellyfish
from bs4 import BeautifulSoup

s3 = boto3.client('s3')
BUCKET = os.environ.get('S3_BUCKET')
FILE_KEY = os.environ.get('DATA_FILE', 'UnclaimedRefunds.xls')
CLAIM_URL = os.environ.get('CLAIM_URL', 'https://example.gov/claim')
VOICE_TIMEOUT_MS = os.environ.get('VOICE_TIMEOUT_MS', '15000')

# Prompts from environment (set via config)
PROMPT_WELCOME = os.environ.get('PROMPT_WELCOME', 'Welcome. Please provide your name to search for refunds.')
PROMPT_NOT_FOUND = os.environ.get('PROMPT_NOT_FOUND', "I couldn't find any refunds for {name}.")
PROMPT_FOUND = os.environ.get('PROMPT_FOUND', 'Found {count} refund(s) for {name} totaling ${total}. Visit {url} to claim.')
PROMPT_ERROR = os.environ.get('PROMPT_ERROR', 'An error occurred. Please try again.')

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
            records.append({
                'name': cells[0].get_text(strip=True),
                'amount': amount,
            })
    _records_cache = records
    return records

def match_name(query, records):
    if not query:
        return []
    q = query.lower().strip()
    scored = [(jellyfish.jaro_winkler_similarity(q, r['name'].lower()), r) for r in records]
    return [r for s, r in sorted(scored, key=lambda x: -x[0]) if s >= 0.7]

def lambda_handler(event, context):
    intent = event.get('sessionState', {}).get('intent', {})
    transcript = event.get('inputTranscript', '').strip().lower()
    session = event.get('sessionState', {}).get('sessionAttributes', {}) or {}

    welcomed = session.get('welcomed') == 'true'
    query = transcript if transcript not in ('hi', 'hello', '') else ''

    if not query:
        if welcomed:
            return {'sessionState': {'dialogAction': {'type': 'ElicitIntent'}, 'intent': intent, 'sessionAttributes': session}}
        session['welcomed'] = 'true'
        session['x-amz-lex:audio:start-timeout-ms:*:*'] = VOICE_TIMEOUT_MS
        return {
            'sessionState': {'dialogAction': {'type': 'ElicitIntent'}, 'intent': intent, 'sessionAttributes': session},
            'messages': [{'contentType': 'PlainText', 'content': PROMPT_WELCOME}]
        }

    try:
        matches = match_name(query, load_records())
        if matches:
            top = matches[0]['name']
            same = [m for m in matches if m['name'] == top]
            total = sum(m['amount'] for m in same)
            msg = PROMPT_FOUND.format(count=len(same), name=top, total=f'{total:.2f}', url=CLAIM_URL)
        else:
            msg = PROMPT_NOT_FOUND.format(name=query)
        return {
            'sessionState': {'dialogAction': {'type': 'ElicitIntent'}, 'intent': intent, 'sessionAttributes': {**session, 'welcomed': 'true'}},
            'messages': [{'contentType': 'PlainText', 'content': msg}]
        }
    except Exception:
        return {
            'sessionState': {'dialogAction': {'type': 'Close'}, 'intent': {**intent, 'state': 'Failed'}},
            'messages': [{'contentType': 'PlainText', 'content': PROMPT_ERROR}]
        }
