"""Tax-refund lookup Lambda — pure tool backend.

Invoked synchronously by the chat handler when the model emits a `tax_lookup`
tool call. Returns a JSON-shaped string (refund list + portal URL, or a
disambiguation prompt, or a no-match message).
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


def build_portal_url(records: list[dict[str, Any]], confidence: str = '') -> str:
    """Build a single claims portal URL with all refund types and amounts.

    `confidence` ('high'|'low') is propagated as a query param so the upload
    portal stores it on the submission, and the admin dashboard can flag
    low-confidence claims for extra-doc review.
    """
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
    if confidence:
        params['confidence'] = confidence
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


# Confidence bands. High = exact / alias hit at correct address. Low = match
# above threshold but with name-level edits — admin should require extra docs.
HIGH_CONFIDENCE_THRESHOLD = 0.95


def find_best_match(query: str) -> tuple[str | None, list[dict[str, Any]], float]:
    """Return (best_name, matching_records, score). Score is 0 if no match."""
    if not query:
        return None, [], 0.0

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
            return None, [], best_score

    best_name = best_record['name']
    matching_records = [r for r in records if r['name'] == best_name]
    logger.info("Matched: '%s' (score=%.3f) | %d record(s)", best_name, best_score, len(matching_records))

    return best_name, matching_records, best_score


def extract_street(address: str) -> str:
    """Extract just the street name/number portion (before the city comma)."""
    parts = address.split(',')
    return parts[0].strip() if parts else address.strip()


def split_street_parts(street: str) -> tuple[str, str]:
    """Split '789 MISSION BLVD' into ('789', 'MISSION BLVD'). Number may be empty."""
    parts = street.strip().split(None, 1)
    if not parts:
        return ('', '')
    if parts[0].replace('-', '').isdigit() or _looks_like_house_num(parts[0]):
        number = parts[0]
        rest = parts[1] if len(parts) > 1 else ''
        return (number, rest)
    return ('', street.strip())


def _looks_like_house_num(token: str) -> bool:
    """Some addresses use forms like '4080A' or '23-19'. Permit alphanumeric leads with a digit."""
    if not token:
        return False
    return any(c.isdigit() for c in token) and len(token) <= 8


def street_name_only(address: str) -> str:
    """Return the street part with any leading house number stripped.

    >>> street_name_only('789 MISSION BLVD, San Diego, CA 92154')
    'MISSION BLVD'
    """
    _, name = split_street_parts(extract_street(address))
    return name.strip()


_CONTROL_BYTES = re.compile(r'[\x00-\x1f\x7f-\x9f]')


def sanitize_input(value: str, max_len: int = 200) -> str:
    """Strip control bytes, collapse whitespace, truncate.

    Mitigates injection via Unicode controls / null bytes / oversized inputs
    before the value reaches the fuzzy matcher or downstream tools.
    """
    if not isinstance(value, str):
        return ''
    cleaned = _CONTROL_BYTES.sub('', value)
    cleaned = ' '.join(cleaned.split())  # collapse whitespace runs
    return cleaned[:max_len]


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


def _street_matches(claim_street_name: str, real_address: str) -> bool:
    """Compare claimant's street-name guess against the real street name (no number).

    Both sides are normalized: lower-cased, leading house number stripped.
    Tolerates partial matches (e.g. "Mission" vs "Mission Blvd").
    """
    claim = (claim_street_name or '').lower().strip()
    if not claim:
        return False
    real_name = street_name_only(real_address).lower().strip()
    if not real_name:
        return False
    if claim == real_name:
        return True
    # Word-overlap tolerance — handle "mission blvd" vs "mission boulevard"
    real_words = [w for w in real_name.split() if len(w) >= 4 and not w.isdigit()]
    return any(w in claim or claim in w for w in real_words)


def _number_matches(claim_number: str, real_address: str) -> bool:
    """Compare claimant's house-number guess against the real number."""
    claim = (claim_number or '').strip()
    if not claim:
        return False
    real_num, _ = split_street_parts(extract_street(real_address))
    if not real_num:
        return False
    return claim.lower().replace('-', '') == real_num.lower().replace('-', '')


def lookup(name: str, street: str = '', number: str = '') -> str:
    """Two-step verified refund lookup.

    Verification flow:
      step 1 (no street, no number): identity quiz with decoy street options
      step 2 (street provided, no number): verify street matches, then ask for number
      step 3 (street + number): verify both, return refunds without `address` field

    Output never includes the real address. The caller (chat handler) is
    expected to track failed-attempt counts per session and stop calling
    once locked.
    """
    name = sanitize_input(name)
    street = sanitize_input(street, max_len=80)
    number = sanitize_input(number, max_len=20)

    best_name, records, score = find_best_match(name)
    if not best_name:
        return json.dumps({
            'no_match': True,
            'message': (
                f"We found no refunds for {name}. "
                "You may have no refunds, or your refund may have passed its claim deadline."
            ),
        })

    addresses = sorted(set(r.get('address', '') for r in records))

    # Disambiguation when multiple records share the name (different addresses).
    if len(addresses) > 1 and not street:
        street_options = [street_name_only(a) for a in addresses]
        return json.dumps({
            'disambiguation_needed': True,
            'name': best_name,
            'street_options': street_options,
            'message': f"We found multiple people named {best_name}. Which street have you lived on?",
        })

    # Filter to the matching record(s) once we have a street hint.
    if len(addresses) > 1 and street:
        matches = [r for r in records if _street_matches(street, r.get('address', ''))]
        if not matches:
            return json.dumps({
                'verification_failed': True,
                'message': (
                    "That doesn't match our records. For security, we cannot proceed. "
                    "Please contact the Auditor-Controller's office at (951) 955-3800."
                ),
            })
        records = matches

    real_address = records[0].get('address', '')
    if not real_address:
        return json.dumps({
            'verification_failed': True,
            'message': "We can't verify identity for this record. Please contact the office.",
        })

    # Step 1 — present the decoy quiz, ask which street.
    if not street:
        real_street_name = street_name_only(real_address)
        decoy_addrs = generate_decoy_streets(real_address, count=3)
        options = [real_street_name] + [street_name_only(a) for a in decoy_addrs]
        random.shuffle(options)
        return json.dumps({
            'address_verification': 'street',
            'name': best_name,
            'street_options': options,
            'message': "To verify your identity, which of the following streets have you currently or previously lived on?",
        })

    # Step 2 — street picked; verify it matches.
    if not _street_matches(street, real_address):
        return json.dumps({
            'verification_failed': True,
            'message': (
                "That doesn't match our records. For security, we cannot proceed. "
                "Please contact the Auditor-Controller's office at (951) 955-3800."
            ),
        })

    # Step 2b — street ok, ask for the house number.
    if not number:
        return json.dumps({
            'address_verification': 'number',
            'name': best_name,
            'street_picked': street_name_only(real_address),
            'message': (
                f"Thanks. To complete verification, what's the street number on "
                f"{street_name_only(real_address)}?"
            ),
        })

    # Step 3 — verify number too.
    if not _number_matches(number, real_address):
        return json.dumps({
            'verification_failed': True,
            'message': (
                "That doesn't match our records. For security, we cannot proceed. "
                "Please contact the Auditor-Controller's office at (951) 955-3800."
            ),
        })

    # Verification passed — return refunds WITHOUT address.
    results = []
    for r in records:
        results.append({
            'name': r['name'],
            'refund_type': r['refund_type'],
            'amount': f"${r['amount']:,.2f}",
            'claim_deadline': r['claim_deadline'],
        })

    # `confidence` reflects the name-match strength, not the address verification.
    # 'high' = exact / alias hit. 'low' = above threshold but with name-level
    # edits — the dashboard surfaces low-confidence claims for extra-doc review.
    confidence = 'high' if score >= HIGH_CONFIDENCE_THRESHOLD else 'low'
    portal_url = build_portal_url(records, confidence)
    return json.dumps({
        'refunds': results,
        'portal_url': portal_url,
        'confidence': confidence,
        'match_score': round(score, 3),
    })


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Sync invocation. Payload: `{customer_name, customer_street?, customer_number?}`.

    Backwards-compat: a `customer_address` field maps to `customer_street`.
    """
    logger.info("Event: %s", json.dumps({k: v for k, v in event.items() if k != 'customer_number'}, default=str))
    try:
        name = (event.get('customer_name') or '').strip()
        if not name:
            return {'result': json.dumps({'error': 'customer_name is required'})}
        street = (event.get('customer_street') or event.get('customer_address') or '').strip()
        number = (event.get('customer_number') or '').strip()
        return {'result': lookup(name, street, number)}
    except Exception:
        logger.exception("Unhandled error")
        return {'result': json.dumps({'error': 'An error occurred. Please try again.'})}
