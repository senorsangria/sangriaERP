"""
Distribution views: Distributor CRUD.
Supplier Admin only.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse

from apps.core.models import User
from apps.catalog.models import Item
from .models import Distributor, DistributorItemProfile
from .forms import DistributorForm


def _require_supplier_admin(request):
    """Return 403 response if user is not a Supplier Admin, else None."""
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.has_permission('can_manage_distributors'):
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

    if not request.user.company:
        messages.error(request, "Your account is not associated with a company. Please contact your administrator.")
        return redirect('dashboard')

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

    if not request.user.company:
        messages.error(request, "Your account is not associated with a company. Please contact your administrator.")
        return redirect('dashboard')

    distributor = get_object_or_404(Distributor, pk=pk, company=request.user.company)
    can_manage_inventory = request.user.has_permission('can_manage_distributor_inventory')

    if request.method == 'POST':
        form = DistributorForm(request.POST, instance=distributor, company=request.user.company)
        if form.is_valid():
            form.save()
            messages.success(request, f'Distributor "{distributor.name}" has been updated.')
            return redirect(reverse('distributor_edit', kwargs={'pk': distributor.pk}) + '?tab=basic')
    else:
        form = DistributorForm(instance=distributor, company=request.user.company)

    items = []
    safety_stock_map = {}
    if can_manage_inventory:
        items = list(
            Item.objects.filter(brand__company=request.user.company, is_active=True)
            .select_related('brand')
            .order_by('brand__name', 'sort_order', 'name')
        )
        safety_stock_map = {
            p.item_id: p.safety_stock_cases
            for p in DistributorItemProfile.objects.filter(distributor=distributor)
        }

    active_tab = request.GET.get('tab', 'basic')
    if active_tab not in ('basic', 'order-profile', 'safety-stock'):
        active_tab = 'basic'

    return render(request, 'distribution/distributor_edit.html', {
        'form': form,
        'distributor': distributor,
        'can_manage_inventory': can_manage_inventory,
        'items': items,
        'safety_stock_map': safety_stock_map,
        'active_tab': active_tab,
    })


@login_required
def distributor_order_profile_save(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    if not request.user.has_permission('can_manage_distributor_inventory'):
        return render(request, '403.html', status=403)

    distributor = get_object_or_404(Distributor, pk=pk, company=request.user.company)

    if request.method != 'POST':
        return redirect(reverse('distributor_edit', kwargs={'pk': pk}) + '?tab=order-profile')

    raw_value = request.POST.get('order_quantity_value', '').strip()
    raw_unit = request.POST.get('order_quantity_unit', '').strip()

    value = None
    unit = None
    errors = []

    if raw_value:
        try:
            value = int(raw_value)
            if value <= 0:
                errors.append('Order quantity must be a positive number.')
                value = None
        except ValueError:
            errors.append('Order quantity must be a whole number.')

    valid_units = [c[0] for c in Distributor.OrderQuantityUnit.choices]
    if raw_unit and raw_unit not in valid_units:
        errors.append('Please select a valid order quantity unit.')
    elif raw_unit:
        unit = raw_unit

    if value is not None and unit is None:
        errors.append('Please select a unit (Pallets or Cases) when setting an order quantity.')
    if unit is not None and value is None and not errors:
        errors.append('Please enter an order quantity value when selecting a unit.')

    if errors:
        for err in errors:
            messages.error(request, err)
    else:
        distributor.order_quantity_value = value
        distributor.order_quantity_unit = unit
        distributor.save(update_fields=['order_quantity_value', 'order_quantity_unit'])
        messages.success(request, f'Order profile for "{distributor.name}" has been saved.')

    return redirect(reverse('distributor_edit', kwargs={'pk': pk}) + '?tab=order-profile')


@login_required
def distributor_safety_stock_save(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    if not request.user.has_permission('can_manage_distributor_inventory'):
        return render(request, '403.html', status=403)

    distributor = get_object_or_404(Distributor, pk=pk, company=request.user.company)

    if request.method != 'POST':
        return redirect(reverse('distributor_edit', kwargs={'pk': pk}) + '?tab=safety-stock')

    items = Item.objects.filter(
        brand__company=request.user.company, is_active=True
    ).select_related('brand')

    warning_items = []

    for item in items:
        raw = request.POST.get(f'safety_stock_{item.pk}', '').strip()

        if not raw or raw == '0':
            DistributorItemProfile.objects.filter(
                distributor=distributor, item=item
            ).delete()
            continue

        try:
            value = int(raw)
            if value <= 0:
                raise ValueError
        except ValueError:
            warning_items.append(item.name)
            continue

        profile, created = DistributorItemProfile.objects.get_or_create(
            distributor=distributor,
            item=item,
            defaults={'safety_stock_cases': value},
        )
        if not created:
            profile.safety_stock_cases = value
            profile.save(update_fields=['safety_stock_cases'])

    if warning_items:
        messages.warning(
            request,
            f'Invalid values skipped for: {", ".join(warning_items)}. '
            'Enter a positive integer or leave blank.',
        )

    messages.success(request, f'Safety stock saved for "{distributor.name}".')
    return redirect(reverse('distributor_edit', kwargs={'pk': pk}) + '?tab=safety-stock')


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
