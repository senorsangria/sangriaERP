"""
Production views. Gated by can_manage_production permission.
Phase A: production_home placeholder.
Phase B: snapshot entry and management.
Phase C: tabbed home (Forecast + Inventory), demand breakdown modal.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.catalog.models import Brand, Item
from apps.distribution.models import DistributorPOLine
from .forecast import compute_production_forecast, MONTH_SHORT
from .forms import MONTH_CHOICES, OwnInventorySnapshotPeriodForm
from .models import OwnInventorySnapshot


# Month names list for views (full names, indexed 0-based)
MONTH_NAMES = [m for _, m in MONTH_CHOICES]


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


def _build_snapshot_rows(snap_qs):
    """Convert OwnInventorySnapshot queryset into display dicts for the Inventory tab."""
    return [
        {
            'pk': s.pk,
            'year': s.year,
            'month': s.month,
            'period_display': f'{MONTH_NAMES[s.month - 1]} {s.year}',
            'brand_name': s.item.brand.name,
            'item_code': s.item.item_code,
            'quantity_display': _format_quantity_cases(s.quantity_cases),
            'created_at': s.created_at,
        }
        for s in snap_qs
    ]


# ---------------------------------------------------------------------------
# Production home (tabbed: Forecast + Inventory)
# ---------------------------------------------------------------------------

@login_required
def production_home(request):
    denied = _require_production_permission(request)
    if denied:
        return denied

    company = request.user.company

    active_tab = request.GET.get('tab', 'forecast')
    if active_tab not in ('forecast', 'inventory'):
        active_tab = 'forecast'

    # Forecast tab — always computed eagerly so JS tab-switching works without reload
    forecast_result = compute_production_forecast(company)

    # Inventory tab — snapshot list with optional filters
    snap_qs = OwnInventorySnapshot.objects.filter(company=company).select_related(
        'item', 'item__brand',
    )

    filter_period = request.GET.get('filter_period', '').strip()
    filter_brand = request.GET.get('filter_brand', '').strip()

    filter_year = None
    filter_month_val = None
    if filter_period:
        parts = filter_period.split('-')
        if len(parts) == 2:
            try:
                filter_year = int(parts[0])
                filter_month_val = int(parts[1])
                snap_qs = snap_qs.filter(year=filter_year, month=filter_month_val)
            except (ValueError, TypeError):
                filter_period = ''

    if filter_brand:
        try:
            snap_qs = snap_qs.filter(item__brand_id=int(filter_brand))
        except (ValueError, TypeError):
            filter_brand = ''

    # Phase B tweaks: sort by period DESC, then brand name, then item sort_order/name
    snap_qs = snap_qs.order_by(
        '-year', '-month', 'item__brand__name', 'item__sort_order', 'item__name'
    )

    snapshots = _build_snapshot_rows(snap_qs)

    # Filter dropdown choices (populated from existing data)
    all_periods = (
        OwnInventorySnapshot.objects.filter(company=company)
        .values('year', 'month')
        .distinct()
        .order_by('-year', '-month')
    )
    period_choices = [
        {
            'value': f"{p['year']}-{p['month']:02d}",
            'display': f"{MONTH_NAMES[p['month'] - 1]} {p['year']}",
        }
        for p in all_periods
    ]

    all_brands = Brand.objects.filter(
        company=company,
        items__own_inventory_snapshots__isnull=False,
    ).distinct().order_by('name')

    has_any_snapshots = OwnInventorySnapshot.objects.filter(company=company).exists()

    return render(request, 'production/production_home.html', {
        'company': company,
        'active_tab': active_tab,
        # Forecast tab
        'forecast_result': forecast_result,
        # Inventory tab
        'snapshots': snapshots,
        'period_choices': period_choices,
        'all_brands': all_brands,
        'filter_period': filter_period,
        'filter_brand': filter_brand,
        'has_any_snapshots': has_any_snapshots,
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

        if OwnInventorySnapshot.objects.filter(company=company, year=year, month=month).exists():
            month_label = MONTH_NAMES[month - 1]
            return render(request, 'production/inventory_upload.html', {
                'company': company,
                'items': items,
                'period_form': period_form,
                'input_values': input_values,
                'item_errors': {},
                'general_error': (
                    f'A snapshot for {month_label} {year} already exists. '
                    'Return to Production and use the Inventory tab to delete it first.'
                ),
            })

        parsed_values = {}
        item_errors = {}
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

        month_label = MONTH_NAMES[month - 1]
        messages.success(
            request,
            f'Saved {len(parsed_values)} snapshot(s) for {month_label} {year}.',
        )
        return redirect(reverse('production_home') + '?tab=inventory')

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
# Inventory snapshots list — redirect stub (content moved to production_home tab)
# ---------------------------------------------------------------------------

@login_required
def production_inventory_snapshots(request):
    return redirect(reverse('production_home') + '?tab=inventory')


# ---------------------------------------------------------------------------
# Inventory bulk delete
# ---------------------------------------------------------------------------

@login_required
def production_inventory_bulk_delete(request):
    denied = _require_production_permission(request)
    if denied:
        return denied

    if request.method != 'POST':
        return redirect(reverse('production_home') + '?tab=inventory')

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
        return redirect(reverse('production_home') + '?tab=inventory')

    with transaction.atomic():
        qs = OwnInventorySnapshot.objects.filter(pk__in=ids, company=company)
        count = qs.count()
        qs.delete()

    messages.success(
        request,
        f'Deleted {count} inventory snapshot{"s" if count != 1 else ""}.',
    )
    return redirect(reverse('production_home') + '?tab=inventory')


# ---------------------------------------------------------------------------
# Demand breakdown modal endpoint
# ---------------------------------------------------------------------------

@login_required
def production_demand_modal(request, year, month):
    denied = _require_production_permission(request)
    if denied:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    company = request.user.company

    rows = (
        DistributorPOLine.objects
        .filter(
            po__distributor__company=company,
            po__year=year,
            po__month=month,
        )
        .values(
            'item_id',
            'item__name',
            'item__item_code',
            'item__brand__name',
            'item__sort_order',
            'po__distributor_id',
            'po__distributor__name',
        )
        .annotate(total_cases=Sum('quantity_cases'))
        .order_by(
            'item__brand__name', 'item__sort_order', 'item__name',
            'po__distributor__name',
        )
    )

    items_seen = {}
    distributors_seen = {}
    cells = []
    item_totals = {}
    distributor_totals = {}
    grand_total = 0.0

    for r in rows:
        iid = r['item_id']
        did = r['po__distributor_id']
        cases = round(float(r['total_cases']), 2)

        if iid not in items_seen:
            items_seen[iid] = {
                'id': iid,
                'name': r['item__name'],
                'item_code': r['item__item_code'],
            }
        if did not in distributors_seen:
            distributors_seen[did] = {
                'id': did,
                'name': r['po__distributor__name'],
            }

        cells.append({'item_id': iid, 'distributor_id': did, 'cases': cases})
        item_totals[str(iid)] = round(item_totals.get(str(iid), 0.0) + cases, 2)
        distributor_totals[str(did)] = round(distributor_totals.get(str(did), 0.0) + cases, 2)
        grand_total = round(grand_total + cases, 2)

    return JsonResponse({
        'period': f'{MONTH_NAMES[month - 1]} {year}',
        'items': list(items_seen.values()),
        'distributors': list(distributors_seen.values()),
        'cells': cells,
        'item_totals': item_totals,
        'distributor_totals': distributor_totals,
        'grand_total': grand_total,
    })
