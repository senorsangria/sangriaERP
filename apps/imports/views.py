"""
Imports app views: sales data import, item mapping, and batch history.

All views require Supplier Admin role. All data is scoped to the logged-in
user's company.

Import flow (two-step):
  1. import_upload  — select distributor, upload CSV, validate, build preview
  2. import_preview — review summary, confirm to execute or cancel
  3. import_success — summary after successful import

Item mapping:
  mapping_list / mapping_create / mapping_edit

Batch history:
  batch_list / batch_detail / batch_delete
"""

import csv
import os
import uuid
from datetime import datetime

from django.contrib import messages
from django.db import transaction
from django.db.models.functions import ExtractYear
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Account
from apps.catalog.models import Item
from apps.distribution.models import Distributor
from apps.imports.forms import ImportUploadForm, ItemMappingForm
from apps.imports.models import ImportBatch, ItemMapping
from apps.sales.models import SalesRecord
from utils.normalize import normalize_address


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_supplier_admin(request):
    """Return redirect if user is not a Supplier Admin, else None."""
    if not request.user.is_authenticated or not request.user.is_supplier_admin:
        return redirect('access_denied')
    return None


def _temp_import_dir():
    """Return the directory used for temporary CSV uploads."""
    from django.conf import settings
    path = os.path.join(settings.MEDIA_ROOT, 'temp_imports')
    os.makedirs(path, exist_ok=True)
    return path


def _save_temp_file(uploaded_file):
    """Save uploaded file to temp storage; return the file path."""
    ext = os.path.splitext(uploaded_file.name)[1] or '.csv'
    filename = f'{uuid.uuid4().hex}{ext}'
    filepath = os.path.join(_temp_import_dir(), filename)
    with open(filepath, 'wb') as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)
    return filepath


def _cleanup_temp_file(filepath):
    """Delete a temp file if it exists."""
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except OSError:
            pass


def _parse_csv_headers(headers):
    """
    Detect column indices from CSV header row.

    Returns a dict mapping logical field names to column indices.
    Raises ValueError if a required column is missing.
    """
    headers = [h.strip() for h in headers]

    required_cols = [
        'Retail Accounts', 'Address', 'City', 'State',
        'Zip Code', 'VIP Outlet ID', 'Dates', 'Item Names', 'Item Name ID',
    ]
    for col in required_cols:
        if col not in headers:
            raise ValueError(f'Required column missing: "{col}"')

    return {
        'account':    headers.index('Retail Accounts'),
        'address':    headers.index('Address'),
        'city':       headers.index('City'),
        'state':      headers.index('State'),
        'zip':        headers.index('Zip Code'),
        'vip':        headers.index('VIP Outlet ID'),
        'counties':   headers.index('Counties') if 'Counties' in headers else None,
        'on_off':     headers.index('OnOff Premises') if 'OnOff Premises' in headers else None,
        'dates':      headers.index('Dates'),
        'item_names': headers.index('Item Names'),
        'item_id':    headers.index('Item Name ID'),
        'quantity':   len(headers) - 1,     # always last column
    }


def _parse_date(date_str):
    """
    Parse a date string in common formats; raise ValueError if unrecognised.

    Handles formats commonly produced by VIP and Excel CSV exports, including
    variants with or without zero-padding and with time components appended.
    """
    raw = date_str.strip()

    # Strip trailing time component if present (e.g. "01/15/2024 00:00:00")
    if ' ' in raw:
        raw = raw.split(' ')[0].strip()

    for fmt in (
        '%m/%d/%Y',   # 01/15/2024  (most common VIP format)
        '%m/%d/%y',   # 01/15/24
        '%Y-%m-%d',   # 2024-01-15
        '%Y/%m/%d',   # 2024/01/15
        '%m-%d-%Y',   # 01-15-2024
        '%d/%m/%Y',   # 15/01/2024
        '%d-%m-%Y',   # 15-01-2024
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'Cannot parse date: {date_str!r}')


def _read_csv_rows(filepath, cols):
    """
    Read all data rows from the CSV and return a list of dicts.

    Each dict has:
        account_name, address, city, state, zip_code, vip_outlet_id,
        county, on_off_premise, sale_date (date), item_id, quantity (int)
    """
    rows = []
    errors = []

    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader)  # skip header

        for line_num, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue  # skip blank rows

            try:
                # County: strip state suffix e.g. "HUDSON, NJ" → "HUDSON"
                county = 'Unknown'
                if cols['counties'] is not None and len(row) > cols['counties']:
                    raw = row[cols['counties']].strip()
                    county = raw.split(',')[0].strip() or 'Unknown'

                # On/Off premise
                on_off = 'Unknown'
                if cols['on_off'] is not None and len(row) > cols['on_off']:
                    val = row[cols['on_off']].strip().upper()
                    if val in ('ON', 'OFF'):
                        on_off = val

                # Quantity — remove commas, allow negatives
                qty_str = row[cols['quantity']].strip().replace(',', '') if len(row) > cols['quantity'] else '0'
                try:
                    quantity = int(float(qty_str)) if qty_str else 0
                except (ValueError, TypeError):
                    quantity = 0

                rows.append({
                    'account_name':  row[cols['account']].strip(),
                    'address':       row[cols['address']].strip(),
                    'city':          row[cols['city']].strip(),
                    'state':         row[cols['state']].strip(),
                    'zip_code':      row[cols['zip']].strip(),
                    'vip_outlet_id': row[cols['vip']].strip(),
                    'county':        county,
                    'on_off_premise': on_off,
                    'sale_date':     _parse_date(row[cols['dates']]),
                    'item_id':       row[cols['item_id']].strip(),
                    'quantity':      quantity,
                })
            except Exception as exc:
                errors.append(f'Line {line_num}: {exc}')

    return rows, errors


# ---------------------------------------------------------------------------
# Import upload — Step 1
# ---------------------------------------------------------------------------

def import_upload(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    company = request.user.company

    # Pre-select distributor if passed as a URL param (e.g. from the success page)
    initial = {}
    distributor_param = request.GET.get('distributor')
    if distributor_param:
        initial['distributor'] = distributor_param

    form = ImportUploadForm(company=company, initial=initial)

    if request.method == 'POST':
        form = ImportUploadForm(request.POST, request.FILES, company=company)
        if form.is_valid():
            distributor = form.cleaned_data['distributor']
            uploaded_file = request.FILES['csv_file']

            # Save file to temp storage
            filepath = _save_temp_file(uploaded_file)

            try:
                # Parse headers
                with open(filepath, newline='', encoding='utf-8-sig') as f:
                    reader = csv.reader(f)
                    header_row = next(reader)

                cols = _parse_csv_headers(header_row)

                # Read all rows
                rows, row_errors = _read_csv_rows(filepath, cols)

                if not rows:
                    _cleanup_temp_file(filepath)
                    if row_errors:
                        # Every row failed to parse — show the first few errors so
                        # the user can diagnose the actual problem (e.g. date format).
                        sample = row_errors[:3]
                        detail = ' | '.join(sample)
                        messages.error(
                            request,
                            f'Could not read any data rows from this file '
                            f'({len(row_errors)} rows failed). '
                            f'First errors: {detail}',
                        )
                    else:
                        messages.error(request, 'The CSV file contains no data rows.')
                    return render(request, 'imports/upload.html', {'form': form})

                # --- Validation 1: Duplicate date check ---
                all_dates = {r['sale_date'] for r in rows}
                conflicting_dates = set(
                    SalesRecord.objects.filter(
                        company=company,
                        import_batch__distributor=distributor,
                        sale_date__in=all_dates,
                    ).values_list('sale_date', flat=True).distinct()
                )
                if conflicting_dates:
                    _cleanup_temp_file(filepath)
                    sorted_dates = sorted(conflicting_dates)
                    date_list = ', '.join(d.strftime('%m/%d/%Y') for d in sorted_dates)
                    messages.error(
                        request,
                        f'Import aborted. Sales records already exist for {distributor.name} '
                        f'on the following dates: {date_list}. '
                        f'These dates cannot be imported again.',
                    )
                    return render(request, 'imports/upload.html', {'form': form})

                # --- Validation 2: Unknown item code check ---
                all_item_ids = {r['item_id'] for r in rows}
                known_codes = set(
                    ItemMapping.objects.filter(
                        company=company,
                        distributor=distributor,
                        status__in=[ItemMapping.Status.MAPPED, ItemMapping.Status.IGNORED],
                        raw_item_name__in=all_item_ids,
                    ).values_list('raw_item_name', flat=True)
                )
                unknown_codes = all_item_ids - known_codes
                if unknown_codes:
                    _cleanup_temp_file(filepath)
                    code_list = ', '.join(sorted(unknown_codes))
                    messages.error(
                        request,
                        f'Import aborted. The following item codes from {distributor.name} '
                        f'are not recognized: {code_list}. '
                        f'Please create these items in Brand Management and set up their '
                        f'mappings before importing.',
                    )
                    return render(request, 'imports/upload.html', {'form': form})

                # --- Build preview data ---
                min_date = min(all_dates)
                max_date = max(all_dates)

                # Existing accounts for this distributor (in-memory lookup)
                existing_accounts = list(
                    Account.active_accounts.filter(
                        company=company,
                        distributor=distributor,
                    ).values('address_normalized', 'city_normalized', 'state_normalized')
                )
                existing_keys = {
                    (a['address_normalized'], a['city_normalized'], a['state_normalized'])
                    for a in existing_accounts
                }

                unique_account_keys = set()
                new_account_keys = set()
                for r in rows:
                    key = (
                        normalize_address(r['address']),
                        normalize_address(r['city']),
                        normalize_address(r['state']),
                    )
                    unique_account_keys.add(key)
                    if key not in existing_keys:
                        new_account_keys.add(key)

                existing_count = len(unique_account_keys) - len(new_account_keys)

                # Item code → mapped item info
                mapping_qs = ItemMapping.objects.filter(
                    company=company,
                    distributor=distributor,
                    raw_item_name__in=all_item_ids,
                ).select_related('mapped_item', 'mapped_item__brand')

                item_mappings = []
                for m in mapping_qs:
                    if m.status == ItemMapping.Status.MAPPED and m.mapped_item:
                        mapped_label = f'{m.mapped_item.brand.name} — {m.mapped_item.name} ({m.mapped_item.item_code})'
                    elif m.status == ItemMapping.Status.IGNORED:
                        mapped_label = '(Ignored — will be skipped)'
                    else:
                        mapped_label = '(Not mapped)'
                    item_mappings.append({
                        'code': m.raw_item_name,
                        'mapped_to': mapped_label,
                        'status': m.status,
                    })
                item_mappings.sort(key=lambda x: x['code'])

                # Store pending import in session
                request.session['pending_import'] = {
                    'distributor_id': distributor.pk,
                    'distributor_name': distributor.name,
                    'filename': uploaded_file.name,
                    'temp_file_path': filepath,
                    'preview': {
                        'date_range_start': min_date.isoformat(),
                        'date_range_end': max_date.isoformat(),
                        'total_records': len(rows),
                        'unique_accounts': len(unique_account_keys),
                        'existing_accounts': existing_count,
                        'new_accounts': len(new_account_keys),
                        'item_mappings': item_mappings,
                    },
                }

                return redirect('import_preview')

            except ValueError as exc:
                _cleanup_temp_file(filepath)
                messages.error(request, f'CSV file error: {exc}')
            except Exception as exc:
                _cleanup_temp_file(filepath)
                messages.error(request, f'Unexpected error reading file: {exc}')

    return render(request, 'imports/upload.html', {'form': form})


# ---------------------------------------------------------------------------
# Import preview — Step 2
# ---------------------------------------------------------------------------

def import_preview(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    pending = request.session.get('pending_import')
    if not pending:
        messages.warning(request, 'No pending import found. Please start over.')
        return redirect('import_upload')

    company = request.user.company
    preview = pending['preview']

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'cancel':
            _cleanup_temp_file(pending.get('temp_file_path'))
            del request.session['pending_import']
            messages.info(request, 'Import cancelled.')
            return redirect('import_upload')

        if action == 'confirm':
            filepath = pending.get('temp_file_path')
            if not filepath or not os.path.exists(filepath):
                messages.error(request, 'Upload file not found. Please start over.')
                del request.session['pending_import']
                return redirect('import_upload')

            try:
                distributor = Distributor.objects.get(
                    pk=pending['distributor_id'], company=company
                )
            except Distributor.DoesNotExist:
                messages.error(request, 'Distributor not found.')
                return redirect('import_upload')

            try:
                batch = _execute_import(request, company, distributor, filepath, pending['filename'])
                _cleanup_temp_file(filepath)
                del request.session['pending_import']
                return redirect('import_success', batch_pk=batch.pk)

            except Exception as exc:
                _cleanup_temp_file(filepath)
                if 'pending_import' in request.session:
                    del request.session['pending_import']
                messages.error(request, f'Import failed: {exc}')
                return redirect('import_upload')

    context = {
        'pending': pending,
        'preview': preview,
    }
    return render(request, 'imports/preview.html', context)


def _execute_import(request, company, distributor, filepath, filename):
    """
    Execute the full import inside a single database transaction.

    Performance strategy:
    - Read entire CSV into memory first
    - Build all account lookups as in-memory dicts before touching the database
    - Use bulk_create for new Account records (batches of 500)
    - Use bulk_create for all SalesRecord rows (batches of 1000)
    """
    # Re-parse the file
    with open(filepath, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header_row = next(reader)

    cols = _parse_csv_headers(header_row)
    rows, _ = _read_csv_rows(filepath, cols)

    # Pre-load item mappings into memory (item_id → Item)
    item_mapping_qs = ItemMapping.objects.filter(
        company=company,
        distributor=distributor,
        status=ItemMapping.Status.MAPPED,
    ).select_related('mapped_item')
    code_to_item = {m.raw_item_name: m.mapped_item for m in item_mapping_qs}

    # Pre-load existing accounts into memory dict
    # Key: (address_normalized, city_normalized, state_normalized)
    existing_account_qs = Account.active_accounts.filter(
        company=company,
        distributor=distributor,
    )
    account_lookup = {}
    for acc in existing_account_qs:
        key = (acc.address_normalized, acc.city_normalized, acc.state_normalized)
        account_lookup[key] = acc

    # Process rows: determine new accounts needed
    new_account_map = {}  # key → Account (not yet saved)

    for r in rows:
        item = code_to_item.get(r['item_id'])
        if item is None:
            # Ignored item code — skip this row
            continue

        key = (
            normalize_address(r['address']),
            normalize_address(r['city']),
            normalize_address(r['state']),
        )

        if key not in account_lookup and key not in new_account_map:
            new_account_map[key] = Account(
                company=company,
                distributor=distributor,
                name=r['account_name'],
                street=r['address'],
                city=r['city'],
                state=r['state'],
                zip_code=r['zip_code'],
                address_normalized=key[0],
                city_normalized=key[1],
                state_normalized=key[2],
                vip_outlet_id=r['vip_outlet_id'],
                county=r['county'],
                on_off_premise=r['on_off_premise'],
                auto_created=True,
                is_active=True,
            )

    with transaction.atomic():
        # Bulk create new accounts in batches of 500
        new_accounts_list = list(new_account_map.values())
        created_accounts = []
        for i in range(0, len(new_accounts_list), 500):
            batch_chunk = new_accounts_list[i:i + 500]
            created_chunk = Account.objects.bulk_create(batch_chunk)
            created_accounts.extend(created_chunk)

        # Merge newly created accounts into the lookup dict
        for acc in created_accounts:
            key = (acc.address_normalized, acc.city_normalized, acc.state_normalized)
            account_lookup[key] = acc

        # Build sales records
        all_dates = [r['sale_date'] for r in rows]
        min_date = min(all_dates)
        max_date = max(all_dates)

        # Create the ImportBatch record first so we can reference it
        import_batch = ImportBatch.objects.create(
            company=company,
            distributor=distributor,
            import_type=ImportBatch.ImportType.SALES_DATA,
            filename=filename,
            date_range_start=min_date,
            date_range_end=max_date,
            status=ImportBatch.Status.PENDING,
        )

        sales_records = []
        records_skipped = 0

        for r in rows:
            item = code_to_item.get(r['item_id'])
            if item is None:
                records_skipped += 1
                continue

            key = (
                normalize_address(r['address']),
                normalize_address(r['city']),
                normalize_address(r['state']),
            )
            account = account_lookup.get(key)
            if account is None:
                records_skipped += 1
                continue

            sales_records.append(SalesRecord(
                company=company,
                import_batch=import_batch,
                account=account,
                item=item,
                sale_date=r['sale_date'],
                quantity=r['quantity'],
            ))

        # Bulk create sales records in batches of 1000
        for i in range(0, len(sales_records), 1000):
            SalesRecord.objects.bulk_create(sales_records[i:i + 1000])

        # Update the batch with final statistics
        import_batch.records_imported = len(sales_records)
        import_batch.accounts_created = len(created_accounts)
        import_batch.records_skipped = records_skipped
        import_batch.status = ImportBatch.Status.COMPLETE
        import_batch.save()

    return import_batch


# ---------------------------------------------------------------------------
# Import success — Step 3
# ---------------------------------------------------------------------------

def import_success(request, batch_pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    batch = get_object_or_404(
        ImportBatch, pk=batch_pk, company=request.user.company
    )
    return render(request, 'imports/success.html', {'batch': batch})


# ---------------------------------------------------------------------------
# Item Mapping
# ---------------------------------------------------------------------------

def mapping_list(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    company = request.user.company
    qs = ItemMapping.objects.filter(company=company).select_related(
        'distributor', 'brand', 'mapped_item', 'mapped_item__brand'
    )

    # Filters
    distributor_id = request.GET.get('distributor')
    brand_id = request.GET.get('brand')
    status_filter = request.GET.get('status')

    if distributor_id:
        qs = qs.filter(distributor_id=distributor_id)
    if brand_id:
        qs = qs.filter(brand_id=brand_id)
    if status_filter:
        qs = qs.filter(status=status_filter)

    distributors = Distributor.objects.filter(company=company, is_active=True).order_by('name')
    from apps.catalog.models import Brand
    brands = Brand.objects.filter(company=company, is_active=True).order_by('name')

    context = {
        'mappings': qs,
        'distributors': distributors,
        'brands': brands,
        'status_choices': ItemMapping.Status.choices,
        'selected_distributor': distributor_id,
        'selected_brand': brand_id,
        'selected_status': status_filter,
    }
    return render(request, 'imports/mapping_list.html', context)


def mapping_create(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    company = request.user.company
    form = ItemMappingForm(company=company)

    if request.method == 'POST':
        form = ItemMappingForm(request.POST, company=company)
        if form.is_valid():
            mapping = form.save(commit=False)
            mapping.company = company
            try:
                mapping.save()
                messages.success(request, f'Mapping for "{mapping.raw_item_name}" created.')
                return redirect('mapping_list')
            except Exception:
                messages.error(
                    request,
                    'A mapping for this item code and distributor already exists.',
                )

    return render(request, 'imports/mapping_form.html', {
        'form': form,
        'title': 'Create Item Mapping',
        'is_edit': False,
    })


def mapping_edit(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    company = request.user.company
    mapping = get_object_or_404(ItemMapping, pk=pk, company=company)
    form = ItemMappingForm(instance=mapping, company=company)

    if request.method == 'POST':
        form = ItemMappingForm(request.POST, instance=mapping, company=company)
        if form.is_valid():
            form.save()
            messages.success(request, f'Mapping for "{mapping.raw_item_name}" updated.')
            return redirect('mapping_list')

    return render(request, 'imports/mapping_form.html', {
        'form': form,
        'mapping': mapping,
        'title': f'Edit Mapping: {mapping.raw_item_name}',
        'is_edit': True,
    })


# ---------------------------------------------------------------------------
# Batch History
# ---------------------------------------------------------------------------

def batch_list(request):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    company = request.user.company
    active_tab = request.GET.get('tab', 'list')
    if active_tab not in ('list', 'monthly'):
        active_tab = 'list'

    # List view queryset
    qs = ImportBatch.objects.filter(company=company).select_related('distributor')
    distributor_id = request.GET.get('distributor')
    if distributor_id:
        qs = qs.filter(distributor_id=distributor_id)

    distributors = Distributor.objects.filter(company=company, is_active=True).order_by('name')

    # Monthly view — available years (most recent first)
    available_years = list(
        ImportBatch.objects.filter(company=company, date_range_start__isnull=False)
        .annotate(year=ExtractYear('date_range_start'))
        .values_list('year', flat=True)
        .distinct()
        .order_by('-year')
    )

    # Selected year — default to most recent
    year_param = request.GET.get('year')
    if year_param and year_param.isdigit() and int(year_param) in available_years:
        selected_year = int(year_param)
    elif available_years:
        selected_year = available_years[0]
    else:
        selected_year = None

    # Build monthly grid: one row per active distributor, 12 month cells each
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    monthly_grid = []
    if selected_year:
        year_batches = (
            ImportBatch.objects.filter(
                company=company,
                date_range_start__year=selected_year,
            )
            .select_related('distributor')
            .order_by('date_range_start')
        )
        # Organize into {distributor_id: {month_number: [batch, ...]}}
        batch_map = {}
        for batch in year_batches:
            batch_map.setdefault(batch.distributor_id, {}).setdefault(
                batch.date_range_start.month, []
            ).append(batch)

        for dist in distributors:
            dist_data = batch_map.get(dist.pk, {})
            monthly_grid.append({
                'distributor': dist,
                'months': [dist_data.get(m, []) for m in range(1, 13)],
            })

    context = {
        'batches': qs,
        'distributors': distributors,
        'selected_distributor': distributor_id,
        'active_tab': active_tab,
        'available_years': available_years,
        'selected_year': selected_year,
        'monthly_grid': monthly_grid,
        'month_names': month_names,
    }
    return render(request, 'imports/batch_list.html', context)


def batch_detail(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    company = request.user.company
    batch = get_object_or_404(ImportBatch, pk=pk, company=company)

    auto_created_accounts = Account.objects.filter(
        company=company,
        auto_created=True,
        sales_records__import_batch=batch,
    ).distinct().order_by('name')

    context = {
        'batch': batch,
        'auto_created_accounts': auto_created_accounts,
    }
    return render(request, 'imports/batch_detail.html', context)


def batch_delete(request, pk):
    denied = _require_supplier_admin(request)
    if denied:
        return denied

    company = request.user.company
    batch = get_object_or_404(ImportBatch, pk=pk, company=company)

    # Pre-compute counts for the confirmation dialog
    sales_count = SalesRecord.objects.filter(import_batch=batch).count()
    auto_account_ids = list(
        Account.objects.filter(
            company=company,
            auto_created=True,
            sales_records__import_batch=batch,
        ).values_list('id', flat=True).distinct()
    )

    # Accounts that would be deleted: auto-created with no sales outside this batch
    deletable_account_ids = []
    for acct_id in auto_account_ids:
        other_records = SalesRecord.objects.filter(
            account_id=acct_id,
        ).exclude(import_batch=batch).exists()
        if not other_records:
            deletable_account_ids.append(acct_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'cancel':
            return redirect('batch_detail', pk=pk)

        if action == 'confirm':
            try:
                with transaction.atomic():
                    # Re-check deletable accounts inside the transaction
                    final_deletable_ids = []
                    for acct_id in auto_account_ids:
                        other_records = SalesRecord.objects.filter(
                            account_id=acct_id,
                        ).exclude(import_batch=batch).exists()
                        if not other_records:
                            final_deletable_ids.append(acct_id)

                    # Delete all sales records for this batch (cascades from batch delete)
                    deleted_sales, _ = SalesRecord.objects.filter(import_batch=batch).delete()

                    # Delete auto-created accounts with no remaining sales
                    deleted_accounts = 0
                    if final_deletable_ids:
                        deleted_accounts, _ = Account.objects.filter(
                            id__in=final_deletable_ids
                        ).delete()

                    # Delete the batch record
                    batch.delete()

                messages.success(
                    request,
                    f'Deleted {deleted_sales} sales records and '
                    f'{deleted_accounts} accounts successfully.',
                )
                return redirect('batch_list')

            except Exception as exc:
                messages.error(request, f'Delete failed: {exc}')

    context = {
        'batch': batch,
        'sales_count': sales_count,
        'deletable_account_count': len(deletable_account_ids),
    }
    return render(request, 'imports/batch_delete.html', context)
