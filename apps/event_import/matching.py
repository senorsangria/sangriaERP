"""
Matching engine for the historical event import tool.

Matches a CSV row (distributor, location, address, city) against the
accounts database using fuzzy string matching via rapidfuzz.
"""
import re

from rapidfuzz import fuzz


def normalize_for_match(s: str) -> str:
    """
    Normalize a string for fuzzy matching.

    Steps:
    - Uppercase
    - Strip leading/trailing whitespace
    - Remove punctuation: . , ' -
    - Collapse multiple spaces to one

    Deliberately does NOT expand street abbreviations so that the same
    transform applies equally to name matching and address matching.
    """
    if not s:
        return ''
    s = s.upper().strip()
    s = re.sub(r"[.,'\-]", '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def match_csv_row(row: dict, accounts_by_distributor: dict) -> dict:
    """
    Attempt to match a CSV row to an existing Account.

    Args:
        row: dict with keys: distributor, location, address, city
        accounts_by_distributor: dict mapping normalized distributor name
            (strip + title case) → list of account dicts with keys:
            pk, name, street, city, name_normalized, street_normalized,
            city_normalized

    Returns a dict:
        {
            'status':     'high' | 'review' | 'none',
            'match':      account dict or None,
            'score':      float (0–100),
            'candidates': list of top 3 account dicts with scores,
        }

    Matching logic:
    1. Normalize distributor name (strip + title case)
    2. Look up accounts for that distributor
    3. If no accounts found: status='none', score=0
    4. Score each candidate:
         name_score = token_sort_ratio(location, account name)   × 0.6
         addr_score = token_sort_ratio(address,  account street) × 0.3
         city_score = token_sort_ratio(city,     account city)   × 0.1
    5. Sort descending, take top 3
    6. Best score ≥ 85  → status='high',   match=top candidate
       Best score 50–84 → status='review', match=None
       Best score < 50  → status='none',   match=None
    """
    dist_key = row.get('distributor', '').strip().title()
    candidates_raw = accounts_by_distributor.get(dist_key, [])

    if not candidates_raw:
        return {
            'status': 'none',
            'match': None,
            'score': 0.0,
            'candidates': [],
        }

    csv_name = normalize_for_match(row.get('location', ''))
    csv_addr = normalize_for_match(row.get('address', ''))
    csv_city = normalize_for_match(row.get('city', ''))

    scored = []
    for acct in candidates_raw:
        name_score = fuzz.token_sort_ratio(
            csv_name, normalize_for_match(acct.get('name', ''))
        )
        addr_score = fuzz.token_sort_ratio(
            csv_addr, normalize_for_match(acct.get('street', ''))
        )
        city_score = fuzz.token_sort_ratio(
            csv_city, normalize_for_match(acct.get('city', ''))
        )
        combined = (name_score * 0.6) + (addr_score * 0.3) + (city_score * 0.1)
        scored.append({**acct, 'score': round(combined, 2)})

    scored.sort(key=lambda x: x['score'], reverse=True)
    top3 = scored[:3]
    best_score = top3[0]['score'] if top3 else 0.0

    if best_score >= 85:
        status = 'high'
        match = top3[0]
    elif best_score >= 50:
        status = 'review'
        match = None
    else:
        status = 'none'
        match = None

    return {
        'status': status,
        'match': match,
        'score': best_score,
        'candidates': top3,
    }
