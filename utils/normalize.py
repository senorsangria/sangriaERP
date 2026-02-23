"""
Address normalization utility for productERP.

This is the single source of truth for address normalization.
Import and use normalize_address wherever normalization is needed.
Do NOT duplicate this logic elsewhere.

Usage:
    from utils.normalize import normalize_address

    normalized = normalize_address("123 Main Street")
    # → "123 MAIN ST"
"""

import re

# Map full word forms to standard abbreviations.
# Uses word boundaries (\b) to prevent partial matches
# (e.g. "COURTNEY" stays "COURTNEY", not "CTNEY").
_STREET_ABBREVIATIONS = [
    (r'\bSTREET\b', 'ST'),
    (r'\bAVENUE\b', 'AVE'),
    (r'\bBOULEVARD\b', 'BLVD'),
    (r'\bDRIVE\b', 'DR'),
    (r'\bROAD\b', 'RD'),
    (r'\bLANE\b', 'LN'),
    (r'\bCOURT\b', 'CT'),
    (r'\bPLACE\b', 'PL'),
]


def normalize_address(value: str) -> str:
    """
    Normalize an address string for consistent matching.

    Steps applied in order:
    1. Convert to uppercase
    2. Strip leading/trailing whitespace
    3. Remove punctuation (periods and commas)
    4. Standardize street-type abbreviations (STREET→ST, AVENUE→AVE, etc.)
    5. Collapse multiple consecutive spaces to a single space

    Returns the normalized string. Returns empty string for None or empty input.
    """
    if not value:
        return ''

    value = value.upper().strip()
    value = re.sub(r'[.,]', '', value)

    for pattern, replacement in _STREET_ABBREVIATIONS:
        value = re.sub(pattern, replacement, value)

    value = re.sub(r'\s+', ' ', value).strip()

    return value
