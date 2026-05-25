"""
Production views. Gated by can_manage_production permission.
Phase A: production_home placeholder.
Phase B: snapshot entry and management.
Phase C: tabbed home (Forecast + Inventory), demand breakdown modal.
Phase D: production PO modal endpoints, production_po_additions forecast integration.
Phase D2: COMPLETE status, Production POs tab list, single-PO modal endpoint.
"""
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Sum, Value, When
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.catalog.models import Brand, CoPacker, Item
from apps.core.filters import apply_session_filters, compute_active_filter_count
from apps.distribution.models import DistributorPO, DistributorPOLine
from .cases import compute_production_cases_view
from .forecast import compute_production_forecast, MONTH_SHORT
from .forms import MONTH_CHOICES, OwnInventorySnapshotPeriodForm
from .models import OwnInventorySnapshot, ProductionPO, ProductionPOLine


# Month names list for views (full names, indexed 0-based)
MONTH_NAMES = [m for _, m in MONTH_CHOICES]

DEFAULT_CASES_FILTERS = {
    'status': [],  # list of ProductionPO.Status choice values; empty = all statuses
}


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
    if active_tab not in ('forecast', 'inventory', 'production_pos', 'production_cases'):
        active_tab = 'forecast'

    # Build production_po_additions dict for the forecast algorithm
    production_po_additions = {}
    for po in ProductionPO.objects.filter(company=company).prefetch_related('lines__item'):
        for line in po.lines.all():
            key = (line.item_id, po.year, po.month)
            production_po_additions[key] = production_po_additions.get(key, 0.0) + float(line.quantity_cases)

    # Forecast tab — always computed eagerly so JS tab-switching works without reload
    forecast_result = compute_production_forecast(
        company,
        production_po_additions=production_po_additions or None,
    )

    # Group forecast rows by co-packer for visual grouping in template
    _grouped_rows = defaultdict(list)
    for _row in forecast_result.get('rows', []):
        _item = _row['item']
        _co_packer = _item.co_packer
        _cp_key = (_co_packer.pk, _co_packer.name) if _co_packer else (None, 'No co-packer')
        _grouped_rows[_cp_key].append(_row)
    production_forecast_grouped = []
    for _cp_key in sorted(_grouped_rows.keys(), key=lambda k: (k[0] is None, k[1])):
        production_forecast_grouped.append({
            'co_packer_name': _cp_key[1],
            'rows': sorted(_grouped_rows[_cp_key], key=lambda r: r['item'].name),
        })

    # Production PO count by month for the Production POs row in the grid
    production_po_count_rows = (
        ProductionPO.objects.filter(company=company)
        .values('year', 'month')
        .annotate(count=Count('id'))
    )
    production_pos_by_month = {
        f"{r['year']}-{r['month']:02d}": r['count']
        for r in production_po_count_rows
    }

    # Dist Orders count by month (Phase C tweak: count POs, not sum of cases)
    dist_po_count_rows = (
        DistributorPO.objects.filter(distributor__company=company)
        .values('year', 'month')
        .annotate(count=Count('id'))
    )
    dist_orders_by_month = {
        f"{r['year']}-{r['month']:02d}": r['count']
        for r in dist_po_count_rows
    }

    # Warning banner: items missing co_packer or cases_per_batch
    items_missing_config = []
    for item in Item.objects.filter(
        brand__company=company, is_active=True
    ).select_related('brand', 'co_packer').order_by('brand__name', 'sort_order', 'name'):
        if item.co_packer_id is None:
            items_missing_config.append({'item': item, 'issue': 'missing co-packer'})
        elif item.cases_per_batch is None:
            items_missing_config.append({'item': item, 'issue': 'missing cases per batch'})

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

    # Production POs tab data
    pos_qs_base = ProductionPO.objects.filter(company=company)
    has_any_pos = pos_qs_base.exists()

    filter_pos_period = request.GET.get('filter_pos_period', '').strip()
    filter_pos_status = request.GET.get('filter_pos_status', 'active').strip()
    filter_pos_co_packer = request.GET.get('filter_pos_co_packer', '').strip()

    if filter_pos_status not in ('active', 'complete', 'all'):
        filter_pos_status = 'active'

    pos_qs = pos_qs_base.select_related('co_packer')

    if filter_pos_period:
        parts = filter_pos_period.split('-')
        if len(parts) == 2:
            try:
                pos_qs = pos_qs.filter(year=int(parts[0]), month=int(parts[1]))
            except (ValueError, TypeError):
                filter_pos_period = ''

    if filter_pos_status == 'active':
        pos_qs = pos_qs.filter(status__in=['projected', 'actual'])
    elif filter_pos_status == 'complete':
        pos_qs = pos_qs.filter(status='complete')

    if filter_pos_co_packer:
        try:
            pos_qs = pos_qs.filter(co_packer_id=int(filter_pos_co_packer))
        except (ValueError, TypeError):
            filter_pos_co_packer = ''

    pos_qs = pos_qs.order_by(
        'year', 'month', 'co_packer__name', 'status',
        Case(
            When(external_po_number='', then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        ),
        'external_po_number',
    )

    production_pos_list = list(pos_qs)

    period_choices_pos = [
        (f"{p['year']}-{p['month']:02d}", f"{MONTH_NAMES[p['month'] - 1]} {p['year']}")
        for p in pos_qs_base.values('year', 'month').distinct().order_by('-year', '-month')
    ]

    co_packer_choices_pos = list(
        CoPacker.objects.filter(company=company, production_pos__isnull=False)
        .distinct()
        .order_by('name')
    )

    status_group_choices = [
        ('active', 'Active (Projected + Actual)'),
        ('complete', 'Complete'),
        ('all', 'All'),
    ]

    month_names_dict = {i: name for i, name in enumerate(MONTH_NAMES, 1)}

    # Production Cases tab — always computed (matches behaviour of other tabs)
    if request.GET.get('clear_filters') == '1':
        request.session.pop('production_cases_filters', None)
        return redirect('production_home')

    cases_active_filters, _ = apply_session_filters(
        request, 'production_cases_filters', DEFAULT_CASES_FILTERS
    )
    cases_view = compute_production_cases_view(company, cases_active_filters)
    cases_active_filter_count = compute_active_filter_count(
        cases_active_filters, DEFAULT_CASES_FILTERS
    )
    cases_filters_active = cases_active_filter_count > 0

    return render(request, 'production/production_home.html', {
        'company': company,
        'active_tab': active_tab,
        # Forecast tab
        'forecast_result': forecast_result,
        'production_forecast_grouped': production_forecast_grouped,
        'production_pos_by_month': production_pos_by_month,
        'dist_orders_by_month': dist_orders_by_month,
        'items_missing_config': items_missing_config,
        # Inventory tab
        'snapshots': snapshots,
        'period_choices': period_choices,
        'all_brands': all_brands,
        'filter_period': filter_period,
        'filter_brand': filter_brand,
        'has_any_snapshots': has_any_snapshots,
        # Production POs tab
        'production_pos_list': production_pos_list,
        'has_any_pos': has_any_pos,
        'filter_pos_period': filter_pos_period,
        'filter_pos_status': filter_pos_status,
        'filter_pos_co_packer': filter_pos_co_packer,
        'period_choices_pos': period_choices_pos,
        'co_packer_choices_pos': co_packer_choices_pos,
        'status_group_choices': status_group_choices,
        'month_names_dict': month_names_dict,
        # Production Cases tab
        'cases_view': cases_view,
        'cases_active_filters': cases_active_filters,
        'cases_active_filter_count': cases_active_filter_count,
        'cases_filters_active': cases_filters_active,
        'po_status_choices': ProductionPO.Status.choices,
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
# Shared helper: items grouped by co-packer (used by both modal endpoints)
# ---------------------------------------------------------------------------

def _build_items_by_co_packer(company):
    """Return (co_packers list, items_by_co_packer dict) for active co-packers with items."""
    co_packers = list(CoPacker.objects.filter(company=company, is_active=True).order_by('name'))
    items_by_co_packer = {}
    for cp in co_packers:
        cp_items = list(
            Item.objects.filter(
                brand__company=company,
                co_packer=cp,
                is_active=True,
                cases_per_batch__isnull=False,
            ).select_related('brand').order_by('brand__name', 'sort_order', 'name')
        )
        items_by_co_packer[str(cp.pk)] = [
            {
                'id': item.pk,
                'name': item.name,
                'brand_name': item.brand.name,
                'cases_per_batch': item.cases_per_batch,
            }
            for item in cp_items
        ]
    return co_packers, items_by_co_packer


# ---------------------------------------------------------------------------
# Production PO modal data endpoint (Phase D)
# ---------------------------------------------------------------------------

@login_required
def production_po_modal_data(request, year, month):
    denied = _require_production_permission(request)
    if denied:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    company = request.user.company

    co_packers, items_by_co_packer = _build_items_by_co_packer(company)

    saved_pos_qs = (
        ProductionPO.objects
        .filter(company=company, year=year, month=month)
        .select_related('co_packer')
        .prefetch_related('lines__item')
        .order_by('id')
    )

    saved_pos_json = []
    for po in saved_pos_qs:
        lines = [
            {
                'item_id': line.item_id,
                'batch_count': line.batch_count,
                'quantity_cases': float(line.quantity_cases),
            }
            for line in po.lines.all()
        ]
        saved_pos_json.append({
            'po_id': po.pk,
            'co_packer_id': po.co_packer_id,
            'co_packer_name': po.co_packer.name,
            'status': po.status,
            'external_po_number': po.external_po_number or '',
            'notes': po.notes or '',
            'generated_by_algorithm': po.generated_by_algorithm,
            'lines': lines,
        })

    period_label = f"{MONTH_NAMES[month - 1]} {year}"

    return JsonResponse({
        'year': year,
        'month': month,
        'mode': 'month',
        'period_label': period_label,
        'co_packers': [{'id': cp.pk, 'name': cp.name} for cp in co_packers],
        'items_by_co_packer': items_by_co_packer,
        'saved_pos': saved_pos_json,
    })


# ---------------------------------------------------------------------------
# Production PO single-mode modal data endpoint (Phase D2)
# ---------------------------------------------------------------------------

@login_required
def production_po_modal_data_single(request, po_pk):
    denied = _require_production_permission(request)
    if denied:
        return JsonResponse({'error': 'Permission denied'}, status=403)

    company = request.user.company

    try:
        po = (
            ProductionPO.objects
            .select_related('co_packer')
            .prefetch_related('lines__item')
            .get(pk=po_pk, company=company)
        )
    except ProductionPO.DoesNotExist:
        return JsonResponse({'error': 'PO not found'}, status=404)

    co_packers, items_by_co_packer = _build_items_by_co_packer(company)

    lines = [
        {
            'item_id': line.item_id,
            'batch_count': line.batch_count,
            'quantity_cases': float(line.quantity_cases),
        }
        for line in po.lines.all()
    ]

    saved_pos_json = [{
        'po_id': po.pk,
        'co_packer_id': po.co_packer_id,
        'co_packer_name': po.co_packer.name,
        'status': po.status,
        'external_po_number': po.external_po_number or '',
        'notes': po.notes or '',
        'generated_by_algorithm': po.generated_by_algorithm,
        'lines': lines,
    }]

    period_label = f"{MONTH_NAMES[po.month - 1]} {po.year}"

    return JsonResponse({
        'year': po.year,
        'month': po.month,
        'mode': 'single',
        'period_label': period_label,
        'co_packers': [{'id': cp.pk, 'name': cp.name} for cp in co_packers],
        'items_by_co_packer': items_by_co_packer,
        'saved_pos': saved_pos_json,
    })


# ---------------------------------------------------------------------------
# Production PO save endpoint (Phase D)
# ---------------------------------------------------------------------------

@login_required
def production_po_save(request):
    denied = _require_production_permission(request)
    if denied:
        return JsonResponse({'error': 'Permission denied'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    company = request.user.company

    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    year = data.get('year')
    month = data.get('month')
    pos_payload = data.get('pos', [])

    if not isinstance(year, int) or year < 2000 or year > 2100:
        return JsonResponse({'error': 'Invalid year'}, status=400)
    if not isinstance(month, int) or month < 1 or month > 12:
        return JsonResponse({'error': 'Invalid month'}, status=400)
    if not isinstance(pos_payload, list):
        return JsonResponse({'error': 'Invalid pos list'}, status=400)

    # Collect all item IDs and co-packer IDs for bulk validation
    all_item_ids = set()
    all_co_packer_ids = set()
    for po in pos_payload:
        co_packer_id = po.get('co_packer_id')
        if co_packer_id is None:
            return JsonResponse(
                {'error': 'A PO is missing a co-packer selection. Please select a co-packer for each PO.'},
                status=400,
            )
        all_co_packer_ids.add(co_packer_id)
        for line in po.get('lines', []):
            item_id = line.get('item_id')
            if item_id:
                all_item_ids.add(item_id)

    # Validate co-packers belong to company
    valid_co_packer_ids = set(
        CoPacker.objects.filter(pk__in=all_co_packer_ids, company=company)
        .values_list('pk', flat=True)
    )
    if all_co_packer_ids - valid_co_packer_ids:
        return JsonResponse({'error': 'One or more co-packers are invalid for your company.'}, status=400)

    # Validate items and build lookup map
    item_map = {
        item.pk: item
        for item in Item.objects.filter(pk__in=all_item_ids, brand__company=company)
        .select_related('co_packer')
    }
    if all_item_ids - set(item_map.keys()):
        return JsonResponse({'error': 'One or more items are invalid for your company.'}, status=400)

    # Per-PO and per-line validation
    for po in pos_payload:
        co_packer_id = po.get('co_packer_id')
        status = po.get('status', 'projected')
        external_po_number = (po.get('external_po_number') or '').strip()

        if status not in ('projected', 'actual', 'complete'):
            return JsonResponse({'error': f'Invalid status: {status}'}, status=400)
        if status in ('actual', 'complete') and not external_po_number:
            return JsonResponse({'error': 'PO number is required when status is Actual or Complete.'}, status=400)

        seen_item_ids = set()
        for line in po.get('lines', []):
            item_id = line.get('item_id')
            batch_count = line.get('batch_count')

            if item_id in seen_item_ids:
                return JsonResponse({'error': 'Duplicate item in a PO.'}, status=400)
            seen_item_ids.add(item_id)

            if not isinstance(batch_count, int) or batch_count < 0:
                return JsonResponse({'error': 'Batch count must be a non-negative integer.'}, status=400)

            item = item_map.get(item_id)
            if item is None:
                return JsonResponse({'error': 'Invalid item.'}, status=400)
            if item.co_packer_id != co_packer_id:
                return JsonResponse(
                    {'error': f'Item "{item.name}" does not belong to the selected co-packer.'},
                    status=400,
                )
            if item.cases_per_batch is None:
                return JsonResponse(
                    {'error': f'Item "{item.name}" has no cases per batch configured.'},
                    status=400,
                )

    # Atomic save
    with transaction.atomic():
        for po_data in pos_payload:
            po_id = po_data.get('po_id')
            co_packer_id = po_data['co_packer_id']
            status = po_data.get('status', 'projected')
            external_po_number = (po_data.get('external_po_number') or '').strip()
            notes = (po_data.get('notes') or '').strip()
            lines = po_data.get('lines', [])

            nonzero_lines = [line for line in lines if int(line.get('batch_count', 0)) > 0]

            if po_id is not None:
                try:
                    po = ProductionPO.objects.get(pk=po_id, company=company)
                except ProductionPO.DoesNotExist:
                    continue

                if not nonzero_lines:
                    po.delete()
                    continue

                po.co_packer_id = co_packer_id
                po.status = status
                po.external_po_number = external_po_number
                po.notes = notes
                po.generated_by_algorithm = False
                po.save()

                po.lines.all().delete()
                for line in nonzero_lines:
                    item = item_map[line['item_id']]
                    batch_count = int(line['batch_count'])
                    ProductionPOLine.objects.create(
                        po=po,
                        item=item,
                        batch_count=batch_count,
                        quantity_cases=Decimal(batch_count) * Decimal(item.cases_per_batch),
                    )
            else:
                if not nonzero_lines:
                    continue

                po = ProductionPO.objects.create(
                    company=company,
                    co_packer_id=co_packer_id,
                    year=year,
                    month=month,
                    status=status,
                    external_po_number=external_po_number,
                    notes=notes,
                    generated_by_algorithm=False,
                    created_by=request.user,
                )
                for line in nonzero_lines:
                    item = item_map[line['item_id']]
                    batch_count = int(line['batch_count'])
                    ProductionPOLine.objects.create(
                        po=po,
                        item=item,
                        batch_count=batch_count,
                        quantity_cases=Decimal(batch_count) * Decimal(item.cases_per_batch),
                    )

    return JsonResponse({'success': True})


# ---------------------------------------------------------------------------
# Production PO delete endpoint (Phase D)
# ---------------------------------------------------------------------------

@login_required
def production_po_delete(request, po_pk):
    denied = _require_production_permission(request)
    if denied:
        return JsonResponse({'error': 'Permission denied'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    company = request.user.company

    try:
        po = ProductionPO.objects.get(pk=po_pk, company=company)
    except ProductionPO.DoesNotExist:
        return JsonResponse({'error': 'PO not found'}, status=404)

    po.delete()
    return JsonResponse({'success': True})


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
