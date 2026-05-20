"""
Smart matching module for resolving unmapped item codes during CSV uploads.

Public API:
  batch_find_best_matches(company, distributor, raw_codes) -> {raw_code: match | None}
  build_candidate_list(raw_code, all_items, other_mappings_by_code) -> [match, ...]

Priority order:
  1. Existing ItemMapping at another distributor in the same company (high)
  2. Exact item_code match, case-insensitive (medium)
  3. Substring match on item_code (low — not pre-filled)
  4. Word match on item name (low — not pre-filled)

Priorities 3 and 4 are surfaced via build_candidate_list for dropdown hints only;
_find_priority_match returns None for them so they are never pre-selected.
"""
import re

from apps.catalog.models import Item
from apps.imports.models import ItemMapping


def batch_find_best_matches(company, distributor, raw_codes):
    """
    Return {raw_code: {'item': Item, 'confidence': 'high'|'medium', 'reason': str} | None}
    for every code in raw_codes.

    Uses exactly 2 DB queries regardless of batch size:
      1. Existing MAPPED ItemMappings at other distributors for these codes
      2. All active Items for the company
    """
    raw_codes = list(set(raw_codes))

    # Query 1: existing mappings at other distributors for these exact codes
    other_mappings = list(
        ItemMapping.objects.filter(
            company=company,
            raw_item_name__in=raw_codes,
            status=ItemMapping.Status.MAPPED,
            mapped_item__isnull=False,
        )
        .exclude(distributor=distributor)
        .select_related('mapped_item', 'distributor')
    )

    # Keep first hit per code (stable: whichever Django returns first)
    other_mappings_by_code = {}
    for m in other_mappings:
        if m.raw_item_name not in other_mappings_by_code:
            other_mappings_by_code[m.raw_item_name] = m

    # Query 2: all active items for the company
    all_items = list(
        Item.objects.filter(brand__company=company, is_active=True)
        .select_related('brand')
    )

    # Build case-insensitive item_code lookup
    items_by_code = {}
    for item in all_items:
        if item.item_code:
            key = item.item_code.lower()
            items_by_code.setdefault(key, []).append(item)

    results = {}
    for raw_code in raw_codes:
        results[raw_code] = _find_priority_match(
            raw_code, other_mappings_by_code, items_by_code
        )

    return results


def _find_priority_match(raw_code, other_mappings_by_code, items_by_code):
    """
    Return the single best match for raw_code at high or medium confidence, or None.

    Priority 1 and 2 are pre-fillable; 3 and 4 are handled by build_candidate_list.
    """
    raw_lower = raw_code.lower()

    # Priority 1: existing mapping at another distributor
    if raw_code in other_mappings_by_code:
        m = other_mappings_by_code[raw_code]
        return {
            'item': m.mapped_item,
            'confidence': 'high',
            'reason': f'Mapped at {m.distributor.name}',
        }

    # Priority 2: exact item_code match (case-insensitive)
    if raw_lower in items_by_code:
        item = items_by_code[raw_lower][0]
        return {
            'item': item,
            'confidence': 'medium',
            'reason': 'Exact item code match',
        }

    return None


def build_candidate_list(raw_code, all_items, other_mappings_by_code):
    """
    Return low-confidence candidate items (Priority 3 & 4) for dropdown hints.
    Does not include Priority 1 or 2 matches (already handled by _find_priority_match).

    Returns list of {'item': Item, 'confidence': 'low', 'reason': str}.
    """
    raw_lower = raw_code.lower()
    tokens = [t.lower() for t in re.split(r'[^A-Za-z0-9]+', raw_code) if t]

    # Collect pks already covered by higher priorities
    seen_pks = set()
    if raw_code in other_mappings_by_code:
        seen_pks.add(other_mappings_by_code[raw_code].mapped_item_id)
    for item in all_items:
        if item.item_code and item.item_code.lower() == raw_lower:
            seen_pks.add(item.pk)

    candidates = []
    for item in all_items:
        if item.pk in seen_pks:
            continue

        item_code_lower = (item.item_code or '').lower()
        item_name_lower = item.name.lower()

        # Priority 3: substring match on item_code
        if item_code_lower and (raw_lower in item_code_lower or item_code_lower in raw_lower):
            candidates.append({
                'item': item,
                'confidence': 'low',
                'reason': 'Partial code match',
            })
            seen_pks.add(item.pk)
            continue

        # Priority 4: word match on item name
        if tokens and all(t in item_name_lower for t in tokens):
            candidates.append({
                'item': item,
                'confidence': 'low',
                'reason': 'Name partially matches',
            })
            seen_pks.add(item.pk)

    return candidates
