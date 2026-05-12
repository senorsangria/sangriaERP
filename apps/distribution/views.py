"""
Distribution views: Distributor CRUD and inventory snapshot import.
Supplier Admin only.
"""
import calendar
import csv
import os
import uuid
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.catalog.models import Brand as CatalogBrand, Item
from apps.imports.models import ItemMapping
from .forecast import compute_distributor_forecast
from .forms import DistributorForm, InventoryImportUploadForm
from .order_generation import generate_projected_orders
from .models import Distributor, DistributorItemProfile, InventoryImportBatch, InventorySnapshot


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
      2. Resolve each item code via ItemMapping (case-insensitive on raw_item_name).
      3. Check for period conflicts (snapshot already exists for that distributor/period).

    Any error in any step aborts the entire upload.

    Returns (resolved_rows, errors) where resolved_rows is a list of dicts:
        {distributor (Distributor), item (Item), quantity (Decimal), row_number}
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
        return [], errors

    # Step 2: Item code resolution via ItemMapping
    unique_pairs = {(r['distributor_name'], r['item_code']) for r in rows}
    item_map = {}  # {(csv_dist_name, item_code): Item}
    for dist_name, item_code in sorted(unique_pairs):
        dist = distributor_map[dist_name]
        mapping = ItemMapping.objects.filter(
            company=company,
            distributor=dist,
            raw_item_name__iexact=item_code,
        ).first()

        if mapping is None or mapping.status != ItemMapping.Status.MAPPED or mapping.mapped_item is None:
            errors.append(
                f"Item code '{item_code}' for distributor '{dist_name}' is not mapped. "
                f"Add the mapping at /imports/item-mappings/ first."
            )
            continue

        item_map[(dist_name, item_code)] = mapping.mapped_item

    if errors:
        return [], errors

    # Step 3: Period conflict check
    month_name = calendar.month_name[month]
    for csv_name, dist in sorted(distributor_map.items(), key=lambda x: x[0]):
        if InventorySnapshot.objects.filter(distributor=dist, year=year, month=month).exists():
            errors.append(
                f"Distributor '{dist.name}' already has inventory data for "
                f"{month_name} {year}. Delete the existing snapshot before re-uploading."
            )

    if errors:
        return [], errors

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

    return resolved_rows, []


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

    distributors = Distributor.objects.filter(company=company).order_by('name')
    search = request.GET.get('q', '').strip()
    if search:
        distributors = distributors.filter(name__icontains=search)

    active_tab = request.GET.get('tab', 'distributors')
    if active_tab not in ('distributors', 'inventory', 'forecast'):
        active_tab = 'distributors'
    if active_tab in ('inventory', 'forecast') and not can_manage_inventory:
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
    available_distributors = []

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

        # Forecast tab — compute eagerly so Bootstrap tab-switching shows data
        available_distributors = list(Distributor.objects.filter(company=company).order_by('name'))
        forecast_dist_pk = request.GET.get('forecast_distributor', '')
        if forecast_dist_pk:
            try:
                forecast_distributor = Distributor.objects.get(
                    pk=int(forecast_dist_pk), company=company
                )
            except (ValueError, Distributor.DoesNotExist):
                forecast_distributor = None
        if forecast_distributor is None and available_distributors:
            forecast_distributor = available_distributors[0]
        if forecast_distributor:
            forecast_result = compute_distributor_forecast(forecast_distributor)
            orders_result = generate_projected_orders(forecast_distributor, forecast_result)

    return render(request, 'distribution/distributor_list.html', {
        'distributors': distributors,
        'search': search,
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
        'available_distributors': available_distributors,
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

                resolved_rows, val_errors = validate_inventory_import(rows, company, year, month)
                if val_errors:
                    _inv_cleanup_temp_file(temp_path)
                    return render(request, 'distribution/inventory_upload.html', {
                        'form': form,
                        'import_errors': val_errors,
                    })

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
    resolved_rows, val_errors = validate_inventory_import(rows, company, year, month)
    if val_errors:
        for err in val_errors:
            messages.error(request, err)
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
