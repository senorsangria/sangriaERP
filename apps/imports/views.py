"""
Imports app views: sales data import, item mapping, and batch history.

All views require Supplier Admin role. All data is scoped to the logged-in
user's company.

Import flow (multi-distributor, two-step):
  1. import_upload  — upload CSV(s) with Distributors column, validate, build preview
  2. import_preview — review summary, confirm to execute or cancel
  3. import_success — summary after successful import

Distributor is read per-row from the CSV "Distributors" column.
One ImportBatch per unique distributor; all created in one transaction.

Item mapping:
  mapping_list / mapping_create / mapping_edit

Batch history:
  batch_list / batch_detail / batch_delete
"""

import calendar
import csv
import json
import os
import tempfile
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import ExtractMonth, ExtractYear
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.models import Account, AccountItem
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


def _require_permission(request, codename):
    """Return redirect if the authenticated user lacks the named permission, else None."""
    if not request.user.is_authenticated or not request.user.has_permission(codename):
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


def _write_combined_csv(rows):
    """
    Write pre-parsed rows (list of dicts from _read_csv_rows) to a canonical
    temp CSV file that _execute_import can re-parse.  Returns the file path.

    Distributors column is written second-to-last so Quantity remains last
    (preserving the len(headers)-1 quantity detection in _parse_csv_headers).
    """
    headers = [
        'Retail Accounts', 'Address', 'City', 'State', 'Zip Code',
        'VIP Outlet ID', 'Counties', 'OnOff Premises', 'Dates',
        'Item Names', 'Item Name ID', 'Price', 'Distributors', 'Quantity',
    ]
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.csv', delete=False,
        encoding='utf-8-sig', dir=_temp_import_dir(),
    )
    writer = csv.writer(tmp)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([
            r['account_name'],
            r['address'],
            r['city'],
            r['state'],
            r['zip_code'],
            r['vip_outlet_id'],
            r['county'],
            r['on_off_premise'],
            r['sale_date'].isoformat(),  # YYYY-MM-DD — supported by _parse_date
            '',                          # Item Names — not used during execution
            r['item_id'],
            '' if r.get('price') is None else str(r['price']),
            r['distributor'].name,       # Distributor name per row
            r['quantity'],               # Quantity always last
        ])
    tmp.flush()
    tmp.close()
    return tmp.name


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
        'Distributors',
    ]
    for col in required_cols:
        if col not in headers:
            raise ValueError(f'Required column missing: "{col}"')

    return {
        'account':          headers.index('Retail Accounts'),
        'address':          headers.index('Address'),
        'city':             headers.index('City'),
        'state':            headers.index('State'),
        'zip':              headers.index('Zip Code'),
        'vip':              headers.index('VIP Outlet ID'),
        'counties':         headers.index('Counties') if 'Counties' in headers else None,
        'on_off':           headers.index('OnOff Premises') if 'OnOff Premises' in headers else None,
        'dates':            headers.index('Dates'),
        'item_names':       headers.index('Item Names'),
        'item_id':          headers.index('Item Name ID'),
        'price':            headers.index('Price') if 'Price' in headers else None,
        'distributor_name': headers.index('Distributors'),
        'quantity':         len(headers) - 1,     # always last column
    }


def _strip_excel_zip(value):
    """
    Strip Excel's leading-zero preservation format from a zip code string.

    Excel saves leading-zero zip codes as ="07030" in CSV exports.  This
    function handles all three common forms:
        ="07030"  →  07030
        "07030"   →  07030
        07030     →  07030  (unchanged)
    """
    v = value.strip()
    if v.startswith('='):
        v = v[1:]
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v


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

                # Price — optional column; null if absent, blank, or non-numeric
                price = None
                if cols['price'] is not None and len(row) > cols['price']:
                    raw_price = row[cols['price']].strip()
                    try:
                        price = Decimal(raw_price) if raw_price else None
                    except InvalidOperation:
                        price = None

                rows.append({
                    'account_name':     row[cols['account']].strip(),
                    'address':          row[cols['address']].strip(),
                    'city':             row[cols['city']].strip(),
                    'state':            row[cols['state']].strip(),
                    'zip_code':         _strip_excel_zip(row[cols['zip']].strip()),
                    'vip_outlet_id':    row[cols['vip']].strip(),
                    'county':           county,
                    'on_off_premise':   on_off,
                    'sale_date':        _parse_date(row[cols['dates']]),
                    'item_id':          row[cols['item_id']].strip(),
                    'quantity':         quantity,
                    'price':            price,
                    'distributor_name': row[cols['distributor_name']].strip(),
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
    form = ImportUploadForm()

    if request.method == 'POST':
        form = ImportUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_files = request.FILES.getlist('csv_file')

            temp_filepaths = []
            combined_filepath = None

            try:
                all_rows = []
                all_errors = []

                _required_cols = [
                    'Retail Accounts', 'Address', 'City', 'State',
                    'Zip Code', 'VIP Outlet ID', 'Dates', 'Item Names', 'Item Name ID',
                    'Distributors',
                ]

                for uploaded_file in uploaded_files:
                    filepath = _save_temp_file(uploaded_file)
                    temp_filepaths.append(filepath)

                    with open(filepath, newline='', encoding='utf-8-sig') as f:
                        reader = csv.reader(f)
                        header_row = next(reader)

                    header_set = {h.strip() for h in header_row}
                    missing = [c for c in _required_cols if c not in header_set]
                    if missing:
                        raise ValueError(
                            f'File "{uploaded_file.name}": missing required columns: {missing}'
                        )

                    cols = _parse_csv_headers(header_row)
                    file_rows, file_errors = _read_csv_rows(filepath, cols)
                    all_rows.extend(file_rows)
                    all_errors.extend(file_errors)

                # Individual temp files are no longer needed
                for fp in temp_filepaths:
                    _cleanup_temp_file(fp)
                temp_filepaths = []

                if not all_rows:
                    if all_errors:
                        sample = all_errors[:3]
                        detail = ' | '.join(sample)
                        messages.error(
                            request,
                            f'Could not read any data rows from the file(s) '
                            f'({len(all_errors)} rows failed). '
                            f'First errors: {detail}',
                        )
                    else:
                        messages.error(request, 'The CSV file(s) contain no data rows.')
                    return render(request, 'imports/upload.html', {'form': form})

                # Sort combined rows by date before validation
                all_rows = sorted(all_rows, key=lambda r: r['sale_date'])

                # --- Distributor validation (case-insensitive, active only) ---
                from apps.imports.utils import _resolve_distributors
                all_rows, dist_errors = _resolve_distributors(all_rows, company)
                if dist_errors:
                    messages.error(request, dist_errors[0])
                    return render(request, 'imports/upload.html', {'form': form})

                # --- Validation: Unknown item codes → redirect to resolve_mappings ---
                # (The former per-(distributor, date) hard stop is gone; overlap is
                #  now DETECTED below and replaced on confirm — see replace-on-import.)
                from collections import defaultdict
                csv_dist_items = {(r['distributor'].pk, r['item_id']) for r in all_rows}
                all_dist_ids = {pk for pk, _ in csv_dist_items}
                all_item_ids = {item_id for _, item_id in csv_dist_items}

                existing_mappings = set(
                    ItemMapping.objects.filter(
                        company=company,
                        distributor_id__in=all_dist_ids,
                        status__in=[ItemMapping.Status.MAPPED, ItemMapping.Status.IGNORED],
                        raw_item_name__in=all_item_ids,
                    ).values_list('distributor_id', 'raw_item_name')
                )
                unknown_pairs = csv_dist_items - existing_mappings
                if unknown_pairs:
                    unknown_by_dist_id = defaultdict(set)
                    for dist_id, raw_code in unknown_pairs:
                        unknown_by_dist_id[dist_id].add(raw_code)
                    request.session['pending_mapping_resolution'] = {
                        'unknown_codes': {
                            str(dist_id): sorted(codes)
                            for dist_id, codes in unknown_by_dist_id.items()
                        },
                        'next_url': reverse('import_upload'),
                        'context': 'sales',
                    }
                    return redirect('resolve_mappings')

                # --- Build preview data ---
                all_dates = {r['sale_date'] for r in all_rows}
                min_date = min(all_dates)
                max_date = max(all_dates)

                # Per-distributor row summary
                dist_row_counts = defaultdict(int)
                for r in all_rows:
                    dist_row_counts[r['distributor'].name] += 1
                distributor_summaries = [
                    {'name': name, 'row_count': count}
                    for name, count in sorted(dist_row_counts.items())
                ]

                # Account existence check (scoped per-distributor)
                existing_account_qs = Account.active_accounts.filter(
                    company=company,
                    distributor_id__in=all_dist_ids,
                ).values('address_normalized', 'city_normalized', 'state_normalized', 'distributor_id')
                existing_keys = {
                    (a['address_normalized'], a['city_normalized'],
                     a['state_normalized'], a['distributor_id'])
                    for a in existing_account_qs
                }
                unique_account_keys = set()
                new_account_keys = set()
                for r in all_rows:
                    key = (
                        normalize_address(r['address']),
                        normalize_address(r['city']),
                        normalize_address(r['state']),
                        r['distributor'].pk,
                    )
                    unique_account_keys.add(key)
                    if key not in existing_keys:
                        new_account_keys.add(key)
                existing_count = len(unique_account_keys) - len(new_account_keys)

                # Item mappings preview (all distributors, all item codes)
                mapping_qs = ItemMapping.objects.filter(
                    company=company,
                    distributor_id__in=all_dist_ids,
                    raw_item_name__in=all_item_ids,
                ).select_related('mapped_item', 'mapped_item__brand', 'distributor')

                item_mappings = []
                for m in mapping_qs:
                    if m.status == ItemMapping.Status.MAPPED and m.mapped_item:
                        mapped_label = (
                            f'{m.mapped_item.brand.name} — '
                            f'{m.mapped_item.name} ({m.mapped_item.item_code})'
                        )
                    elif m.status == ItemMapping.Status.IGNORED:
                        mapped_label = '(Ignored — will be skipped)'
                    else:
                        mapped_label = '(Not mapped)'
                    item_mappings.append({
                        'distributor': m.distributor.name,
                        'code': m.raw_item_name,
                        'mapped_to': mapped_label,
                        'status': m.status,
                    })
                item_mappings.sort(key=lambda x: (x['distributor'], x['code']))

                # --- Replace-on-import: month-grain overlap DETECTION (no abort) ---
                # Which (distributor, year, month) combos in this upload already
                # have existing sales data?  Those months will be deleted and
                # replaced on confirm.  Distributor is read via account__distributor
                # (non-null, PROTECT), matching every other sales query.
                overlap, replace_preview = _detect_overlap(company, all_rows)

                # Write all combined rows to a single temp file
                combined_filepath = _write_combined_csv(all_rows)

                filenames = [f.name for f in uploaded_files]
                request.session['pending_import'] = {
                    'filename': json.dumps(filenames),
                    'files_count': len(uploaded_files),
                    'temp_file_path': combined_filepath,
                    # Raw overlap set (the exact combos the user reviews + confirms),
                    # used verbatim by the execute step — no re-derivation surprises.
                    'overlap': [[dpk, y, m] for (dpk, y, m) in overlap],
                    'replace_preview': replace_preview,
                    'preview': {
                        'date_range_start': min_date.isoformat(),
                        'date_range_end': max_date.isoformat(),
                        'total_records': len(all_rows),
                        'unique_accounts': len(unique_account_keys),
                        'existing_accounts': existing_count,
                        'new_accounts': len(new_account_keys),
                        'distributor_summaries': distributor_summaries,
                        'item_mappings': item_mappings,
                    },
                }

                return redirect('import_preview')

            except ValueError as exc:
                for fp in temp_filepaths:
                    _cleanup_temp_file(fp)
                _cleanup_temp_file(combined_filepath)
                messages.error(request, f'CSV file error: {exc}')
            except Exception as exc:
                for fp in temp_filepaths:
                    _cleanup_temp_file(fp)
                _cleanup_temp_file(combined_filepath)
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
    overlap = pending.get('overlap') or []
    replace_preview = pending.get('replace_preview') or {'has_overlap': False}

    filenames = json.loads(pending.get('filename', '[]'))
    if isinstance(filenames, list):
        if len(filenames) == 1:
            filename_display = filenames[0]
        else:
            filename_display = f'{len(filenames)} files: ' + ', '.join(filenames)
    else:
        filename_display = filenames  # legacy plain string

    context = {
        'pending': pending,
        'preview': preview,
        'filename_display': filename_display,
        'replace_preview': replace_preview,
    }

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'cancel':
            _cleanup_temp_file(pending.get('temp_file_path'))
            del request.session['pending_import']
            messages.info(request, 'Import cancelled.')
            return redirect('import_upload')

        if action == 'confirm':
            # Server-side typed-confirmation enforcement (not just JS): when this
            # import will replace existing data, the user must type DELETE exactly.
            if overlap and request.POST.get('confirm_text') != 'DELETE':
                messages.error(
                    request,
                    'This import will replace existing data. Type DELETE '
                    '(uppercase) to confirm.',
                )
                return render(request, 'imports/preview.html', context)

            filepath = pending.get('temp_file_path')
            if not filepath or not os.path.exists(filepath):
                messages.error(request, 'Upload file not found. Please start over.')
                del request.session['pending_import']
                return redirect('import_upload')

            try:
                # Re-parse the combined CSV (distributor names are in the file)
                with open(filepath, newline='', encoding='utf-8-sig') as f:
                    reader = csv.reader(f)
                    header_row = next(reader)
                cols = _parse_csv_headers(header_row)
                all_rows, _ = _read_csv_rows(filepath, cols)

                # Re-resolve distributor names → objects
                from apps.imports.utils import _resolve_distributors
                all_rows, errors = _resolve_distributors(all_rows, company)
                if errors:
                    _cleanup_temp_file(filepath)
                    del request.session['pending_import']
                    messages.error(request, errors[0])
                    return redirect('import_upload')

                # Group rows by distributor
                from collections import defaultdict
                dist_rows: dict = defaultdict(list)
                for row in all_rows:
                    dist_rows[row['distributor']].append(row)

                created_batches = []
                with transaction.atomic():
                    # Replace-on-import: append audit notes to affected batches and
                    # delete the overlapping month(s) BEFORE importing — all in this
                    # one transaction, so a failure below rolls back the deletion too.
                    # Uses the stashed overlap set (the exact combos the user reviewed
                    # and confirmed) rather than re-deriving, so the delete can never
                    # exceed what was shown and confirmed.
                    _replace_overlapping_months(request, company, overlap)

                    for dist in sorted(dist_rows.keys(), key=lambda d: d.name):
                        batch = _execute_import(
                            request, company, dist,
                            dist_rows[dist], pending['filename'],
                        )
                        created_batches.append(batch)

                _cleanup_temp_file(filepath)
                del request.session['pending_import']

                # Store all batch PKs for the success page
                request.session['import_success_batch_pks'] = [b.pk for b in created_batches]
                return redirect('import_success', batch_pk=created_batches[0].pk)

            except Exception as exc:
                _cleanup_temp_file(filepath)
                if 'pending_import' in request.session:
                    del request.session['pending_import']
                messages.error(request, f'Import failed: {exc}')
                return redirect('import_upload')

    return render(request, 'imports/preview.html', context)


def _month_label(year, month):
    """Format a (year, month) pair as a short chronological label, e.g. 'May 2026'."""
    return f'{calendar.month_abbr[month]} {year}'


def _detect_overlap(company, all_rows):
    """
    Month-grain overlap detection for replace-on-import.

    Returns (overlap, replace_preview):
      - overlap: sorted list of (distributor_pk, year, month) tuples whose existing
        sales data overlaps this upload (and will be deleted + replaced on confirm).
      - replace_preview: dict for the preview template — per-distributor groups with
        per-month record/account counts, plus accurate distinct grand totals.

    Distributor is read via account__distributor (non-null, PROTECT), matching the
    grain used by every other sales query.
    """
    incoming = {
        (r['distributor'].pk, r['sale_date'].year, r['sale_date'].month)
        for r in all_rows
    }
    dist_ids = {d for d, _, _ in incoming}

    existing = set(
        SalesRecord.objects
        .filter(company=company, account__distributor_id__in=dist_ids)
        .annotate(y=ExtractYear('sale_date'), m=ExtractMonth('sale_date'))
        .values_list('account__distributor_id', 'y', 'm')
        .distinct()
    )
    overlap = sorted(incoming & existing)

    dist_name_map = {r['distributor'].pk: r['distributor'].name for r in all_rows}

    from collections import defaultdict
    groups_map = defaultdict(list)
    for dpk, y, m in overlap:
        qs = SalesRecord.objects.filter(
            company=company, account__distributor_id=dpk,
            sale_date__year=y, sale_date__month=m,
        )
        groups_map[dpk].append({
            'label': _month_label(y, m),
            'year': y,
            'month': m,
            'record_count': qs.count(),
            'account_count': qs.values('account').distinct().count(),
        })

    groups = []
    for dpk in sorted(groups_map, key=lambda k: dist_name_map.get(k, '')):
        months = groups_map[dpk]
        groups.append({
            'distributor': dist_name_map.get(dpk, 'Unknown'),
            'months': months,
            'subtotal_records': sum(x['record_count'] for x in months),
        })

    # Accurate distinct grand totals across the whole overlap (an account active in
    # two replaced months must be counted once, so sum-of-per-month is wrong).
    if overlap:
        q = Q()
        for dpk, y, m in overlap:
            q |= Q(account__distributor_id=dpk, sale_date__year=y, sale_date__month=m)
        total_qs = SalesRecord.objects.filter(company=company).filter(q)
        total_records = total_qs.count()
        total_accounts = total_qs.values('account').distinct().count()
    else:
        total_records = 0
        total_accounts = 0

    replace_preview = {
        'has_overlap': bool(overlap),
        'groups': groups,
        'total_records': total_records,
        'total_accounts': total_accounts,
        'combo_count': len(overlap),
    }
    return overlap, replace_preview


def _replace_overlapping_months(request, company, overlap):
    """
    Inside an OPEN transaction: append an audit note to every affected ImportBatch,
    then hard-delete the entire overlapping month(s) per distributor.

    `overlap` is an iterable of (distributor_pk, year, month).  For each affected
    batch we append ONE note line listing all of that batch's months replaced in
    this import (chronologically), then delete the SalesRecords for the whole month
    (all days) per distributor.

    Deletes ONLY SalesRecords.  Accounts and any non-overlapping sales (other months,
    other distributors) are preserved.  No batch-stat recompute — the audit note is
    the record of what changed (per-month batches deferred; see REFACTORING_BACKLOG).
    """
    overlap = [tuple(o) for o in overlap]
    if not overlap:
        return

    from collections import defaultdict

    # 1. Capture affected batches → set of (year, month) being replaced — BEFORE delete.
    batch_months = defaultdict(set)
    for dpk, y, m in overlap:
        qs = SalesRecord.objects.filter(
            company=company, account__distributor_id=dpk,
            sale_date__year=y, sale_date__month=m,
        )
        for bid in qs.values_list('import_batch_id', flat=True).distinct():
            batch_months[bid].add((y, m))

    # 2. Append one audit-note line per affected batch (append, never overwrite).
    who = request.user.email or request.user.get_username()
    today = date.today().isoformat()
    for bid, ymset in batch_months.items():
        labels = ', '.join(_month_label(y, m) for (y, m) in sorted(ymset))
        note = f'{labels} data deleted and replaced by import on {today} by {who}.'
        batch = ImportBatch.objects.get(pk=bid)
        batch.notes = (batch.notes + '\n' + note).strip()
        batch.save(update_fields=['notes'])

    # 3. Delete the entire overlapping month(s) per distributor (sales records only).
    for dpk, y, m in overlap:
        SalesRecord.objects.filter(
            company=company, account__distributor_id=dpk,
            sale_date__year=y, sale_date__month=m,
        ).delete()


def _execute_import(request, company, distributor, rows, filename):
    """
    Execute the full import for one distributor's rows.

    rows: pre-parsed dicts filtered to this distributor (from _read_csv_rows +
          _resolve_distributors).  All rows are for the same distributor.

    Performance strategy:
    - rows are already in memory
    - Build all account lookups as in-memory dicts before touching the database
    - Use bulk_create for new Account records (batches of 500)
    - Use bulk_create for all SalesRecord rows (batches of 1000)
    """
    # Pre-load item mappings into memory (item_id → Item)
    item_mapping_qs = ItemMapping.objects.filter(
        company=company,
        distributor=distributor,
        status=ItemMapping.Status.MAPPED,
    ).select_related('mapped_item')
    code_to_item = {m.raw_item_name: m.mapped_item for m in item_mapping_qs}

    # Pre-load existing ACTIVE accounts into memory dict
    # Key: (address_normalized, city_normalized, state_normalized)
    existing_account_qs = Account.active_accounts.filter(
        company=company,
        distributor=distributor,
    )
    account_lookup = {}
    for acc in existing_account_qs:
        key = (acc.address_normalized, acc.city_normalized, acc.state_normalized)
        account_lookup[key] = acc

    # Pre-load INACTIVE accounts (candidates for reactivation)
    inactive_lookup = {}
    for acc in Account.objects.filter(company=company, distributor=distributor, is_active=False):
        key = (acc.address_normalized, acc.city_normalized, acc.state_normalized)
        inactive_lookup[key] = acc

    # Process rows: determine new accounts and accounts to reactivate
    new_account_map = {}   # key → Account (not yet saved)
    reactivate_keys = set()  # keys of inactive accounts seen in this import

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

        if key in account_lookup:
            continue  # already active
        if key in inactive_lookup:
            reactivate_keys.add(key)  # will reactivate inside transaction
        elif key not in new_account_map:
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
        # Reactivate inactive accounts found in this import
        accounts_reactivated = 0
        if reactivate_keys:
            reactivate_ids = [inactive_lookup[k].pk for k in reactivate_keys]
            accounts_reactivated = Account.objects.filter(pk__in=reactivate_ids).update(is_active=True)
            for k in reactivate_keys:
                account_lookup[k] = inactive_lookup[k]

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
        earliest_dates = {}   # (account_id, item_id) -> earliest sale_date
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
                distributor_wholesale_price=r.get('price'),
            ))

            # Track the earliest sale_date seen for each (account, item) pair.
            pair = (account.pk, item.pk)
            if pair not in earliest_dates or r['sale_date'] < earliest_dates[pair]:
                earliest_dates[pair] = r['sale_date']

        # Bulk create sales records in batches of 1000
        for i in range(0, len(sales_records), 1000):
            SalesRecord.objects.bulk_create(sales_records[i:i + 1000])

        # Create AccountItem records for each unique (account, item) pair,
        # using the earliest sale_date from this import as date_first_associated.
        # get_or_create ensures re-importing never overwrites an existing date.
        account_items_created = 0
        for (account_id, item_id), first_date in earliest_dates.items():
            _, created = AccountItem.objects.get_or_create(
                account_id=account_id,
                item_id=item_id,
                defaults={'date_first_associated': first_date},
            )
            if created:
                account_items_created += 1

        # Update the batch with final statistics
        import_batch.records_imported = len(sales_records)
        import_batch.accounts_created = len(created_accounts)
        import_batch.accounts_reactivated = accounts_reactivated
        import_batch.records_skipped = records_skipped
        import_batch.account_items_created = account_items_created
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

    company = request.user.company
    primary_batch = get_object_or_404(ImportBatch, pk=batch_pk, company=company)

    # Collect all batches created in this upload (may be multiple for multi-distributor)
    batch_pks = request.session.pop('import_success_batch_pks', None) or [batch_pk]
    batches = list(
        ImportBatch.objects
        .filter(pk__in=batch_pks, company=company)
        .select_related('distributor')
        .order_by('distributor__name')
    )
    if not batches:
        batches = [primary_batch]

    return render(request, 'imports/success.html', {
        'batch': primary_batch,
        'batches': batches,
    })


# ---------------------------------------------------------------------------
# Item Mapping
# ---------------------------------------------------------------------------

def mapping_list(request):
    denied = _require_permission(request, 'can_manage_item_mapping')
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
    denied = _require_permission(request, 'can_manage_item_mapping')
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
    denied = _require_permission(request, 'can_manage_item_mapping')
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
    denied = _require_permission(request, 'can_view_import_history')
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
    denied = _require_permission(request, 'can_view_import_history')
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
    denied = _require_permission(request, 'can_view_import_history')
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
                    from apps.accounts.utils import get_account_associations

                    # Re-check deletable accounts inside the transaction
                    final_deletable_ids = []
                    for acct_id in auto_account_ids:
                        other_records = SalesRecord.objects.filter(
                            account_id=acct_id,
                        ).exclude(import_batch=batch).exists()
                        if not other_records:
                            final_deletable_ids.append(acct_id)

                    # For each deletable account, check associations to decide
                    # whether to delete it or deactivate it.
                    delete_account_ids = []
                    deactivate_account_ids = []
                    for acct_id in final_deletable_ids:
                        account_obj = Account.objects.get(pk=acct_id)
                        associations = get_account_associations(account_obj)
                        if any(v > 0 for v in associations.values()):
                            deactivate_account_ids.append(acct_id)
                        else:
                            delete_account_ids.append(acct_id)

                    # Delete all sales records for this batch
                    deleted_sales, _ = SalesRecord.objects.filter(import_batch=batch).delete()

                    # Delete accounts with no associations
                    deleted_accounts = 0
                    if delete_account_ids:
                        deleted_accounts, _ = Account.objects.filter(
                            id__in=delete_account_ids
                        ).delete()

                    # Deactivate accounts that have associated data
                    deactivated_accounts = 0
                    if deactivate_account_ids:
                        deactivated_accounts = Account.objects.filter(
                            id__in=deactivate_account_ids
                        ).update(is_active=False)

                    # Delete the batch record
                    batch.delete()

                msg = (
                    f'Deleted {deleted_sales} sales records and '
                    f'{deleted_accounts} accounts successfully.'
                )
                if deactivated_accounts:
                    msg += (
                        f' {deactivated_accounts} account(s) with associated data '
                        f'were deactivated instead of deleted.'
                    )
                messages.success(request, msg)
                return redirect('batch_list')

            except Exception as exc:
                messages.error(request, f'Delete failed: {exc}')

    context = {
        'batch': batch,
        'sales_count': sales_count,
        'deletable_account_count': len(deletable_account_ids),
    }
    return render(request, 'imports/batch_delete.html', context)


# ---------------------------------------------------------------------------
# Inline mapping resolution — reusable for inventory and sales upload flows
# ---------------------------------------------------------------------------

def resolve_mappings(request):
    """
    Display the inline mapping resolution UI for unmapped item codes detected
    during a CSV upload (inventory or sales).

    Session key 'pending_mapping_resolution' must be set by the calling upload
    view before redirecting here.  If the session has expired or is missing,
    redirects to inventory_upload with a warning message.

    Permission: requires both can_import_sales_data AND can_manage_item_mapping.
    The upload flow already checked the upload permission; this view additionally
    requires can_manage_item_mapping because it creates new ItemMapping records.
    """
    if not request.user.is_authenticated:
        return redirect('login')
    if (
        not request.user.has_permission('can_import_sales_data')
        or not request.user.has_permission('can_manage_item_mapping')
    ):
        return render(request, '403.html', status=403)

    company = request.user.company
    pending = request.session.get('pending_mapping_resolution')

    if not pending or not pending.get('unknown_codes'):
        messages.warning(request, 'Your upload session expired. Please re-upload your file.')
        return redirect('inventory_upload')

    unknown_codes_by_dist_id = pending['unknown_codes']
    next_url = pending.get('next_url', reverse('inventory_upload'))

    from apps.imports.matching import batch_find_best_matches
    from apps.distribution.models import Distributor as _Distributor

    distributor_ids = [int(k) for k in unknown_codes_by_dist_id.keys()]
    distributors = {
        d.id: d
        for d in _Distributor.objects.filter(pk__in=distributor_ids, company=company)
    }

    # Pre-load all items for the dropdown (one query, shared across all groups)
    all_items = list(
        Item.objects.filter(brand__company=company, is_active=True)
        .select_related('brand')
        .order_by('brand__name', 'name')
    )

    groups = []
    for dist_id_str, codes in unknown_codes_by_dist_id.items():
        distributor = distributors.get(int(dist_id_str))
        if not distributor:
            continue

        best_matches = batch_find_best_matches(company, distributor, codes)

        rows = [
            {
                'raw_code': code,
                'best_match': best_matches.get(code),
            }
            for code in codes
        ]

        groups.append({
            'distributor': distributor,
            'rows': rows,
        })

    return render(request, 'imports/resolve_mappings.html', {
        'groups': groups,
        'all_items': all_items,
        'next_url': next_url,
    })


@require_POST
def bulk_save_mappings(request):
    """
    Atomically create ItemMapping records for all resolved codes.

    Accepts JSON body: {"mappings": [
        {"distributor_id": int, "raw_item_name": str, "item_id": int, "apply_to_all": bool},
        ...
    ]}

    Returns JSON: {"ok": true, "redirect_url": "..."} on success.

    apply_to_all=true on any mapping causes that raw_item_name to be mapped to the
    same item across ALL active distributors in the company (using bulk_create with
    ignore_conflicts so existing mappings are never overwritten).
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Not authenticated'}, status=401)
    if (
        not request.user.has_permission('can_import_sales_data')
        or not request.user.has_permission('can_manage_item_mapping')
    ):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    company = request.user.company

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    mappings = payload.get('mappings', [])
    if not mappings:
        return JsonResponse({'error': 'No mappings provided'}, status=400)

    # Pre-validate all inputs before touching the database
    validated = []
    apply_to_all_codes = {}  # {raw_item_name: item_id}

    from apps.distribution.models import Distributor as _Distributor

    for m in mappings:
        try:
            distributor = _Distributor.objects.get(pk=m['distributor_id'], company=company)
            item = Item.objects.get(pk=m['item_id'], brand__company=company)
        except (_Distributor.DoesNotExist, Item.DoesNotExist, KeyError, ValueError, TypeError):
            return JsonResponse({'error': 'Invalid mapping reference'}, status=400)

        validated.append({
            'distributor': distributor,
            'raw_item_name': m['raw_item_name'],
            'item': item,
        })

        if m.get('apply_to_all'):
            apply_to_all_codes[m['raw_item_name']] = item.pk

    with transaction.atomic():
        count = 0

        for v in validated:
            ItemMapping.objects.update_or_create(
                company=company,
                distributor=v['distributor'],
                raw_item_name=v['raw_item_name'],
                defaults={
                    'mapped_item': v['item'],
                    'status': ItemMapping.Status.MAPPED,
                },
            )
            count += 1

        # Apply-to-all: create mappings for ALL active company distributors
        if apply_to_all_codes:
            all_distributors = list(
                _Distributor.objects.filter(company=company, is_active=True)
            )

            extra_mappings = []
            for raw_code, item_id in apply_to_all_codes.items():
                for dist in all_distributors:
                    extra_mappings.append(ItemMapping(
                        company=company,
                        distributor=dist,
                        raw_item_name=raw_code,
                        mapped_item_id=item_id,
                        status=ItemMapping.Status.MAPPED,
                    ))

            # ignore_conflicts=True: existing mappings are untouched
            ItemMapping.objects.bulk_create(extra_mappings, ignore_conflicts=True)

    # Read redirect_url before clearing session
    pending = request.session.get('pending_mapping_resolution')
    redirect_url = (pending or {}).get('next_url', reverse('inventory_upload'))

    if 'pending_mapping_resolution' in request.session:
        del request.session['pending_mapping_resolution']

    plural = 's' if count != 1 else ''
    messages.success(
        request,
        f'{count} mapping{plural} saved. Re-upload your CSV to continue with the import.',
    )

    return JsonResponse({'ok': True, 'redirect_url': redirect_url})
