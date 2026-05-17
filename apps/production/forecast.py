"""
Production Inventory Forecast — Phase D1.

Public API: compute_production_forecast(company, today=None, production_po_additions=None)

Algorithm (Phase D1):
- Fetch ALL OwnInventorySnapshots for the company; build two structures:
    item_snapshots_map: {item_id: [snap, ...] ASC by year/month}
    snapshot_lookup:    {(item_id, year, month): snap}
- Anchor month = oldest of each item's most-recent snapshot date
    (min across items of each item's max snapshot year/month)
- Per item: walk forward from earliest snapshot (or from anchor if no snapshot)
    applying production PO additions THEN depletion at each step.
    When a snapshot exists for the item in a given month, its value REPLACES
    the calculated running total (snapshots are source of truth).
- Anchor column (is_snapshot=True): shows 'snapshot' if item has a snapshot there;
    shows calculated green/yellow/red if item has an earlier snapshot that covers
    the anchor month; shows no_data if item has no snapshots at all.
- Mid-horizon snapshot months: status='snapshot' (blue) regardless of value.
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


def _no_data_cell(year, month, is_snapshot):
    return {
        'year': year, 'month': month,
        'inventory': None, 'inventory_display': '—',
        'depletion': None, 'status': 'no_data',
        'is_snapshot': is_snapshot,
    }


def compute_production_forecast(company, today=None, production_po_additions=None):
    """
    Compute a 13-month ending-inventory forecast for the company's own inventory.

    First column is the anchor month (oldest of each item's most-recent snapshot date).
    Columns 2–13 are 12 months of forward projection.

    Depletion source: sum of DistributorPOLine.quantity_cases for that (item, year, month),
    aggregated across all distributors in the company.

    production_po_additions: optional dict {(item_id, year, month): total_cases_float}
    applied at the start of each month before depletion (including pre-anchor walk months).

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
    # Step 1 — fetch all snapshots; build per-item list (ASC) and O(1) override lookup
    item_snapshots_map = {}  # {item_id: [snap, ...] sorted ASC}
    snapshot_lookup = {}     # {(item_id, year, month): snap}
    for s in OwnInventorySnapshot.objects.filter(company=company).order_by('year', 'month'):
        item_snapshots_map.setdefault(s.item_id, []).append(s)
        snapshot_lookup[(s.item_id, s.year, s.month)] = s

    # Step 2 — anchor month = oldest of each item's most-recent snapshot
    item_most_recent = {}
    for item_id, snaps in item_snapshots_map.items():
        last = snaps[-1]  # ASC order → last = most recent
        item_most_recent[item_id] = (last.year, last.month)

    if not item_most_recent:
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

    anchor_year, anchor_month = min(item_most_recent.values())

    # Step 3 — horizon: anchor (snapshot month) + 12 projection months = 13 total
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

    # Step 4 — year spans for the two-row table header
    year_spans = []
    for cell in horizon:
        if year_spans and year_spans[-1]['year'] == cell['year']:
            year_spans[-1]['colspan'] += 1
        else:
            year_spans.append({'year': cell['year'], 'colspan': 1})

    # Step 5 — active items for this company
    items = list(
        Item.objects.filter(brand__company=company, is_active=True)
        .select_related('brand')
        .order_by('brand__name', 'sort_order', 'name')
    )

    safety_stock_map = {item.pk: item.production_safety_stock_cases for item in items}

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

    horizon_end_year  = horizon[-1]['year']
    horizon_end_month = horizon[-1]['month']

    # Step 7 — build one forecast row per item using unified snapshot-override walk
    rows = []
    for item in items:
        item_snaps = item_snapshots_map.get(item.pk, [])
        has_demand  = item.pk in items_with_demand

        # Items with no snapshots and no demand: all cells no_data
        if not item_snaps and not has_demand:
            rows.append({'item': item, 'monthly_data': [
                _no_data_cell(c['year'], c['month'], c['is_snapshot'])
                for c in horizon
            ]})
            continue

        # Determine walk starting point and initial running value
        if item_snaps:
            earliest = item_snaps[0]
            walk_year, walk_month = earliest.year, earliest.month
            running = float(earliest.quantity_cases)
        else:
            # No snapshot but has demand: start projecting from 0 at the anchor month
            walk_year, walk_month = anchor_year, anchor_month
            running = 0.0

        # Walk forward from starting point through end of horizon.
        # At each step: apply production PO additions → apply demand → apply snapshot override.
        # cell_value_at stores the computed result for every walked month.
        cell_value_at = {}
        ss = safety_stock_map.get(item.pk)

        while (walk_year, walk_month) <= (horizon_end_year, horizon_end_month):
            production_adds = (production_po_additions or {}).get((item.pk, walk_year, walk_month), 0.0)
            running += production_adds
            depletion = demand_map.get((item.pk, walk_year, walk_month), 0.0)
            running -= depletion

            snap = snapshot_lookup.get((item.pk, walk_year, walk_month))
            if snap is not None:
                # Snapshot REPLACES the calculated running value
                running = float(snap.quantity_cases)
                status = 'snapshot'
            else:
                status = _cell_status(round(running, 2), ss)

            cell_value_at[(walk_year, walk_month)] = {
                'inventory': round(running, 2),
                'status': status,
                'depletion': depletion,
            }
            walk_year, walk_month = _month_add(walk_year, walk_month, 1)

        # Build monthly_data in horizon order from cell_value_at
        monthly_data = []
        has_any_snapshot = bool(item_snaps)

        for h_cell in horizon:
            y, m = h_cell['year'], h_cell['month']
            key = (y, m)

            if key not in cell_value_at:
                # Walk didn't reach this month (item's earliest snap is after this month)
                monthly_data.append(_no_data_cell(y, m, h_cell['is_snapshot']))
                continue

            v = cell_value_at[key]

            if h_cell['is_snapshot'] and not has_any_snapshot:
                # Item has no real snapshots: anchor column shows no_data (not a calculated red)
                monthly_data.append(_no_data_cell(y, m, True))
                continue

            monthly_data.append({
                'year': y, 'month': m,
                'inventory': v['inventory'],
                'inventory_display': _format_inventory(v['inventory']),
                'depletion': v['depletion'],
                'status': v['status'],
                'is_snapshot': h_cell['is_snapshot'],
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
