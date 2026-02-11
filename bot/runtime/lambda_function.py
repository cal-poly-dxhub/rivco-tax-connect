import json
import os
import boto3
import jellyfish
from bs4 import BeautifulSoup

s3 = boto3.client('s3')
BUCKET = os.environ.get('S3_BUCKET')
FILE_KEY = os.environ.get('DATA_FILE', 'UnclaimedRefunds.xls')
CLAIM_URL = os.environ.get('CLAIM_URL', 'https://apps.auditorcontroller.org/unclaimedrefund/refundform.aspx')

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

def match_name(query, records):
    if not query:
        return []
    q = query.lower().strip()
    scored = [(jellyfish.jaro_winkler_similarity(q, r['name'].lower()), r) for r in records]
    return [r for s, r in sorted(scored, key=lambda x: -x[0]) if s >= 0.7]

def lambda_handler(event, context):
    # Handle Connect flow invocation (custom action)
    if 'Details' in event:
        name = event.get('Details', {}).get('ContactData', {}).get('Attributes', {}).get('customer_name', '')
        if not name:
            # Try to get from Lex session attributes
            name = event.get('Details', {}).get('ContactData', {}).get('Attributes', {}).get('name', '')
        try:
            matches = match_name(name, load_records())
            if matches:
                top = matches[0]['name']
                same = [m for m in matches if m['name'] == top]
                total = sum(m['amount'] for m in same)
                return {'result': f"I found {len(same)} refund(s) for {top} totaling ${total:.2f}. Visit {CLAIM_URL} to claim your refund."}
            return {'result': f"I couldn't find any refunds for {name}. Please check the spelling and try again."}
        except Exception as e:
            return {'result': "I'm sorry, I couldn't look up that information right now."}
    
    # Handle Lex invocation (fallback)
    intent = event.get('sessionState', {}).get('intent', {})
    transcript = event.get('inputTranscript', '').strip()
    session = event.get('sessionState', {}).get('sessionAttributes', {}) or {}
    
    try:
        matches = match_name(transcript, load_records())
        if matches:
            top = matches[0]['name']
            same = [m for m in matches if m['name'] == top]
            total = sum(m['amount'] for m in same)
            msg = f"I found {len(same)} refund(s) for {top} totaling ${total:.2f}. Visit {CLAIM_URL} to claim your refund."
        else:
            msg = f"I couldn't find any refunds for {transcript}."
        return {
            'sessionState': {'dialogAction': {'type': 'ElicitIntent'}, 'intent': intent, 'sessionAttributes': session},
            'messages': [{'contentType': 'PlainText', 'content': msg}]
        }
    except Exception:
        return {
            'sessionState': {'dialogAction': {'type': 'Close'}, 'intent': {**intent, 'state': 'Failed'}},
            'messages': [{'contentType': 'PlainText', 'content': "An error occurred. Please try again."}]
        }
