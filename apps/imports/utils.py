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
    """
    unique_names = {(row.get('distributor_name') or '').strip() for row in rows}
    unique_names.discard('')

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
