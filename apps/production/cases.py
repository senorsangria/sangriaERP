"""
Production cases aggregation — Production Cases tab.

Aggregates ProductionPOLine quantities by (item, year, month) across the
12-month production horizon (current month + next 11), grouped by co-packer.

Data structures use nested dicts ({year: {month: value}}) so Django templates
can do two-level lookups with the existing get_item filter from apps.core.
"""
from collections import defaultdict
from datetime import date

from apps.reports.utils import _month_add


def compute_production_cases_view(company, filters):
    """
    Return a dict suitable for rendering the Production Cases tab.

    Args:
        company: Company instance (tenant scope)
        filters: dict with optional 'status' key (list of ProductionPO.Status values)

    Returns:
        {
            'horizon': [{'year': int, 'month': int, 'label': 'Jan'}, ...],  # 12 entries
            'co_packer_groups': [
                {
                    'co_packer': CoPacker,
                    'items': [
                        {
                            'item': Item,
                            'monthly_cases': {year: {month: int or None}, ...},
                            'total_cases': int,
                        },
                        ...
                    ],
                    'subtotals': {year: {month: int or None}, ...},
                },
                ...
            ],
            'grand_totals': {year: {month: int or None}, ...},
        }

    Notes:
        - Items with zero production across all 12 months are excluded.
        - Items without a co_packer are excluded.
        - Empty cells return None (template renders as em-dash).
        - 'status' filter restricts which POs feed the aggregation;
          an empty list means all statuses are included.
    """
    from apps.production.models import ProductionPOLine

    today = date.today()
    horizon = []
    for i in range(12):
        y, m = _month_add(today.year, today.month, i)
        horizon.append({'year': y, 'month': m, 'label': date(y, m, 1).strftime('%b')})

    horizon_pairs = {(h['year'], h['month']) for h in horizon}

    statuses = filters.get('status', [])

    line_qs = ProductionPOLine.objects.filter(
        po__company=company,
    ).select_related('item', 'item__co_packer', 'po')

    if statuses:
        line_qs = line_qs.filter(po__status__in=statuses)

    # DB-level pre-filter to relevant years and months; exact (year, month) tuple
    # filtering is done in Python below since SQL cannot express tuple membership.
    line_qs = line_qs.filter(
        po__year__in={y for y, _ in horizon_pairs},
        po__month__in={m for _, m in horizon_pairs},
    )

    # Aggregate cases per (co_packer, item, year, month)
    aggregates = defaultdict(int)   # {(cp_pk, item_pk, year, month): total_cases}
    item_lookup = {}
    co_packer_lookup = {}

    for line in line_qs:
        if (line.po.year, line.po.month) not in horizon_pairs:
            continue  # drop cross-year/month false positives from the DB pre-filter
        item = line.item
        co_packer = item.co_packer
        if co_packer is None:
            continue
        item_lookup[item.pk] = item
        co_packer_lookup[co_packer.pk] = co_packer
        agg_key = (co_packer.pk, item.pk, line.po.year, line.po.month)
        aggregates[agg_key] += int(round(float(line.quantity_cases)))

    # Restructure into {cp_pk: {item_pk: {year: {month: cases}}}}
    items_data = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    item_totals = defaultdict(int)   # {(cp_pk, item_pk): total across all months}

    for (cp_pk, item_pk, y, m), cases in aggregates.items():
        items_data[cp_pk][item_pk][y][m] = cases
        item_totals[(cp_pk, item_pk)] += cases

    co_packer_groups = []
    for cp_pk in sorted(co_packer_lookup, key=lambda pk: co_packer_lookup[pk].name):
        co_packer = co_packer_lookup[cp_pk]
        cp_items_data = items_data.get(cp_pk, {})

        items_for_cp = []
        for item_pk, year_month_data in cp_items_data.items():
            total = item_totals.get((cp_pk, item_pk), 0)
            if total <= 0:
                continue
            item = item_lookup[item_pk]
            # Build monthly_cases: {year: {month: cases or None}} for every horizon cell
            monthly_cases = {}
            for h in horizon:
                y, m = h['year'], h['month']
                value = year_month_data.get(y, {}).get(m)
                monthly_cases.setdefault(y, {})[m] = value if value else None
            items_for_cp.append({
                'item': item,
                'monthly_cases': monthly_cases,
                'total_cases': total,
            })

        if not items_for_cp:
            continue

        items_for_cp.sort(key=lambda x: x['item'].name)

        # Per-co-packer subtotals: {year: {month: cases or None}}
        subtotals = {}
        for h in horizon:
            y, m = h['year'], h['month']
            sub = sum(
                (r['monthly_cases'].get(y, {}).get(m) or 0)
                for r in items_for_cp
            )
            subtotals.setdefault(y, {})[m] = sub if sub > 0 else None

        co_packer_groups.append({
            'co_packer': co_packer,
            'items': items_for_cp,
            'subtotals': subtotals,
        })

    # Grand totals: {year: {month: cases or None}}
    grand_totals = {}
    for h in horizon:
        y, m = h['year'], h['month']
        gt = sum(
            (g['subtotals'].get(y, {}).get(m) or 0)
            for g in co_packer_groups
        )
        grand_totals.setdefault(y, {})[m] = gt if gt > 0 else None

    return {
        'horizon': horizon,
        'co_packer_groups': co_packer_groups,
        'grand_totals': grand_totals,
    }
