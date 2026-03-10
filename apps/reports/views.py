"""
Reports views: Account Sales by Year report.
"""
from calendar import monthrange
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max, Sum
from django.shortcuts import redirect, render

from apps.accounts.models import Account
from apps.accounts.utils import get_accounts_for_user, get_distributors_for_user
from apps.catalog.models import Item
from apps.sales.models import SalesRecord


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

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


def _truncate(s, max_len):
    s = (s or '').strip().title()
    if len(s) > max_len:
        return s[:max_len - 1] + '\u2026'
    return s


# ---------------------------------------------------------------------------
# Distributor selector
# ---------------------------------------------------------------------------

@login_required
def distributor_select_view(request):
    """Allow users with multiple distributors to pick one for the report."""
    user = request.user

    if not user.has_permission('can_view_report_account_sales'):
        messages.error(request, 'You do not have permission to view this report.')
        return redirect('dashboard')

    distributors = get_distributors_for_user(user)

    if not distributors.exists():
        messages.error(request, 'No distributors are available for your account.')
        return redirect('dashboard')

    if distributors.count() == 1:
        request.session['report_distributor_pk'] = distributors.first().pk
        return redirect('report_account_sales_by_year')

    if request.method == 'POST':
        dist_pk = request.POST.get('distributor_pk')
        try:
            selected = distributors.get(pk=int(dist_pk))
            request.session['report_distributor_pk'] = selected.pk
            return redirect('report_account_sales_by_year')
        except Exception:
            messages.error(request, 'Please select a valid distributor.')

    current_pk = request.session.get('report_distributor_pk')
    return render(request, 'reports/distributor_select.html', {
        'distributors': distributors,
        'current_pk': current_pk,
    })


# ---------------------------------------------------------------------------
# Main report
# ---------------------------------------------------------------------------

@login_required
def account_sales_by_year(request):
    """Account Sales by Year report."""
    user = request.user

    if not user.has_permission('can_view_report_account_sales'):
        messages.error(request, 'You do not have permission to view this report.')
        return redirect('dashboard')

    distributors = get_distributors_for_user(user)
    multiple_distributors = distributors.count() > 1

    if not distributors.exists():
        return render(request, 'reports/account_sales_by_year.html', {
            'no_data': True,
            'no_data_reason': 'No distributors are available for your account.',
        })

    # ---- Resolve selected distributor -----------------------------------
    selected_distributor = None

    if multiple_distributors:
        dist_pk = request.GET.get('distributor') or request.session.get('report_distributor_pk')
        if dist_pk:
            try:
                selected_distributor = distributors.get(pk=int(dist_pk))
                request.session['report_distributor_pk'] = selected_distributor.pk
            except Exception:
                pass
        if not selected_distributor:
            return redirect('report_account_sales_distributor_select')
    else:
        selected_distributor = distributors.first()

    # ---- Account scoping ------------------------------------------------
    if user.has_role('supplier_admin'):
        accounts_qs = Account.active_accounts.filter(
            company=user.company,
            distributor=selected_distributor,
        )
    else:
        accounts_qs = get_accounts_for_user(user).filter(
            distributor=selected_distributor,
        )

    # ---- Build filter options (before applying user filters) ------------
    items_in_scope_qs = (
        Item.objects
        .filter(sales_records__account__in=accounts_qs)
        .values_list('name', flat=True)
        .distinct()
        .order_by('name')
    )
    filter_options = {
        'items': list(items_in_scope_qs),
        'cities': list(
            accounts_qs.exclude(city='')
            .values_list('city', flat=True).distinct().order_by('city')
        ),
        'counties': list(
            accounts_qs.exclude(county='').exclude(county='Unknown')
            .values_list('county', flat=True).distinct().order_by('county')
        ),
        'classes_of_trade': list(
            accounts_qs.exclude(account_type='')
            .values_list('account_type', flat=True).distinct().order_by('account_type')
        ),
        'distributor_routes': list(
            accounts_qs.exclude(distributor_route='')
            .values_list('distributor_route', flat=True).distinct().order_by('distributor_route')
        ),
    }

    # ---- Parse GET filters ----------------------------------------------
    item_name_filter = request.GET.getlist('item_name')
    on_off_filter = request.GET.get('on_off', '')
    city_filter = request.GET.getlist('city')
    county_filter = request.GET.getlist('county')
    class_of_trade_filter = request.GET.getlist('class_of_trade')
    distributor_route_filter = request.GET.getlist('distributor_route')

    current_filters = {
        'item_name': item_name_filter,
        'on_off': on_off_filter,
        'city': city_filter,
        'county': county_filter,
        'class_of_trade': class_of_trade_filter,
        'distributor_route': distributor_route_filter,
    }

    # ---- Apply account-level filters ------------------------------------
    if on_off_filter in ('ON', 'OFF'):
        accounts_qs = accounts_qs.filter(on_off_premise=on_off_filter)
    if city_filter:
        accounts_qs = accounts_qs.filter(city__in=city_filter)
    if county_filter:
        accounts_qs = accounts_qs.filter(county__in=county_filter)
    if class_of_trade_filter:
        accounts_qs = accounts_qs.filter(account_type__in=class_of_trade_filter)
    if distributor_route_filter:
        accounts_qs = accounts_qs.filter(distributor_route__in=distributor_route_filter)

    # ---- Determine last full month --------------------------------------
    today = date.today()
    current_month_start = today.replace(day=1)

    max_past_sale = (
        SalesRecord.objects
        .filter(
            account__in=accounts_qs,
            quantity__gt=0,
            sale_date__lt=current_month_start,
        )
        .aggregate(Max('sale_date'))['sale_date__max']
    )

    if max_past_sale is None:
        return render(request, 'reports/account_sales_by_year.html', {
            'no_data': True,
            'no_data_reason': 'No sales data is available for the selected distributor and filters.',
            'selected_distributor': selected_distributor,
            'multiple_distributors': multiple_distributors,
            'filter_options': filter_options,
            'current_filters': current_filters,
        })

    lfm_year = max_past_sale.year
    lfm_month = max_past_sale.month
    lfm_start = date(lfm_year, lfm_month, 1)
    lfm_end = date(lfm_year, lfm_month, _last_day(lfm_year, lfm_month))
    last_full_month_display = lfm_start.strftime('%B %Y')

    # ---- Last 12 months window ------------------------------------------
    w_year, w_month = _month_add(lfm_year, lfm_month, -11)
    window_start = date(w_year, w_month, 1)
    window_end = lfm_end
    last_12_label = f"{window_start.strftime('%b %Y')} \u2013 {window_end.strftime('%b %Y')}"

    # ---- Complete calendar years (up to 4, displayed ascending) ---------
    # Fetch the 4 most recent completed years, then sort ascending for display.
    current_year = today.year
    years = sorted(
        SalesRecord.objects
        .filter(
            account__in=accounts_qs,
            quantity__gt=0,
            sale_date__year__lt=current_year,
        )
        .values_list('sale_date__year', flat=True)
        .distinct()
        .order_by('-sale_date__year')
        [:4]
    )  # ascending: oldest year left, newest year right
    most_recent_year = years[-1] if years else None

    # ---- Base queryset: positive quantities only ------------------------
    base_qs = SalesRecord.objects.filter(
        account__in=accounts_qs,
        quantity__gt=0,
    )

    # ---- Apply item-name filter to base queryset -----------------------
    if item_name_filter:
        base_qs = base_qs.filter(item__name__in=item_name_filter)

    # ---- Aggregate sales data per account (one row per account) ---------
    # Per-year aggregation: {(account_id, year): units}
    year_data = {}
    if years:
        for row in (
            base_qs
            .filter(sale_date__year__in=years)
            .values('account_id', 'sale_date__year')
            .annotate(units=Sum('quantity'))
        ):
            year_data[(row['account_id'], row['sale_date__year'])] = row['units']

    # Last-12-months aggregation: {account_id: units}
    last12_data = {}
    for row in (
        base_qs
        .filter(sale_date__gte=window_start, sale_date__lte=window_end)
        .values('account_id')
        .annotate(units=Sum('quantity'))
    ):
        last12_data[row['account_id']] = row['units']

    # Unique account_ids that have any data
    all_account_ids = set()
    for k in year_data:
        all_account_ids.add(k[0])
    all_account_ids.update(last12_data.keys())

    if not all_account_ids:
        return render(request, 'reports/account_sales_by_year.html', {
            'no_data': True,
            'no_data_reason': 'No sales data matches the selected filters.',
            'selected_distributor': selected_distributor,
            'multiple_distributors': multiple_distributors,
            'filter_options': filter_options,
            'current_filters': current_filters,
        })

    # ---- Fetch account objects ------------------------------------------
    accounts_dict = {
        a.pk: a for a in Account.objects.filter(pk__in=all_account_ids)
    }

    # ---- Build rows (one per account) -----------------------------------
    rows = []
    for account_id in sorted(all_account_ids):
        account = accounts_dict.get(account_id)
        if not account:
            continue

        year_units = {y: year_data.get((account_id, y), 0) for y in years}
        last_12_units = last12_data.get(account_id, 0)
        most_recent_year_units = year_units.get(most_recent_year, 0) if most_recent_year else 0
        diff = last_12_units - most_recent_year_units
        diff_pct = round(diff / most_recent_year_units * 100, 1) if most_recent_year_units > 0 else None

        on_off = account.on_off_premise if account.on_off_premise in ('ON', 'OFF') else 'Unknown'

        rows.append({
            'account_name': _truncate(account.name, 20),
            'city': _truncate(account.city, 15),
            'on_off': on_off,
            'year_units': year_units,
            'last_12_units': last_12_units,
            'diff': diff,
            'diff_pct': diff_pct,
        })

    rows.sort(key=lambda r: r['account_name'])

    # ---- Calculate totals row -------------------------------------------
    total_by_year = {y: sum(r['year_units'].get(y, 0) for r in rows) for y in years}
    total_last_12 = sum(r['last_12_units'] for r in rows)
    most_recent_year_total = total_by_year.get(most_recent_year, 0) if most_recent_year else 0
    total_diff = total_last_12 - most_recent_year_total
    total_diff_pct = (
        round(total_diff / most_recent_year_total * 100, 1)
        if most_recent_year_total > 0 else None
    )

    return render(request, 'reports/account_sales_by_year.html', {
        'rows': rows,
        'years': years,
        'last_12_label': last_12_label,
        'last_full_month_display': last_full_month_display,
        'filter_options': filter_options,
        'current_filters': current_filters,
        'selected_distributor': selected_distributor,
        'multiple_distributors': multiple_distributors,
        'total_by_year': total_by_year,
        'total_last_12': total_last_12,
        'total_diff': total_diff,
        'total_diff_pct': total_diff_pct,
    })
