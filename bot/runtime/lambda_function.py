"""Tax-refund lookup Lambda — pure tool backend.

Invoked synchronously by the chat handler when the model emits a `tax_lookup`
tool call. Returns a JSON-shaped string (refund list + portal URL, or a
disambiguation prompt, or a no-match message).

This used to also serve Lex code hooks and Q in Connect MCP gateway calls.
Both are gone; the only entry point is now `{"customer_name": "..."}`.
"""
import json
import logging
import os
import random
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import boto3
import jellyfish

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3')
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
    if not UPLOAD_PORTAL_URL:
        logger.warning("UPLOAD_PORTAL_URL not set — portal link will be empty")
        return ''
    if not records:
        return ''
    types = ','.join(r['refund_type'] for r in records)
    amounts = ','.join(str(r['amount']) for r in records)
    identifiers = ','.join(r.get('warrant_number') or r.get('index') or '' for r in records)
    params = {
        'name': records[0]['name'],
        'type': types,
        'amount': amounts,
        'address': records[0].get('address', ''),
        'id': identifiers,
    }
    pt = next((r for r in records if r['refund_type'] == 'PROPERTY_TAX'), None)
    if pt:
        params.update({k: pt[k] for k in ('assessment', 'taxyear') if k in pt})
    return f"{UPLOAD_PORTAL_URL}?{urlencode(params)}"


ALIAS_MAP = {
    'inc': 'incorporated', 'incorporated': 'incorporated',
    'corp': 'corporation', 'corporation': 'corporation',
    'llc': 'limited liability company', 'l.l.c.': 'limited liability company',
    'llp': 'limited liability partnership',
    'ltd': 'limited', 'limited': 'limited',
    'assn': 'association', 'assoc': 'association', 'association': 'association',
    'co': 'company', 'company': 'company',
    'intl': 'international', "int'l": 'international', 'international': 'international',
    'dept': 'department', 'department': 'department',
    'svcs': 'services', 'svc': 'service',
    'mgmt': 'management', 'mgt': 'management',
    'natl': 'national', "nat'l": 'national',
    'univ': 'university', 'university': 'university',
    'jr': 'junior', 'sr': 'senior',
    'st': 'saint', 'mt': 'mount',
    'govt': 'government', 'gov': 'government',
    'ctr': 'center', 'cntr': 'center',
    'grp': 'group', 'hldgs': 'holdings',
    'props': 'properties', 'prop': 'property',
    'dev': 'development',
}


def normalize_name(name: str) -> str:
    """Expand abbreviations and standardize for comparison."""
    tokens = re.split(r'[\s,./]+', name.lower().strip())
    normalized = []
    for t in tokens:
        t = t.strip('.')
        if not t:
            continue
        normalized.append(ALIAS_MAP.get(t, t))
    return ' '.join(normalized)


def token_similarity(query: str, candidate: str) -> float:
    """Score based on best token-to-token alignment. Handles reordered/partial names."""
    q_tokens = query.split()
    c_tokens = candidate.split()
    if not q_tokens or not c_tokens:
        return 0.0
    total = 0.0
    for qt in q_tokens:
        best = max(jellyfish.jaro_winkler_similarity(qt, ct) for ct in c_tokens)
        total += best
    return total / len(q_tokens)


def combined_score(query_norm: str, record_name_norm: str) -> float:
    """Blend full-string Jaro-Winkler with token-based matching."""
    full_score = jellyfish.jaro_winkler_similarity(query_norm, record_name_norm)
    tok_score = token_similarity(query_norm, record_name_norm)
    return max(full_score, tok_score * 0.95)


def find_best_match(query: str) -> tuple[str | None, list[dict[str, Any]]]:
    if not query:
        return None, []

    q_norm = normalize_name(query)
    records = load_records()

    scored = [
        (combined_score(q_norm, normalize_name(r['name'])), r)
        for r in records
    ]
    scored.sort(key=lambda x: -x[0])

    top5 = [(round(s, 3), r['name']) for s, r in scored[:5]]
    logger.info("Query: '%s' (norm: '%s') | Top 5: %s", query[:50], q_norm[:50], top5)

    best_score, best_record = scored[0]
    if best_score < FUZZY_THRESHOLD:
        # Address-keyed secondary lookup: if a near-miss shares an address with
        # a higher-confidence match, surface it as a possible alias
        near_misses = [(s, r) for s, r in scored if s >= FUZZY_THRESHOLD * 0.75]
        if near_misses:
            addresses_seen = {}
            for s, r in near_misses:
                addr = r.get('address', '')
                if addr and addr not in addresses_seen:
                    addresses_seen[addr] = (s, r)
            # Check if any two near-misses share an address (alias candidate)
            addr_groups: dict[str, list] = {}
            for s, r in scored[:20]:
                addr = r.get('address', '')
                if addr:
                    addr_groups.setdefault(addr, []).append((s, r))
            for addr, group in addr_groups.items():
                if len(group) > 1 and any(s >= FUZZY_THRESHOLD for s, _ in group):
                    # Found a record at the same address that does match
                    best_at_addr = max(group, key=lambda x: x[0])
                    if best_at_addr[0] >= FUZZY_THRESHOLD:
                        best_record = best_at_addr[1]
                        best_score = best_at_addr[0]
                        break

        if best_score < FUZZY_THRESHOLD:
            logger.info("Best score %.3f below threshold %.2f — no match", best_score, FUZZY_THRESHOLD)
            return None, []

    best_name = best_record['name']
    matching_records = [r for r in records if r['name'] == best_name]
    logger.info("Matched: '%s' (score=%.3f) | %d record(s)", best_name, best_score, len(matching_records))

    return best_name, matching_records


def extract_street(address: str) -> str:
    """Extract just the street name/number portion (before the city comma)."""
    parts = address.split(',')
    return parts[0].strip() if parts else address.strip()


def generate_decoy_streets(real_address: str, count: int = 3) -> list[str]:
    """Pick decoy street names from other records, preferring same city/state."""
    records = load_records()
    real_street = extract_street(real_address)
    real_city_state = ','.join(real_address.split(',')[1:]).strip()

    all_addresses = set(r.get('address', '') for r in records if r.get('address'))
    all_addresses.discard(real_address)

    # Prefer addresses in the same city/state for realistic decoys
    same_area = [a for a in all_addresses if ','.join(a.split(',')[1:]).strip() == real_city_state]
    other_area = [a for a in all_addresses if a not in same_area]

    pool = same_area + other_area
    # Extract streets and deduplicate
    candidate_streets = []
    seen = {real_street.lower()}
    for addr in pool:
        street = extract_street(addr)
        if street.lower() not in seen:
            candidate_streets.append(street)
            seen.add(street.lower())

    decoys = candidate_streets[:count * 3]  # oversample then pick
    random.shuffle(decoys)
    return decoys[:count]


def lookup(name: str, address: str = '') -> str:
    best_name, records = find_best_match(name)
    if not best_name:
        return (
            f"We found no refunds for {name}. "
            "You may have no refunds or your refund may have passed its claim deadline."
        )

    addresses = sorted(set(r.get('address', '') for r in records))
    if len(addresses) > 1:
        if not address:
            # Present truncated street names only (no full address for privacy)
            street_options = [extract_street(a) for a in addresses]
            return json.dumps({
                'disambiguation_needed': True,
                'name': best_name,
                'addresses': street_options,
                'message': f"We found multiple people named {best_name}. Which street have you lived on?",
            })
        addr_lower = address.lower().strip()
        selected = next((a for a in addresses if addr_lower in a.lower()), None)
        if not selected:
            selected = next((a for a in addresses if any(w in addr_lower for w in a.lower().split() if len(w) >= 4)), None)
        if selected:
            records = [r for r in records if r.get('address', '') == selected]
        else:
            street_options = [extract_street(a) for a in addresses]
            return json.dumps({
                'disambiguation_needed': True,
                'name': best_name,
                'addresses': street_options,
                'message': f"That didn't match our records for {best_name}. Which of these streets is yours?",
            })

    # Address quiz: present the real street + decoys, ask user to identify theirs
    real_address = records[0].get('address', '')
    if real_address and not address:
        real_street = extract_street(real_address)
        decoys = generate_decoy_streets(real_address, count=3)
        options = [real_street] + decoys
        random.shuffle(options)
        return json.dumps({
            'address_verification': True,
            'name': best_name,
            'street_options': options,
            'message': f"To verify your identity, which of the following streets have you lived on?",
        })

    # If address was provided, verify it matches
    if address and real_address:
        real_street = extract_street(real_address).lower()
        if real_street not in address.lower() and address.lower() not in real_street:
            # Check if any word from the real street appears in their answer
            real_words = [w for w in real_street.split() if len(w) >= 4 and not w.isdigit()]
            if not any(w in address.lower() for w in real_words):
                return json.dumps({
                    'verification_failed': True,
                    'message': "That doesn't match our records. For security, we cannot proceed. Please contact the Auditor-Controller's office at (951) 955-3800.",
                })

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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Sync invocation with `{customer_name, customer_address?}` payload."""
    logger.info("Event: %s", json.dumps(event, default=str))
    try:
        name = (event.get('customer_name') or '').strip()
        if not name:
            return {'result': 'customer_name is required'}
        return {'result': lookup(name, event.get('customer_address', ''))}
    except Exception:
        logger.exception("Unhandled error")
        return {'result': 'An error occurred. Please try again.'}
