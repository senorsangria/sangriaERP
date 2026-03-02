"""
Catalog views: Brand and Item CRUD.
Supplier Admin only.
"""
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count

from apps.core.models import User
from .models import Brand, Item
from .forms import BrandForm, ItemForm


def _require_supplier_admin(request):
    """Return 403 response if user is not a Supplier Admin, else None."""
    if not request.user.is_authenticated:
        return redirect('login')
    if request.user.role != User.Role.SUPPLIER_ADMIN:
        return render(request, '403.html', status=403)
    return None


# ---------------------------------------------------------------------------
# Brand views
# ---------------------------------------------------------------------------

@login_required
def brand_list(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    brands = (
        Brand.objects
        .filter(company=request.user.company)
        .annotate(item_count=Count('items'))
        .order_by('name')
    )

    return render(request, 'catalog/brand_list.html', {'brands': brands})


@login_required
def brand_create(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    if request.method == 'POST':
        form = BrandForm(request.POST, company=request.user.company)
        if form.is_valid():
            brand = form.save()
            messages.success(request, f'Brand "{brand.name}" has been created.')
            return redirect('brand_detail', pk=brand.pk)
    else:
        form = BrandForm(company=request.user.company)

    return render(request, 'catalog/brand_form.html', {
        'form': form,
        'form_title': 'Add Brand',
        'cancel_url': 'brand_list',
    })


@login_required
def brand_edit(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    brand = get_object_or_404(Brand, pk=pk, company=request.user.company)

    if request.method == 'POST':
        form = BrandForm(request.POST, instance=brand, company=request.user.company)
        if form.is_valid():
            form.save()
            messages.success(request, f'Brand "{brand.name}" has been updated.')
            return redirect('brand_list')
    else:
        form = BrandForm(instance=brand, company=request.user.company)

    return render(request, 'catalog/brand_form.html', {
        'form': form,
        'brand': brand,
        'form_title': f'Edit Brand — {brand.name}',
        'cancel_url': None,
    })


@login_required
def brand_detail(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    brand = get_object_or_404(Brand, pk=pk, company=request.user.company)
    items = list(brand.items.order_by('sort_order', 'name'))

    return render(request, 'catalog/brand_detail.html', {
        'brand': brand,
        'items': items,
    })


@login_required
def brand_toggle(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    brand = get_object_or_404(Brand, pk=pk, company=request.user.company)

    if request.method == 'POST':
        brand.is_active = not brand.is_active
        brand.save(update_fields=['is_active'])
        action = 'activated' if brand.is_active else 'deactivated'
        messages.success(request, f'Brand "{brand.name}" has been {action}.')
        return redirect('brand_list')

    return render(request, 'catalog/brand_toggle_confirm.html', {'brand': brand})


# ---------------------------------------------------------------------------
# Item views
# ---------------------------------------------------------------------------

@login_required
def item_create(request, brand_pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    brand = get_object_or_404(Brand, pk=brand_pk, company=request.user.company)

    if request.method == 'POST':
        form = ItemForm(request.POST, brand=brand)
        if form.is_valid():
            item = form.save()
            messages.success(request, f'Item "{item.name}" has been added to {brand.name}.')
            return redirect('brand_detail', pk=brand.pk)
    else:
        form = ItemForm(brand=brand)

    return render(request, 'catalog/item_form.html', {
        'form': form,
        'brand': brand,
        'form_title': f'Add Item to {brand.name}',
    })


@login_required
def item_edit(request, brand_pk, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    brand = get_object_or_404(Brand, pk=brand_pk, company=request.user.company)
    item = get_object_or_404(Item, pk=pk, brand=brand)

    if request.method == 'POST':
        form = ItemForm(request.POST, instance=item, brand=brand)
        if form.is_valid():
            form.save()
            messages.success(request, f'Item "{item.name}" has been updated.')
            return redirect('brand_detail', pk=brand.pk)
    else:
        form = ItemForm(instance=item, brand=brand)

    return render(request, 'catalog/item_form.html', {
        'form': form,
        'brand': brand,
        'item': item,
        'form_title': f'Edit Item — {item.name}',
    })


@login_required
def item_toggle(request, brand_pk, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    brand = get_object_or_404(Brand, pk=brand_pk, company=request.user.company)
    item = get_object_or_404(Item, pk=pk, brand=brand)

    if request.method == 'POST':
        item.is_active = not item.is_active
        item.save(update_fields=['is_active'])
        action = 'activated' if item.is_active else 'deactivated'
        messages.success(request, f'Item "{item.name}" has been {action}.')
        return redirect('brand_detail', pk=brand.pk)

    return render(request, 'catalog/item_toggle_confirm.html', {
        'item': item,
        'brand': brand,
    })


# ---------------------------------------------------------------------------
# Item sort order AJAX endpoints
# ---------------------------------------------------------------------------

@login_required
def item_move_up(request, brand_pk, pk):
    """
    POST: Move an item one position up in its brand's sort order.

    Finds the item immediately before this one (lower sort_order or same
    sort_order but earlier by ID) and swaps sort_order values.
    Returns JSON with updated items list for the brand.
    """
    denied = _require_supplier_admin(request)
    if denied:
        return JsonResponse({'error': 'Permission denied.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    brand = get_object_or_404(Brand, pk=brand_pk, company=request.user.company)
    item = get_object_or_404(Item, pk=pk, brand=brand)

    items = list(brand.items.order_by('sort_order', 'name', 'pk'))
    idx = next((i for i, x in enumerate(items) if x.pk == item.pk), None)

    if idx is None or idx == 0:
        return JsonResponse({'error': 'Item is already first.'}, status=400)

    prev_item = items[idx - 1]

    # Swap sort_order values; if equal, assign distinct values
    a_order, b_order = prev_item.sort_order, item.sort_order
    if a_order == b_order:
        # Assign sequential values to maintain distinct ordering
        prev_item.sort_order = idx - 1
        item.sort_order = idx
    else:
        prev_item.sort_order, item.sort_order = b_order, a_order

    prev_item.save(update_fields=['sort_order'])
    item.save(update_fields=['sort_order'])

    return _items_json_response(brand)


@login_required
def item_move_down(request, brand_pk, pk):
    """
    POST: Move an item one position down in its brand's sort order.
    """
    denied = _require_supplier_admin(request)
    if denied:
        return JsonResponse({'error': 'Permission denied.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    brand = get_object_or_404(Brand, pk=brand_pk, company=request.user.company)
    item = get_object_or_404(Item, pk=pk, brand=brand)

    items = list(brand.items.order_by('sort_order', 'name', 'pk'))
    idx = next((i for i, x in enumerate(items) if x.pk == item.pk), None)

    if idx is None or idx >= len(items) - 1:
        return JsonResponse({'error': 'Item is already last.'}, status=400)

    next_item = items[idx + 1]

    a_order, b_order = item.sort_order, next_item.sort_order
    if a_order == b_order:
        item.sort_order = idx
        next_item.sort_order = idx + 1
    else:
        item.sort_order, next_item.sort_order = b_order, a_order

    item.save(update_fields=['sort_order'])
    next_item.save(update_fields=['sort_order'])

    return _items_json_response(brand)


def _items_json_response(brand):
    """Return JSON list of items in the brand, ordered by sort_order."""
    items = list(brand.items.order_by('sort_order', 'name', 'pk'))
    return JsonResponse({
        'items': [
            {
                'id': item.pk,
                'name': item.name,
                'item_code': item.item_code,
                'sku_number': item.sku_number or '',
                'is_active': item.is_active,
                'sort_order': item.sort_order,
            }
            for item in items
        ]
    })
