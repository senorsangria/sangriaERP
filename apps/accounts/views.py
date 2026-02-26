"""
Accounts views: Account list, detail, create, edit, toggle.
Access: Territory Manager, Ambassador Manager, Sales Manager, Supplier Admin.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q

from apps.core.models import User
from apps.distribution.models import Distributor
from utils.normalize import normalize_address

from .models import Account
from .forms import AccountForm


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


@login_required
def account_list(request):
    denied = _require_account_access(request)
    if denied:
        return denied

    accounts = (
        Account.active_accounts
        .filter(company=request.user.company)
        .select_related('distributor')
    )

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
