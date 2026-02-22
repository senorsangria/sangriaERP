"""
Distribution views: Distributor CRUD.
Supplier Admin only.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q

from apps.core.models import User
from .models import Distributor
from .forms import DistributorForm


def _require_supplier_admin(request):
    """Return 403 response if user is not a Supplier Admin, else None."""
    if not request.user.is_authenticated:
        return redirect('login')
    if request.user.role != User.Role.SUPPLIER_ADMIN:
        return render(request, '403.html', status=403)
    return None


@login_required
def distributor_list(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    distributors = Distributor.objects.filter(company=request.user.company).order_by('name')

    search = request.GET.get('q', '').strip()
    if search:
        distributors = distributors.filter(name__icontains=search)

    return render(request, 'distribution/distributor_list.html', {
        'distributors': distributors,
        'search': search,
    })


@login_required
def distributor_create(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    if request.method == 'POST':
        form = DistributorForm(request.POST, company=request.user.company)
        if form.is_valid():
            distributor = form.save()
            messages.success(request, f'Distributor "{distributor.name}" has been created.')
            return redirect('distributor_detail', pk=distributor.pk)
    else:
        form = DistributorForm(company=request.user.company)

    return render(request, 'distribution/distributor_form.html', {
        'form': form,
        'form_title': 'Add Distributor',
    })


@login_required
def distributor_edit(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    distributor = get_object_or_404(Distributor, pk=pk, company=request.user.company)

    if request.method == 'POST':
        form = DistributorForm(request.POST, instance=distributor, company=request.user.company)
        if form.is_valid():
            form.save()
            messages.success(request, f'Distributor "{distributor.name}" has been updated.')
            return redirect('distributor_detail', pk=distributor.pk)
    else:
        form = DistributorForm(instance=distributor, company=request.user.company)

    return render(request, 'distribution/distributor_form.html', {
        'form': form,
        'distributor': distributor,
        'form_title': f'Edit Distributor — {distributor.name}',
    })


@login_required
def distributor_detail(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    distributor = get_object_or_404(Distributor, pk=pk, company=request.user.company)
    accounts = distributor.accounts.order_by('name')

    return render(request, 'distribution/distributor_detail.html', {
        'distributor': distributor,
        'accounts': accounts,
    })


@login_required
def distributor_toggle(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    distributor = get_object_or_404(Distributor, pk=pk, company=request.user.company)

    if request.method == 'POST':
        distributor.is_active = not distributor.is_active
        distributor.save(update_fields=['is_active'])
        action = 'activated' if distributor.is_active else 'deactivated'
        messages.success(request, f'Distributor "{distributor.name}" has been {action}.')
        return redirect('distributor_list')

    return render(request, 'distribution/distributor_toggle_confirm.html', {
        'distributor': distributor,
    })
