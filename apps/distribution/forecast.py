"""
Distributor Inventory Forecast — Phase 4-step-1.

Public API: compute_distributor_forecast(distributor, today=None)
"""
from datetime import date

from django.db.models import Sum

from apps.catalog.models import Item
from apps.distribution.models import DistributorItemProfile, InventorySnapshot
from apps.reports.utils import _month_add
from apps.sales.models import SalesRecord


MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _fmt_inv(val):
    """Format a numeric inventory value: integer if whole, 2 decimal places if fractional."""
    if val is None:
        return ''
    rounded = round(val, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f'{rounded:.2f}'


def _inv_status(inv, safety_stock):
    """Classify a projected inventory level into a display status string."""
    if inv <= 0:
        return 'red'
    if safety_stock is not None and inv < safety_stock:
        return 'yellow'
    return 'green'


def compute_distributor_forecast(distributor, today=None):
    """
    Compute a 12-month ending-inventory projection for one distributor.

    Horizon starts the month after the distributor's most recent snapshot date.
    Depletion source:
      - Fully-ended months (year,month < current calendar year,month):
        actual SalesRecord aggregation; 0 if no records exist that month.
      - Current month and future months: prior-year same-month sales as
        projection; cell is 'no_data' when no prior-year record exists.
      - Negative depletion (net returns) is floored to 0.
      - Negative depletion (net returns) is floored to 0 in both cases.

    Returns a dict:
    {
        'distributor': distributor,
        'message': str,         # non-empty when rows is empty / unusable
        'horizon': [            # 12-element list of month descriptors
            {'year': int, 'month': int, 'month_short': str},
            ...
        ],
        'year_spans': [         # for the two-row table header
            {'year': int, 'colspan': int},
            ...
        ],
        'rows': [               # one entry per active item
            {
                'item': Item,
                'monthly_data': [
                    {
                        'year': int,
                        'month': int,
                        'inventory': float | None,
                        'inventory_display': str,
                        'status': 'green' | 'yellow' | 'red' | 'no_data',
                        'reason': str,   # '' unless no_data
                    },
                    ...  # 12 entries
                ],
            },
            ...
        ],
    }
    """
    if today is None:
        today = date.today()

    current_year = today.year
    current_month = today.month

    _empty = {
        'distributor': distributor,
        'message': '',
        'horizon': [],
        'year_spans': [],
        'rows': [],
    }

    # Step 1 — most recent snapshot date across all items for this distributor
    max_snap = (
        InventorySnapshot.objects
        .filter(distributor=distributor)
        .order_by('-year', '-month')
        .first()
    )
    if max_snap is None:
        _empty['message'] = 'No inventory snapshots uploaded yet for this distributor.'
        return _empty

    # Step 2 — 12-month horizon starting the month after max_snap
    start_year, start_month = _month_add(max_snap.year, max_snap.month, 1)
    horizon = []
    for i in range(12):
        y, m = _month_add(start_year, start_month, i)
        horizon.append({'year': y, 'month': m, 'month_short': MONTH_SHORT[m - 1]})

    # Step 3 — year spans for two-row table header
    year_spans = []
    for h in horizon:
        if not year_spans or year_spans[-1]['year'] != h['year']:
            year_spans.append({'year': h['year'], 'colspan': 1})
        else:
            year_spans[-1]['colspan'] += 1

    # Step 4 — active items for this distributor
    inactive_item_ids = list(
        DistributorItemProfile.objects.filter(
            distributor=distributor, is_active=False
        ).values_list('item_id', flat=True)
    )
    items = list(
        Item.objects.filter(brand__company=distributor.company, is_active=True)
        .exclude(pk__in=inactive_item_ids)
        .select_related('brand')
        .order_by('brand__name', 'sort_order', 'name')
    )

    # Step 5 — most-recent snapshot per item (Python dedup, newest-first ordering)
    latest_snapshots = {}
    for s in InventorySnapshot.objects.filter(distributor=distributor).order_by('-year', '-month'):
        if s.item_id not in latest_snapshots:
            latest_snapshots[s.item_id] = s

    # Step 6 — bulk-fetch all sales data for this distributor in one query
    sales_data = {}
    for row in (
        SalesRecord.objects
        .filter(account__distributor=distributor)
        .values('item_id', 'sale_date__year', 'sale_date__month')
        .annotate(units=Sum('quantity'))
    ):
        sales_data[(row['item_id'], row['sale_date__year'], row['sale_date__month'])] = row['units']

    items_with_any_sales = {k[0] for k in sales_data}

    # Step 7 — safety stock per item
    safety_stock_map = {
        p.item_id: p.safety_stock_cases
        for p in DistributorItemProfile.objects.filter(distributor=distributor)
        if p.safety_stock_cases is not None
    }

    # Step 8 — build one forecast row per item
    rows = []
    for item in items:
        item_id = item.pk
        snap = latest_snapshots.get(item_id)
        has_snapshot = snap is not None
        running = float(snap.quantity_cases) if snap else 0.0
        safety_stock = safety_stock_map.get(item_id)

        # No snapshot AND no sales data at all → every cell is no_data
        if not has_snapshot and item_id not in items_with_any_sales:
            monthly_data = [
                {
                    'year': h['year'], 'month': h['month'],
                    'inventory': None, 'inventory_display': '',
                    'status': 'no_data',
                    'reason': 'No starting inventory and no prior year data',
                }
                for h in horizon
            ]
            rows.append({'item': item, 'monthly_data': monthly_data})
            continue

        monthly_data = []
        for h in horizon:
            year, month = h['year'], h['month']
            is_past = (year, month) < (current_year, current_month)

            if is_past:
                # Actual sales; 0 if no data (assume no movement that month)
                actual = sales_data.get((item_id, year, month), 0)
                depletion = max(0, actual)
                running -= depletion
                inv = round(running, 2)
                monthly_data.append({
                    'year': year, 'month': month,
                    'inventory': inv, 'inventory_display': _fmt_inv(inv),
                    'status': _inv_status(inv, safety_stock), 'reason': '',
                })
            else:
                # Prior-year projection
                prior_qty = sales_data.get((item_id, year - 1, month))
                if prior_qty is None:
                    # Running inventory carries forward unchanged through no_data cells
                    monthly_data.append({
                        'year': year, 'month': month,
                        'inventory': None, 'inventory_display': '',
                        'status': 'no_data',
                        'reason': 'No prior year data to project depletion',
                    })
                else:
                    depletion = max(0, prior_qty)
                    running -= depletion
                    inv = round(running, 2)
                    monthly_data.append({
                        'year': year, 'month': month,
                        'inventory': inv, 'inventory_display': _fmt_inv(inv),
                        'status': _inv_status(inv, safety_stock), 'reason': '',
                    })

        rows.append({'item': item, 'monthly_data': monthly_data})

    return {
        'distributor': distributor,
        'message': '',
        'horizon': horizon,
        'year_spans': year_spans,
        'rows': rows,
    }
