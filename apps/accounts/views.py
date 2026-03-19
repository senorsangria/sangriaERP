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

from .models import Account, UserCoverageArea
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

    distributors = (
        Distributor.objects.filter(company=company, is_active=True)
        .order_by('name')
    )

    filters_active = bool(search or distributor_id or on_off or source or active_status)

    can_bulk_delete = (
        request.user.has_permission('can_delete_accounts')
        and request.user.has_role('supplier_admin')
    )

    return render(request, 'accounts/account_list.html', {
        'accounts':            accounts,
        'distributors':        distributors,
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
            return redirect('account_detail', pk=account.pk)
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
        return redirect('account_detail', pk=pk)

    if request.method == 'POST':
        form = AccountForm(request.POST, instance=account, company=request.user.company, user=request.user)
        if form.is_valid():
            account = form.save(commit=False)
            account.address_normalized = normalize_address(account.street)
            account.city_normalized = normalize_address(account.city)
            account.state_normalized = normalize_address(account.state)
            account.save()
            messages.success(request, f'Account "{account.name}" updated successfully.')
            return redirect('account_detail', pk=account.pk)
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
        return redirect('account_detail', pk=account.pk)

    return redirect('account_detail', pk=account.pk)


@login_required
def account_delete(request, pk):
    """POST: Delete a manually created account if it has no associated data."""
    denied = _require_account_access(request)
    if denied:
        return denied

    account = get_object_or_404(Account, pk=pk, company=request.user.company)

    if account.auto_created:
        messages.error(request, 'Imported accounts cannot be deleted.')
        return redirect('account_detail', pk=pk)

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
            return redirect('account_detail', pk=pk)

        account_name = account.name
        account.delete()
        messages.success(request, f'Account "{account_name}" has been deleted.')
        return redirect('account_list')

    return redirect('account_detail', pk=pk)


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
