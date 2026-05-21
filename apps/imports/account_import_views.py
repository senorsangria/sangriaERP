"""
Account Import views.

Flow:
  1. account_import_upload  — upload CSV, parse, store preview in session
  2. account_import_preview — review summary and first 20 rows
  3. account_import_execute — execute the import from session data

Distributor is read per-row from the required "Distributors" CSV column.
Case-insensitive name matching against active distributors in company.

Access: Supplier Admin only (has_permission('can_import_sales_data')).
All queries scoped to request.user.company.
"""
import csv
import io
from collections import defaultdict

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from apps.accounts.models import Account
from apps.distribution.models import Distributor
from utils.normalize import normalize_address


# ---------------------------------------------------------------------------
# Permission guard
# ---------------------------------------------------------------------------

def _require_can_import(request):
    """Return redirect if user lacks import permission, else None."""
    if not request.user.is_authenticated:
        return redirect('login')
    if not request.user.has_permission('can_import_sales_data'):
        return render(request, '403.html', status=403)
    return None


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize_key(value):
    """Uppercase + strip whitespace for match key fields."""
    return (value or '').strip().upper()


def _strip_excel_zip(value):
    """
    Strip Excel leading-zero preservation format from a zip code string.
    ="07030" → 07030  |  "07030" → 07030  |  07030 → 07030
    """
    v = (value or '').strip()
    if v.startswith('='):
        v = v[1:]
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v


def _parse_county(raw):
    """Strip state suffix from county string: 'UNION, NJ' → 'UNION'."""
    raw = (raw or '').strip()
    if not raw:
        return ''
    return raw.split(',')[0].strip()


# ---------------------------------------------------------------------------
# CSV column detection
# ---------------------------------------------------------------------------

_ACCOUNT_CSV_COLUMN_MAP = {
    'Distributors':       'distributor_name',
    'Counties':           'counties',
    'OnOff Premises':     'on_off',
    'Classes of Trade':   'classes_of_trade',
    'Retail Accounts':    'account',
    'Address':            'address',
    'City':               'city',
    'State':              'state',
    'Zip Code':           'zip',
    'VIP Outlet ID':      'vip',
    'Distributor Routes': 'dist_routes',
}

_REQUIRED_COLUMNS = {'Retail Accounts', 'Address', 'City', 'State', 'Distributors'}


def _detect_columns(header_row):
    """
    Return dict mapping logical name → column index for all recognised columns.
    Required columns raise ValueError if absent; optional columns map to None.
    """
    headers = [h.strip() for h in header_row]
    missing = _REQUIRED_COLUMNS - set(headers)
    if missing:
        raise ValueError(f'Required column(s) missing: {", ".join(sorted(missing))}')

    result = {}
    for csv_col, key in _ACCOUNT_CSV_COLUMN_MAP.items():
        result[key] = headers.index(csv_col) if csv_col in headers else None
    return result


# ---------------------------------------------------------------------------
# CSV row parser
# ---------------------------------------------------------------------------

def _parse_account_csv(file_obj):
    """
    Parse account import CSV from a file-like object.

    Returns (rows, skipped_count) where rows is a list of dicts with keys:
        distributor_name, name, street, city, state, zip_code, county,
        on_off_premise, account_type, third_party_id, distributor_route
    Rows missing any of the four required account fields are silently skipped.
    """
    text = file_obj.read()
    if isinstance(text, bytes):
        text = text.decode('utf-8-sig')

    reader = csv.reader(io.StringIO(text))
    try:
        header_row = next(reader)
    except StopIteration:
        return [], 0

    cols = _detect_columns(header_row)

    def _get(row, key):
        idx = cols.get(key)
        if idx is None or idx >= len(row):
            return ''
        return row[idx].strip()

    rows = []
    skipped = 0

    for row in reader:
        if not any(cell.strip() for cell in row):
            continue  # blank row

        name   = _get(row, 'account')
        street = _get(row, 'address')
        city   = _get(row, 'city')
        state  = _get(row, 'state')

        # All four key account fields must be present
        if not (name and street and city and state):
            skipped += 1
            continue

        on_off_raw = _get(row, 'on_off').upper()
        on_off = on_off_raw if on_off_raw in ('ON', 'OFF') else 'Unknown'

        rows.append({
            'distributor_name':  _get(row, 'distributor_name'),
            'name':              name,
            'street':            street,
            'city':              city,
            'state':             state,
            'zip_code':          _strip_excel_zip(_get(row, 'zip')),
            'county':            _parse_county(_get(row, 'counties')),
            'on_off_premise':    on_off,
            'account_type':      _get(row, 'classes_of_trade'),
            'third_party_id':    _get(row, 'vip'),
            'distributor_route': _get(row, 'dist_routes'),
        })

    return rows, skipped


# ---------------------------------------------------------------------------
# Match existing accounts
# ---------------------------------------------------------------------------

def _categorize_rows(rows, company):
    """
    For each parsed row determine whether it is a CREATE or UPDATE.

    Match key: (distributor_id, name, street, city, state) — all normalized.
    Uses the per-row 'distributor' object attached by _resolve_distributors.
    """
    dist_ids = {row['distributor'].pk for row in rows if row.get('distributor')}

    existing = {}
    for a in Account.objects.filter(
        company=company, distributor_id__in=dist_ids
    ).only('pk', 'distributor_id', 'name', 'street', 'city', 'state'):
        key = (
            a.distributor_id,
            _normalize_key(a.name),
            normalize_address(a.street),
            _normalize_key(a.city),
            _normalize_key(a.state),
        )
        existing[key] = a.pk

    result = []
    for row in rows:
        dist = row.get('distributor')
        key = (
            dist.pk if dist else None,
            _normalize_key(row['name']),
            normalize_address(row['street']),
            _normalize_key(row['city']),
            _normalize_key(row['state']),
        )
        if key in existing:
            result.append({**row, 'action': 'UPDATE', 'existing_pk': existing[key]})
        else:
            result.append({**row, 'action': 'CREATE', 'existing_pk': None})

    return result


# ---------------------------------------------------------------------------
# VIEW 1 — account_import_upload
# ---------------------------------------------------------------------------

def account_import_upload(request):
    denied = _require_can_import(request)
    if denied:
        return denied

    company = request.user.company

    def _render_upload(error=None):
        return render(request, 'imports/account_import_upload.html', {'error': error})

    if request.method == 'POST':
        uploaded = request.FILES.get('csv_file')
        if not uploaded:
            return _render_upload(error='Please select a CSV file to upload.')

        try:
            rows, skipped = _parse_account_csv(uploaded)
        except ValueError as exc:
            return _render_upload(error=str(exc))
        except Exception as exc:
            return _render_upload(error=f'Could not parse CSV: {exc}')

        if not rows and skipped == 0:
            return _render_upload(error='The CSV file appears to be empty.')

        # Resolve and validate distributor names (case-insensitive, active only)
        from apps.imports.utils import _resolve_distributors
        rows, errors = _resolve_distributors(rows, company)
        if errors:
            return _render_upload(error=errors[0])

        categorized = _categorize_rows(rows, company)

        # Per-distributor summary for the preview page
        dist_summary: dict = defaultdict(lambda: {'creates': 0, 'updates': 0})
        for row in categorized:
            dist = row.get('distributor')
            name = dist.name if dist else 'Unknown'
            if row['action'] == 'CREATE':
                dist_summary[name]['creates'] += 1
            else:
                dist_summary[name]['updates'] += 1
        distributor_summaries = [
            {'name': name, 'creates': counts['creates'], 'updates': counts['updates']}
            for name, counts in sorted(dist_summary.items())
        ]

        # Strip non-serializable distributor objects before session storage
        for row in categorized:
            row.pop('distributor', None)

        request.session['account_import_preview'] = {
            'rows': categorized,
            'skipped': skipped,
            'distributor_summaries': distributor_summaries,
        }

        return redirect('account_import_preview')

    return _render_upload()


# ---------------------------------------------------------------------------
# VIEW 2 — account_import_preview
# ---------------------------------------------------------------------------

def account_import_preview(request):
    denied = _require_can_import(request)
    if denied:
        return denied

    preview_data = request.session.get('account_import_preview')
    if not preview_data:
        messages.warning(request, 'No import in progress. Please upload a CSV file.')
        return redirect('account_import_upload')

    rows                  = preview_data['rows']
    skipped               = preview_data['skipped']
    distributor_summaries = preview_data.get('distributor_summaries', [])
    creates = sum(1 for r in rows if r['action'] == 'CREATE')
    updates = sum(1 for r in rows if r['action'] == 'UPDATE')

    return render(request, 'imports/account_import_preview.html', {
        'total':                len(rows),
        'creates':              creates,
        'updates':              updates,
        'skipped':              skipped,
        'preview_rows':         rows[:20],
        'distributor_summaries': distributor_summaries,
    })


# ---------------------------------------------------------------------------
# VIEW 3 — account_import_execute
# ---------------------------------------------------------------------------

def account_import_execute(request):
    denied = _require_can_import(request)
    if denied:
        return denied

    if request.method != 'POST':
        return redirect('account_import_preview')

    preview_data = request.session.get('account_import_preview')
    if not preview_data:
        messages.warning(request, 'No import in progress. Please upload a CSV file.')
        return redirect('account_import_upload')

    rows    = preview_data['rows']
    company = request.user.company

    # Re-fetch distributor objects from stored PKs (session only holds serializable ints)
    dist_pks = {row.get('distributor_pk') for row in rows if row.get('distributor_pk')}
    dist_map = {
        d.pk: d
        for d in Distributor.objects.filter(pk__in=dist_pks, company=company)
    }

    created_count = 0
    updated_count = 0

    with transaction.atomic():
        for row in rows:
            distributor = dist_map.get(row.get('distributor_pk'))

            if row['action'] == 'CREATE':
                Account.objects.create(
                    company=company,
                    distributor=distributor,
                    name=row['name'],
                    street=row['street'],
                    city=row['city'],
                    state=row['state'],
                    zip_code=row['zip_code'],
                    county=row['county'] or 'Unknown',
                    on_off_premise=row['on_off_premise'],
                    account_type=row['account_type'],
                    third_party_id=row['third_party_id'],
                    distributor_route=row['distributor_route'],
                    is_active=True,
                    auto_created=True,
                    address_normalized=normalize_address(row['street']),
                    city_normalized=_normalize_key(row['city']),
                    state_normalized=_normalize_key(row['state']),
                )
                created_count += 1

            elif row['action'] == 'UPDATE' and row.get('existing_pk'):
                update_fields = {}
                if distributor is not None:
                    update_fields['distributor'] = distributor
                if row['zip_code']:
                    update_fields['zip_code'] = row['zip_code']
                if row['county']:
                    update_fields['county'] = row['county']
                if row['on_off_premise'] != 'Unknown':
                    update_fields['on_off_premise'] = row['on_off_premise']
                if row['account_type']:
                    update_fields['account_type'] = row['account_type']
                if row['third_party_id']:
                    update_fields['third_party_id'] = row['third_party_id']
                if row['distributor_route']:
                    update_fields['distributor_route'] = row['distributor_route']

                if update_fields:
                    Account.objects.filter(
                        pk=row['existing_pk'], company=company
                    ).update(**update_fields)
                updated_count += 1

    del request.session['account_import_preview']

    msg = f'Import complete: {created_count} account(s) created, {updated_count} account(s) updated.'
    messages.success(request, msg)
    return redirect('account_list')
