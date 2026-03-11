"""
Reports views: Account Sales by Year report.
"""
import csv
from calendar import monthrange
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Max, Sum
from django.http import Http404, HttpResponse, HttpResponseForbidden
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
            sale_date__year__lt=current_year,
        )
        .values_list('sale_date__year', flat=True)
        .distinct()
        .order_by('-sale_date__year')
        [:4]
    )  # ascending: oldest year left, newest year right
    most_recent_year = years[-1] if years else None

    # ---- Base queryset --------------------------------------------------
    base_qs = SalesRecord.objects.filter(
        account__in=accounts_qs,
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

        on_off = account.on_off_premise if account.on_off_premise in ('ON', 'OFF') else 'Unknown'

        rows.append({
            'account_id': account_id,
            'account_name': _truncate(account.name, 20),
            'city': _truncate(account.city, 15),
            'on_off': on_off,
            'year_units': year_units,
            'last_12_units': last_12_units,
            'diff': diff,
        })

    rows.sort(key=lambda r: r['account_name'])

    # ---- Calculate totals row -------------------------------------------
    total_by_year = {y: sum(r['year_units'].get(y, 0) for r in rows) for y in years}
    total_last_12 = sum(r['last_12_units'] for r in rows)
    most_recent_year_total = total_by_year.get(most_recent_year, 0) if most_recent_year else 0
    total_diff = total_last_12 - most_recent_year_total

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
    })


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

@login_required
def account_sales_by_year_csv(request):
    """CSV export of Account Sales by Year report with current filters applied."""
    user = request.user

    if not user.has_permission('can_view_report_account_sales'):
        messages.error(request, 'You do not have permission to view this report.')
        return redirect('dashboard')

    distributors = get_distributors_for_user(user)
    multiple_distributors = distributors.count() > 1

    if not distributors.exists():
        return redirect('dashboard')

    # ---- Resolve selected distributor -----------------------------------
    selected_distributor = None

    if multiple_distributors:
        dist_pk = request.GET.get('distributor') or request.session.get('report_distributor_pk')
        if dist_pk:
            try:
                selected_distributor = distributors.get(pk=int(dist_pk))
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

    # ---- Parse GET filters ----------------------------------------------
    item_name_filter = request.GET.getlist('item_name')
    on_off_filter = request.GET.get('on_off', '')
    city_filter = request.GET.getlist('city')
    county_filter = request.GET.getlist('county')
    class_of_trade_filter = request.GET.getlist('class_of_trade')
    distributor_route_filter = request.GET.getlist('distributor_route')

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
            sale_date__lt=current_month_start,
        )
        .aggregate(Max('sale_date'))['sale_date__max']
    )

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="account_sales_by_year.csv"'

    if max_past_sale is None:
        return response

    lfm_year = max_past_sale.year
    lfm_month = max_past_sale.month
    lfm_end = date(lfm_year, lfm_month, _last_day(lfm_year, lfm_month))

    # ---- Last 12 months window ------------------------------------------
    w_year, w_month = _month_add(lfm_year, lfm_month, -11)
    window_start = date(w_year, w_month, 1)
    window_end = lfm_end

    # ---- Complete calendar years (up to 4) ------------------------------
    current_year = today.year
    years = sorted(
        SalesRecord.objects
        .filter(
            account__in=accounts_qs,
            sale_date__year__lt=current_year,
        )
        .values_list('sale_date__year', flat=True)
        .distinct()
        .order_by('-sale_date__year')
        [:4]
    )
    most_recent_year = years[-1] if years else None

    # ---- Base queryset --------------------------------------------------
    base_qs = SalesRecord.objects.filter(
        account__in=accounts_qs,
    )

    if item_name_filter:
        base_qs = base_qs.filter(item__name__in=item_name_filter)

    # ---- Per-year aggregation -------------------------------------------
    year_data = {}
    if years:
        for row in (
            base_qs
            .filter(sale_date__year__in=years)
            .values('account_id', 'sale_date__year')
            .annotate(units=Sum('quantity'))
        ):
            year_data[(row['account_id'], row['sale_date__year'])] = row['units']

    # ---- Last-12 aggregation --------------------------------------------
    last12_data = {}
    for row in (
        base_qs
        .filter(sale_date__gte=window_start, sale_date__lte=window_end)
        .values('account_id')
        .annotate(units=Sum('quantity'))
    ):
        last12_data[row['account_id']] = row['units']

    all_account_ids = set()
    for k in year_data:
        all_account_ids.add(k[0])
    all_account_ids.update(last12_data.keys())

    if not all_account_ids:
        return response

    accounts_dict = {
        a.pk: a for a in Account.objects.filter(pk__in=all_account_ids)
    }

    # ---- Build rows -----------------------------------------------------
    csv_rows = []
    for account_id in all_account_ids:
        account = accounts_dict.get(account_id)
        if not account:
            continue

        year_units = {y: year_data.get((account_id, y), 0) for y in years}
        last_12_units = last12_data.get(account_id, 0)
        most_recent_year_units = year_units.get(most_recent_year, 0) if most_recent_year else 0
        diff = last_12_units - most_recent_year_units
        on_off = account.on_off_premise if account.on_off_premise in ('ON', 'OFF') else 'Unknown'

        csv_rows.append({
            'account_name': (account.name or '').strip(),
            'city': (account.city or '').strip(),
            'on_off': on_off,
            'year_units': year_units,
            'last_12_units': last_12_units,
            'diff': diff,
        })

    csv_rows.sort(key=lambda r: r['account_name'].lower())

    # ---- Totals ---------------------------------------------------------
    total_by_year = {y: sum(r['year_units'].get(y, 0) for r in csv_rows) for y in years}
    total_last_12 = sum(r['last_12_units'] for r in csv_rows)
    most_recent_year_total = total_by_year.get(most_recent_year, 0) if most_recent_year else 0
    total_diff = total_last_12 - most_recent_year_total

    # ---- Write CSV ------------------------------------------------------
    writer = csv.writer(response)
    header = ['Account Name', 'City', 'On/Off'] + [str(y) for y in years] + ['Last 12m', 'Diff']
    writer.writerow(header)

    for row in csv_rows:
        data_row = [row['account_name'], row['city'], row['on_off']]
        data_row += [row['year_units'].get(y, 0) for y in years]
        data_row += [row['last_12_units'], row['diff']]
        writer.writerow(data_row)

    totals_row = ['TOTAL', '', '']
    totals_row += [total_by_year.get(y, 0) for y in years]
    totals_row += [total_last_12, total_diff]
    writer.writerow(totals_row)

    return response


# ---------------------------------------------------------------------------
# Account Detail Sales view
# ---------------------------------------------------------------------------

@login_required
def account_detail_sales(request, account_id):
    """Monthly sales breakdown per item for a single account, with trend projection."""
    user = request.user

    if not user.has_permission('can_view_report_account_sales'):
        messages.error(request, 'You do not have permission to view this report.')
        return redirect('dashboard')

    try:
        account = Account.objects.get(pk=account_id, company=user.company)
    except Account.DoesNotExist:
        raise Http404

    if not user.has_role('supplier_admin'):
        if not get_accounts_for_user(user).filter(pk=account_id).exists():
            return HttpResponseForbidden()

    # ---- Date setup -------------------------------------------------------
    today = date.today()
    current_year = today.year
    current_month_start = today.replace(day=1)

    max_past_sale = (
        SalesRecord.objects
        .filter(account=account, sale_date__lt=current_month_start)
        .aggregate(Max('sale_date'))['sale_date__max']
    )

    if max_past_sale is None:
        return render(request, 'reports/account_detail_sales.html', {
            'account': account,
            'no_data': True,
        })

    lfm_year = max_past_sale.year
    lfm_month = max_past_sale.month
    lfm_end = date(lfm_year, lfm_month, _last_day(lfm_year, lfm_month))
    last_full_month_display = date(lfm_year, lfm_month, 1).strftime('%B %Y')

    last_full_year = current_year - 1

    if lfm_year == current_year:
        actual_months = list(range(1, lfm_month + 1))
        projected_months = list(range(lfm_month + 1, 13))
    else:
        actual_months = []
        projected_months = list(range(1, 13))

    # Last 12 months window (same as main report)
    w_year, w_month = _month_add(lfm_year, lfm_month, -11)
    window_start = date(w_year, w_month, 1)
    window_end = lfm_end

    # ---- Items with sales for this account --------------------------------
    items = (
        Item.objects
        .filter(sales_records__account=account)
        .distinct()
        .select_related('brand')
        .order_by('brand__name', 'sort_order', 'name')
    )

    # ---- Aggregate queries ------------------------------------------------
    # Last full year: sum per (item, month)
    lfy_data = {}
    for row in (
        SalesRecord.objects
        .filter(account=account, sale_date__year=last_full_year)
        .values('item_id', 'sale_date__month')
        .annotate(units=Sum('quantity'))
    ):
        lfy_data[(row['item_id'], row['sale_date__month'])] = row['units']

    # Current year actuals: sum per (item, month)
    actual_data = {}
    if actual_months:
        for row in (
            SalesRecord.objects
            .filter(
                account=account,
                sale_date__year=current_year,
                sale_date__month__in=actual_months,
            )
            .values('item_id', 'sale_date__month')
            .annotate(units=Sum('quantity'))
        ):
            actual_data[(row['item_id'], row['sale_date__month'])] = row['units']

    # Last 12 months: sum per item
    last12_data = {}
    for row in (
        SalesRecord.objects
        .filter(account=account, sale_date__gte=window_start, sale_date__lte=window_end)
        .values('item_id')
        .annotate(units=Sum('quantity'))
    ):
        last12_data[row['item_id']] = row['units']

    # ---- Build per-item rows ---------------------------------------------
    all_months = list(range(1, 13))
    last_6_actual_months = actual_months[-6:] if len(actual_months) >= 6 else actual_months

    rows = []
    for item in items:
        item_id = item.pk

        last_full_year_by_month = {m: lfy_data.get((item_id, m), 0) for m in all_months}
        current_actual_by_month = {m: actual_data.get((item_id, m), 0) for m in actual_months}

        lfy_has_data = any(v != 0 for v in last_full_year_by_month.values())

        # Trend multiplier: only when LFY has data and 6+ actual months exist
        if lfy_has_data and len(actual_months) >= 6:
            actual_6 = sum(current_actual_by_month.get(m, 0) for m in last_6_actual_months)
            prior_6 = sum(last_full_year_by_month.get(m, 0) for m in last_6_actual_months)
            multiplier = (actual_6 / prior_6) if prior_6 > 0 else 1.0
        else:
            multiplier = 1.0

        current_projected_by_month = {}
        for m in projected_months:
            if lfy_has_data:
                base = last_full_year_by_month[m]
                current_projected_by_month[m] = max(0, round(base * multiplier))
            else:
                if len(actual_months) < 6:
                    current_projected_by_month[m] = None
                else:
                    avg = sum(current_actual_by_month.get(am, 0) for am in last_6_actual_months) / 6
                    current_projected_by_month[m] = max(0, round(avg))

        last_full_year_total = sum(last_full_year_by_month.values())
        current_actual_total = sum(current_actual_by_month.values())
        current_projected_total = sum(
            v for v in current_projected_by_month.values() if v is not None
        )
        current_combined_total = current_actual_total + current_projected_total
        last_12_units = last12_data.get(item_id, 0)

        rows.append({
            'item_name': item.name,
            'brand_name': item.brand.name,
            'last_full_year_by_month': last_full_year_by_month,
            'current_actual_by_month': current_actual_by_month,
            'current_projected_by_month': current_projected_by_month,
            'last_full_year_total': last_full_year_total,
            'current_actual_total': current_actual_total,
            'current_projected_total': current_projected_total,
            'current_combined_total': current_combined_total,
            'last_12_units': last_12_units,
            'diff_last_12_vs_last_year': last_12_units - last_full_year_total,
            'diff_current_vs_last_year': current_combined_total - last_full_year_total,
        })

    # ---- Totals -----------------------------------------------------------
    totals = {
        'last_full_year_by_month': {
            m: sum(r['last_full_year_by_month'][m] for r in rows) for m in all_months
        },
        'current_actual_by_month': {
            m: sum(r['current_actual_by_month'].get(m, 0) for r in rows) for m in actual_months
        },
        'current_projected_by_month': {
            m: sum((r['current_projected_by_month'].get(m) or 0) for r in rows)
            for m in projected_months
        },
        'last_full_year_total': sum(r['last_full_year_total'] for r in rows),
        'current_actual_total': sum(r['current_actual_total'] for r in rows),
        'current_projected_total': sum(r['current_projected_total'] for r in rows),
        'current_combined_total': sum(r['current_combined_total'] for r in rows),
        'last_12_total': sum(r['last_12_units'] for r in rows),
        'diff_last_12_vs_last_year': sum(r['diff_last_12_vs_last_year'] for r in rows),
        'diff_current_vs_last_year': sum(r['diff_current_vs_last_year'] for r in rows),
    }

    month_names = {i: date(2000, i, 1).strftime('%b') for i in all_months}

    return render(request, 'reports/account_detail_sales.html', {
        'account': account,
        'rows': rows,
        'last_full_year': last_full_year,
        'current_year': current_year,
        'all_months': all_months,
        'actual_months': actual_months,
        'projected_months': projected_months,
        'last_full_month_display': last_full_month_display,
        'month_names': month_names,
        'totals': totals,
        'current_year_colspan': len(actual_months) + len(projected_months),
    })
