"""
Matching engine for the historical event import tool.

Matches a CSV row (distributor, location, address, city) against the
accounts database using fuzzy string matching via rapidfuzz.
"""
import re

from rapidfuzz import fuzz

# Confidence thresholds
HIGH_THRESHOLD   = 75   # ≥ 75 → auto-accepted (lowered from 80; street number
                         #         boost of +10 makes genuinely correct matches
                         #         score 85+ while wrong matches stay below 75)
REVIEW_THRESHOLD = 50   # 50–79 → needs user review

# Common abbreviations found in CSV location names that may differ from
# the full form stored in the accounts database.
ABBREVIATION_MAP = [
    (r'\bW\s*&\s*L\b',   'WINE AND LIQUOR'),
    (r'\bW\s*&\s*S\b',   'WINE AND SPIRITS'),
    (r'\bB\s*&\s*W\b',   'BEER AND WINE'),
    (r'\bLIQ\b',         'LIQUOR'),
    (r'\bSPIRITS\b',     'SPIRITS'),  # handles Spirts typo via fuzzy
    (r'\bMKT\b',         'MARKET'),
    (r'\bSQ\b',          'SQUARE'),
    (r'\bSTS\b',         'SPIRITS'),
]


def normalize_for_match(s: str) -> str:
    """
    Normalize a string for fuzzy matching.

    Steps:
    - Uppercase
    - Strip leading/trailing whitespace
    - Remove punctuation: . , ' -  and apostrophes
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


def _expand_abbreviations(s: str) -> str:
    """
    Expand common abbreviations in a normalized CSV location name.

    Applied to CSV location names only — never to account names from
    the database.
    """
    for pattern, replacement in ABBREVIATION_MAP:
        s = re.sub(pattern, replacement, s, flags=re.IGNORECASE)
    return s


def _strip_city(name: str, city: str) -> str:
    """
    Remove the city name from the start or end of a location name
    before fuzzy comparison.

    Examples:
      _strip_city('BOURBON ST WINE SPIRITS ASBURY', 'ASBURY')
        → 'BOURBON ST WINE SPIRITS'
      _strip_city('PRINCETON MCCAFFREYS', 'PRINCETON')
        → 'MCCAFFREYS'
      _strip_city('SHOPRITE BYRAM', 'STANHOPE')
        → 'SHOPRITE BYRAM'  (city not found, unchanged)
    """
    if not city:
        return name
    city_norm = normalize_for_match(city)
    name_norm = name  # already normalized by caller

    # Strip from end
    if name_norm.endswith(' ' + city_norm):
        name_norm = name_norm[:-len(' ' + city_norm)].strip()
    # Strip from start
    elif name_norm.startswith(city_norm + ' '):
        name_norm = name_norm[len(city_norm + ' '):].strip()
    return name_norm


def _strip_trailing_single_letter(name: str) -> str:
    """
    Strip a trailing single uppercase letter from a normalized account name.

    Some account names in the database carry a trailing route/category
    letter suffix (e.g. "JIMMY S LIQUORS B", "SAJOMA LIQUOR INC R") that
    does not appear in the CSV location name. Stripping it before comparison
    prevents the suffix from dragging down the fuzzy score.

    Only strips when the final token is exactly one letter preceded by a
    space — e.g. " B" at end. Does not affect names where the last token
    happens to be a meaningful single letter (those are rare, and removing
    them in that edge case does not materially hurt matching).

    Applied to account names only — never to CSV location names.
    """
    return re.sub(r' [A-Z]$', '', name)


def _extract_street_number(address: str) -> str:
    """
    Extract the leading street number from an address string.
    Returns the number as a string, or '' if none found.

    Examples:
      '1179 St Georges Ave' → '1179'
      '90-70 Rt 206'        → '90'   (first numeric group only)
      '39-05 104TH ST'      → '39'
      ''                    → ''
    """
    m = re.match(r'^(\d+)', address.strip())
    return m.group(1) if m else ''


def match_csv_row(row: dict, accounts_by_distributor: dict) -> dict:
    """
    Attempt to match a CSV row to an existing Account.

    Args:
        row: dict with keys: distributor, location, address, city
        accounts_by_distributor: dict mapping normalized distributor name
            (strip + title case) → list of account dicts with keys:
            pk, name, street, city

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
         csv_name   = normalize → expand abbreviations → strip city
         acct_name  = normalize → strip trailing single letter → strip city
         name_score = token_sort_ratio(csv_name, acct_name)            × 0.6
         addr_score = token_sort_ratio(address,  account street)        × 0.3
         city_score = token_sort_ratio(city,     account city)          × 0.1
         combined   = weighted sum above
         → if csv street number and account street number both present
           and match exactly: combined = min(100, combined + 10)
    5. Sort by combined score descending, take top 3
    6. Best score ≥ HIGH_THRESHOLD   → status='high',   match=top candidate
       Best score ≥ REVIEW_THRESHOLD → status='review', match=None
       Best score < REVIEW_THRESHOLD → status='none',   match=None
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

    csv_city = normalize_for_match(row.get('city', ''))

    # CSV location: normalize → expand abbreviations → strip city
    csv_name_raw = normalize_for_match(row.get('location', ''))
    csv_name_raw = _expand_abbreviations(csv_name_raw)
    csv_name = _strip_city(csv_name_raw, csv_city)

    csv_addr = normalize_for_match(row.get('address', ''))
    csv_num  = _extract_street_number(csv_addr)

    scored = []
    for acct in candidates_raw:
        # Account name: normalize → strip trailing letter → strip city
        acct_name = _strip_trailing_single_letter(
            normalize_for_match(acct.get('name', ''))
        )
        acct_name = _strip_city(acct_name, csv_city)
        acct_street = normalize_for_match(acct.get('street', ''))

        name_score = fuzz.token_sort_ratio(csv_name, acct_name)
        addr_score = fuzz.token_sort_ratio(csv_addr, acct_street)
        city_score = fuzz.token_sort_ratio(
            csv_city, normalize_for_match(acct.get('city', ''))
        )
        combined = (name_score * 0.6) + (addr_score * 0.3) + (city_score * 0.1)

        # Street number boost: matching street numbers are strong evidence
        # of a correct match and rarely coincide by accident.
        cand_num = _extract_street_number(acct_street)
        if csv_num and cand_num and csv_num == cand_num:
            combined = min(100.0, combined + 10)

        scored.append({**acct, 'score': round(combined, 2)})

    scored.sort(key=lambda x: x['score'], reverse=True)
    top3 = scored[:3]
    best_score = top3[0]['score'] if top3 else 0.0

    if best_score >= HIGH_THRESHOLD:
        status = 'high'
        match = top3[0]
    elif best_score >= REVIEW_THRESHOLD:
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
