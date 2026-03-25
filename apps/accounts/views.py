"""
Accounts views: Account list, detail, create, edit, toggle.
AJAX endpoints: states, counties, cities, account search.
Coverage area management: add, remove.
Access: Territory Manager, Ambassador Manager, Sales Manager, Supplier Admin.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.template.loader import render_to_string

from apps.core.models import User
from apps.distribution.models import Distributor
from apps.events.models import Event
from utils.normalize import normalize_address

from .models import Account, AccountContact, UserCoverageArea
from .forms import AccountForm
from .constants import US_STATES, US_STATES_DICT
from .utils import get_account_associations
from .utils import get_accounts_for_user


def _require_account_access(request):
    """Return a 403 response if the user lacks account access, else None."""
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.has_permission('can_view_accounts'):
        return render(request, '403.html', status=403)
    return None


# ---------------------------------------------------------------------------
# Coverage area helpers
# ---------------------------------------------------------------------------

def _build_enhanced_coverage_areas(user, company):
    """
    Return a list of dicts for rendering the coverage areas table.

    Each dict has:
      ca               — the UserCoverageArea object
      display_value    — human-readable value
      display_state    — state abbreviation (for county/city types only)
      distributor_name — name of the scoping distributor (always set)
    """
    coverage_areas = (
        UserCoverageArea.objects.filter(user=user, company=company)
        .select_related('distributor', 'account')
        .order_by('distributor__name', 'coverage_type', 'state', 'county', 'city')
    )

    enhanced = []
    for ca in coverage_areas:
        ct = ca.coverage_type
        distributor_name = ca.distributor.name
        if ct == UserCoverageArea.CoverageType.DISTRIBUTOR:
            display_value = distributor_name
            display_state = ''
        elif ct == UserCoverageArea.CoverageType.COUNTY:
            display_value = ca.county or '—'
            display_state = ca.state
        elif ct == UserCoverageArea.CoverageType.CITY:
            display_value = ca.city or '—'
            display_state = ca.state
        elif ct == UserCoverageArea.CoverageType.ACCOUNT:
            if ca.account:
                display_value = (
                    f'{ca.account.name}, {ca.account.city} {ca.account.state}'
                )
            else:
                display_value = '—'
            display_state = ''
        else:
            display_value = '—'
            display_state = ''

        enhanced.append({
            'ca': ca,
            'display_value': display_value,
            'display_state': display_state,
            'distributor_name': distributor_name,
        })

    return enhanced


def _render_coverage_areas_table(user, company):
    """Render the coverage areas table partial as an HTML string (for AJAX)."""
    enhanced = _build_enhanced_coverage_areas(user, company)
    return render_to_string(
        'accounts/_coverage_areas_table.html',
        {
            'enhanced_coverage_areas': enhanced,
            'target': user,
        },
    )


# ---------------------------------------------------------------------------
# Account views
# ---------------------------------------------------------------------------

@login_required
def account_list(request):
    denied = _require_account_access(request)
    if denied:
        return denied

    company = request.user.company

    # ---- Session-based filter persistence ----
    SESSION_KEY = 'account_list_filters'

    if request.GET.get('clear_filters'):
        request.session.pop(SESSION_KEY, None)
        return redirect('account_list')

    _known_filter_keys = ('q', 'distributor', 'on_off', 'source', 'active_status')
    if any(k in request.GET for k in _known_filter_keys):
        filters = {
            'q':             request.GET.get('q', '').strip(),
            'distributor':   request.GET.get('distributor', '').strip(),
            'on_off':        request.GET.get('on_off', '').strip(),
            'source':        request.GET.get('source', '').strip(),
            'active_status': request.GET.get('active_status', '').strip(),
        }
        request.session[SESSION_KEY] = filters
    else:
        filters = request.session.get(SESSION_KEY, {
            'q': '', 'distributor': '', 'on_off': '', 'source': '', 'active_status': '',
        })

    search        = filters.get('q', '')
    distributor_id = filters.get('distributor', '')
    on_off        = filters.get('on_off', '')
    source        = filters.get('source', '')
    active_status = filters.get('active_status', '')

    # ---- Base queryset ----
    # Ambassador Manager: sees only accounts linked to their own events
    # (created by them, or where they are ambassador or event_manager).
    if request.user.is_ambassador_manager:
        ambassador_q = (
            Q(events__created_by=request.user) |
            Q(events__ambassador=request.user) |
            Q(events__event_manager=request.user)
        )
        if active_status == 'inactive':
            accounts = (
                Account.objects.filter(
                    company=company,
                    is_active=False,
                    merged_into__isnull=True,
                )
                .filter(ambassador_q)
                .distinct()
            )
        else:
            accounts = (
                Account.active_accounts
                .filter(company=company)
                .filter(ambassador_q)
                .distinct()
            )
    # For the inactive filter we cannot use the active_accounts manager (it
    # filters is_active=True).  Build the appropriate base queryset, applying
    # the same coverage-area scoping that get_accounts_for_user() provides.
    elif active_status == 'inactive':
        is_privileged = request.user.has_permission('can_view_all_accounts')
        if is_privileged:
            accounts = Account.objects.filter(
                company=company, is_active=False, merged_into__isnull=True
            )
        else:
            coverage_areas = list(
                UserCoverageArea.objects.filter(user=request.user, company=company)
                .select_related('distributor', 'account')
            )
            if not coverage_areas:
                accounts = Account.objects.none()
            else:
                cq = Q(pk__in=[])
                for ca in coverage_areas:
                    ct = ca.coverage_type
                    if ct == UserCoverageArea.CoverageType.DISTRIBUTOR and ca.distributor_id:
                        cq |= Q(distributor_id=ca.distributor_id)
                    elif ct == UserCoverageArea.CoverageType.COUNTY and ca.county and ca.state:
                        cq |= Q(county=ca.county, state_normalized=ca.state)
                    elif ct == UserCoverageArea.CoverageType.CITY and ca.city and ca.state:
                        cq |= Q(city=ca.city, state_normalized=ca.state)
                    elif ct == UserCoverageArea.CoverageType.ACCOUNT and ca.account_id:
                        cq |= Q(pk=ca.account_id)
                accounts = Account.objects.filter(
                    company=company, is_active=False, merged_into__isnull=True
                ).filter(cq)
    else:
        # Default (All) and Active: use active_accounts manager via helper
        accounts = get_accounts_for_user(request.user)

    accounts = accounts.select_related('distributor')

    # Determine if we should show the "no coverage areas" message
    is_privileged = request.user.has_permission('can_view_all_accounts')
    show_no_coverage_message = False
    if not is_privileged:
        has_coverage = UserCoverageArea.objects.filter(
            user=request.user, company=company
        ).exists()
        if not has_coverage:
            show_no_coverage_message = True

    # ---- Apply remaining filters ----

    # Search by name or city
    if search:
        accounts = accounts.filter(
            Q(name__icontains=search) | Q(city__icontains=search)
        )

    # Filter: distributor
    if distributor_id == 'none':
        accounts = accounts.filter(distributor__isnull=True)
    elif distributor_id:
        accounts = accounts.filter(distributor_id=distributor_id)

    # Filter: on/off premise
    if on_off:
        accounts = accounts.filter(on_off_premise=on_off)

    # Filter: source (manual vs imported)
    if source == 'manual':
        accounts = accounts.filter(auto_created=False)
    elif source == 'imported':
        accounts = accounts.filter(auto_created=True)

    # Dynamic filter options from visible accounts
    filter_distributors = (
        Distributor.objects.filter(
            pk__in=accounts.values('distributor_id').distinct()
        ).order_by('name')
    )

    on_off_values = list(
        accounts.exclude(on_off_premise='')
        .values_list('on_off_premise', flat=True)
        .distinct()
        .order_by('on_off_premise')
    )

    has_manual = accounts.filter(auto_created=False).exists()
    has_imported = accounts.filter(auto_created=True).exists()

    filters_active = bool(search or distributor_id or on_off or source or active_status)

    can_bulk_delete = (
        request.user.has_permission('can_delete_accounts')
        and request.user.has_role('supplier_admin')
    )

    return render(request, 'accounts/account_list.html', {
        'accounts':            accounts,
        'filter_distributors': filter_distributors,
        'on_off_values':       on_off_values,
        'has_manual':          has_manual,
        'has_imported':        has_imported,
        'filters':             filters,
        'filters_active':      filters_active,
        'show_no_coverage_message': show_no_coverage_message,
        'can_bulk_delete':     can_bulk_delete,
    })


@login_required
def account_detail(request, pk):
    denied = _require_account_access(request)
    if denied:
        return denied

    # Use default manager so inactive accounts remain viewable
    account = get_object_or_404(Account, pk=pk, company=request.user.company)

    # Gather AccountItem records grouped by brand (brand name → sort_order → name)
    account_items_qs = (
        account.account_items
        .select_related('item__brand')
        .order_by('item__brand__name', 'item__sort_order', 'item__name')
    )
    items_by_brand = []
    current_brand = None
    for ai in account_items_qs:
        brand = ai.item.brand
        if brand != current_brand:
            current_brand = brand
            items_by_brand.append({'brand': brand, 'items': []})
        items_by_brand[-1]['items'].append(ai)

    return render(request, 'accounts/account_detail.html', {
        'account': account,
        'items_by_brand': items_by_brand,
    })


@login_required
def account_detail_combined(request, pk):
    """Combined account detail: Account Details tab + Account Sales tab."""
    if not request.user.has_permission('can_view_accounts'):
        messages.error(request, 'You do not have permission to view accounts.')
        return redirect('dashboard')

    account = get_object_or_404(Account, pk=pk, company=request.user.company)

    active_tab = request.GET.get('tab', 'details')
    if active_tab not in ('details', 'sales'):
        active_tab = 'details'

    return_to = request.GET.get('return_to', 'accounts')
    if return_to not in ('accounts', 'report'):
        return_to = 'accounts'

    can_view_sales = request.user.has_permission('can_view_report_account_sales')

    # Redirect to details tab if sales tab requested without permission
    if active_tab == 'sales' and not can_view_sales:
        from django.urls import reverse as _rev
        url = _rev('account_detail_combined', args=[pk]) + '?tab=details'
        if return_to != 'accounts':
            url += f'&return_to={return_to}'
        return redirect(url)

    # ---- Details tab data -----------------------------------------------
    account_items_qs = (
        account.account_items
        .select_related('item__brand')
        .order_by('item__brand__name', 'item__sort_order', 'item__name')
    )
    items_by_brand = []
    current_brand = None
    for ai in account_items_qs:
        brand = ai.item.brand
        if brand != current_brand:
            current_brand = brand
            items_by_brand.append({'brand': brand, 'items': []})
        items_by_brand[-1]['items'].append(ai)

    from apps.events.views import _get_visible_events
    recent_events = list(
        _get_visible_events(request.user)
        .filter(account=account)
        .select_related('ambassador')
        .order_by('-date', '-pk')[:10]
    )

    ctx = {
        'account': account,
        'active_tab': active_tab,
        'return_to': return_to,
        'can_view_sales': can_view_sales,
        'items_by_brand': items_by_brand,
        'recent_events': recent_events,
        'no_sales_data': False,
        'contact_count': account.contacts.count(),
        'can_manage_contacts': request.user.has_permission('can_manage_contacts'),
    }

    # ---- Sales tab data (only when user has permission) -----------------
    if can_view_sales:
        from calendar import monthrange
        from datetime import date as _date
        from django.db.models import Count, Max, Sum
        from django.db.models.functions import ExtractMonth
        from apps.catalog.models import Item
        from apps.sales.models import SalesRecord

        def _mo_add(year, month, delta):
            month += delta
            while month > 12: month -= 12; year += 1
            while month < 1:  month += 12; year -= 1
            return year, month

        def _last_day(year, month):
            return monthrange(year, month)[1]

        today = _date.today()
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
            ctx['no_sales_data'] = True
        else:
            lfm_year = max_past_sale.year
            lfm_month = max_past_sale.month
            lfm_end = _date(lfm_year, lfm_month, _last_day(lfm_year, lfm_month))
            last_full_month_display = _date(lfm_year, lfm_month, 1).strftime('%B %Y')
            last_full_year = current_year - 1

            if lfm_year == current_year:
                actual_months = list(range(1, lfm_month + 1))
                projected_months = list(range(lfm_month + 1, 13))
            else:
                actual_months = []
                projected_months = list(range(1, 13))

            w_year, w_month = _mo_add(lfm_year, lfm_month, -11)
            window_start = _date(w_year, w_month, 1)
            window_end = lfm_end

            items = (
                Item.objects
                .filter(sales_records__account=account)
                .distinct()
                .select_related('brand')
                .order_by('brand__name', 'sort_order', 'name')
            )

            lfy_data = {}
            for r in (
                SalesRecord.objects
                .filter(account=account, sale_date__year=last_full_year)
                .values('item_id', 'sale_date__month')
                .annotate(units=Sum('quantity'))
            ):
                lfy_data[(r['item_id'], r['sale_date__month'])] = r['units']

            actual_data = {}
            if actual_months:
                for r in (
                    SalesRecord.objects
                    .filter(account=account, sale_date__year=current_year,
                            sale_date__month__in=actual_months)
                    .values('item_id', 'sale_date__month')
                    .annotate(units=Sum('quantity'))
                ):
                    actual_data[(r['item_id'], r['sale_date__month'])] = r['units']

            last12_data = {}
            for r in (
                SalesRecord.objects
                .filter(account=account, sale_date__gte=window_start, sale_date__lte=window_end)
                .values('item_id')
                .annotate(units=Sum('quantity'))
            ):
                last12_data[r['item_id']] = r['units']

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

            lfy_events = Event.objects.filter(
                account=account,
                date__year=last_full_year,
            ).values(
                month=ExtractMonth('date')
            ).annotate(count=Count('id'))

            lfy_events_by_month = {
                r['month']: r['count'] for r in lfy_events
            }

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

            all_months = list(range(1, 13))
            rows = []
            for item in items:
                iid = item.pk
                lfy_by_m = {m: lfy_data.get((iid, m), 0) for m in all_months}
                act_by_m = {m: actual_data.get((iid, m), 0) for m in actual_months}
                lfy_total = sum(lfy_by_m.values())
                l12 = last12_data.get(iid, 0)

                multiplier = None if lfy_total == 0 else l12 / lfy_total

                proj_by_m = {}
                for m in projected_months:
                    if multiplier is None:
                        proj_by_m[m] = None
                    else:
                        proj_by_m[m] = max(0, round(lfy_by_m[m] * multiplier))

                act_total  = sum(act_by_m.values())
                proj_total = sum(v for v in proj_by_m.values() if v is not None)
                comb_total = act_total + proj_total

                if lfy_total == 0 and l12 == 0:
                    continue

                if lfy_total > 0 and l12 == 0:
                    status, spri = 'non_buy', 1
                elif l12 < lfy_total:
                    status, spri = 'declining', 2
                elif l12 == lfy_total:
                    status, spri = 'steady', 3
                elif lfy_total == 0 and l12 > 0:
                    status, spri = 'new', 5
                else:
                    status, spri = 'growing', 4

                prior_by_m = {
                    m: prior_year_data.get((iid, m), 0)
                    for m in all_months
                }

                diff_lfy_by_m = {
                    m: lfy_by_m[m] - prior_by_m[m]
                    for m in all_months
                }

                diff_act_by_m = {
                    m: act_by_m[m] - lfy_by_m[m]
                    for m in actual_months
                }

                diff_proj_by_m = {
                    m: (proj_by_m[m] - lfy_by_m[m])
                       if proj_by_m[m] is not None
                       else None
                    for m in projected_months
                }

                rows.append({
                    'item_name': item.name, 'item_code': item.item_code,
                    'brand_name': item.brand.name, 'sort_order': item.sort_order,
                    'last_full_year_by_month': lfy_by_m,
                    'current_actual_by_month': act_by_m,
                    'current_projected_by_month': proj_by_m,
                    'prior_year_by_month': prior_by_m,
                    'diff_lfy_by_month': diff_lfy_by_m,
                    'diff_cy_actual_by_month': diff_act_by_m,
                    'diff_cy_projected_by_month': diff_proj_by_m,
                    'last_full_year_total': lfy_total,
                    'current_actual_total': act_total,
                    'current_projected_total': proj_total,
                    'current_combined_total': comb_total,
                    'last_12_units': l12,
                    'diff_last_12_vs_last_year': l12 - lfy_total,
                    'diff_current_vs_last_year': comb_total - lfy_total,
                    'status': status, 'status_priority': spri,
                    'change_pct': (
                        round((l12 - lfy_total) / lfy_total * 100, 1)
                        if lfy_total > 0 else None
                    ),
                })

            rows.sort(key=lambda r: (
                r['status_priority'], r['brand_name'], r['sort_order'], r['item_name']
            ))
            prev_pri = None
            for row in rows:
                row['first_in_group'] = (row['status_priority'] != prev_pri)
                prev_pri = row['status_priority']

            status_counts = {
                s: sum(1 for r in rows if r['status'] == s)
                for s in ('non_buy', 'declining', 'steady', 'growing', 'new')
            }
            _pl12 = sum(r['last_12_units'] for r in rows)
            _ppr  = sum(r['last_full_year_total'] for r in rows)

            totals = {
                'last_full_year_by_month': {
                    m: sum(r['last_full_year_by_month'][m] for r in rows)
                    for m in all_months
                },
                'current_actual_by_month': {
                    m: sum(r['current_actual_by_month'].get(m, 0) for r in rows)
                    for m in actual_months
                },
                'current_projected_by_month': {
                    m: sum((r['current_projected_by_month'].get(m) or 0) for r in rows)
                    for m in projected_months
                },
                'last_full_year_total': sum(r['last_full_year_total'] for r in rows),
                'current_actual_total': sum(r['current_actual_total'] for r in rows),
                'current_projected_total': sum(r['current_projected_total'] for r in rows),
                'current_combined_total': sum(r['current_combined_total'] for r in rows),
                'last_12_total': _pl12,
                'diff_last_12_vs_last_year': sum(r['diff_last_12_vs_last_year'] for r in rows),
                'diff_current_vs_last_year': sum(r['diff_current_vs_last_year'] for r in rows),
            }
            totals['diff_lfy_by_month'] = {
                m: sum(r['diff_lfy_by_month'][m] for r in rows)
                for m in all_months
            }
            totals['diff_cy_actual_by_month'] = {
                m: sum(r['diff_cy_actual_by_month'][m] for r in rows)
                for m in actual_months
            }
            totals['diff_cy_projected_by_month'] = {
                m: sum(
                    v for v in [r['diff_cy_projected_by_month'][m] for r in rows]
                    if v is not None
                )
                for m in projected_months
            }

            ctx.update({
                'rows': rows,
                'last_full_year': last_full_year,
                'current_year': current_year,
                'all_months': all_months,
                'actual_months': actual_months,
                'projected_months': projected_months,
                'last_full_month_display': last_full_month_display,
                'last_reported': last_full_month_display,
                'month_names': {i: _date(2000, i, 1).strftime('%b') for i in all_months},
                'totals': totals,
                'current_year_colspan': len(actual_months) + len(projected_months),
                'lfy_events_by_month': lfy_events_by_month,
                'cy_events_by_month': cy_events_by_month,
                'status_counts': status_counts,
                'portfolio_totals': {
                    'last_12_total': _pl12, 'prior_year_total': _ppr,
                    'change_total': _pl12 - _ppr,
                    'total_change_pct': (
                        round((_pl12 - _ppr) / _ppr * 100, 1) if _ppr > 0 else None
                    ),
                },
            })

    return render(request, 'accounts/account_detail_combined.html', ctx)


@login_required
def account_create(request):
    denied = _require_account_access(request)
    if denied:
        return denied

    if request.method == 'POST':
        form = AccountForm(request.POST, company=request.user.company, user=request.user)
        if form.is_valid():
            account = form.save(commit=False)
            account.company = request.user.company
            account.auto_created = False
            account.address_normalized = normalize_address(account.street)
            account.city_normalized = normalize_address(account.city)
            account.state_normalized = normalize_address(account.state)
            account.save()
            messages.success(request, f'Account "{account.name}" created successfully.')
            return redirect('account_detail_combined', pk=account.pk)
    else:
        form = AccountForm(company=request.user.company, user=request.user)

    return render(request, 'accounts/account_form.html', {
        'form': form,
        'form_title': 'Create Account',
    })


@login_required
def account_edit(request, pk):
    denied = _require_account_access(request)
    if denied:
        return denied

    account = get_object_or_404(Account, pk=pk, company=request.user.company)

    if account.auto_created:
        messages.error(
            request,
            'This account was created from a sales data import and cannot be edited manually.',
        )
        return redirect('account_detail_combined', pk=pk)

    if request.method == 'POST':
        form = AccountForm(request.POST, instance=account, company=request.user.company, user=request.user)
        if form.is_valid():
            account = form.save(commit=False)
            account.address_normalized = normalize_address(account.street)
            account.city_normalized = normalize_address(account.city)
            account.state_normalized = normalize_address(account.state)
            account.save()
            messages.success(request, f'Account "{account.name}" updated successfully.')
            return redirect('account_detail_combined', pk=account.pk)
    else:
        form = AccountForm(instance=account, company=request.user.company, user=request.user)

    return render(request, 'accounts/account_form.html', {
        'form': form,
        'account': account,
        'form_title': f'Edit Account — {account.name}',
    })


@login_required
def account_toggle(request, pk):
    denied = _require_account_access(request)
    if denied:
        return denied

    account = get_object_or_404(Account, pk=pk, company=request.user.company)

    if request.method == 'POST':
        account.is_active = not account.is_active
        account.save(update_fields=['is_active'])
        if account.is_active:
            messages.success(request, f'Account "{account.name}" has been reactivated.')
        else:
            messages.success(request, f'Account "{account.name}" has been deactivated.')
        return redirect('account_detail_combined', pk=account.pk)

    return redirect('account_detail_combined', pk=account.pk)


@login_required
def account_delete(request, pk):
    """POST: Delete a manually created account if it has no associated data."""
    denied = _require_account_access(request)
    if denied:
        return denied

    account = get_object_or_404(Account, pk=pk, company=request.user.company)

    if account.auto_created:
        messages.error(request, 'Imported accounts cannot be deleted.')
        return redirect('account_detail_combined', pk=pk)

    if request.method == 'POST':
        associations = get_account_associations(account)

        blocking = [
            f'{count} {key.replace("_", " ")}'
            for key, count in associations.items()
            if count > 0
        ]

        if blocking:
            messages.error(
                request,
                'This account cannot be deleted because it has associated data: '
                + ', '.join(blocking)
                + '. You can deactivate the account instead.',
            )
            return redirect('account_detail_combined', pk=pk)

        account_name = account.name
        account.delete()
        messages.success(request, f'Account "{account_name}" has been deleted.')
        return redirect('account_list')

    return redirect('account_detail_combined', pk=pk)


# ---------------------------------------------------------------------------
# Bulk delete (Supplier Admin only)
# ---------------------------------------------------------------------------

@login_required
def account_bulk_delete(request):
    """
    POST: Delete or deactivate a list of accounts.

    Gate: requires can_delete_accounts permission AND supplier_admin role.

    For each selected account:
      - If no associations: delete the account permanently.
      - If has associations: deactivate (is_active = False) instead.

    Returns a redirect to account_list with a summary message.
    """
    if not request.user.is_authenticated:
        return render(request, '403.html', status=403)
    if not (request.user.has_permission('can_delete_accounts')
            and request.user.has_role('supplier_admin')):
        return render(request, '403.html', status=403)

    if request.method != 'POST':
        return redirect('account_list')

    company = request.user.company
    pks_raw = request.POST.getlist('account_pks')

    # Sanitise to integer PKs
    try:
        pks = [int(pk) for pk in pks_raw if str(pk).strip().isdigit()]
    except (ValueError, TypeError):
        pks = []

    if not pks:
        messages.warning(request, 'No accounts selected.')
        return redirect('account_list')

    accounts = Account.objects.filter(pk__in=pks, company=company)

    deleted_count = 0
    deactivated_count = 0

    for account in accounts:
        associations = get_account_associations(account)
        has_data = any(v > 0 for v in associations.values())
        if has_data:
            account.is_active = False
            account.save(update_fields=['is_active'])
            deactivated_count += 1
        else:
            account.delete()
            deleted_count += 1

    parts = []
    if deleted_count:
        parts.append(f'{deleted_count} account{"s" if deleted_count != 1 else ""} deleted')
    if deactivated_count:
        parts.append(
            f'{deactivated_count} account{"s" if deactivated_count != 1 else ""} '
            f'deactivated (had associated data)'
        )
    msg = ', '.join(parts) + '.' if parts else 'No accounts were changed.'
    messages.success(request, msg)
    return redirect('account_list')


# ---------------------------------------------------------------------------
# Coverage area CRUD (Supplier Admin only, AJAX-driven)
# ---------------------------------------------------------------------------

def coverage_area_add(request, user_pk):
    """
    POST: Add a UserCoverageArea record for a user.
    Returns JSON {success, html} or {error}.
    Only Supplier Admins may call this endpoint.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)
    if not request.user.is_supplier_admin:
        return JsonResponse({'error': 'Access denied.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    target = get_object_or_404(User, pk=user_pk, company=request.user.company)
    company = request.user.company

    coverage_type = request.POST.get('coverage_type', '').strip()
    valid_types = {ct[0] for ct in UserCoverageArea.CoverageType.choices}
    if coverage_type not in valid_types:
        return JsonResponse({'error': 'Invalid coverage type.'}, status=400)

    # Distributor is always required — every coverage area row is scoped to one.
    distributor_id = request.POST.get('distributor_id', '').strip()
    if not distributor_id:
        return JsonResponse({'error': 'Please select a distributor.'}, status=400)
    try:
        distributor = Distributor.objects.get(pk=distributor_id, company=company, is_active=True)
    except Distributor.DoesNotExist:
        return JsonResponse({'error': 'Distributor not found.'}, status=400)

    # Build create kwargs and check for duplicates based on type.
    # distributor is always included in the duplicate key.
    kwargs = {
        'user': target,
        'company': company,
        'coverage_type': coverage_type,
        'distributor': distributor,
    }

    if coverage_type == UserCoverageArea.CoverageType.DISTRIBUTOR:
        exists = UserCoverageArea.objects.filter(
            user=target, company=company,
            coverage_type=coverage_type, distributor=distributor,
        ).exists()

    elif coverage_type == UserCoverageArea.CoverageType.COUNTY:
        county = request.POST.get('county', '').strip()
        if not county:
            return JsonResponse({'error': 'Please select a county.'}, status=400)
        # Derive state from accounts so get_accounts_for_user keeps working.
        state = (
            Account.active_accounts
            .filter(company=company, distributor=distributor, county=county)
            .exclude(state_normalized='')
            .values_list('state_normalized', flat=True)
            .first() or ''
        )
        kwargs['county'] = county
        kwargs['state'] = state
        exists = UserCoverageArea.objects.filter(
            user=target, company=company,
            coverage_type=coverage_type, distributor=distributor, county=county,
        ).exists()

    elif coverage_type == UserCoverageArea.CoverageType.CITY:
        city = request.POST.get('city', '').strip()
        if not city:
            return JsonResponse({'error': 'Please select a city.'}, status=400)
        # Derive state from accounts so get_accounts_for_user keeps working.
        state = (
            Account.active_accounts
            .filter(company=company, distributor=distributor, city=city)
            .exclude(state_normalized='')
            .values_list('state_normalized', flat=True)
            .first() or ''
        )
        kwargs['city'] = city
        kwargs['state'] = state
        exists = UserCoverageArea.objects.filter(
            user=target, company=company,
            coverage_type=coverage_type, distributor=distributor, city=city,
        ).exists()

    elif coverage_type == UserCoverageArea.CoverageType.ACCOUNT:
        account_id = request.POST.get('account_id', '').strip()
        if not account_id:
            return JsonResponse({'error': 'Please select an account.'}, status=400)
        try:
            account = Account.active_accounts.get(pk=account_id, company=company)
        except Account.DoesNotExist:
            return JsonResponse({'error': 'Account not found.'}, status=400)
        kwargs['account'] = account
        exists = UserCoverageArea.objects.filter(
            user=target, company=company,
            coverage_type=coverage_type, distributor=distributor, account=account,
        ).exists()

    else:
        return JsonResponse({'error': 'Invalid coverage type.'}, status=400)

    if exists:
        return JsonResponse(
            {'error': 'This coverage area is already assigned to this user.'},
            status=400,
        )

    UserCoverageArea.objects.create(**kwargs)

    html = _render_coverage_areas_table(target, company)
    return JsonResponse({'success': True, 'html': html})


def coverage_area_remove(request, user_pk, ca_pk):
    """
    POST: Remove a UserCoverageArea record.
    Returns JSON {success, html} or {error}.
    Only Supplier Admins may call this endpoint.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)
    if not request.user.is_supplier_admin:
        return JsonResponse({'error': 'Access denied.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed.'}, status=405)

    target = get_object_or_404(User, pk=user_pk, company=request.user.company)
    company = request.user.company

    ca = get_object_or_404(UserCoverageArea, pk=ca_pk, user=target, company=company)
    ca.delete()

    html = _render_coverage_areas_table(target, company)
    return JsonResponse({'success': True, 'html': html})


# ---------------------------------------------------------------------------
# AJAX endpoints (authentication required, company-scoped)
# ---------------------------------------------------------------------------

def ajax_states(request):
    """
    GET /accounts/ajax/states/
    Returns distinct state_normalized values for the company.
    Only states with at least one active account are returned.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

    states = (
        Account.active_accounts
        .filter(company=request.user.company)
        .exclude(state_normalized='')
        .values_list('state_normalized', flat=True)
        .distinct()
        .order_by('state_normalized')
    )

    return JsonResponse({'states': list(states)})


def ajax_counties(request):
    """
    GET /accounts/ajax/counties/?distributor_id=5
    Returns distinct county values for the company and distributor.
    Falls back to ?state=NJ (legacy) if distributor_id is absent.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

    distributor_id = request.GET.get('distributor_id', '').strip()
    if distributor_id:
        qs = Account.active_accounts.filter(
            company=request.user.company,
            distributor_id=distributor_id,
        ).exclude(county='').exclude(county='Unknown')
        counties = list(qs.values_list('county', flat=True).distinct().order_by('county'))
        return JsonResponse({'counties': counties})

    # Fallback: state-based (existing behaviour, called from elsewhere)
    state = request.GET.get('state', '').strip().upper()
    if not state:
        return JsonResponse({'counties': []})

    counties = (
        Account.active_accounts
        .filter(company=request.user.company, state_normalized=state)
        .exclude(county='')
        .exclude(county='Unknown')
        .values_list('county', flat=True)
        .distinct()
        .order_by('county')
    )
    return JsonResponse({'counties': list(counties)})


def ajax_cities(request):
    """
    GET /accounts/ajax/cities/?distributor_id=5
    Returns distinct city values for the company and distributor.
    Falls back to ?state=NJ (legacy) if distributor_id is absent.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

    distributor_id = request.GET.get('distributor_id', '').strip()
    if distributor_id:
        qs = Account.active_accounts.filter(
            company=request.user.company,
            distributor_id=distributor_id,
        ).exclude(city='')
        cities = list(qs.values_list('city', flat=True).distinct().order_by('city'))
        return JsonResponse({'cities': cities})

    # Fallback: state-based (existing behaviour, called from elsewhere)
    state = request.GET.get('state', '').strip().upper()
    if not state:
        return JsonResponse({'cities': []})

    cities = (
        Account.active_accounts
        .filter(company=request.user.company, state_normalized=state)
        .exclude(city='')
        .values_list('city', flat=True)
        .distinct()
        .order_by('city')
    )
    return JsonResponse({'cities': list(cities)})


def ajax_accounts_search(request):
    """
    GET /accounts/ajax/search/?q=barrel
    Returns accounts matching the search query (max 20), filtered through
    get_accounts_for_user() to respect coverage area rules.
    Searches name, street, city, state.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'accounts': []})

    # Build an AND-of-ORs Q: every whitespace-separated term must appear in
    # at least one of the four searchable fields (cross-field multi-word support).
    term_q = Q()
    for term in q.split():
        term_q &= (
            Q(name__icontains=term)
            | Q(street__icontains=term)
            | Q(city__icontains=term)
            | Q(state__icontains=term)
        )

    accounts = (
        get_accounts_for_user(request.user)
        .filter(term_q)
        .select_related('distributor')
        .order_by('name')
        [:20]
    )

    result = [
        {
            'id': a.pk,
            'name': a.name,
            'street': a.street,
            'city': a.city,
            'state': a.state,
            'distributor': a.distributor.name if a.distributor else '',
        }
        for a in accounts
    ]

    return JsonResponse({'accounts': result})


# ---------------------------------------------------------------------------
# Contact API views
# ---------------------------------------------------------------------------

def _contact_to_dict(c):
    return {
        'id': c.pk,
        'name': c.name,
        'title': c.title,
        'title_display': c.get_title_display(),
        'email': c.email,
        'phone': c.phone,
        'note': c.note,
        'is_tasting_contact': c.is_tasting_contact,
    }


@login_required
def contact_list(request, pk):
    """GET /accounts/<pk>/contacts/ — list contacts for an account."""
    if not request.user.has_permission('can_view_accounts'):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    account = get_object_or_404(Account, pk=pk, company=request.user.company)
    contacts = [_contact_to_dict(c) for c in account.contacts.all()]
    return JsonResponse({'contacts': contacts})


@login_required
def contact_create(request, pk):
    """POST /accounts/<pk>/contacts/create/ — create a new contact."""
    if not request.user.has_permission('can_manage_contacts'):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    if request.method != 'POST' or request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Bad request'}, status=400)
    account = get_object_or_404(Account, pk=pk, company=request.user.company)

    import json
    try:
        data = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    name = (data.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Name is required.'})

    contact = AccountContact.objects.create(
        account=account,
        name=name,
        title=data.get('title', AccountContact.Title.OTHER),
        email=(data.get('email') or '').strip(),
        phone=(data.get('phone') or '').strip(),
        note=(data.get('note') or '').strip(),
        is_tasting_contact=bool(data.get('is_tasting_contact', False)),
    )
    return JsonResponse({'success': True, 'contact': _contact_to_dict(contact)})


@login_required
def contact_update(request, pk, cpk):
    """POST /accounts/<pk>/contacts/<cpk>/update/ — update a contact."""
    if not request.user.has_permission('can_manage_contacts'):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    if request.method != 'POST' or request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Bad request'}, status=400)
    account = get_object_or_404(Account, pk=pk, company=request.user.company)
    contact = get_object_or_404(AccountContact, pk=cpk, account=account)

    import json
    try:
        data = json.loads(request.body)
    except (ValueError, KeyError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    name = (data.get('name') or '').strip()
    if not name:
        return JsonResponse({'success': False, 'error': 'Name is required.'})

    contact.name = name
    contact.title = data.get('title', contact.title)
    contact.email = (data.get('email') or '').strip()
    contact.phone = (data.get('phone') or '').strip()
    contact.note = (data.get('note') or '').strip()
    contact.is_tasting_contact = bool(data.get('is_tasting_contact', False))
    contact.save()
    return JsonResponse({'success': True, 'contact': _contact_to_dict(contact)})


@login_required
def contact_delete(request, pk, cpk):
    """POST /accounts/<pk>/contacts/<cpk>/delete/ — delete a contact."""
    if not request.user.has_permission('can_manage_contacts'):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    if request.method != 'POST' or request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Bad request'}, status=400)
    account = get_object_or_404(Account, pk=pk, company=request.user.company)
    contact = get_object_or_404(AccountContact, pk=cpk, account=account)
    contact.delete()
    return JsonResponse({'success': True})
