"""
Production Inventory Forecast — Phase D.

Public API: compute_production_forecast(company, today=None, production_po_additions=None)
"""
from django.db.models import Sum

from apps.catalog.models import Item
from apps.distribution.models import DistributorPOLine
from apps.production.models import OwnInventorySnapshot
from apps.reports.utils import _month_add


MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _format_inventory(value):
    """Display inventory as integer if whole, otherwise 2 decimal places."""
    if value is None:
        return '—'
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f'{rounded:.2f}'


def _cell_status(inv, safety_stock):
    """Return color status string. Zero inventory is red (out of stock)."""
    if inv <= 0:
        return 'red'
    if safety_stock is not None and inv < safety_stock:
        return 'yellow'
    return 'green'


def compute_production_forecast(company, today=None, production_po_additions=None):
    """
    Compute a 13-month ending-inventory forecast for the company's own inventory.

    First column is the anchor month (most recent snapshot date company-wide).
    Columns 2–13 are 12 months of forward projection.

    Depletion source: sum of DistributorPOLine.quantity_cases for that (item, year, month),
    aggregated across all distributors in the company.

    production_po_additions: optional dict {(item_id, year, month): total_cases_float}
    applied at the start of each projection month before depletion.

    For items whose most recent snapshot is BEFORE the anchor month, demand is walked
    forward from the snapshot month to the anchor to estimate the current running value.

    Returns:
    {
        'message': str,                 # non-empty when no data (empty state)
        'horizon': [                    # 13 dicts: anchor + 12 projection months
            {'year', 'month', 'month_short', 'is_snapshot'},
        ],
        'year_spans': [{'year', 'colspan'}],
        'anchor_year': int | None,
        'anchor_month': int | None,
        'rows': [
            {
                'item': Item,
                'monthly_data': [       # 13 dicts, one per horizon column
                    {'year', 'month', 'inventory', 'inventory_display',
                     'depletion', 'status', 'is_snapshot'},
                ],
            },
        ],
        'demand_by_month': {str: float},   # keys like '2026-05', values total cases
        'safety_stock_map': {item_id: int | None},
    }
    """
    # Step 1 — most recent snapshot date across all items for this company
    most_recent = (
        OwnInventorySnapshot.objects
        .filter(company=company)
        .order_by('-year', '-month')
        .first()
    )
    if most_recent is None:
        return {
            'message': 'No inventory snapshots yet. Enter a snapshot to enable production forecasting.',
            'horizon': [],
            'year_spans': [],
            'anchor_year': None,
            'anchor_month': None,
            'rows': [],
            'demand_by_month': {},
            'safety_stock_map': {},
        }

    anchor_year, anchor_month = most_recent.year, most_recent.month

    # Step 2 — horizon: anchor (snapshot month) + 12 projection months = 13 total
    horizon = [{
        'year': anchor_year,
        'month': anchor_month,
        'month_short': MONTH_SHORT[anchor_month - 1],
        'is_snapshot': True,
    }]
    next_year, next_month = _month_add(anchor_year, anchor_month, 1)
    for i in range(12):
        y, m = _month_add(next_year, next_month, i)
        horizon.append({
            'year': y, 'month': m,
            'month_short': MONTH_SHORT[m - 1],
            'is_snapshot': False,
        })

    # Step 3 — year spans for the two-row table header
    year_spans = []
    for cell in horizon:
        if year_spans and year_spans[-1]['year'] == cell['year']:
            year_spans[-1]['colspan'] += 1
        else:
            year_spans.append({'year': cell['year'], 'colspan': 1})

    # Step 4 — active items for this company
    items = list(
        Item.objects.filter(brand__company=company, is_active=True)
        .select_related('brand')
        .order_by('brand__name', 'sort_order', 'name')
    )

    safety_stock_map = {item.pk: item.production_safety_stock_cases for item in items}

    # Step 5 — most-recent snapshot per item (Python dedup, newest-first ordering)
    latest_own_snapshots = {}
    for s in OwnInventorySnapshot.objects.filter(company=company).order_by('-year', '-month'):
        if s.item_id not in latest_own_snapshots:
            latest_own_snapshots[s.item_id] = s

    # Step 6 — aggregate all distributor PO demand for this company
    demand_rows = (
        DistributorPOLine.objects
        .filter(po__distributor__company=company)
        .values('item_id', 'po__year', 'po__month')
        .annotate(total_cases=Sum('quantity_cases'))
    )
    demand_map = {
        (r['item_id'], r['po__year'], r['po__month']): float(r['total_cases'])
        for r in demand_rows
    }

    items_with_demand = {item_id for (item_id, _y, _m) in demand_map}

    # Step 7 — build one forecast row per item
    rows = []
    for item in items:
        snap = latest_own_snapshots.get(item.pk)
        has_any_data = snap is not None or item.pk in items_with_demand

        if not has_any_data:
            monthly_data = [
                {
                    'year': cell['year'], 'month': cell['month'],
                    'inventory': None, 'inventory_display': '—',
                    'depletion': None,
                    'status': 'no_data',
                    'is_snapshot': cell['is_snapshot'],
                }
                for cell in horizon
            ]
            rows.append({'item': item, 'monthly_data': monthly_data})
            continue

        # Baseline running inventory from item's most recent snapshot (or 0)
        if snap is not None:
            running = float(snap.quantity_cases)
            snap_year, snap_month = snap.year, snap.month
        else:
            running = 0.0
            snap_year, snap_month = None, None

        # If the item's snapshot is BEFORE the anchor month, walk demand forward
        # from one month after the snapshot through the anchor month, inclusive.
        if snap is not None and (snap_year, snap_month) != (anchor_year, anchor_month):
            walk_year, walk_month = _month_add(snap_year, snap_month, 1)
            while (walk_year, walk_month) <= (anchor_year, anchor_month):
                running -= demand_map.get((item.pk, walk_year, walk_month), 0.0)
                walk_year, walk_month = _month_add(walk_year, walk_month, 1)

        # Build cell data
        monthly_data = []
        for cell in horizon:
            y, m = cell['year'], cell['month']

            if cell['is_snapshot']:
                # Anchor column: only show 'snapshot' if item has a snapshot for this month
                has_anchor_snap = (
                    snap is not None
                    and snap.year == anchor_year
                    and snap.month == anchor_month
                )
                if has_anchor_snap:
                    monthly_data.append({
                        'year': y, 'month': m,
                        'inventory': running,
                        'inventory_display': _format_inventory(running),
                        'depletion': None,
                        'status': 'snapshot',
                        'is_snapshot': True,
                    })
                else:
                    monthly_data.append({
                        'year': y, 'month': m,
                        'inventory': None, 'inventory_display': '—',
                        'depletion': None,
                        'status': 'no_data',
                        'is_snapshot': True,
                    })
            else:
                # Projection month — production PO cases arrive before depletion
                production_adds = (production_po_additions or {}).get((item.pk, y, m), 0.0)
                running += production_adds
                depletion = demand_map.get((item.pk, y, m), 0.0)
                running -= depletion
                inv = round(running, 2)
                ss = safety_stock_map.get(item.pk)
                monthly_data.append({
                    'year': y, 'month': m,
                    'inventory': inv,
                    'inventory_display': _format_inventory(inv),
                    'depletion': depletion,
                    'status': _cell_status(inv, ss),
                    'is_snapshot': False,
                })

        rows.append({'item': item, 'monthly_data': monthly_data})

    # Step 8 — demand row totals per month (string key "YYYY-MM" for template)
    demand_by_month = {}
    for cell in horizon:
        y, m = cell['year'], cell['month']
        key = f'{y}-{m:02d}'
        total = sum(
            demand_map.get((item.pk, y, m), 0.0)
            for item in items
        )
        demand_by_month[key] = total

    return {
        'message': '',
        'horizon': horizon,
        'year_spans': year_spans,
        'anchor_year': anchor_year,
        'anchor_month': anchor_month,
        'rows': rows,
        'demand_by_month': demand_by_month,
        'safety_stock_map': safety_stock_map,
    }
