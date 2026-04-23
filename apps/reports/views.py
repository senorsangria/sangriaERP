"""
Reports views: Account Sales by Year report.
"""
import csv
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Sum
from django.db.models.functions import ExtractMonth
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Account
from apps.accounts.utils import get_accounts_for_user, get_distributors_for_user
from apps.catalog.models import Item
from apps.routes.models import Route
from apps.events.models import Event
from apps.sales.models import SalesRecord
from apps.reports.utils import _month_add, _last_day, get_portfolio_status, get_order_history


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _truncate(s, max_len):
    s = (s or '').strip().title()
    if len(s) > max_len:
        return s[:max_len - 1] + '\u2026'
    return s


# ---------------------------------------------------------------------------
# Filter session helpers
# ---------------------------------------------------------------------------

_REPORT_FILTER_SESSION_KEY = 'report_account_sales_filters'
_REPORT_FILTER_DEFAULTS = {
    'item_name': [],
    'on_off': '',
    'city': [],
    'county': [],
    'class_of_trade': [],
    'distributor_route': [],
    'route_id': '',
    'account_name': '',
    'account_type': [],
}


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

    # ---- Base queryset for filter options (unfiltered by user filters) --
    base_accounts_qs = accounts_qs

    # ---- Build filter options from unfiltered base ----------------------
    available_counties = list(
        base_accounts_qs
        .exclude(county='')
        .exclude(county='Unknown')
        .values_list('county', flat=True)
        .distinct()
        .order_by('county')
    )
    available_account_types = list(
        base_accounts_qs
        .exclude(account_type='')
        .values_list('account_type', flat=True)
        .distinct()
        .order_by('account_type')
    )
    items_in_scope_qs = (
        Item.objects
        .filter(sales_records__account__in=base_accounts_qs)
        .values_list('name', flat=True)
        .distinct()
        .order_by('name')
    )
    filter_options = {
        'items': list(items_in_scope_qs),
        'cities': list(
            base_accounts_qs.exclude(city='')
            .values_list('city', flat=True).distinct().order_by('city')
        ),
        'counties': available_counties,
        'classes_of_trade': list(
            base_accounts_qs.exclude(account_type='')
            .values_list('account_type', flat=True).distinct().order_by('account_type')
        ),
        'distributor_routes': list(
            base_accounts_qs.exclude(distributor_route='')
            .values_list('distributor_route', flat=True).distinct().order_by('distributor_route')
        ),
    }

    # ---- Parse filters (GET params → session; no GET → restore session) -
    _filter_keys = list(_REPORT_FILTER_DEFAULTS.keys())
    is_filter_submit = any(k in request.GET for k in _filter_keys)

    if 'clear_filters' in request.GET:
        request.session.pop(_REPORT_FILTER_SESSION_KEY, None)
        filters = dict(_REPORT_FILTER_DEFAULTS)
    elif is_filter_submit:
        filters = {
            'item_name': request.GET.getlist('item_name'),
            'on_off': request.GET.get('on_off', ''),
            'city': request.GET.getlist('city'),
            'county': request.GET.getlist('county'),
            'class_of_trade': request.GET.getlist('class_of_trade'),
            'distributor_route': request.GET.getlist('distributor_route'),
            'route_id': request.GET.get('route_id', ''),
            'account_name': request.GET.get('account_name', ''),
            'account_type': request.GET.getlist('account_type'),
        }
        request.session[_REPORT_FILTER_SESSION_KEY] = filters
    else:
        stored = request.session.get(_REPORT_FILTER_SESSION_KEY, {})
        filters = {**_REPORT_FILTER_DEFAULTS, **stored}

    current_filters = filters

    active_filter_count = sum([
        1 if current_filters.get('account_name') else 0,
        1 if current_filters.get('item_name') else 0,
        1 if current_filters.get('on_off') else 0,
        1 if current_filters.get('city') else 0,
        1 if current_filters.get('county') else 0,
        1 if current_filters.get('class_of_trade') else 0,
        1 if current_filters.get('distributor_route') else 0,
        1 if current_filters.get('route_id') else 0,
    ])

    # ---- Apply account-level filters ------------------------------------
    on_off_filter = filters.get('on_off', '')
    city_filter = filters.get('city', [])
    county_filter = filters.get('county', [])
    class_of_trade_filter = filters.get('class_of_trade', [])
    distributor_route_filter = filters.get('distributor_route', [])
    route_id = filters.get('route_id', '')
    item_name_filter = filters.get('item_name', [])
    account_name_query = filters.get('account_name', '').strip()
    account_type_filter = filters.get('account_type', [])

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
    if route_id:
        try:
            route = Route.objects.get(
                pk=route_id,
                created_by=request.user,
                distributor=selected_distributor,
            )
            route_account_ids = route.route_accounts.values_list('account_id', flat=True)
            accounts_qs = accounts_qs.filter(pk__in=route_account_ids)
        except Route.DoesNotExist:
            pass  # invalid route_id — ignore filter
    if account_name_query:
        words = account_name_query.split()
        for word in words:
            accounts_qs = accounts_qs.filter(name__icontains=word)
    if account_type_filter:
        accounts_qs = accounts_qs.filter(account_type__in=account_type_filter)

    # ---- Routes for this user + distributor ----------------------------
    user_routes = Route.objects.filter(
        created_by=request.user,
        distributor=selected_distributor,
    ).order_by('name')

    # ---- Determine last full month --------------------------------------
    today = date.today()
    current_month_start = today.replace(day=1)

    # Unfiltered distributor scope for stable report structure — date window
    # and year columns must not shift when user filters are applied.
    distributor_qs = SalesRecord.objects.filter(
        account__distributor=selected_distributor,
        account__company=user.company,
    )

    max_past_sale = distributor_qs.filter(
        sale_date__lt=current_month_start,
    ).aggregate(Max('sale_date'))['sale_date__max']

    if max_past_sale is None:
        return render(request, 'reports/account_sales_by_year.html', {
            'no_data': True,
            'no_data_reason': 'No sales data is available for the selected distributor and filters.',
            'selected_distributor': selected_distributor,
            'multiple_distributors': multiple_distributors,
            'filter_options': filter_options,
            'current_filters': current_filters,
            'available_counties': available_counties,
            'available_account_types': available_account_types,
            'user_routes': user_routes,
            'active_filter_count': active_filter_count,
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
    # Uses distributor_qs so year columns stay stable regardless of filters.
    current_year = today.year
    years = sorted(
        distributor_qs
        .filter(sale_date__year__lt=current_year)
        .values_list('sale_date__year', flat=True)
        .distinct()
        .order_by('-sale_date__year')
        [:4]
    )  # ascending: oldest year left, newest year right
    most_recent_year = years[-1] if years else None
    prior_year = years[-2] if len(years) >= 2 else None

    # ---- Base queryset (filtered) for per-account row data --------------
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
            'available_counties': available_counties,
            'available_account_types': available_account_types,
            'user_routes': user_routes,
            'active_filter_count': active_filter_count,
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
        lfy_diff = (
            year_units.get(most_recent_year, 0) - year_units.get(prior_year, 0)
            if prior_year and most_recent_year else None
        )

        on_off = account.on_off_premise if account.on_off_premise in ('ON', 'OFF') else 'Unknown'

        rows.append({
            'account_id': account_id,
            'account_name': _truncate(account.name, 20),
            'city': _truncate(account.city, 15),
            'on_off': on_off,
            'year_units': year_units,
            'last_12_units': last_12_units,
            'diff': diff,
            'lfy_diff': lfy_diff,
        })

    rows.sort(key=lambda r: r['account_name'])

    # ---- Calculate totals row -------------------------------------------
    total_by_year = {y: sum(r['year_units'].get(y, 0) for r in rows) for y in years}
    total_last_12 = sum(r['last_12_units'] for r in rows)
    most_recent_year_total = total_by_year.get(most_recent_year, 0) if most_recent_year else 0
    total_diff = total_last_12 - most_recent_year_total
    total_lfy_diff = (
        total_by_year.get(most_recent_year, 0) - total_by_year.get(prior_year, 0)
        if prior_year and most_recent_year else None
    )

    return render(request, 'reports/account_sales_by_year.html', {
        'rows': rows,
        'years': years,
        'last_12_label': last_12_label,
        'last_full_month_display': last_full_month_display,
        'filter_options': filter_options,
        'current_filters': current_filters,
        'available_counties': available_counties,
        'available_account_types': available_account_types,
        'selected_distributor': selected_distributor,
        'multiple_distributors': multiple_distributors,
        'total_by_year': total_by_year,
        'total_last_12': total_last_12,
        'total_diff': total_diff,
        'total_lfy_diff': total_lfy_diff,
        'user_routes': user_routes,
        'active_filter_count': active_filter_count,
        'most_recent_year': most_recent_year,
        'prior_year': prior_year,
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

    # ---- Parse filters from GET or session ------------------------------
    _filter_keys = list(_REPORT_FILTER_DEFAULTS.keys())
    is_filter_submit = any(k in request.GET for k in _filter_keys)

    if is_filter_submit:
        filters = {
            'item_name': request.GET.getlist('item_name'),
            'on_off': request.GET.get('on_off', ''),
            'city': request.GET.getlist('city'),
            'county': request.GET.getlist('county'),
            'class_of_trade': request.GET.getlist('class_of_trade'),
            'distributor_route': request.GET.getlist('distributor_route'),
            'route_id': request.GET.get('route_id', ''),
            'account_name': request.GET.get('account_name', ''),
            'account_type': request.GET.getlist('account_type'),
        }
    else:
        stored = request.session.get(_REPORT_FILTER_SESSION_KEY, {})
        filters = {**_REPORT_FILTER_DEFAULTS, **stored}

    item_name_filter = filters.get('item_name', [])
    on_off_filter = filters.get('on_off', '')
    city_filter = filters.get('city', [])
    county_filter = filters.get('county', [])
    class_of_trade_filter = filters.get('class_of_trade', [])
    distributor_route_filter = filters.get('distributor_route', [])
    route_id = filters.get('route_id', '')
    account_name_query = filters.get('account_name', '').strip()
    account_type_filter = filters.get('account_type', [])

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
    if route_id:
        try:
            route = Route.objects.get(
                pk=route_id,
                created_by=request.user,
                distributor=selected_distributor,
            )
            route_account_ids = route.route_accounts.values_list('account_id', flat=True)
            accounts_qs = accounts_qs.filter(pk__in=route_account_ids)
        except Route.DoesNotExist:
            pass  # invalid route_id — ignore filter
    if account_name_query:
        words = account_name_query.split()
        for word in words:
            accounts_qs = accounts_qs.filter(name__icontains=word)
    if account_type_filter:
        accounts_qs = accounts_qs.filter(account_type__in=account_type_filter)

    # ---- Determine last full month --------------------------------------
    today = date.today()
    current_month_start = today.replace(day=1)

    # Unfiltered distributor scope for stable report structure.
    distributor_qs = SalesRecord.objects.filter(
        account__distributor=selected_distributor,
        account__company=user.company,
    )

    max_past_sale = distributor_qs.filter(
        sale_date__lt=current_month_start,
    ).aggregate(Max('sale_date'))['sale_date__max']

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

    # ---- Complete calendar years (up to 4) — uses distributor_qs --------
    current_year = today.year
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
        lfy_diff = (
            year_units.get(most_recent_year, 0) - year_units.get(prior_year, 0)
            if prior_year and most_recent_year else None
        )
        on_off = account.on_off_premise if account.on_off_premise in ('ON', 'OFF') else 'Unknown'

        csv_rows.append({
            'account_name': (account.name or '').strip(),
            'city': (account.city or '').strip(),
            'on_off': on_off,
            'year_units': year_units,
            'last_12_units': last_12_units,
            'diff': diff,
            'lfy_diff': lfy_diff,
        })

    csv_rows.sort(key=lambda r: r['account_name'].lower())

    # ---- Totals ---------------------------------------------------------
    total_by_year = {y: sum(r['year_units'].get(y, 0) for r in csv_rows) for y in years}
    total_last_12 = sum(r['last_12_units'] for r in csv_rows)
    most_recent_year_total = total_by_year.get(most_recent_year, 0) if most_recent_year else 0
    total_diff = total_last_12 - most_recent_year_total
    total_lfy_diff = (
        total_by_year.get(most_recent_year, 0) - total_by_year.get(prior_year, 0)
        if prior_year and most_recent_year else None
    )

    # ---- Write CSV ------------------------------------------------------
    writer = csv.writer(response)
    if prior_year and most_recent_year:
        header = (['Account Name', 'City', 'On/Off'] + [str(y) for y in years]
                  + [f'{most_recent_year} Diff', 'Last 12m', 'Diff'])
    else:
        header = ['Account Name', 'City', 'On/Off'] + [str(y) for y in years] + ['Last 12m', 'Diff']
    writer.writerow(header)

    for row in csv_rows:
        data_row = [row['account_name'], row['city'], row['on_off']]
        data_row += [row['year_units'].get(y, 0) for y in years]
        if prior_year and most_recent_year:
            data_row.append(row['lfy_diff'] if row['lfy_diff'] is not None else '')
        data_row += [row['last_12_units'], row['diff']]
        writer.writerow(data_row)

    totals_row = ['TOTAL', '', '']
    totals_row += [total_by_year.get(y, 0) for y in years]
    if prior_year and most_recent_year:
        totals_row.append(total_lfy_diff if total_lfy_diff is not None else '')
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

    # ---- Date setup via portfolio utility ---------------------------------
    today = date.today()
    current_year = today.year

    portfolio_data = get_portfolio_status(account, today=today)
    if portfolio_data is None:
        return render(request, 'reports/account_detail_sales.html', {
            'account': account,
            'no_data': True,
        })

    lfm_year = portfolio_data['lfm_year']
    lfm_month = portfolio_data['lfm_month']
    lfm_end = date(lfm_year, lfm_month, _last_day(lfm_year, lfm_month))
    last_full_month_display = date(lfm_year, lfm_month, 1).strftime('%B %Y')

    last_full_year = current_year - 1

    if lfm_year == current_year:
        actual_months = list(range(1, lfm_month + 1))
        projected_months = list(range(lfm_month + 1, 13))
    else:
        actual_months = []
        projected_months = list(range(1, 13))

    # L12M window
    w_year, w_month = _month_add(lfm_year, lfm_month, -11)
    window_start = date(w_year, w_month, 1)
    window_end = lfm_end

    # Per-item portfolio lookup for status and L12M
    portfolio_by_id = {r['item_id']: r for r in portfolio_data['rows']}

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

    # Prior year monthly data for diff calculation
    prior_year = last_full_year - 1

    prior_year_qs = SalesRecord.objects.filter(
        account=account,
        sale_date__year=prior_year,
    ).values(
        'item_id',
        month=ExtractMonth('sale_date'),
    ).annotate(total=Sum('quantity'))

    prior_year_data = {
        (r['item_id'], r['month']): r['total']
        for r in prior_year_qs
    }

    # Events per month for LFY
    lfy_events = Event.objects.filter(
        account=account,
        date__year=last_full_year,
    ).values(
        month=ExtractMonth('date')
    ).annotate(count=Count('id'))

    lfy_events_by_month = {
        r['month']: r['count'] for r in lfy_events
    }

    # Events per month for CY (actual months only)
    cy_events = Event.objects.filter(
        account=account,
        date__year=current_year,
        date__month__in=actual_months,
    ).values(
        month=ExtractMonth('date')
    ).annotate(count=Count('id'))

    cy_events_by_month = {
        r['month']: r['count'] for r in cy_events
    }

    # ---- Build per-item rows ---------------------------------------------
    all_months = list(range(1, 13))
    _STATUS_PRIORITY = {'non_buy': 1, 'declining': 2, 'steady': 3, 'growing': 4, 'new': 5}

    rows = []
    for item in items:
        item_id = item.pk

        if item_id not in portfolio_by_id:
            continue

        last_full_year_by_month = {m: lfy_data.get((item_id, m), 0) for m in all_months}
        current_actual_by_month = {m: actual_data.get((item_id, m), 0) for m in actual_months}

        last_full_year_total = sum(last_full_year_by_month.values())
        last_12_units = portfolio_by_id[item_id]['last_12']

        # Projection multiplier = last_12m / last_full_year_total
        # New item (last_full_year_total == 0): no projection, all projected months = None
        # Non-buy (last_full_year_total > 0, last_12_units == 0): multiplier = 0.0, all = 0
        if last_full_year_total == 0:
            multiplier = None
        else:
            multiplier = last_12_units / last_full_year_total

        current_projected_by_month = {}
        for m in projected_months:
            if multiplier is None:
                current_projected_by_month[m] = None
            else:
                base = last_full_year_by_month[m]
                current_projected_by_month[m] = max(0, round(base * multiplier))

        current_actual_total = sum(current_actual_by_month.values())
        current_projected_total = sum(
            v for v in current_projected_by_month.values() if v is not None
        )
        current_combined_total = current_actual_total + current_projected_total

        # Status from portfolio utility (exclusion already applied via portfolio_by_id)
        status = portfolio_by_id[item_id]['status']
        status_priority = _STATUS_PRIORITY[status]

        change_pct = (
            round((last_12_units - last_full_year_total) / last_full_year_total * 100, 1)
            if last_full_year_total > 0
            else None
        )
        prior_year_by_month = {
            m: prior_year_data.get((item_id, m), 0)
            for m in all_months
        }

        diff_lfy_by_month = {
            m: last_full_year_by_month[m] - prior_year_by_month[m]
            for m in all_months
        }

        diff_cy_actual_by_month = {
            m: current_actual_by_month[m] - last_full_year_by_month[m]
            for m in actual_months
        }

        diff_cy_projected_by_month = {
            m: (current_projected_by_month[m] - last_full_year_by_month[m])
               if current_projected_by_month[m] is not None
               else None
            for m in projected_months
        }

        rows.append({
            'item_name': item.name,
            'item_code': item.item_code,
            'brand_name': item.brand.name,
            'sort_order': item.sort_order,
            'last_full_year_by_month': last_full_year_by_month,
            'current_actual_by_month': current_actual_by_month,
            'current_projected_by_month': current_projected_by_month,
            'prior_year_by_month': prior_year_by_month,
            'diff_lfy_by_month': diff_lfy_by_month,
            'diff_cy_actual_by_month': diff_cy_actual_by_month,
            'diff_cy_projected_by_month': diff_cy_projected_by_month,
            'last_full_year_total': last_full_year_total,
            'current_actual_total': current_actual_total,
            'current_projected_total': current_projected_total,
            'current_combined_total': current_combined_total,
            'last_12_units': last_12_units,
            'diff_last_12_vs_last_year': last_12_units - last_full_year_total,
            'diff_current_vs_last_year': current_combined_total - last_full_year_total,
            'status': status,
            'status_priority': status_priority,
            'change_pct': change_pct,
        })

    # Sort by status_priority, then brand_name, sort_order, item_name
    rows.sort(key=lambda r: (r['status_priority'], r['brand_name'], r['sort_order'], r['item_name']))

    # Mark the first row of each status group for visual dividers in the template
    prev_priority = None
    for row in rows:
        row['first_in_group'] = (row['status_priority'] != prev_priority)
        prev_priority = row['status_priority']

    # Status counts for summary bar
    status_counts = {
        'non_buy': sum(1 for r in rows if r['status'] == 'non_buy'),
        'declining': sum(1 for r in rows if r['status'] == 'declining'),
        'steady': sum(1 for r in rows if r['status'] == 'steady'),
        'growing': sum(1 for r in rows if r['status'] == 'growing'),
        'new': sum(1 for r in rows if r['status'] == 'new'),
    }

    _p_last12 = sum(r['last_12_units'] for r in rows)
    _p_prior = sum(r['last_full_year_total'] for r in rows)
    portfolio_totals = {
        'last_12_total': _p_last12,
        'prior_year_total': _p_prior,
        'change_total': _p_last12 - _p_prior,
        'total_change_pct': (
            round((_p_last12 - _p_prior) / _p_prior * 100, 1)
            if _p_prior > 0
            else None
        ),
    }

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
    totals['diff_lfy_by_month'] = {
        m: sum(row['diff_lfy_by_month'][m] for row in rows)
        for m in all_months
    }
    totals['diff_cy_actual_by_month'] = {
        m: sum(row['diff_cy_actual_by_month'][m] for row in rows)
        for m in actual_months
    }
    totals['diff_cy_projected_by_month'] = {
        m: sum(
            r for r in [
                row['diff_cy_projected_by_month'][m]
                for row in rows
            ]
            if r is not None
        )
        for m in projected_months
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
        'last_reported': last_full_month_display,
        'month_names': month_names,
        'totals': totals,
        'current_year_colspan': len(actual_months) + len(projected_months),
        'lfy_events_by_month': lfy_events_by_month,
        'cy_events_by_month': cy_events_by_month,
        'status_counts': status_counts,
        'portfolio_totals': portfolio_totals,
    })


# ---------------------------------------------------------------------------
# Portfolio JSON endpoint (for Account Actions Modal)
# ---------------------------------------------------------------------------

@login_required
def account_portfolio_json(request, account_id):
    """GET /reports/account/<id>/portfolio/ — JSON portfolio data for the AAM."""
    user = request.user

    if not user.has_permission('can_view_report_account_sales'):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    account = get_object_or_404(Account, pk=account_id, company=user.company)

    if not user.has_role('supplier_admin'):
        if not get_accounts_for_user(user).filter(pk=account_id).exists():
            return JsonResponse({'error': 'Forbidden'}, status=403)

    portfolio = get_portfolio_status(account)
    orders = get_order_history(account)
    if portfolio is None:
        return JsonResponse({
            'years': [], 'rows': [], 'totals': {}, 'lfm_year': None, 'lfm_month': None,
            'orders': orders['orders'],
        })
    return JsonResponse({**portfolio, 'orders': orders['orders']})
