"""
Production views. Gated by can_manage_production permission.
Phase A: production_home placeholder.
Phase B: snapshot entry and management.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from apps.catalog.models import Brand, Item
from .forms import MONTH_CHOICES, OwnInventorySnapshotPeriodForm
from .models import OwnInventorySnapshot


def _format_quantity_cases(qty):
    """Format Decimal quantity: integer if whole, 2 decimal places if fractional."""
    if qty == qty.to_integral_value():
        return str(int(qty))
    return f'{qty:.2f}'


def _require_production_permission(request):
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.has_permission('can_manage_production'):
        return HttpResponseForbidden('You do not have permission to access this page.')
    return None


# ---------------------------------------------------------------------------
# Production home
# ---------------------------------------------------------------------------

@login_required
def production_home(request):
    denied = _require_production_permission(request)
    if denied:
        return denied
    return render(request, 'production/production_home.html', {
        'company': request.user.company,
    })


# ---------------------------------------------------------------------------
# Inventory snapshot entry
# ---------------------------------------------------------------------------

@login_required
def production_inventory_upload(request):
    denied = _require_production_permission(request)
    if denied:
        return denied

    company = request.user.company
    items = list(
        Item.objects.filter(brand__company=company, is_active=True)
        .select_related('brand')
        .order_by('brand__name', 'sort_order', 'name')
    )

    if request.method == 'POST':
        period_form = OwnInventorySnapshotPeriodForm(request.POST)

        # Rebuild input_values keyed by item PK (int) for template re-render
        input_values = {}
        for key, val in request.POST.items():
            if key.startswith('qty_'):
                try:
                    pk = int(key[4:])
                    input_values[pk] = val
                except (ValueError, TypeError):
                    pass

        if not period_form.is_valid():
            return render(request, 'production/inventory_upload.html', {
                'company': company,
                'items': items,
                'period_form': period_form,
                'input_values': input_values,
                'item_errors': {},
                'general_error': None,
            })

        year = period_form.cleaned_data['year']
        month = period_form.cleaned_data['month']

        # Period conflict check
        if OwnInventorySnapshot.objects.filter(company=company, year=year, month=month).exists():
            month_label = dict(MONTH_CHOICES)[month]
            return render(request, 'production/inventory_upload.html', {
                'company': company,
                'items': items,
                'period_form': period_form,
                'input_values': input_values,
                'item_errors': {},
                'general_error': (
                    f'A snapshot for {month_label} {year} already exists. '
                    'Delete it from the Manage Snapshots page first.'
                ),
            })

        # Pre-validation pass — collect all errors before writing anything
        parsed_values = {}   # item_pk (int) → Decimal
        item_errors = {}     # item_pk (int) → error string
        any_input = False

        for item in items:
            raw = request.POST.get(f'qty_{item.pk}', '').strip()
            if raw == '':
                continue
            any_input = True
            try:
                value = Decimal(raw)
            except (InvalidOperation, ValueError):
                item_errors[item.pk] = 'Invalid number'
                continue
            if value < 0:
                item_errors[item.pk] = 'Cannot be negative'
                continue
            parsed_values[item.pk] = value

        if not any_input:
            messages.info(request, 'Nothing to save — please enter at least one quantity.')
            return render(request, 'production/inventory_upload.html', {
                'company': company,
                'items': items,
                'period_form': period_form,
                'input_values': {},
                'item_errors': {},
                'general_error': None,
            })

        if item_errors:
            return render(request, 'production/inventory_upload.html', {
                'company': company,
                'items': items,
                'period_form': period_form,
                'input_values': input_values,
                'item_errors': item_errors,
                'general_error': 'Please fix the errors below.',
            })

        # Save pass — all-or-nothing transaction
        item_map = {i.pk: i for i in items}
        with transaction.atomic():
            for item_pk, qty in parsed_values.items():
                OwnInventorySnapshot.objects.create(
                    company=company,
                    item=item_map[item_pk],
                    year=year,
                    month=month,
                    quantity_cases=qty,
                    created_by=request.user,
                )

        month_label = dict(MONTH_CHOICES)[month]
        messages.success(
            request,
            f'Saved {len(parsed_values)} snapshot(s) for {month_label} {year}.',
        )
        return redirect('production_inventory_snapshots')

    # GET — fresh form
    period_form = OwnInventorySnapshotPeriodForm()
    return render(request, 'production/inventory_upload.html', {
        'company': company,
        'items': items,
        'period_form': period_form,
        'input_values': {},
        'item_errors': {},
        'general_error': None,
    })


# ---------------------------------------------------------------------------
# Inventory snapshots list
# ---------------------------------------------------------------------------

@login_required
def production_inventory_snapshots(request):
    denied = _require_production_permission(request)
    if denied:
        return denied

    company = request.user.company
    snap_qs = OwnInventorySnapshot.objects.filter(company=company).select_related(
        'item', 'item__brand', 'created_by',
    )

    # Optional filters
    filter_period = request.GET.get('filter_period', '').strip()
    filter_brand = request.GET.get('filter_brand', '').strip()

    filter_year = None
    filter_month = None
    if filter_period:
        parts = filter_period.split('-')
        if len(parts) == 2:
            try:
                filter_year = int(parts[0])
                filter_month = int(parts[1])
                snap_qs = snap_qs.filter(year=filter_year, month=filter_month)
            except (ValueError, TypeError):
                filter_period = ''

    if filter_brand:
        try:
            snap_qs = snap_qs.filter(item__brand_id=int(filter_brand))
        except (ValueError, TypeError):
            filter_brand = ''

    snapshots = [
        {
            'pk': s.pk,
            'period_display': f'{dict(MONTH_CHOICES)[s.month]} {s.year}',
            'brand_name': s.item.brand.name,
            'item_name': s.item.name,
            'item_code': s.item.item_code,
            'quantity_display': _format_quantity_cases(s.quantity_cases),
            'created_by': s.created_by,
            'created_at': s.created_at,
        }
        for s in snap_qs
    ]

    # Filter option choices (populated from existing data)
    all_periods = (
        OwnInventorySnapshot.objects.filter(company=company)
        .values('year', 'month')
        .distinct()
        .order_by('-year', '-month')
    )
    period_choices = [
        {
            'value': f'{p["year"]}-{p["month"]:02d}',
            'display': f'{dict(MONTH_CHOICES)[p["month"]]} {p["year"]}',
        }
        for p in all_periods
    ]

    all_brands = Brand.objects.filter(
        company=company,
        items__own_inventory_snapshots__isnull=False,
    ).distinct().order_by('name')

    has_any_snapshots = OwnInventorySnapshot.objects.filter(company=company).exists()

    return render(request, 'production/inventory_snapshots.html', {
        'company': company,
        'snapshots': snapshots,
        'period_choices': period_choices,
        'all_brands': all_brands,
        'filter_period': filter_period,
        'filter_brand': filter_brand,
        'has_any_snapshots': has_any_snapshots,
    })


# ---------------------------------------------------------------------------
# Inventory bulk delete
# ---------------------------------------------------------------------------

@login_required
def production_inventory_bulk_delete(request):
    denied = _require_production_permission(request)
    if denied:
        return denied

    if request.method != 'POST':
        return redirect('production_inventory_snapshots')

    company = request.user.company
    raw_ids = request.POST.getlist('snapshot_ids')

    ids = []
    for raw in raw_ids:
        try:
            ids.append(int(raw))
        except (ValueError, TypeError):
            continue

    if not ids:
        messages.info(request, 'No snapshots were selected for deletion.')
        return redirect('production_inventory_snapshots')

    with transaction.atomic():
        qs = OwnInventorySnapshot.objects.filter(pk__in=ids, company=company)
        count = qs.count()
        qs.delete()

    messages.success(
        request,
        f'Deleted {count} inventory snapshot{"s" if count != 1 else ""}.',
    )
    return redirect('production_inventory_snapshots')
