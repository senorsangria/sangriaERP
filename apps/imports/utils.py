"""
Shared utilities for the imports app.
"""
from apps.distribution.models import Distributor


def _resolve_distributors(rows, company):
    """
    Validate and resolve distributor names from parsed CSV rows.

    Returns (resolved_rows, errors).
    - resolved_rows: same list with 'distributor' (Distributor object) and
      'distributor_pk' (int) added to each row.
    - errors: list of error message strings (empty = all valid).

    One DB query regardless of CSV size.  Matching is case-insensitive.
    inactive distributors are treated as unknown (hard abort).
    A blank/missing distributor is also a hard abort: every row must name a
    distributor (Account.distributor is required), so a blank cell is reported
    as a clean validation error rather than silently producing a
    null-distributor account.
    """
    raw_names = [(row.get('distributor_name') or '').strip() for row in rows]

    # Blank distributor → hard abort (mirrors the unknown-name handling below).
    blank_count = sum(1 for n in raw_names if not n)
    if blank_count:
        error = (
            f'Import aborted. {blank_count} row(s) have a blank distributor. '
            'Every row must specify a distributor in the "Distributors" column. '
            'Fill in the missing distributor name(s) and re-upload.'
        )
        return rows, [error]

    unique_names = set(raw_names)

    distributor_map = {
        d.name.lower(): d
        for d in Distributor.objects.filter(company=company, is_active=True)
    }

    unknown_names = sorted(
        name for name in unique_names if name.lower() not in distributor_map
    )
    if unknown_names:
        error = (
            'Import aborted. The following distributor name(s) from the CSV are not '
            'recognized: '
            + ', '.join(f'"{n}"' for n in unknown_names)
            + '. Check the spelling against your active distributor list and re-upload.'
        )
        return rows, [error]

    name_to_dist = {name: distributor_map[name.lower()] for name in unique_names}
    for row in rows:
        raw = (row.get('distributor_name') or '').strip()
        dist = name_to_dist.get(raw)
        row['distributor'] = dist
        row['distributor_pk'] = dist.pk if dist else None

    return rows, []
