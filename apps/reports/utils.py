"""
Shared utilities for the reports app.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date

from django.db.models import Max, Sum

from apps.catalog.models import Item
from apps.sales.models import SalesRecord


def _month_add(year, month, delta_months):
    """Return (year, month) after adding delta_months (may be negative)."""
    month += delta_months
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    return year, month


def _last_day(year, month):
    return monthrange(year, month)[1]


def get_portfolio_status(account, today=None):
    """
    Return per-item portfolio status data for a single account.

    Returns None if no past sales data exists.

    today: optional date override (defaults to date.today()); callers
    may pass their own value so that date mocks in tests propagate here.

    Return shape:
    {
        'years': [2022, 2023, 2024, 2025],
        'rows': [
            {
                'item_id': 7,
                'item_code': 'SS-CL-RED',
                'item_name': 'Classic Red',
                'year_totals': {2022: 24, 2023: 31, 2024: 28, 2025: 35},
                'diff_yr': 7,
                'last_12': 38,
                'diff_l12': 3,
                'status': 'growing',
            },
            ...
        ],
        'totals': {
            'year_totals': {...},
            'diff_yr': 32,
            'last_12': 111,
            'diff_l12': 14,
        },
        'lfm_year': 2025,
        'lfm_month': 3,
    }
    """
    if today is None:
        today = date.today()
    current_year = today.year
    current_month_start = today.replace(day=1)

    distributor = account.distributor

    max_past_sale = (
        SalesRecord.objects
        .filter(
            account__distributor=distributor,
            account__company=account.company,
            sale_date__lt=current_month_start,
        )
        .aggregate(Max('sale_date'))['sale_date__max']
    )

    if max_past_sale is None:
        return None

    lfm_year = max_past_sale.year
    lfm_month = max_past_sale.month
    lfm_end = date(lfm_year, lfm_month, _last_day(lfm_year, lfm_month))

    last_full_year = current_year - 1

    # L12M window
    w_year, w_month = _month_add(lfm_year, lfm_month, -11)
    window_start = date(w_year, w_month, 1)
    window_end = lfm_end

    # 4 most recent complete calendar years (distributor-scoped for stability)
    distributor_qs = SalesRecord.objects.filter(
        account__distributor=distributor,
        account__company=account.company,
    )
    years = sorted(
        distributor_qs
        .filter(sale_date__year__lt=current_year)
        .values_list('sale_date__year', flat=True)
        .distinct()
        .order_by('-sale_date__year')
        [:4]
    )
    most_recent_year = years[-1] if years else None
    prior_year = years[-2] if len(years) >= 2 else None

    # Items with any sales for this account
    items = (
        Item.objects
        .filter(sales_records__account=account)
        .distinct()
        .select_related('brand')
        .order_by('brand__name', 'sort_order', 'name')
    )

    # Per-item per-year aggregation
    year_data = {}
    if years:
        for row in (
            SalesRecord.objects
            .filter(account=account, sale_date__year__in=years)
            .values('item_id', 'sale_date__year')
            .annotate(units=Sum('quantity'))
        ):
            year_data[(row['item_id'], row['sale_date__year'])] = row['units']

    # Per-item L12M
    last12_data = {}
    for row in (
        SalesRecord.objects
        .filter(account=account, sale_date__gte=window_start, sale_date__lte=window_end)
        .values('item_id')
        .annotate(units=Sum('quantity'))
    ):
        last12_data[row['item_id']] = row['units']

    # Per-item last full calendar year total
    lfy_data = {}
    for row in (
        SalesRecord.objects
        .filter(account=account, sale_date__year=last_full_year)
        .values('item_id')
        .annotate(units=Sum('quantity'))
    ):
        lfy_data[row['item_id']] = row['units']

    # Build rows
    rows = []
    for item in items:
        item_id = item.pk

        year_totals = {y: year_data.get((item_id, y), 0) for y in years}
        last_full_year_total = lfy_data.get(item_id, 0)
        last_12 = last12_data.get(item_id, 0)

        if last_full_year_total == 0 and last_12 == 0:
            continue

        # Status classification (mirrors account_detail_sales)
        if last_full_year_total > 0 and last_12 == 0:
            status = 'non_buy'
        elif last_12 < last_full_year_total:
            status = 'declining'
        elif last_12 == last_full_year_total:
            status = 'steady'
        elif last_full_year_total == 0 and last_12 > 0:
            status = 'new'
        else:
            status = 'growing'

        diff_yr = (
            year_totals.get(most_recent_year, 0) - year_totals.get(prior_year, 0)
            if prior_year and most_recent_year else 0
        )
        diff_l12 = last_12 - last_full_year_total

        rows.append({
            'item_id': item_id,
            'item_code': item.item_code,
            'item_name': item.name,
            'year_totals': year_totals,
            'diff_yr': diff_yr,
            'last_12': last_12,
            'diff_l12': diff_l12,
            'status': status,
        })

    total_year_totals = {y: sum(r['year_totals'].get(y, 0) for r in rows) for y in years}
    total_last_12 = sum(r['last_12'] for r in rows)
    total_diff_l12 = sum(r['diff_l12'] for r in rows)
    total_diff_yr = (
        total_year_totals.get(most_recent_year, 0) - total_year_totals.get(prior_year, 0)
        if prior_year and most_recent_year else 0
    )

    return {
        'years': years,
        'rows': rows,
        'totals': {
            'year_totals': total_year_totals,
            'diff_yr': total_diff_yr,
            'last_12': total_last_12,
            'diff_l12': total_diff_l12,
        },
        'lfm_year': lfm_year,
        'lfm_month': lfm_month,
    }


def get_order_history(account):
    """
    Return order history for a single account, grouped by sale_date.

    All SalesRecords on the same date are treated as one order.
    Returns records sorted by date descending.

    Return shape:
    {
        'orders': [
            {
                'date': '2025-03-15',
                'date_display': 'Mar 15, 2025',
                'year': 2025,
                'item_count': 2,
                'total_qty': 13,
                'total_amount': 142.50,
                'items': [
                    {
                        'item_code': 'SS-CL-RED',
                        'item_name': 'Classic Red',
                        'qty': 5,
                        'cost': 12.50,
                        'amount': 62.50,
                    },
                    ...
                ]
            },
            ...
        ]
    }
    """
    records = (
        SalesRecord.objects
        .filter(account=account)
        .select_related('item')
        .order_by('-sale_date', 'item__item_code')
    )

    # Group by date in Python since we need item-level detail
    by_date = defaultdict(list)
    for rec in records:
        by_date[rec.sale_date].append(rec)

    orders = []
    for sale_date in sorted(by_date.keys(), reverse=True):
        date_records = by_date[sale_date]

        # Group by item within this date
        by_item = defaultdict(list)
        for rec in date_records:
            by_item[rec.item_id].append(rec)

        items = []
        for item_id, item_recs in by_item.items():
            item = item_recs[0].item
            qty = sum(r.quantity for r in item_recs)
            amount = float(sum(
                r.quantity * r.distributor_wholesale_price
                for r in item_recs
                if r.distributor_wholesale_price is not None
            ))
            cost = (amount / qty) if qty != 0 else 0.0
            items.append({
                'item_code': item.item_code,
                'item_name': item.name,
                'qty': qty,
                'cost': round(cost, 2),
                'amount': round(amount, 2),
            })

        items.sort(key=lambda x: x['item_code'])

        total_qty = sum(i['qty'] for i in items)
        total_amount = round(sum(i['amount'] for i in items), 2)

        orders.append({
            'date': sale_date.strftime('%Y-%m-%d'),
            'date_display': sale_date.strftime('%b %-d, %Y'),
            'year': sale_date.year,
            'item_count': len(items),
            'total_qty': total_qty,
            'total_amount': total_amount,
            'items': items,
        })

    return {'orders': orders}
