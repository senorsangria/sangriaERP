"""
Distributor Inventory Forecast — Phase 4-step-1 (walker extracted in Phase G2).

Public API:
  compute_distributor_forecast(distributor, today=None, po_additions=None)
  compute_group_forecast(group, po_additions=None, today=None)
"""
from datetime import date
from types import SimpleNamespace

from django.db.models import Sum

from apps.catalog.models import Item
from apps.distribution.models import (
    DistributorItemProfile, DistributorPOLine, InventorySnapshot,
)
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


def _walk_inventory_forward(
    items,
    latest_snapshots,
    sales_data,
    po_additions,
    safety_stock_map,
    anchor_year,
    anchor_month,
    horizon,
    current_year,
    current_month,
):
    """Walk inventory forward through horizon, producing per-item monthly_data rows.

    latest_snapshots: {item_id: object_with_quantity_cases} — None means no snapshot.
    po_additions: {(item_id, year, month): cases} or None.
    """
    items_with_any_sales = {k[0] for k in sales_data}
    rows = []

    for item in items:
        item_id = item.pk
        snap = latest_snapshots.get(item_id)
        has_snapshot = snap is not None
        running = float(snap.quantity_cases) if snap else 0.0
        safety_stock = safety_stock_map.get(item_id)

        anchor_cell = {
            'year': anchor_year, 'month': anchor_month,
            'inventory': float(snap.quantity_cases) if snap else None,
            'inventory_display': _fmt_inv(float(snap.quantity_cases)) if snap else '',
            'depletion': None,
            'status': 'snapshot' if snap else 'no_data',
            'reason': '' if snap else 'No inventory snapshot for this item',
            'is_snapshot': True,
        }

        if not has_snapshot and item_id not in items_with_any_sales:
            monthly_data = [anchor_cell] + [
                {
                    'year': h['year'], 'month': h['month'],
                    'inventory': None, 'inventory_display': '',
                    'depletion': None,
                    'status': 'no_data',
                    'reason': 'No starting inventory and no prior year data',
                    'is_snapshot': False,
                }
                for h in horizon[1:]
            ]
            rows.append({'item': item, 'monthly_data': monthly_data})
            continue

        monthly_data = [anchor_cell]
        for h in horizon[1:]:
            year, month = h['year'], h['month']
            is_past = (year, month) < (current_year, current_month)

            if is_past:
                actual = sales_data.get((item_id, year, month), 0)
                depletion = max(0, actual)
                if po_additions:
                    running += po_additions.get((item_id, year, month), 0.0)
                running -= depletion
                inv = round(running, 2)
                monthly_data.append({
                    'year': year, 'month': month,
                    'inventory': inv, 'inventory_display': _fmt_inv(inv),
                    'depletion': depletion,
                    'status': _inv_status(inv, safety_stock), 'reason': '',
                    'is_snapshot': False,
                })
            else:
                prior_qty = sales_data.get((item_id, year - 1, month))
                if prior_qty is None:
                    if po_additions:
                        running += po_additions.get((item_id, year, month), 0.0)
                    monthly_data.append({
                        'year': year, 'month': month,
                        'inventory': None, 'inventory_display': '',
                        'depletion': None,
                        'status': 'no_data',
                        'reason': 'No prior year data to project depletion',
                        'is_snapshot': False,
                    })
                else:
                    depletion = max(0, prior_qty)
                    if po_additions:
                        running += po_additions.get((item_id, year, month), 0.0)
                    running -= depletion
                    inv = round(running, 2)
                    monthly_data.append({
                        'year': year, 'month': month,
                        'inventory': inv, 'inventory_display': _fmt_inv(inv),
                        'depletion': depletion,
                        'status': _inv_status(inv, safety_stock), 'reason': '',
                        'is_snapshot': False,
                    })

        rows.append({'item': item, 'monthly_data': monthly_data})

    return rows


def compute_distributor_forecast(distributor, today=None, po_additions=None):
    """
    Compute a 13-month ending-inventory forecast for one distributor.

    First column is the snapshot anchor month (actual on-hand, status='snapshot').
    Columns 2-13 are 12 months of forward projection.

    Depletion source for projection months:
      - Fully-ended months (year,month < current calendar year,month):
        actual SalesRecord aggregation; 0 if no records exist that month.
      - Current month and future months: prior-year same-month sales as
        projection; cell is 'no_data' when no prior-year record exists.
      - Negative depletion (net returns) is floored to 0 in both cases.

    Returns a dict:
    {
        'distributor': distributor,
        'message': str,
        'horizon': [            # 13-element list (anchor + 12 projection months)
            {'year': int, 'month': int, 'month_short': str, 'is_snapshot': bool},
            ...
        ],
        'year_spans': [
            {'year': int, 'colspan': int},
            ...
        ],
        'safety_stock_map': {item_id: int},   # items with a safety stock target set
        'rows': [
            {
                'item': Item,
                'monthly_data': [
                    {
                        'year': int, 'month': int,
                        'inventory': float | None,
                        'inventory_display': str,
                        'depletion': float | None,  # cases consumed; None when unknown
                        'status': 'snapshot'|'green'|'yellow'|'red'|'no_data',
                        'reason': str,
                        'is_snapshot': bool,
                    },
                    ...  # 13 entries
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
        'safety_stock_map': {},
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

    # Step 2 — horizon: anchor (snapshot month) + 12 projection months = 13 total
    anchor_year, anchor_month = max_snap.year, max_snap.month
    horizon = [
        {
            'year': anchor_year,
            'month': anchor_month,
            'month_short': MONTH_SHORT[anchor_month - 1],
            'is_snapshot': True,
        }
    ]
    start_year, start_month = _month_add(anchor_year, anchor_month, 1)
    for i in range(12):
        y, m = _month_add(start_year, start_month, i)
        horizon.append({
            'year': y, 'month': m,
            'month_short': MONTH_SHORT[m - 1],
            'is_snapshot': False,
        })

    # Step 3 — year spans for two-row table header (covers all 13 columns)
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
        .select_related('brand', 'co_packer')
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

    # Step 7 — safety stock per item
    safety_stock_map = {
        p.item_id: p.safety_stock_cases
        for p in DistributorItemProfile.objects.filter(distributor=distributor)
        if p.safety_stock_cases is not None
    }

    # Step 8 — walk inventory forward using shared helper
    rows = _walk_inventory_forward(
        items=items,
        latest_snapshots=latest_snapshots,
        sales_data=sales_data,
        po_additions=po_additions,
        safety_stock_map=safety_stock_map,
        anchor_year=anchor_year,
        anchor_month=anchor_month,
        horizon=horizon,
        current_year=current_year,
        current_month=current_month,
    )

    return {
        'distributor': distributor,
        'message': '',
        'horizon': horizon,
        'year_spans': year_spans,
        'safety_stock_map': safety_stock_map,
        'rows': rows,
    }


def compute_group_forecast(group, po_additions=None, today=None):
    """
    Compute aggregated forecast for a DistributorGroup.

    Alignment check: each member must have snapshots for all of their own active
    items (Item.is_active=True AND NOT DistributorItemProfile.is_active=False) in
    the same year-month. Most recent such period = anchor.

    Returns same shape as compute_distributor_forecast plus:
      'alignment_status': 'ok' | 'misaligned' | 'no_data'
      'alignment_errors': list[{distributor: str, missing_items: [str]}]
      'anchor_period': (year, month) or None
    """
    if today is None:
        today = date.today()

    current_year = today.year
    current_month = today.month
    company = group.company
    primary = group.primary_distributor
    members = list(group.members.all())

    _base = {
        'group': group,
        'horizon': [],
        'year_spans': [],
        'safety_stock_map': {},
        'rows': [],
    }

    if not members:
        return {**_base, 'alignment_status': 'no_data', 'message': 'Group has no members.'}

    # member_active_items: all active catalog items minus those explicitly marked
    # inactive — used for union display logic (mirrors compute_distributor_forecast).
    all_active_item_ids = set(
        Item.objects.filter(brand__company=company, is_active=True)
        .values_list('pk', flat=True)
    )
    member_active_items = {}
    for member in members:
        explicitly_inactive = set(
            DistributorItemProfile.objects.filter(
                distributor=member, is_active=False
            ).values_list('item_id', flat=True)
        )
        member_active_items[member.id] = all_active_item_ids - explicitly_inactive

    # member_required_items: only items with an explicit is_active=True profile for
    # that member. Used for the alignment check — a missing profile means the item
    # is not required from that member. This prevents members from being penalised
    # for not snapshotting items they don't carry (i.e. items with no profile at all).
    member_required_items = {}
    for member in members:
        member_required_items[member.id] = set(
            DistributorItemProfile.objects.filter(
                distributor=member, is_active=True
            ).values_list('item_id', flat=True)
        )

    # Fetch all member snapshots in one query
    all_snaps = list(
        InventorySnapshot.objects
        .filter(distributor__in=members)
        .values('distributor_id', 'year', 'month', 'item_id', 'quantity_cases')
    )

    # Group by (year, month) → {member_id: set(item_id)}
    period_member_items = {}
    for s in all_snaps:
        ym = (s['year'], s['month'])
        period_member_items.setdefault(ym, {}).setdefault(
            s['distributor_id'], set()
        ).add(s['item_id'])

    # Find most-recent aligned period: all members present AND each member has
    # snapshots for all of their own required items (explicit is_active=True profiles).
    anchor_period = None
    for ym in sorted(period_member_items.keys(), reverse=True):
        per_member = period_member_items[ym]
        if not all(m.id in per_member for m in members):
            continue
        if all(
            member_required_items[m.id].issubset(per_member.get(m.id, set()))
            for m in members
        ):
            anchor_period = ym
            break

    if anchor_period is None:
        candidate = max(period_member_items.keys()) if period_member_items else None
        alignment_errors = []
        for member in members:
            if candidate:
                snapshotted = period_member_items.get(candidate, {}).get(member.id, set())
                missing_ids = member_required_items[member.id] - snapshotted
            else:
                missing_ids = member_required_items[member.id]
            missing_names = list(
                Item.objects.filter(pk__in=missing_ids)
                .order_by('brand__name', 'name')
                .values_list('name', flat=True)
            ) if missing_ids else []
            alignment_errors.append({
                'distributor': member.name,
                'missing_items': missing_names,
            })
        return {
            **_base,
            'alignment_status': 'misaligned',
            'alignment_errors': alignment_errors,
            'candidate_period': candidate,
        }

    anchor_year, anchor_month = anchor_period

    # Build horizon (anchor + 12 projection months)
    horizon = [
        {
            'year': anchor_year,
            'month': anchor_month,
            'month_short': MONTH_SHORT[anchor_month - 1],
            'is_snapshot': True,
        }
    ]
    start_year, start_month = _month_add(anchor_year, anchor_month, 1)
    for i in range(12):
        y, m = _month_add(start_year, start_month, i)
        horizon.append({
            'year': y, 'month': m,
            'month_short': MONTH_SHORT[m - 1],
            'is_snapshot': False,
        })

    year_spans = []
    for h in horizon:
        if not year_spans or year_spans[-1]['year'] != h['year']:
            year_spans.append({'year': h['year'], 'colspan': 1})
        else:
            year_spans[-1]['colspan'] += 1

    # Item set: union of all members' active items
    union_active_ids = set()
    for item_ids in member_active_items.values():
        union_active_ids |= item_ids

    items = list(
        Item.objects.filter(brand__company=company, is_active=True, pk__in=union_active_ids)
        .select_related('brand', 'co_packer')
        .order_by('brand__name', 'sort_order', 'name')
    )

    # Aggregated starting inventory at anchor: sum across all members per item
    snap_lookup = {
        (s['distributor_id'], s['item_id'], s['year'], s['month']): float(s['quantity_cases'])
        for s in all_snaps
    }
    latest_snapshots = {
        item.id: SimpleNamespace(
            quantity_cases=sum(
                snap_lookup.get((m.id, item.id, anchor_year, anchor_month), 0.0)
                for m in members
            )
        )
        for item in items
    }

    # Aggregated sales across all members
    sales_data = {
        (s['item_id'], s['sale_date__year'], s['sale_date__month']): s['units']
        for s in (
            SalesRecord.objects
            .filter(account__distributor__in=members)
            .values('item_id', 'sale_date__year', 'sale_date__month')
            .annotate(units=Sum('quantity'))
        )
    }

    # Safety stock from primary distributor only
    safety_stock_map = {
        p.item_id: p.safety_stock_cases
        for p in DistributorItemProfile.objects.filter(
            distributor=primary, safety_stock_cases__isnull=False
        )
    }

    # Build po_additions if not provided
    if po_additions is None:
        po_additions = {}
        for line in DistributorPOLine.objects.filter(
            po__distributor__in=members
        ).select_related('po'):
            key = (line.item_id, line.po.year, line.po.month)
            po_additions[key] = po_additions.get(key, 0.0) + float(line.quantity_cases)

    rows = _walk_inventory_forward(
        items=items,
        latest_snapshots=latest_snapshots,
        sales_data=sales_data,
        po_additions=po_additions,
        safety_stock_map=safety_stock_map,
        anchor_year=anchor_year,
        anchor_month=anchor_month,
        horizon=horizon,
        current_year=current_year,
        current_month=current_month,
    )

    return {
        'group': group,
        'alignment_status': 'ok',
        'horizon': horizon,
        'year_spans': year_spans,
        'safety_stock_map': safety_stock_map,
        'rows': rows,
        'anchor_period': anchor_period,
    }
