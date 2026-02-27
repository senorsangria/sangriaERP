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
from utils.normalize import normalize_address

from .models import Account, UserCoverageArea
from .forms import AccountForm
from .constants import US_STATES, US_STATES_DICT
from .utils import get_accounts_for_user


_ALLOWED_ROLES = {
    User.Role.TERRITORY_MANAGER,
    User.Role.AMBASSADOR_MANAGER,
    User.Role.SALES_MANAGER,
    User.Role.SUPPLIER_ADMIN,
}


def _require_account_access(request):
    """Return a 403 response if the user's role is not allowed, else None."""
    if not request.user.is_authenticated:
        return redirect('login')
    if request.user.role not in _ALLOWED_ROLES:
        return render(request, '403.html', status=403)
    return None


# ---------------------------------------------------------------------------
# Coverage area helpers
# ---------------------------------------------------------------------------

def _build_enhanced_coverage_areas(user, company):
    """
    Return a list of dicts for rendering the coverage areas table.

    Each dict has:
      ca           — the UserCoverageArea object
      display_value — human-readable value
      display_state — state abbreviation (for county/city types only)
    """
    coverage_areas = (
        UserCoverageArea.objects.filter(user=user, company=company)
        .select_related('distributor', 'account')
        .order_by('coverage_type', 'state', 'county', 'city')
    )

    enhanced = []
    for ca in coverage_areas:
        ct = ca.coverage_type
        if ct == UserCoverageArea.CoverageType.DISTRIBUTOR:
            display_value = ca.distributor.name if ca.distributor else '—'
            display_state = ''
        elif ct == UserCoverageArea.CoverageType.STATE:
            display_value = US_STATES_DICT.get(ca.state, ca.state)
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

    # Apply universal coverage area scoping
    accounts = get_accounts_for_user(request.user).select_related('distributor')

    # Determine if we should show the "no coverage areas" message:
    # Only Supplier Admin and SaaS Admin are not coverage-area scoped.
    # All other roles (including Sales Manager) use coverage area filtering.
    is_privileged = request.user.role in (
        User.Role.SUPPLIER_ADMIN, User.Role.SAAS_ADMIN
    )
    show_no_coverage_message = False
    if not is_privileged:
        has_coverage = UserCoverageArea.objects.filter(
            user=request.user, company=request.user.company
        ).exists()
        if not has_coverage:
            show_no_coverage_message = True

    # Search by name or city
    search = request.GET.get('q', '').strip()
    if search:
        accounts = accounts.filter(
            Q(name__icontains=search) | Q(city__icontains=search)
        )

    # Filter: distributor
    distributor_id = request.GET.get('distributor', '').strip()
    if distributor_id == 'none':
        accounts = accounts.filter(distributor__isnull=True)
    elif distributor_id:
        accounts = accounts.filter(distributor_id=distributor_id)

    # Filter: on/off premise
    on_off = request.GET.get('on_off', '').strip()
    if on_off:
        accounts = accounts.filter(on_off_premise=on_off)

    # Filter: source (manual vs imported)
    source = request.GET.get('source', '').strip()
    if source == 'manual':
        accounts = accounts.filter(auto_created=False)
    elif source == 'imported':
        accounts = accounts.filter(auto_created=True)

    distributors = (
        Distributor.objects.filter(company=request.user.company, is_active=True)
        .order_by('name')
    )

    return render(request, 'accounts/account_list.html', {
        'accounts': accounts,
        'distributors': distributors,
        'search': search,
        'selected_distributor': distributor_id,
        'selected_on_off': on_off,
        'selected_source': source,
        'show_no_coverage_message': show_no_coverage_message,
    })


@login_required
def account_detail(request, pk):
    denied = _require_account_access(request)
    if denied:
        return denied

    # Use default manager so inactive accounts remain viewable
    account = get_object_or_404(Account, pk=pk, company=request.user.company)

    return render(request, 'accounts/account_detail.html', {
        'account': account,
    })


@login_required
def account_create(request):
    denied = _require_account_access(request)
    if denied:
        return denied

    if request.method == 'POST':
        form = AccountForm(request.POST, company=request.user.company)
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
        form = AccountForm(company=request.user.company)

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
        form = AccountForm(request.POST, instance=account, company=request.user.company)
        if form.is_valid():
            account = form.save(commit=False)
            account.address_normalized = normalize_address(account.street)
            account.city_normalized = normalize_address(account.city)
            account.state_normalized = normalize_address(account.state)
            account.save()
            messages.success(request, f'Account "{account.name}" updated successfully.')
            return redirect('account_detail', pk=account.pk)
    else:
        form = AccountForm(instance=account, company=request.user.company)

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

    return render(request, '403.html', status=403)


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

    # Coverage area assignments use union logic.
    # A user sees ALL accounts that match ANY of their coverage area entries
    # combined. Example: if a user has Distributor X AND City "Hoboken"
    # assigned, they see all accounts under Distributor X plus all accounts
    # in Hoboken, regardless of distributor. Sets are combined, not intersected.

    # Build create kwargs and check for duplicates based on type
    kwargs = {
        'user': target,
        'company': company,
        'coverage_type': coverage_type,
    }

    if coverage_type == UserCoverageArea.CoverageType.DISTRIBUTOR:
        distributor_id = request.POST.get('distributor_id', '').strip()
        if not distributor_id:
            return JsonResponse({'error': 'Please select a distributor.'}, status=400)
        try:
            distributor = Distributor.objects.get(
                pk=distributor_id, company=company, is_active=True
            )
        except Distributor.DoesNotExist:
            return JsonResponse({'error': 'Distributor not found.'}, status=400)
        kwargs['distributor'] = distributor
        exists = UserCoverageArea.objects.filter(
            user=target, company=company,
            coverage_type=coverage_type, distributor=distributor,
        ).exists()

    elif coverage_type == UserCoverageArea.CoverageType.STATE:
        state = request.POST.get('state', '').strip().upper()
        if not state:
            return JsonResponse({'error': 'Please select a state.'}, status=400)
        kwargs['state'] = state
        exists = UserCoverageArea.objects.filter(
            user=target, company=company,
            coverage_type=coverage_type, state=state,
        ).exists()

    elif coverage_type == UserCoverageArea.CoverageType.COUNTY:
        state = request.POST.get('state', '').strip().upper()
        county = request.POST.get('county', '').strip()
        if not state or state not in US_STATES_DICT:
            return JsonResponse({'error': 'Please select a valid state.'}, status=400)
        if not county:
            return JsonResponse({'error': 'Please select a county.'}, status=400)
        kwargs['state'] = state
        kwargs['county'] = county
        exists = UserCoverageArea.objects.filter(
            user=target, company=company,
            coverage_type=coverage_type, state=state, county=county,
        ).exists()

    elif coverage_type == UserCoverageArea.CoverageType.CITY:
        state = request.POST.get('state', '').strip().upper()
        city = request.POST.get('city', '').strip()
        if not state or state not in US_STATES_DICT:
            return JsonResponse({'error': 'Please select a valid state.'}, status=400)
        if not city:
            return JsonResponse({'error': 'Please select a city.'}, status=400)
        kwargs['state'] = state
        kwargs['city'] = city
        exists = UserCoverageArea.objects.filter(
            user=target, company=company,
            coverage_type=coverage_type, state=state, city=city,
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
            coverage_type=coverage_type, account=account,
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
    GET /accounts/ajax/counties/?state=NJ
    Returns distinct county values for the company and state.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

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
    GET /accounts/ajax/cities/?state=NJ
    Returns distinct city values for the company and state.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

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

    accounts = (
        get_accounts_for_user(request.user)
        .filter(
            Q(name__icontains=q)
            | Q(street__icontains=q)
            | Q(city__icontains=q)
            | Q(state__icontains=q)
        )
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
