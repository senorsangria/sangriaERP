"""
Distribution views: Distributor CRUD and inventory snapshot import.
Supplier Admin only.
"""
import calendar
import csv
import json
import os
import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from django.http import HttpResponseForbidden
from django.utils.http import url_has_allowed_host_and_scheme

from apps.catalog.models import Brand as CatalogBrand, Item
from apps.imports.models import ItemMapping
from .forecast import compute_distributor_forecast, compute_group_forecast
from .forms import DistributorForm, DistributorGroupForm, InventoryImportUploadForm
from .order_generation import generate_projected_orders, suggest_po_for_month
from apps.core.filters import apply_session_filters, compute_active_filter_count
from .models import (
    Distributor, DistributorGroup, DistributorItemProfile, DistributorPO, DistributorPOLine,
    InventoryImportBatch, InventorySnapshot, assign_so_number,
)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def _require_supplier_admin(request):
    """Return 403 response if user is not a Supplier Admin, else None."""
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.has_permission('can_manage_distributors'):
        return render(request, '403.html', status=403)
    return None


def _require_inventory_permission(request):
    """Return redirect with error if user lacks can_manage_distributor_inventory."""
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.has_permission('can_manage_distributor_inventory'):
        messages.error(request, 'You do not have permission to manage inventory imports.')
        return redirect(reverse('distributor_list') + '?tab=inventory')
    return None


# ---------------------------------------------------------------------------
# Inventory upload helpers
# ---------------------------------------------------------------------------

def _inv_temp_dir():
    from django.conf import settings
    path = os.path.join(settings.MEDIA_ROOT, 'temp_inventory_imports')
    os.makedirs(path, exist_ok=True)
    return path


def _inv_save_temp_file(uploaded_file):
    """Save uploaded CSV to temp storage; return file path."""
    ext = os.path.splitext(uploaded_file.name)[1] or '.csv'
    filename = f'{uuid.uuid4().hex}{ext}'
    filepath = os.path.join(_inv_temp_dir(), filename)
    with open(filepath, 'wb') as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)
    return filepath


def _inv_cleanup_temp_file(filepath):
    """Delete a temp file if it exists."""
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except OSError:
            pass


def _format_quantity_cases(qty):
    """Format Decimal quantity: integer if whole, 2 decimal places if fractional."""
    if qty == qty.to_integral_value():
        return str(int(qty))
    return f'{qty:.2f}'


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

def parse_inventory_csv(filepath):
    """
    Parse an inventory snapshot CSV file.

    Expected format:
      Row 1: headers — column 1 "Distributors", column 2 "Item Name ID",
             column 3 any name (quantity). Exactly 3 columns required.
      Rows 2+: data rows.

    Returns (rows, errors) where rows is a list of dicts:
        {row_number, distributor_name, item_code, quantity (Decimal)}
    and errors is a list of error strings.
    """
    rows = []
    errors = []

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)

        try:
            header_row = next(reader)
        except StopIteration:
            return [], ['CSV file is empty or has no header row.']

        headers = [h.strip() for h in header_row]

        if len(headers) != 3:
            return [], [
                f'Expected exactly 3 columns (Distributors, Item Name ID, quantity). '
                f'Got {len(headers)} column(s).'
            ]

        if headers[0].lower() != 'distributors' or headers[1].lower() != 'item name id':
            return [], [
                f"Expected columns 'Distributors' and 'Item Name ID' in positions 1 and 2. "
                f"Got: {', '.join(repr(h) for h in headers[:2])}"
            ]

        for line_num, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue  # silently skip blank rows

            if len(row) < 3 or not row[0].strip() or not row[1].strip() or not row[2].strip():
                errors.append(f'Row {line_num} is missing required data.')
                continue

            distributor_name = row[0].strip()
            item_code = row[1].strip()
            raw_qty = row[2].strip()

            try:
                qty = Decimal(raw_qty)
            except InvalidOperation:
                errors.append(f"Row {line_num}: invalid quantity '{raw_qty}'")
                continue

            if qty < 0:
                errors.append(f"Row {line_num}: invalid quantity '{raw_qty}' (negative values not allowed)")
                continue

            rows.append({
                'row_number': line_num,
                'distributor_name': distributor_name,
                'item_code': item_code,
                'quantity': qty,
            })

    return rows, errors


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_inventory_import(rows, company, year, month):
    """
    Validate parsed CSV rows against the database.

    Steps:
      1. Resolve each distributor by name (case-insensitive, active only).
      2. Detect unmapped item codes (no MAPPED ItemMapping exists).
      3. Check for period conflicts (snapshot already exists for that distributor/period).

    Returns (resolved_rows, errors, unmapped_by_dist_id) where:
      - resolved_rows: list of {distributor, item, quantity, row_number} on success
      - errors: non-mapping validation errors (distributor not found, period conflict)
      - unmapped_by_dist_id: {str(dist_id): [raw_code, ...]} when unmapped codes found

    Callers should:
      - If errors: show error page
      - Elif unmapped_by_dist_id: redirect to mapping resolution UI
      - Else: proceed with resolved_rows
    """
    errors = []

    # Step 1: Distributor resolution
    unique_dist_names = {r['distributor_name'] for r in rows}
    distributor_map = {}  # {csv_name: Distributor}
    for name in sorted(unique_dist_names):
        dist = Distributor.objects.filter(
            company=company, name__iexact=name, is_active=True
        ).first()
        if dist is None:
            errors.append(f"Distributor not found in system: '{name}'")
        else:
            distributor_map[name] = dist

    if errors:
        return [], errors, {}

    # Step 2: Item code resolution via ItemMapping
    unique_pairs = {(r['distributor_name'], r['item_code']) for r in rows}
    item_map = {}  # {(csv_dist_name, item_code): Item}
    unmapped_by_dist = {}  # {dist.id: [raw_code, ...]}

    for dist_name, item_code in sorted(unique_pairs):
        dist = distributor_map[dist_name]
        mapping = ItemMapping.objects.filter(
            company=company,
            distributor=dist,
            raw_item_name__iexact=item_code,
        ).first()

        if mapping is None or mapping.status != ItemMapping.Status.MAPPED or mapping.mapped_item is None:
            unmapped_by_dist.setdefault(dist.id, []).append(item_code)
        else:
            item_map[(dist_name, item_code)] = mapping.mapped_item

    if unmapped_by_dist:
        # Return unmapped codes grouped by distributor ID (string keys for JSON serialisation)
        return [], [], {str(k): v for k, v in unmapped_by_dist.items()}

    # Step 3: Period conflict check
    month_name = calendar.month_name[month]
    for csv_name, dist in sorted(distributor_map.items(), key=lambda x: x[0]):
        if InventorySnapshot.objects.filter(distributor=dist, year=year, month=month).exists():
            errors.append(
                f"Distributor '{dist.name}' already has inventory data for "
                f"{month_name} {year}. Delete the existing snapshot before re-uploading."
            )

    if errors:
        return [], errors, {}

    # Build resolved rows
    resolved_rows = []
    for row in rows:
        dist = distributor_map[row['distributor_name']]
        item = item_map[(row['distributor_name'], row['item_code'])]
        resolved_rows.append({
            'distributor': dist,
            'item': item,
            'quantity': row['quantity'],
            'row_number': row['row_number'],
        })

    return resolved_rows, [], {}


# ---------------------------------------------------------------------------
# Distributor Group CRUD
# ---------------------------------------------------------------------------

@login_required
def distributor_group_list(request):
    if not request.user.has_permission('can_manage_distributor_groups'):
        return HttpResponseForbidden('You do not have permission to access this page.')
    company = request.user.company
    groups = (
        DistributorGroup.objects.filter(company=company)
        .select_related('primary_distributor')
        .prefetch_related('members')
        .order_by('name')
    )
    return render(request, 'distribution/distributor_group_list.html', {
        'groups': groups,
    })


@login_required
def distributor_group_create(request):
    if not request.user.has_permission('can_manage_distributor_groups'):
        return HttpResponseForbidden('You do not have permission to access this page.')
    company = request.user.company
    if request.method == 'POST':
        form = DistributorGroupForm(request.POST, company=company)
        if form.is_valid():
            group = form.save()
            messages.success(request, f'Created distributor group "{group.name}".')
            return redirect('distributor_group_list')
    else:
        form = DistributorGroupForm(company=company)
    conflicts = getattr(form, '_conflicts', None)
    return render(request, 'distribution/distributor_group_form.html', {
        'form': form,
        'is_create': True,
        'conflicts': conflicts,
    })


@login_required
def distributor_group_edit(request, pk):
    if not request.user.has_permission('can_manage_distributor_groups'):
        return HttpResponseForbidden('You do not have permission to access this page.')
    company = request.user.company
    group = get_object_or_404(DistributorGroup, pk=pk, company=company)
    next_url = request.GET.get('next', '') or request.POST.get('next', '')
    if next_url and not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = ''
    if request.method == 'POST':
        form = DistributorGroupForm(request.POST, instance=group, company=company)
        if form.is_valid():
            form.save()
            messages.success(request, f'Updated distributor group "{group.name}".')
            if next_url:
                return redirect(next_url)
            return redirect('distributor_group_list')
    else:
        form = DistributorGroupForm(instance=group, company=company)
    conflicts = getattr(form, '_conflicts', None)
    return render(request, 'distribution/distributor_group_form.html', {
        'form': form,
        'group': group,
        'is_create': False,
        'next_url': next_url,
        'conflicts': conflicts,
    })


@login_required
def distributor_group_delete(request, pk):
    if not request.user.has_permission('can_manage_distributor_groups'):
        return HttpResponseForbidden('You do not have permission to access this page.')
    company = request.user.company
    group = get_object_or_404(DistributorGroup, pk=pk, company=company)
    if request.method == 'POST':
        name = group.name
        member_count = group.members.count()
        with transaction.atomic():
            group.delete()
        messages.success(request, f'Deleted distributor group "{name}". {member_count} distributors are now ungrouped.')
        return redirect('distributor_group_list')
    return render(request, 'distribution/distributor_group_confirm_delete.html', {
        'group': group,
        'member_count': group.members.count(),
    })


# ---------------------------------------------------------------------------
# Distributor POs tab — filter defaults and helper
# ---------------------------------------------------------------------------

_DEFAULT_DISTRIBUTOR_POS_FILTERS = {
    'status': [],
    'distributor': [],
    'item': [],
    'so_number': '',
}


def _get_filtered_distributor_pos_queryset(company, filters):
    """Return DistributorPO queryset — all statuses, user filters via modal."""
    qs = DistributorPO.objects.filter(distributor__company=company).select_related('distributor')

    statuses = filters.get('status', [])
    if statuses:
        qs = qs.filter(status__in=statuses)

    distributors = filters.get('distributor', [])
    if distributors:
        qs = qs.filter(distributor_id__in=distributors)

    items = filters.get('item', [])
    if items:
        qs = qs.filter(lines__item_id__in=items).distinct()

    so_search = filters.get('so_number', '')
    if so_search:
        try:
            qs = qs.filter(so_number=int(so_search))
        except ValueError:
            pass

    return qs


# ---------------------------------------------------------------------------
# Distributor list (3-tab page)
# ---------------------------------------------------------------------------

@login_required
def distributor_list(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    can_manage_inventory = request.user.has_permission('can_manage_distributor_inventory')
    company = request.user.company

    base_qs = Distributor.objects.filter(company=company)

    # Distributors tab always renders the grouped view (no search box — the
    # distributor list is short enough not to need search).
    from itertools import groupby
    grouped_qs = (
        base_qs.filter(group__isnull=False)
        .select_related('group', 'group__primary_distributor')
        .order_by('group__name', 'name')
    )
    ungrouped_qs = base_qs.filter(group__isnull=True).order_by('name')
    grouped_data = []
    for group_obj, members_iter in groupby(grouped_qs, key=lambda d: d.group):
        grouped_data.append({
            'group': group_obj,
            'members': list(members_iter),
        })
    ungrouped_data = list(ungrouped_qs)
    is_grouped_view = True

    total_count = sum(len(g['members']) for g in grouped_data) + len(ungrouped_data)

    active_tab = request.GET.get('tab', 'distributors')
    if active_tab not in ('distributors', 'inventory', 'forecast', 'distributor_pos'):
        active_tab = 'distributors'
    if active_tab in ('inventory', 'forecast', 'distributor_pos') and not can_manage_inventory:
        active_tab = 'distributors'

    # Inventory tab data
    inventory_rows = []
    inventory_distributor_choices = []
    inventory_brand_choices = []
    inventory_period_choices = []
    inv_distributor_filter = ''
    inv_brand_filter = ''
    inv_period_filter = ''
    inventory_sort = 'distributor'
    has_any_snapshots = False

    # Forecast tab data
    forecast_result = None
    orders_result = None
    forecast_distributor = None
    forecast_group = None
    primary_distributor = None
    available_distributors = []
    available_groups = []

    # Distributor POs tab data
    pos_page_obj = None
    pos_rows = []
    all_items = []
    brand_groups = []
    all_distributors_for_filter = []
    status_choices = []
    po_status_choices = []
    pos_active_filters = {}
    pos_active_filter_count = 0
    pos_filters_active = False
    move_modal_data_json = '{}'
    pos_data_json = '{}'
    selected_totals_json = '{}'
    current_inventory_json = '{}'
    selected_po_count = 0

    if can_manage_inventory:
        inv_distributor_filter = request.GET.get('inv_distributor', '')
        inv_brand_filter = request.GET.get('inv_brand', '')
        inv_period_filter = request.GET.get('inv_period', '')
        inventory_sort = request.GET.get('sort', 'distributor')
        if inventory_sort not in ('distributor', 'brand', 'item', 'item_code', 'quantity', 'period'):
            inventory_sort = 'distributor'

        has_any_snapshots = InventorySnapshot.objects.filter(
            distributor__company=company
        ).exists()

        # Filter option choices (populated from existing snapshots)
        inventory_distributor_choices = list(
            Distributor.objects.filter(
                company=company,
                inventory_snapshots__isnull=False,
            ).distinct().order_by('name')
        )
        inventory_brand_choices = list(
            CatalogBrand.objects.filter(
                company=company,
                items__inventory_snapshots__isnull=False,
            ).distinct().order_by('name')
        )
        period_values = (
            InventorySnapshot.objects.filter(distributor__company=company)
            .values_list('year', 'month')
            .distinct()
            .order_by('-year', '-month')
        )
        month_abbr = {i: calendar.month_abbr[i] for i in range(1, 13)}
        inventory_period_choices = [
            {
                'value': f'{y}-{m:02d}',
                'display': f'{month_abbr[m]} {y}',
            }
            for y, m in period_values
        ]

        # Base queryset with optional filters
        snap_qs = InventorySnapshot.objects.filter(
            distributor__company=company
        ).select_related('distributor', 'item', 'item__brand')

        if inv_distributor_filter:
            snap_qs = snap_qs.filter(distributor_id=inv_distributor_filter)
        if inv_brand_filter:
            snap_qs = snap_qs.filter(item__brand_id=inv_brand_filter)

        # Parse period filter (YYYY-MM)
        inv_year = None
        inv_month_val = None
        if inv_period_filter:
            parts = inv_period_filter.split('-')
            if len(parts) == 2:
                try:
                    inv_year = int(parts[0])
                    inv_month_val = int(parts[1])
                except ValueError:
                    inv_period_filter = ''

        if inv_year and inv_month_val:
            snap_qs = snap_qs.filter(year=inv_year, month=inv_month_val)

        snapshots_list = list(
            snap_qs.order_by('distributor__name', 'item__brand__name', 'item__name', '-year', '-month')
        )

        # Sort
        if inventory_sort == 'brand':
            snapshots_list.sort(key=lambda s: s.item.brand.name.lower())
        elif inventory_sort == 'item':
            snapshots_list.sort(key=lambda s: s.item.name.lower())
        elif inventory_sort == 'item_code':
            snapshots_list.sort(key=lambda s: s.item.item_code.lower())
        elif inventory_sort == 'quantity':
            snapshots_list.sort(key=lambda s: s.quantity_cases, reverse=True)
        elif inventory_sort == 'period':
            snapshots_list.sort(key=lambda s: (s.year, s.month), reverse=True)
        else:
            snapshots_list.sort(key=lambda s: (
                s.distributor.name.lower(),
                s.item.brand.name.lower(),
                s.item.name.lower(),
            ))

        inventory_rows = [
            {
                'pk': s.pk,
                'distributor_name': s.distributor.name,
                'brand_name': s.item.brand.name,
                'item_name': s.item.name,
                'item_code': s.item.item_code,
                'quantity_display': _format_quantity_cases(s.quantity_cases),
                'period_display': f'{month_abbr[s.month]} {s.year}',
                'uploaded': s.created_at,
            }
            for s in snapshots_list
        ]

        # Forecast tab — active distributors only in the selection dropdown.
        available_distributors = list(
            Distributor.objects.filter(company=company, is_active=True)
            .select_related('group', 'group__primary_distributor')
            .order_by('name')
        )
        available_groups = list(DistributorGroup.objects.filter(company=company).order_by('name'))
        # No auto-selection: the forecast is only computed when a distributor or
        # group is explicitly chosen via ?forecast_distributor= / ?forecast_group=.
        # The dropdown defaults to a "Select a distributor" prompt with a friendly
        # empty state below. The two params are mutually exclusive; if both are
        # present, the group takes precedence.
        forecast_group_pk = request.GET.get('forecast_group', '')
        forecast_dist_pk = request.GET.get('forecast_distributor', '')
        if forecast_group_pk:
            # Group mode: render the aggregated group forecast in-tab. Mirrors the
            # standalone distributor_group_forecast view's body (kept as a fallback
            # until the modal is unified). Distributor mode is skipped entirely.
            try:
                group_pk = int(forecast_group_pk)
                forecast_group = DistributorGroup.objects.filter(
                    company=company, pk=group_pk
                ).select_related('primary_distributor').first()
            except (ValueError, TypeError):
                forecast_group = None
            if forecast_group:
                primary_distributor = forecast_group.primary_distributor
                members = list(forecast_group.members.all())

                # Build po_additions and saved_pos_by_month from all member POs
                saved_pos = list(
                    DistributorPO.objects.filter(distributor__in=members)
                    .prefetch_related('lines')
                )
                po_additions = {}
                saved_pos_by_month = {}
                for po in saved_pos:
                    ym = (po.year, po.month)
                    saved_pos_by_month.setdefault(ym, []).append(po)
                    for line in po.lines.all():
                        key = (line.item_id, po.year, po.month)
                        po_additions[key] = po_additions.get(key, 0.0) + float(line.quantity_cases)

                forecast_result = compute_group_forecast(
                    forecast_group, po_additions=po_additions or None,
                )

                # Orders are only generated when the group's snapshots align.
                if forecast_result.get('alignment_status') == 'ok':
                    orders_result = generate_projected_orders(
                        primary_distributor, forecast_result,
                    )
                    for slot in orders_result.get('orders_per_horizon', []):
                        ym = (slot['year'], slot['month'])
                        saved_count = len(saved_pos_by_month.get(ym, []))
                        slot['saved_count'] = saved_count
                        slot['total_count'] = saved_count
        elif forecast_dist_pk:
            try:
                pk = int(forecast_dist_pk)
                forecast_distributor = next(
                    (d for d in available_distributors if d.pk == pk), None
                )
            except (ValueError, TypeError):
                forecast_distributor = None
        if forecast_distributor:
            # Build po_additions from saved POs so the forecast reflects pending orders
            saved_pos = list(
                DistributorPO.objects.filter(distributor=forecast_distributor)
                .prefetch_related('lines')
            )
            po_additions = {}
            saved_pos_by_month = {}
            for po in saved_pos:
                ym = (po.year, po.month)
                saved_pos_by_month.setdefault(ym, []).append(po)
                for line in po.lines.all():
                    key = (line.item_id, po.year, po.month)
                    po_additions[key] = po_additions.get(key, 0.0) + float(line.quantity_cases)

            forecast_result = compute_distributor_forecast(
                forecast_distributor,
                po_additions=po_additions or None,
            )
            orders_result = generate_projected_orders(forecast_distributor, forecast_result)

            # Augment each slot with saved_count and total_count
            for slot in orders_result['orders_per_horizon']:
                ym = (slot['year'], slot['month'])
                saved_count = len(saved_pos_by_month.get(ym, []))
                slot['saved_count'] = saved_count
                slot['total_count'] = saved_count

        # Always available for modal status dropdown
        po_status_choices = DistributorPO.Status.choices

        # Distributor POs tab
        if active_tab == 'distributor_pos':
            session_key = 'distributor_pos_filters'

            if request.GET.get('clear_filters') == '1':
                request.session.pop(session_key, None)
                return redirect(f"{reverse('distributor_list')}?tab={active_tab}")

            pos_active_filters, _ = apply_session_filters(
                request, session_key, _DEFAULT_DISTRIBUTOR_POS_FILTERS
            )

            pos_qs = _get_filtered_distributor_pos_queryset(company, pos_active_filters)

            # Ordering is fixed: year, month, then manual within-month position.
            # There are no column-header sorts — manual sort_position is the only
            # within-month order (seeded by data migration, maintained by the move
            # endpoint). distributor__name is a final tiebreaker.
            pos_qs = pos_qs.order_by('year', 'month', 'sort_position', 'distributor__name')

            pos_qs = pos_qs.prefetch_related('lines__item__brand')

            paginator = Paginator(pos_qs, 50)
            page_number = request.GET.get('page', 1)
            pos_page_obj = paginator.get_page(page_number)

            # Items ordered by brand, then sort_order, then name
            all_items = list(
                Item.objects.filter(brand__company=company, is_active=True)
                .select_related('brand')
                .order_by('brand__name', 'sort_order', 'name')
            )

            # Group items by brand for header span calculation
            brand_groups = []
            current_brand = None
            current_brand_items = []
            for item in all_items:
                if item.brand.name != current_brand:
                    if current_brand_items:
                        brand_groups.append({'brand_name': current_brand, 'items': current_brand_items})
                    current_brand = item.brand.name
                    current_brand_items = []
                current_brand_items.append(item)
            if current_brand_items:
                brand_groups.append({'brand_name': current_brand, 'items': current_brand_items})

            # Compute month parity across the full ordered queryset for page-stable banding.
            # Walk distinct (year, month) in render order and assign alternating 0/1 parity.
            month_parity_map = {}
            _parity = 0
            _prev_ym = None
            for (_y, _m) in pos_qs.values_list('year', 'month'):
                _ym = (_y, _m)
                if _ym not in month_parity_map:
                    if _prev_ym is not None:
                        _parity = 1 - _parity
                    month_parity_map[_ym] = _parity
                    _prev_ym = _ym

            pos_rows = []
            pos_data = {}  # {po_pk: {str(item_id): cases}} for current-page rows
            for po in pos_page_obj:
                line_map = {line.item_id: float(line.quantity_cases) for line in po.lines.all()}
                item_cases = [line_map.get(item.pk) for item in all_items]
                # PO Month label as 'YY-Mon (e.g., "'26-Nov")
                po_month_label = f"'{str(po.year)[-2:]}-{calendar.month_abbr[po.month]}"
                pos_rows.append({
                    'po': po,
                    'item_cases': item_cases,
                    'po_month_label': po_month_label,
                    'is_selected': po.selected_for_projection,
                    'band_parity': month_parity_map.get((po.year, po.month), 0),
                })
                pos_data[po.pk] = {str(k): v for k, v in line_map.items()}

            # Projection tool: sum selected POs' cases per item across ALL pages
            selected_pos = (
                DistributorPO.objects.filter(
                    distributor__company=company,
                    selected_for_projection=True,
                )
                .prefetch_related('lines')
            )
            selected_totals = {}
            selected_po_count = 0
            for po in selected_pos:
                selected_po_count += 1
                for line in po.lines.all():
                    selected_totals[line.item_id] = (
                        selected_totals.get(line.item_id, 0) + float(line.quantity_cases)
                    )

            # Current inventory per item (ad-hoc, company-scoped)
            current_inventory = {
                str(item.pk): float(item.forecast_current_inventory or 0)
                for item in all_items
            }

            pos_data_json = json.dumps(pos_data)
            selected_totals_json = json.dumps({str(k): v for k, v in selected_totals.items()})
            current_inventory_json = json.dumps(current_inventory)

            # Filter distributors: only those with POs in the unfiltered base queryset
            base_pos_qs = _get_filtered_distributor_pos_queryset(company, {})
            filter_dist_ids = base_pos_qs.values_list('distributor_id', flat=True).distinct()
            all_distributors_for_filter = list(
                Distributor.objects.filter(pk__in=filter_dist_ids, company=company).order_by('name')
            )

            # Move-modal reference data: month ("YYYY-MM") -> ordered PO list
            # (pk, position, label). Company-wide, all months, so the move modal can
            # show any target month's current order without an extra round trip.
            all_company_pos = (
                DistributorPO.objects.filter(distributor__company=company)
                .select_related('distributor')
                .order_by('year', 'month', 'sort_position', 'distributor__name')
            )
            move_modal_data = {}
            for po in all_company_pos:
                key = f"{po.year}-{po.month:02d}"
                month_list = move_modal_data.setdefault(key, [])
                so_suffix = f" — SO# {po.so_number}" if po.so_number else ''
                month_list.append({
                    'pk': po.pk,
                    'position': len(month_list) + 1,  # 1-based within month
                    'label': f"{po.distributor.display_code} — {po.get_status_display()}{so_suffix}",
                })
            move_modal_data_json = json.dumps(move_modal_data)

            status_choices = DistributorPO.Status.choices

            pos_active_filter_count = compute_active_filter_count(
                pos_active_filters, _DEFAULT_DISTRIBUTOR_POS_FILTERS
            )
            pos_filters_active = pos_active_filter_count > 0

    return render(request, 'distribution/distributor_list.html', {
        'grouped_data': grouped_data,
        'ungrouped_data': ungrouped_data,
        'is_grouped_view': is_grouped_view,
        'total_count': total_count,
        'active_tab': active_tab,
        'can_manage_inventory': can_manage_inventory,
        # Inventory tab
        'inventory_rows': inventory_rows,
        'has_any_snapshots': has_any_snapshots,
        'inventory_distributor_choices': inventory_distributor_choices,
        'inventory_brand_choices': inventory_brand_choices,
        'inventory_period_choices': inventory_period_choices,
        'inv_distributor_filter': inv_distributor_filter,
        'inv_brand_filter': str(inv_brand_filter),
        'inv_period_filter': inv_period_filter,
        'inventory_sort': inventory_sort,
        # Forecast tab
        'forecast_result': forecast_result,
        'orders_result': orders_result,
        'forecast_distributor': forecast_distributor,
        'forecast_group': forecast_group,
        'primary_distributor': primary_distributor,
        'available_distributors': available_distributors,
        'available_groups': available_groups,
        # Distributor POs / Invoiced POs tabs
        'pos_page_obj': pos_page_obj,
        'pos_rows': pos_rows,
        'all_items': all_items,
        'brand_groups': brand_groups,
        'all_distributors_for_filter': all_distributors_for_filter,
        'status_choices': status_choices,
        'po_status_choices': DistributorPO.Status.choices,
        'pos_active_filters': pos_active_filters,
        'pos_active_filter_count': pos_active_filter_count,
        'pos_filters_active': pos_filters_active,
        'move_modal_data_json': move_modal_data_json,
        # Inventory projection tool
        'pos_data_json': pos_data_json,
        'selected_totals_json': selected_totals_json,
        'current_inventory_json': current_inventory_json,
        'selected_po_count': selected_po_count,
        'all_items_for_inventory': all_items,
    })


# ---------------------------------------------------------------------------
# Distributor CRUD (unchanged)
# ---------------------------------------------------------------------------

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
            return redirect('distributor_list')
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

    next_url = request.GET.get('next', '') or request.POST.get('next', '')
    if next_url and not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = ''

    if request.method == 'POST':
        form = DistributorForm(request.POST, instance=distributor, company=request.user.company)
        if form.is_valid():
            form.save()
            messages.success(request, f'Distributor "{distributor.name}" has been updated.')
            if next_url:
                return redirect(next_url)
            return redirect('distributor_list')
    else:
        form = DistributorForm(instance=distributor, company=request.user.company)

    items = []
    safety_stock_map = {}
    active_status_map = {}
    if can_manage_inventory:
        items = list(
            Item.objects.filter(brand__company=request.user.company, is_active=True)
            .select_related('brand')
            .order_by('brand__name', 'sort_order', 'name')
        )
        profiles = {
            p.item_id: p
            for p in DistributorItemProfile.objects.filter(distributor=distributor)
        }
        safety_stock_map = {pid: p.safety_stock_cases for pid, p in profiles.items()}
        active_status_map = {
            item.pk: profiles[item.pk].is_active if item.pk in profiles else True
            for item in items
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
        'active_status_map': active_status_map,
        'active_tab': active_tab,
        'next_url': next_url,
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
        is_active = f'is_active_{item.pk}' in request.POST
        raw_ss = request.POST.get(f'safety_stock_{item.pk}', '').strip()

        if is_active:
            if raw_ss and raw_ss != '0':
                try:
                    value = int(raw_ss)
                    if value <= 0:
                        raise ValueError
                except ValueError:
                    warning_items.append(item.name)
                    continue

                profile, created = DistributorItemProfile.objects.get_or_create(
                    distributor=distributor,
                    item=item,
                    defaults={'safety_stock_cases': value, 'is_active': True},
                )
                if not created:
                    profile.safety_stock_cases = value
                    profile.is_active = True
                    profile.save(update_fields=['safety_stock_cases', 'is_active'])
            else:
                DistributorItemProfile.objects.filter(
                    distributor=distributor, item=item
                ).delete()
        else:
            profile, created = DistributorItemProfile.objects.get_or_create(
                distributor=distributor,
                item=item,
                defaults={'is_active': False, 'safety_stock_cases': None},
            )
            if not created:
                profile.is_active = False
                profile.safety_stock_cases = None
                profile.save(update_fields=['is_active', 'safety_stock_cases'])

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


# ---------------------------------------------------------------------------
# Inventory upload — Step 1
# ---------------------------------------------------------------------------

@login_required
def inventory_upload(request):
    denied = _require_inventory_permission(request)
    if denied:
        return denied

    company = request.user.company
    form = InventoryImportUploadForm()
    import_errors = []

    if request.method == 'POST':
        form = InventoryImportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            year = form.cleaned_data['year']
            month = form.cleaned_data['month']
            csv_file = form.cleaned_data['csv_file']

            temp_path = _inv_save_temp_file(csv_file)

            try:
                rows, parse_errors = parse_inventory_csv(temp_path)
                if parse_errors:
                    _inv_cleanup_temp_file(temp_path)
                    return render(request, 'distribution/inventory_upload.html', {
                        'form': form,
                        'import_errors': parse_errors,
                    })

                if not rows:
                    _inv_cleanup_temp_file(temp_path)
                    return render(request, 'distribution/inventory_upload.html', {
                        'form': form,
                        'import_errors': ['The CSV file contains no data rows.'],
                    })

                resolved_rows, val_errors, unmapped_by_dist = validate_inventory_import(
                    rows, company, year, month
                )
                if val_errors:
                    _inv_cleanup_temp_file(temp_path)
                    return render(request, 'distribution/inventory_upload.html', {
                        'form': form,
                        'import_errors': val_errors,
                    })

                if unmapped_by_dist:
                    _inv_cleanup_temp_file(temp_path)
                    request.session['pending_mapping_resolution'] = {
                        'unknown_codes': unmapped_by_dist,
                        'next_url': reverse('inventory_upload'),
                        'context': 'inventory',
                    }
                    return redirect('resolve_mappings')

                # Build per-distributor preview summary
                dist_summary = {}
                for r in resolved_rows:
                    name = r['distributor'].name
                    if name not in dist_summary:
                        dist_summary[name] = {'item_count': 0, 'total_cases': Decimal('0')}
                    dist_summary[name]['item_count'] += 1
                    dist_summary[name]['total_cases'] += r['quantity']

                distributor_summaries = [
                    {
                        'name': name,
                        'item_count': d['item_count'],
                        'total_cases': str(d['total_cases']),
                    }
                    for name, d in sorted(dist_summary.items())
                ]

                request.session['pending_inventory_import'] = {
                    'year': year,
                    'month': month,
                    'filename': csv_file.name,
                    'temp_file_path': temp_path,
                    'preview': {
                        'total_rows': len(resolved_rows),
                        'distributor_summaries': distributor_summaries,
                    },
                }
                return redirect('inventory_preview')

            except Exception as exc:
                _inv_cleanup_temp_file(temp_path)
                return render(request, 'distribution/inventory_upload.html', {
                    'form': form,
                    'import_errors': [f'Unexpected error reading file: {exc}'],
                })

    return render(request, 'distribution/inventory_upload.html', {
        'form': form,
        'import_errors': import_errors,
    })


# ---------------------------------------------------------------------------
# Inventory preview — Step 2
# ---------------------------------------------------------------------------

@login_required
def inventory_preview(request):
    denied = _require_inventory_permission(request)
    if denied:
        return denied

    pending = request.session.get('pending_inventory_import')
    if not pending:
        messages.warning(request, 'No pending import found. Please start over.')
        return redirect('inventory_upload')

    if request.method == 'POST' and request.POST.get('action') == 'cancel':
        _inv_cleanup_temp_file(pending.get('temp_file_path'))
        del request.session['pending_inventory_import']
        messages.info(request, 'Import cancelled.')
        return redirect(reverse('distributor_list') + '?tab=inventory')

    year = pending['year']
    month = pending['month']
    preview = pending['preview']

    month_name = calendar.month_name[month]

    # Format total_cases for display in the distributor summaries
    summaries_display = []
    for s in preview['distributor_summaries']:
        qty = Decimal(s['total_cases'])
        summaries_display.append({
            'name': s['name'],
            'item_count': s['item_count'],
            'total_cases_display': _format_quantity_cases(qty),
        })

    return render(request, 'distribution/inventory_preview.html', {
        'pending': pending,
        'year': year,
        'month': month,
        'period_display': f'{month_name} {year}',
        'filename': pending['filename'],
        'total_rows': preview['total_rows'],
        'distributor_summaries': summaries_display,
    })


# ---------------------------------------------------------------------------
# Inventory confirm — Step 3
# ---------------------------------------------------------------------------

@login_required
def inventory_confirm(request):
    denied = _require_inventory_permission(request)
    if denied:
        return denied

    if request.method != 'POST':
        return redirect('inventory_preview')

    pending = request.session.get('pending_inventory_import')
    if not pending:
        messages.warning(request, 'No pending import found. Please start over.')
        return redirect('inventory_upload')

    company = request.user.company
    year = pending['year']
    month = pending['month']
    filename = pending['filename']
    filepath = pending.get('temp_file_path')

    if not filepath or not os.path.exists(filepath):
        messages.error(request, 'Upload file not found. Please start over.')
        if 'pending_inventory_import' in request.session:
            del request.session['pending_inventory_import']
        return redirect('inventory_upload')

    # Re-parse from temp file
    rows, parse_errors = parse_inventory_csv(filepath)
    if parse_errors:
        for err in parse_errors:
            messages.error(request, err)
        return redirect('inventory_preview')

    # Re-validate against fresh DB state
    resolved_rows, val_errors, unmapped_by_dist = validate_inventory_import(
        rows, company, year, month
    )
    if val_errors:
        for err in val_errors:
            messages.error(request, err)
        return redirect('inventory_preview')
    if unmapped_by_dist:
        messages.error(
            request,
            'Some item codes are still unmapped. Please resolve all mappings before importing.',
        )
        return redirect('inventory_preview')

    try:
        with transaction.atomic():
            distributor_ids = {r['distributor'].pk for r in resolved_rows}
            distributor_count = len(distributor_ids)

            batch = InventoryImportBatch.objects.create(
                company=company,
                year=year,
                month=month,
                uploaded_by=request.user,
                filename=filename,
                distributor_count=distributor_count,
                snapshots_created=0,
            )

            snapshots_created = 0
            for row in resolved_rows:
                InventorySnapshot.objects.create(
                    distributor=row['distributor'],
                    item=row['item'],
                    quantity_cases=row['quantity'],
                    year=year,
                    month=month,
                    created_by=request.user,
                    import_batch=batch,
                )
                snapshots_created += 1

                # Auto-activate item in DistributorItemProfile
                profile, created = DistributorItemProfile.objects.get_or_create(
                    distributor=row['distributor'],
                    item=row['item'],
                    defaults={'is_active': True},
                )
                if not created and not profile.is_active:
                    profile.is_active = True
                    profile.save(update_fields=['is_active'])

            batch.snapshots_created = snapshots_created
            batch.save(update_fields=['snapshots_created'])

    except Exception as exc:
        messages.error(request, f'Import failed: {exc}')
        return redirect('inventory_preview')

    _inv_cleanup_temp_file(filepath)
    del request.session['pending_inventory_import']

    month_name = calendar.month_name[month]
    messages.success(
        request,
        f'Successfully imported {snapshots_created} item(s) for '
        f'{distributor_count} distributor(s) for {month_name} {year}.',
    )
    return redirect(reverse('distributor_list') + '?tab=inventory')


# ---------------------------------------------------------------------------
# Inventory bulk delete (Phase 2b-2)
# ---------------------------------------------------------------------------

@login_required
def inventory_bulk_delete(request):
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return render(request, '403.html', status=403)

    if request.method != 'POST':
        return redirect(reverse('distributor_list') + '?tab=inventory')

    company = request.user.company
    raw_ids = request.POST.getlist('snapshot_ids')

    if not raw_ids:
        messages.info(request, 'No inventory records selected.')
        return redirect(reverse('distributor_list') + '?tab=inventory')

    ids = []
    for raw_id in raw_ids:
        try:
            ids.append(int(raw_id))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.info(request, 'No valid inventory records selected.')
        return redirect(reverse('distributor_list') + '?tab=inventory')

    with transaction.atomic():
        qs = InventorySnapshot.objects.filter(pk__in=ids, distributor__company=company)
        count = qs.count()
        qs.delete()

    messages.success(request, f'Deleted {count} inventory record(s).')
    return redirect(reverse('distributor_list') + '?tab=inventory')


# ---------------------------------------------------------------------------
# PO modal endpoints (Phase 4-step-2b)
# ---------------------------------------------------------------------------

@login_required
def distributor_po_modal_data(request, dist_pk, year, month):
    """Return JSON data needed to render the PO modal for a given (distributor, year, month)."""
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    distributor = get_object_or_404(Distributor, pk=dist_pk, company=request.user.company)

    # Active items for this distributor
    inactive_item_ids = list(
        DistributorItemProfile.objects.filter(
            distributor=distributor, is_active=False
        ).values_list('item_id', flat=True)
    )
    items = list(
        Item.objects.filter(brand__company=distributor.company, is_active=True)
        .exclude(pk__in=inactive_item_ids)
        .select_related('brand')
        .order_by('brand__name', 'sort_order', 'name')
    )

    # Saved POs for this month. Optional ?po_pk=N narrows the modal to a single
    # PO (clicking a specific PO row), so two POs in the same month don't both
    # open. Response shape is identical either way.
    saved_pos_qs = (
        DistributorPO.objects.filter(distributor=distributor, year=year, month=month)
        .prefetch_related('lines__item')
        .order_by('pk')
    )
    po_pk = request.GET.get('po_pk')
    if po_pk:
        saved_pos_qs = saved_pos_qs.filter(pk=po_pk)
    saved_pos = list(saved_pos_qs)

    items_data = [
        {
            'id': item.pk,
            'name': item.name,
            'item_code': item.item_code,
            'cases_per_pallet': item.cases_per_pallet,
        }
        for item in items
    ]

    saved_orders_data = [
        {
            'id': po.pk,
            'year': po.year,
            'month': po.month,
            'status': po.status,
            'external_po_number': po.external_po_number,
            'notes': po.notes,
            'generated_by_algorithm': po.generated_by_algorithm,
            'so_number': po.so_number,
            'lines': [
                {
                    'item_id': line.item_id,
                    'item_name': line.item.name,
                    'quantity_cases': float(line.quantity_cases),
                }
                for line in po.lines.all()
            ],
        }
        for po in saved_pos
    ]

    return JsonResponse({
        'distributor': {
            'id': distributor.pk,
            'name': distributor.name,
            'order_quantity_value': distributor.order_quantity_value,
            'order_quantity_unit': distributor.order_quantity_unit,
        },
        'items': items_data,
        'saved_orders': saved_orders_data,
    })


@login_required
def distributor_po_save(request, dist_pk):
    """Atomically create/update/delete POs for a given (distributor, year, month)."""
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'AJAX required'}, status=400)

    distributor = get_object_or_404(Distributor, pk=dist_pk, company=request.user.company)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    year = body.get('year')
    month = body.get('month')
    orders = body.get('orders', [])

    if not isinstance(year, int) or not isinstance(month, int):
        return JsonResponse({'error': 'year and month required'}, status=400)
    if not 1 <= month <= 12:
        return JsonResponse({'error': 'Invalid month'}, status=400)

    # Full pre-validation before any DB writes
    errors = []
    all_item_ids = set()
    existing_po_ids = []

    valid_statuses = [s[0] for s in DistributorPO.Status.choices]

    for i, order_data in enumerate(orders):
        label = f'Order {i + 1}'
        status = order_data.get('status', 'projected')
        if status not in valid_statuses:
            errors.append(f'{label}: invalid status "{status}"')
            continue

        po_number = (order_data.get('external_po_number') or '').strip()
        if status == 'actual' and not po_number:
            errors.append(f'{label}: PO number is required for Actual status')

        lines = order_data.get('lines', [])
        seen_items = set()
        for line in lines:
            item_id = line.get('item_id')
            if not item_id:
                errors.append(f'{label}: missing item_id in line')
                continue
            if item_id in seen_items:
                errors.append(f'{label}: duplicate item ID {item_id}')
                continue
            seen_items.add(item_id)
            all_item_ids.add(item_id)
            qty = line.get('quantity_cases', 0)
            try:
                qty = float(qty)
            except (TypeError, ValueError):
                errors.append(f'{label}: invalid quantity_cases')
                continue
            if qty < 0:
                errors.append(f'{label}: negative quantity not allowed')

        po_id = order_data.get('id')
        if po_id is not None:
            existing_po_ids.append(po_id)

    if errors:
        return JsonResponse({'error': '; '.join(errors)}, status=400)

    # Validate item IDs belong to this company
    if all_item_ids:
        valid_item_ids = set(
            Item.objects.filter(
                pk__in=all_item_ids,
                brand__company=request.user.company,
            ).values_list('pk', flat=True)
        )
        invalid = all_item_ids - valid_item_ids
        if invalid:
            return JsonResponse({'error': f'Invalid item IDs: {sorted(invalid)}'}, status=400)

    # Validate existing PO IDs belong to this distributor/month
    if existing_po_ids:
        valid_po_count = DistributorPO.objects.filter(
            pk__in=existing_po_ids,
            distributor=distributor,
            year=year,
            month=month,
        ).count()
        if valid_po_count != len(existing_po_ids):
            return JsonResponse({'error': 'Invalid PO IDs'}, status=400)

    # Guard the save-path deletion: emptying an existing PO's lines deletes it
    # below. Only projected POs may be deleted (matches distributor_po_delete).
    # Eligibility is based on the PO's PERSISTED status, not the unsaved dropdown
    # value. Reject the whole save (atomic) if any to-be-deleted PO isn't projected.
    delete_po_ids = [
        order_data.get('id')
        for order_data in orders
        if order_data.get('id') is not None
        and not [l for l in order_data.get('lines', []) if float(l.get('quantity_cases', 0)) > 0]
    ]
    if delete_po_ids:
        non_projected_exists = DistributorPO.objects.filter(
            pk__in=delete_po_ids, distributor=distributor,
        ).exclude(status=DistributorPO.Status.PROJECTED).exists()
        if non_projected_exists:
            return JsonResponse({'error': 'Only projected POs can be deleted.'}, status=400)

    # Atomic save
    try:
        with transaction.atomic():
            for order_data in orders:
                po_id = order_data.get('id')
                status = order_data.get('status', 'projected')
                po_number = (order_data.get('external_po_number') or '').strip()
                notes = (order_data.get('notes') or '').strip()
                lines = order_data.get('lines', [])

                nonzero_lines = [
                    l for l in lines
                    if float(l.get('quantity_cases', 0)) > 0
                ]

                if po_id is not None:
                    po = DistributorPO.objects.get(pk=po_id, distributor=distributor)
                    if not nonzero_lines:
                        po.delete()
                    else:
                        po.status = status
                        po.external_po_number = po_number
                        po.notes = notes
                        po.generated_by_algorithm = False
                        update_fields = ['status', 'external_po_number', 'notes', 'generated_by_algorithm']
                        if status == DistributorPO.Status.SUBMITTED and po.so_number is None:
                            assign_so_number(po)
                            update_fields.append('so_number')
                        po.save(update_fields=update_fields)
                        po.lines.all().delete()
                        for line in nonzero_lines:
                            DistributorPOLine.objects.create(
                                po=po,
                                item_id=line['item_id'],
                                quantity_cases=float(line['quantity_cases']),
                            )
                else:
                    if not nonzero_lines:
                        continue
                    po = DistributorPO(
                        distributor=distributor,
                        year=year,
                        month=month,
                        status=status,
                        external_po_number=po_number,
                        notes=notes,
                        generated_by_algorithm=False,
                        created_by=request.user,
                    )
                    if status == DistributorPO.Status.SUBMITTED:
                        assign_so_number(po)
                    po.save()
                    for line in nonzero_lines:
                        DistributorPOLine.objects.create(
                            po=po,
                            item_id=line['item_id'],
                            quantity_cases=float(line['quantity_cases']),
                        )
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception:
        return JsonResponse(
            {'success': False, 'error': 'An unexpected error occurred while saving. Please try again.'},
            status=500,
        )

    return JsonResponse({'ok': True})


@login_required
def distributor_po_delete(request, dist_pk, po_pk):
    """Delete a single saved PO."""
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'AJAX required'}, status=400)

    distributor = get_object_or_404(Distributor, pk=dist_pk, company=request.user.company)
    po = get_object_or_404(DistributorPO, pk=po_pk, distributor=distributor)

    # Only projected POs may be deleted. Eligibility is based on the PO's SAVED
    # status (what is in the DB), not any unsaved dropdown selection in the modal.
    if po.status != DistributorPO.Status.PROJECTED:
        return JsonResponse(
            {'error': 'Only projected POs can be deleted.'},
            status=400,
        )

    po.delete()

    return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
# Group forecast views (Phase G2)
# ---------------------------------------------------------------------------

@login_required
def distributor_group_forecast(request, group_pk):
    """Read-only aggregated forecast for a DistributorGroup."""
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return render(request, '403.html', status=403)

    company = request.user.company
    group = get_object_or_404(
        DistributorGroup.objects.select_related('primary_distributor', 'company'),
        pk=group_pk, company=company,
    )
    members = list(group.members.order_by('name'))
    primary = group.primary_distributor

    # Build po_additions and saved_pos_by_month from all member POs
    saved_pos = list(
        DistributorPO.objects.filter(distributor__in=members)
        .select_related('distributor')
        .prefetch_related('lines')
    )
    po_additions = {}
    saved_pos_by_month = {}
    for po in saved_pos:
        ym = (po.year, po.month)
        saved_pos_by_month.setdefault(ym, []).append(po)
        for line in po.lines.all():
            key = (line.item_id, po.year, po.month)
            po_additions[key] = po_additions.get(key, 0.0) + float(line.quantity_cases)

    forecast_result = compute_group_forecast(group, po_additions=po_additions or None)

    orders_result = None
    if forecast_result.get('alignment_status') == 'ok':
        orders_result = generate_projected_orders(primary, forecast_result)
        for slot in orders_result.get('orders_per_horizon', []):
            ym = (slot['year'], slot['month'])
            slot['saved_count'] = len(saved_pos_by_month.get(ym, []))
            slot['total_count'] = slot['saved_count']

    available_distributors = list(
        Distributor.objects.filter(company=company, is_active=True)
        .select_related('group', 'group__primary_distributor')
        .order_by('name')
    )
    available_groups = list(DistributorGroup.objects.filter(company=company).order_by('name'))

    return render(request, 'distribution/distributor_group_forecast.html', {
        'group': group,
        'members': members,
        'primary_distributor': primary,
        'forecast_result': forecast_result,
        'orders_result': orders_result,
        'available_distributors': available_distributors,
        'available_groups': available_groups,
        'po_status_choices': DistributorPO.Status.choices,
    })


@login_required
def distributor_group_orders_modal_data(request, group_pk, year, month):
    """Return JSON for the editable multi-PO modal on the group forecast page (G3).

    Replaces the G2 read-only endpoint at the same URL. Expanded response includes
    items list, algorithm suggestions, and is_primary flag per saved PO.
    """
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    company = request.user.company
    try:
        group = DistributorGroup.objects.select_related('primary_distributor').get(
            pk=group_pk, company=company,
        )
    except DistributorGroup.DoesNotExist:
        return JsonResponse({'error': 'Group not found'}, status=404)

    primary = group.primary_distributor
    members = list(group.members.all())

    # Saved POs for the month across all members
    saved_pos_qs = (
        DistributorPO.objects
        .filter(distributor__in=members, year=year, month=month)
        .select_related('distributor')
        .prefetch_related('lines__item')
        .order_by('distributor__name', 'pk')
    )

    # Items: union of active items across all members (mirrors group forecast logic).
    # Item is excluded only when ALL members have it explicitly inactive.
    all_active_item_ids = set(
        Item.objects.filter(brand__company=company, is_active=True)
        .values_list('pk', flat=True)
    )
    per_member_inactive = [
        set(
            DistributorItemProfile.objects.filter(
                distributor=member, is_active=False,
            ).values_list('item_id', flat=True)
        )
        for member in members
    ]
    excluded_ids = set()
    if per_member_inactive:
        excluded_ids = per_member_inactive[0].copy()
        for s in per_member_inactive[1:]:
            excluded_ids &= s
    active_group_item_ids = all_active_item_ids - excluded_ids
    items = list(
        Item.objects.filter(pk__in=active_group_item_ids)
        .select_related('brand')
        .order_by('brand__name', 'sort_order', 'name')
    )

    items_data = [
        {
            'id': item.pk,
            'name': item.name,
            'item_code': item.item_code,
            'cases_per_pallet': item.cases_per_pallet,
        }
        for item in items
    ]

    saved_orders_data = [
        {
            'id': po.pk,
            'year': po.year,
            'month': po.month,
            'status': po.status,
            'external_po_number': po.external_po_number or '',
            'notes': po.notes or '',
            'generated_by_algorithm': po.generated_by_algorithm,
            'so_number': po.so_number,
            'is_primary': po.distributor_id == primary.pk,
            'distributor_name': po.distributor.name,
            'distributor_pk': po.distributor_id,
            'lines': [
                {
                    'item_id': line.item_id,
                    'item_name': line.item.name,
                    'quantity_cases': float(line.quantity_cases),
                }
                for line in po.lines.all()
            ],
        }
        for po in saved_pos_qs
    ]

    month_name = calendar.month_name[month]
    return JsonResponse({
        'group': {'id': group.pk, 'name': group.name},
        'primary_distributor': {
            'id': primary.pk,
            'name': primary.name,
            'order_quantity_value': primary.order_quantity_value,
            'order_quantity_unit': primary.order_quantity_unit,
        },
        'year': year,
        'month': month,
        'period_label': f'{month_name} {year}',
        'items': items_data,
        'saved_orders': saved_orders_data,
    })


@login_required
def distributor_po_suggest(request, dist_pk, year, month):
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    company = request.user.company
    distributor = get_object_or_404(Distributor, pk=dist_pk, company=company)

    saved_pos = list(
        DistributorPO.objects.filter(distributor=distributor)
        .prefetch_related('lines')
    )
    po_additions = {}
    for po in saved_pos:
        for line in po.lines.all():
            key = (line.item_id, po.year, po.month)
            po_additions[key] = po_additions.get(key, 0.0) + float(line.quantity_cases)

    forecast_result = compute_distributor_forecast(distributor, po_additions=po_additions or None)
    suggestion = suggest_po_for_month(distributor, year, month, forecast_result)
    return JsonResponse(suggestion)


@login_required
def distributor_group_po_suggest(request, group_pk, year, month):
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    company = request.user.company
    try:
        group = DistributorGroup.objects.select_related('primary_distributor').get(
            pk=group_pk, company=company,
        )
    except DistributorGroup.DoesNotExist:
        return JsonResponse({'error': 'Group not found'}, status=404)

    primary = group.primary_distributor
    members = list(group.members.all())

    po_additions = {}
    for line in DistributorPOLine.objects.filter(po__distributor__in=members).select_related('po'):
        key = (line.item_id, line.po.year, line.po.month)
        po_additions[key] = po_additions.get(key, 0.0) + float(line.quantity_cases)

    forecast_result = compute_group_forecast(group, po_additions=po_additions or None)

    if forecast_result.get('alignment_status') != 'ok':
        return JsonResponse({'lines': []})

    suggestion = suggest_po_for_month(primary, year, month, forecast_result)
    return JsonResponse(suggestion)


@login_required
def distributor_group_po_save(request, group_pk):
    """Atomically create/update/delete POs from the group forecast modal (G3).

    New POs are always created against the group's primary distributor.
    Edits are only allowed for POs that already belong to the primary distributor.
    """
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'AJAX required'}, status=400)

    company = request.user.company
    try:
        group = DistributorGroup.objects.select_related('primary_distributor').get(
            pk=group_pk, company=company,
        )
    except DistributorGroup.DoesNotExist:
        return JsonResponse({'error': 'Group not found'}, status=404)

    primary = group.primary_distributor

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    year = body.get('year')
    month = body.get('month')
    orders = body.get('orders', [])

    if not isinstance(year, int) or not isinstance(month, int):
        return JsonResponse({'error': 'year and month required'}, status=400)
    if not 1 <= month <= 12:
        return JsonResponse({'error': 'Invalid month'}, status=400)

    # Full pre-validation before any DB writes
    errors = []
    all_item_ids = set()
    existing_po_ids = []

    valid_statuses = [s[0] for s in DistributorPO.Status.choices]

    for i, order_data in enumerate(orders):
        label = f'Order {i + 1}'
        status = order_data.get('status', 'projected')
        if status not in valid_statuses:
            errors.append(f'{label}: invalid status "{status}"')
            continue

        po_number = (order_data.get('external_po_number') or '').strip()
        if status == 'actual' and not po_number:
            errors.append(f'{label}: PO number is required for Actual status')

        lines = order_data.get('lines', [])
        seen_items = set()
        for line in lines:
            item_id = line.get('item_id')
            if not item_id:
                errors.append(f'{label}: missing item_id in line')
                continue
            if item_id in seen_items:
                errors.append(f'{label}: duplicate item ID {item_id}')
                continue
            seen_items.add(item_id)
            all_item_ids.add(item_id)
            qty = line.get('quantity_cases', 0)
            try:
                qty = float(qty)
            except (TypeError, ValueError):
                errors.append(f'{label}: invalid quantity_cases')
                continue
            if qty < 0:
                errors.append(f'{label}: negative quantity not allowed')

        po_id = order_data.get('id')
        if po_id is not None:
            existing_po_ids.append(po_id)

    if errors:
        return JsonResponse({'error': '; '.join(errors)}, status=400)

    # Validate item IDs belong to this company
    if all_item_ids:
        valid_item_ids = set(
            Item.objects.filter(
                pk__in=all_item_ids,
                brand__company=request.user.company,
            ).values_list('pk', flat=True)
        )
        invalid = all_item_ids - valid_item_ids
        if invalid:
            return JsonResponse({'error': f'Invalid item IDs: {sorted(invalid)}'}, status=400)

    # Validate existing PO IDs — must belong to primary distributor, year, and month.
    # This blocks editing non-primary POs from the group view.
    if existing_po_ids:
        valid_po_count = DistributorPO.objects.filter(
            pk__in=existing_po_ids,
            distributor=primary,
            year=year,
            month=month,
        ).count()
        if valid_po_count != len(existing_po_ids):
            return JsonResponse(
                {'error': 'Invalid PO IDs — only primary distributor POs may be edited from the group view'},
                status=400,
            )

    # Guard the save-path deletion: emptying an existing PO's lines deletes it
    # below. Only projected POs may be deleted (matches distributor_po_delete).
    # Eligibility is based on the PO's PERSISTED status, not the unsaved dropdown
    # value. Reject the whole save (atomic) if any to-be-deleted PO isn't projected.
    delete_po_ids = [
        order_data.get('id')
        for order_data in orders
        if order_data.get('id') is not None
        and not [l for l in order_data.get('lines', []) if float(l.get('quantity_cases', 0)) > 0]
    ]
    if delete_po_ids:
        non_projected_exists = DistributorPO.objects.filter(
            pk__in=delete_po_ids, distributor=primary,
        ).exclude(status=DistributorPO.Status.PROJECTED).exists()
        if non_projected_exists:
            return JsonResponse({'error': 'Only projected POs can be deleted.'}, status=400)

    # Atomic save
    try:
        with transaction.atomic():
            for order_data in orders:
                po_id = order_data.get('id')
                status = order_data.get('status', 'projected')
                po_number = (order_data.get('external_po_number') or '').strip()
                notes = (order_data.get('notes') or '').strip()
                lines = order_data.get('lines', [])

                nonzero_lines = [
                    l for l in lines
                    if float(l.get('quantity_cases', 0)) > 0
                ]

                if po_id is not None:
                    po = DistributorPO.objects.get(pk=po_id, distributor=primary)
                    if not nonzero_lines:
                        po.delete()
                    else:
                        po.status = status
                        po.external_po_number = po_number
                        po.notes = notes
                        po.generated_by_algorithm = False
                        update_fields = ['status', 'external_po_number', 'notes', 'generated_by_algorithm']
                        if status == DistributorPO.Status.SUBMITTED and po.so_number is None:
                            assign_so_number(po)
                            update_fields.append('so_number')
                        po.save(update_fields=update_fields)
                        po.lines.all().delete()
                        for line in nonzero_lines:
                            DistributorPOLine.objects.create(
                                po=po,
                                item_id=line['item_id'],
                                quantity_cases=float(line['quantity_cases']),
                            )
                else:
                    if not nonzero_lines:
                        continue
                    po = DistributorPO(
                        distributor=primary,
                        year=year,
                        month=month,
                        status=status,
                        external_po_number=po_number,
                        notes=notes,
                        generated_by_algorithm=False,
                        created_by=request.user,
                    )
                    if status == DistributorPO.Status.SUBMITTED:
                        assign_so_number(po)
                    po.save()
                    for line in nonzero_lines:
                        DistributorPOLine.objects.create(
                            po=po,
                            item_id=line['item_id'],
                            quantity_cases=float(line['quantity_cases']),
                        )
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception:
        return JsonResponse(
            {'success': False, 'error': 'An unexpected error occurred while saving. Please try again.'},
            status=500,
        )

    return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
# Inventory projection tool (Distributor POs tab)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def save_forecast_inventory(request):
    """
    Save ad-hoc current inventory values for items (company-scoped).
    Expects JSON: {"inventory": {"<item_id>": <value>, ...}}
    """
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    inventory = data.get('inventory', {})
    if not isinstance(inventory, dict):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    company = request.user.company

    # Only allow updating items belonging to this company
    company_item_ids = set(
        Item.objects.filter(brand__company=company).values_list('pk', flat=True)
    )

    updated = 0
    with transaction.atomic():
        for item_id_str, value in inventory.items():
            try:
                item_id = int(item_id_str)
            except (ValueError, TypeError):
                continue
            if item_id not in company_item_ids:
                continue
            try:
                dec_value = Decimal(str(value)) if value not in (None, '') else Decimal('0')
            except (InvalidOperation, ValueError):
                continue
            Item.objects.filter(pk=item_id).update(forecast_current_inventory=dec_value)
            updated += 1

    return JsonResponse({'ok': True, 'updated': updated})


@login_required
@require_POST
def toggle_po_selection(request):
    """
    Toggle selected_for_projection on a single PO (company-scoped).
    Expects JSON: {"po_pk": <int>, "selected": <bool>}
    """
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body)
        po_pk = int(data['po_pk'])
        selected = bool(data['selected'])
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    company = request.user.company

    updated = DistributorPO.objects.filter(
        pk=po_pk, distributor__company=company
    ).update(selected_for_projection=selected)

    if updated == 0:
        return JsonResponse({'error': 'PO not found'}, status=404)

    return JsonResponse({'ok': True})


@login_required
@require_POST
def bulk_toggle_po_selection(request):
    """
    Set selected_for_projection for multiple POs at once (company-scoped).
    Expects JSON: {"po_pks": [<int>, ...], "selected": <bool>}
    """
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body)
        po_pks = [int(p) for p in data['po_pks']]
        selected = bool(data['selected'])
    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    company = request.user.company

    updated = DistributorPO.objects.filter(
        pk__in=po_pks, distributor__company=company
    ).update(selected_for_projection=selected)

    return JsonResponse({'ok': True, 'updated': updated})


@login_required
@require_POST
def move_distributor_po(request):
    """
    Move a PO to a target month + position. Renumbers affected month(s).
    Expects JSON: {"po_pk": <int>, "target_year": <int>, "target_month": <int>, "target_position": <int>}
    target_position is 1-based; the PO is inserted at that position and others slide down.
    """
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return JsonResponse({'error': 'Invalid request'}, status=400)
    if not request.user.has_permission('can_manage_distributor_inventory'):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body)
        po_pk = int(data['po_pk'])
        target_year = int(data['target_year'])
        target_month = int(data['target_month'])
        target_position = int(data['target_position'])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    company = request.user.company

    try:
        po = DistributorPO.objects.select_related('distributor').get(
            pk=po_pk, distributor__company=company
        )
    except DistributorPO.DoesNotExist:
        return JsonResponse({'error': 'PO not found'}, status=404)

    old_year, old_month = po.year, po.month
    cross_month = (old_year, old_month) != (target_year, target_month)

    with transaction.atomic():
        # Move the PO to the target month (year/month may change).
        po.year = target_year
        po.month = target_month
        po.save(update_fields=['year', 'month'])

        # Renumber the TARGET month with the PO inserted at target_position.
        target_pos_qs = list(
            DistributorPO.objects.filter(
                distributor__company=company, year=target_year, month=target_month
            ).exclude(pk=po.pk).order_by('sort_position', 'distributor__name')
        )
        # Clamp target_position into valid range [1, len+1].
        pos = max(1, min(target_position, len(target_pos_qs) + 1))
        # Insert po at index pos-1.
        target_pos_qs.insert(pos - 1, po)
        for idx, p in enumerate(target_pos_qs, start=1):
            if p.sort_position != idx:
                p.sort_position = idx
                p.save(update_fields=['sort_position'])

        # If cross-month, renumber the OLD month to close the gap.
        if cross_month:
            old_pos_qs = list(
                DistributorPO.objects.filter(
                    distributor__company=company, year=old_year, month=old_month
                ).order_by('sort_position', 'distributor__name')
            )
            for idx, p in enumerate(old_pos_qs, start=1):
                if p.sort_position != idx:
                    p.sort_position = idx
                    p.save(update_fields=['sort_position'])

    return JsonResponse({'ok': True})
