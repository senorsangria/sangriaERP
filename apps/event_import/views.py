"""
Event Import views: upload CSV, review matches, confirm selections, execute import.

Access: Supplier Admin only.

Flow:
  1. event_import_upload  — upload CSV → run matching → store in session
  2. event_import_review  — display match results, let user resolve 'review' rows
  3. event_import_confirm — merge high + user selections → store final map
                            → show summary with "Proceed to Import"
  4. event_import_execute — create Event + EventItemRecap records (Stage 3)
"""
import csv
import io
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from rapidfuzz import fuzz

from apps.accounts.models import Account
from apps.catalog.models import Item
from apps.core.models import User
from apps.distribution.models import Distributor
from apps.event_import.matching import match_csv_row, normalize_for_match
from apps.event_import.models import HistoricalImportBatch
from apps.events.models import Event, EventItemRecap
from apps.events.views import _apply_price_updates

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Access guard
# ---------------------------------------------------------------------------

def _require_supplier_admin(request):
    if not request.user.is_authenticated or not request.user.is_supplier_admin:
        messages.error(request, 'Access denied. Supplier Admin only.')
        return redirect('dashboard')
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_accounts_by_distributor(company):
    """
    Load all active accounts for this company and group them by distributor
    name (strip + title case) for fast lookup during matching.

    Returns:
        dict mapping distributor_name_title → list of account dicts:
        { pk, name, street, city }
    """
    qs = (
        Account.active_accounts
        .filter(company=company)
        .select_related('distributor')
        .values('pk', 'name', 'street', 'city', 'distributor__name')
    )
    result = {}
    for row in qs:
        dist_name = (row['distributor__name'] or '').strip().title()
        acct_dict = {
            'pk':   row['pk'],
            'name': row['name'],
            'street': row['street'],
            'city': row['city'],
        }
        result.setdefault(dist_name, []).append(acct_dict)
    return result


COLUMN_MAP = {
    'event location':               'location',
    'event date':                   'date',
    'event note 1 (retail contact)': 'note1',
    'event note 2 (retailer phone)': 'note2',
    'retail contact':               'note1',
    'retailer phone':               'note2',
    'promo person':                 'promo_person',
    'sample\ncups':                 'samples',
    'qr code scans':                'qr_scans',
    'racap note 1':                 'recap1',
    'recap note 2':                 'recap2',
    'bottles sold bwred0750':       'sold_bwred0750',
    'bottles sold bwred1500':       'sold_bwred1500',
    'bottles sold bwwht0750':       'sold_bwwht0750',
    'bottles sold bwwht1500':       'sold_bwwht1500',
    'bottles sold bwapprasp1l':     'sold_bwapprasp1l',
    'bottles used bwred0750':       'used_bwred0750',
    'bottles used bwred1500':       'used_bwred1500',
    'bottles used bwwht0750':       'used_bwwht0750',
    'bottles used bwwht1500':       'used_bwwht1500',
    'bottles used bwapprasp1l':     'used_bwapprasp1l',
    'bottle price bwred0750':       'price_bwred0750',
    'bottle price bwred1500':       'price_bwred1500',
    'bottle price bwwht0750':       'price_bwwht0750',
    'bottle price bwwht1500':       'price_bwwht1500',
    'bottle price bwapprasp1l':     'price_bwapprasp1l',
}


def _parse_csv(file_obj):
    """
    Parse an uploaded CSV file-like object.
    Returns a list of dicts (one per row, keys lowercased, stripped,
    and renamed via COLUMN_MAP to match the internal field names used
    by the matching engine and Stage 3 import).
    """
    text = file_obj.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        normalized = {
            k.strip().lower(): (v or '').strip()
            for k, v in row.items()
        }
        mapped = {
            COLUMN_MAP.get(k, k): v
            for k, v in normalized.items()
        }
        # Skip blank rows (spreadsheet trailing empty rows)
        if not any([
            mapped.get('location', ''),
            mapped.get('address', ''),
            mapped.get('city', ''),
        ]):
            continue
        rows.append(mapped)
    return rows


def _csv_key(row):
    """Unique key for a (distributor, location, address, city) combo."""
    return '||'.join([
        row.get('distributor', '').strip().title(),
        row.get('location', '').strip(),
        row.get('address', '').strip(),
        row.get('city', '').strip(),
    ])


def _parse_int(value):
    """Parse an integer from a string, returning None if blank or invalid."""
    if not value or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned.upper() in ('N/A', 'NA', '-', 'NONE'):
        return None
    try:
        return int(float(cleaned))
    except (ValueError, AttributeError):
        return None


def _parse_price(value):
    """Parse a Decimal price from a string, stripping $ and commas."""
    if not value or not value.strip():
        return None
    cleaned = value.strip().replace('$', '').replace(',', '').strip()
    if cleaned.upper() in ('N/A', 'NA', '-', 'NONE'):
        return None
    try:
        return Decimal(cleaned) if cleaned else None
    except (InvalidOperation, AttributeError):
        return None


# ---------------------------------------------------------------------------
# View 1 — Upload
# ---------------------------------------------------------------------------

def event_import_upload(request):
    guard = _require_supplier_admin(request)
    if guard:
        return guard

    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            messages.error(request, 'Please select a CSV file to upload.')
            return render(request, 'event_import/upload.html')

        if not csv_file.name.lower().endswith('.csv'):
            messages.error(request, 'File must be a CSV (.csv) file.')
            return render(request, 'event_import/upload.html')

        try:
            rows = _parse_csv(csv_file)
        except Exception as exc:
            messages.error(request, f'Could not read CSV file: {exc}')
            return render(request, 'event_import/upload.html')

        if not rows:
            messages.error(request, 'The CSV file is empty.')
            return render(request, 'event_import/upload.html')

        # Load accounts grouped by distributor
        accounts_by_distributor = _build_accounts_by_distributor(
            request.user.company
        )

        # Deduplicate rows by (distributor, location, address, city)
        # Track how many CSV rows map to each unique combo
        unique_combos = {}   # key → first row seen
        combo_counts = {}    # key → count

        for row in rows:
            key = _csv_key(row)
            combo_counts[key] = combo_counts.get(key, 0) + 1
            if key not in unique_combos:
                unique_combos[key] = row

        # Run matching on each unique combo
        high = []
        review = []
        none = []

        for key, row in unique_combos.items():
            result = match_csv_row(row, accounts_by_distributor)
            row_count = combo_counts[key]

            if result['status'] == 'high':
                high.append({
                    'csv_key':          key,
                    'distributor':      row.get('distributor', '').strip().title(),
                    'location':         row.get('location', '').strip(),
                    'address':          row.get('address', '').strip(),
                    'city':             row.get('city', '').strip(),
                    'match_account_pk':   result['match']['pk'],
                    'match_account_name': result['match']['name'],
                    'row_count':        row_count,
                    'score':            result['score'],
                })
            elif result['status'] == 'review':
                review.append({
                    'csv_key':    key,
                    'distributor': row.get('distributor', '').strip().title(),
                    'location':   row.get('location', '').strip(),
                    'address':    row.get('address', '').strip(),
                    'city':       row.get('city', '').strip(),
                    'candidates': result['candidates'],
                    'row_count':  row_count,
                    'best_score': result['score'],
                })
            else:
                none.append({
                    'csv_key':    key,
                    'distributor': row.get('distributor', '').strip().title(),
                    'location':   row.get('location', '').strip(),
                    'address':    row.get('address', '').strip(),
                    'city':       row.get('city', '').strip(),
                    'row_count':  row_count,
                })

        # Store in session
        request.session['event_import_matches'] = {
            'high':   high,
            'review': review,
            'none':   none,
        }
        # Store raw rows for Stage 3
        request.session['event_import_rows'] = rows

        return redirect('event_import_review')

    batches = HistoricalImportBatch.objects.filter(
        company=request.user.company
    ).order_by('-imported_at')
    return render(request, 'event_import/upload.html', {'batches': batches})


# ---------------------------------------------------------------------------
# View 2 — Review
# ---------------------------------------------------------------------------

def event_import_review(request):
    guard = _require_supplier_admin(request)
    if guard:
        return guard

    session_data = request.session.get('event_import_matches')
    if not session_data:
        messages.error(request, 'No import in progress. Please upload a CSV first.')
        return redirect('event_import_upload')

    high   = sorted(session_data['high'],   key=lambda x: x['score'], reverse=True)
    review = sorted(session_data['review'], key=lambda x: x['row_count'], reverse=True)
    none   = session_data['none']

    total_rows = (
        sum(x['row_count'] for x in high)
        + sum(x['row_count'] for x in review)
        + sum(x['row_count'] for x in none)
    )

    summary = {
        'total_unique_locations': len(high) + len(review) + len(none),
        'high_count':   len(high),
        'review_count': len(review),
        'none_count':   len(none),
        'total_rows':   total_rows,
        'high_rows':    sum(x['row_count'] for x in high),
        'review_rows':  sum(x['row_count'] for x in review),
        'none_rows':    sum(x['row_count'] for x in none),
    }

    return render(request, 'event_import/review.html', {
        'high_matches':   high,
        'review_matches': review,
        'none_matches':   none,
        'summary':        summary,
    })


# ---------------------------------------------------------------------------
# View 3 — Confirm
# ---------------------------------------------------------------------------

def event_import_confirm(request):
    guard = _require_supplier_admin(request)
    if guard:
        return guard

    if request.method != 'POST':
        return redirect('event_import_review')

    session_data = request.session.get('event_import_matches')
    if not session_data:
        messages.error(request, 'No import in progress. Please upload a CSV first.')
        return redirect('event_import_upload')

    # Build final match map: csv_key → account_pk (or None = skip)
    final_map = {}

    # Auto-accept high confidence matches
    for item in session_data['high']:
        final_map[item['csv_key']] = item['match_account_pk']

    # Apply user selections for review items
    for item in session_data['review']:
        field_name = 'match_' + item['csv_key']
        selected = request.POST.get(field_name, 'none')
        if selected == 'none':
            final_map[item['csv_key']] = None
        else:
            try:
                final_map[item['csv_key']] = int(selected)
            except (ValueError, TypeError):
                final_map[item['csv_key']] = None

    # No-match items are always skipped
    for item in session_data['none']:
        final_map[item['csv_key']] = None

    # Store confirmed map in session
    request.session['event_import_confirmed'] = final_map

    # Build summary counts
    matched_keys   = [k for k, v in final_map.items() if v is not None]
    skipped_keys   = [k for k, v in final_map.items() if v is None]

    # Count events (rows) for each
    rows = request.session.get('event_import_rows', [])
    row_counts = {}
    for row in rows:
        k = _csv_key(row)
        row_counts[k] = row_counts.get(k, 0) + 1

    matched_events = sum(row_counts.get(k, 0) for k in matched_keys)
    skipped_events = sum(row_counts.get(k, 0) for k in skipped_keys)
    matched_accounts = len(set(v for v in final_map.values() if v is not None))

    imported_count = Event.objects.filter(
        is_imported=True,
        company=request.user.company,
    ).count()

    return render(request, 'event_import/confirm.html', {
        'matched_events':   matched_events,
        'matched_accounts': matched_accounts,
        'skipped_events':   skipped_events,
        'total_events':     matched_events + skipped_events,
        'imported_count':   imported_count,
    })


# ---------------------------------------------------------------------------
# View 4 — Execute Import (Stage 3)
# ---------------------------------------------------------------------------

ITEM_CODES = ['BWRed0750', 'BWRed1500', 'BWWht0750', 'BWWht1500', 'BWAppRasp1L']


def event_import_execute(request):
    """
    Stage 3: Create Event and EventItemRecap records for all confirmed matches.
    POST only.
    """
    guard = _require_supplier_admin(request)
    if guard:
        return guard

    if request.method != 'POST':
        return redirect('event_import_confirm')

    confirmed = request.session.get('event_import_confirmed')
    rows      = request.session.get('event_import_rows')
    matches   = request.session.get('event_import_matches')

    if confirmed is None or rows is None or matches is None:
        messages.error(request, 'No import in progress. Please upload a CSV first.')
        return redirect('event_import_upload')

    # Sort rows oldest-first so events are created in ascending date order
    def _parse_date_for_sort(row):
        raw = row.get('date', '')
        for fmt in ('%m/%d/%y', '%m/%d/%Y'):
            try:
                return datetime.strptime(raw.strip(), fmt)
            except (ValueError, AttributeError):
                continue
        return datetime.min  # unparseable dates sort first

    rows = sorted(rows, key=_parse_date_for_sort)

    # Find Supplier Admin for this company to use as ambassador/event_manager
    supplier_admin = User.objects.filter(
        company=request.user.company,
        roles__codename='supplier_admin',
    ).first() or request.user

    # Create batch record
    batch = HistoricalImportBatch.objects.create(
        company=request.user.company,
        imported_by=request.user,
        csv_filename='Old_Events_to_Import.csv',
    )

    # Build item lookup by item_code
    items_by_code = {
        item.item_code: item
        for item in Item.objects.filter(brand__company=request.user.company)
    }

    # Process each row
    for row in rows:
        key        = _csv_key(row)
        account_pk = confirmed.get(key)

        if account_pk is None:
            continue

        try:
            account = Account.objects.get(pk=account_pk)
        except Account.DoesNotExist:
            logger.warning(f'event_import_execute: account pk={account_pk} not found, skipping')
            continue

        # Parse date
        raw_date = row.get('date', '')
        event_date = None
        for fmt in ('%m/%d/%y', '%m/%d/%Y'):
            try:
                event_date = datetime.strptime(raw_date, fmt).date()
                break
            except (ValueError, TypeError):
                continue
        if event_date is None:
            logger.warning(f'event_import_execute: could not parse date "{raw_date}", skipping row')
            continue

        # Parse start_time
        raw_start = row.get('start', '')
        start_time = None
        try:
            start_time = datetime.strptime(raw_start.strip(), '%I:%M %p').time()
        except (ValueError, AttributeError):
            pass

        # Parse duration_hours
        raw_hrs = row.get('hrs', '')
        try:
            duration_hours = int(float(raw_hrs)) if raw_hrs.strip() else 1
        except (ValueError, AttributeError):
            duration_hours = 1

        # Build notes
        parts = []
        retail_contact = row.get('note1') or row.get('retail contact', '')
        retailer_phone = row.get('note2') or row.get('retailer phone', '')
        promo_person = row.get('promo_person', '')

        if retail_contact:
            parts.append(f"Retail Contact: {retail_contact}")
        if retailer_phone:
            parts.append(f"Retail Phone: {retailer_phone}")
        if promo_person:
            parts.append(f"Promo Person: {promo_person}")
        notes = '\n'.join(parts) if parts else ''

        # Build recap_notes
        parts = []
        if row.get('recap1'):
            parts.append(row['recap1'])
        if row.get('recap2'):
            parts.append(row['recap2'])
        recap_notes = ' | '.join(parts) if parts else ''

        # Parse recap fields
        recap_samples_poured      = _parse_int(row.get('samples', ''))
        recap_qr_codes_scanned    = _parse_int(row.get('qr_scans', ''))

        # Create Event
        event = Event.objects.create(
            company=request.user.company,
            account=account,
            event_type='tasting',
            status='paid',
            date=event_date,
            start_time=start_time,
            duration_hours=duration_hours,
            duration_minutes=0,
            ambassador=supplier_admin,
            event_manager=supplier_admin,
            created_by=request.user,
            notes=notes,
            recap_notes=recap_notes,
            recap_samples_poured=recap_samples_poured,
            recap_qr_codes_scanned=recap_qr_codes_scanned,
            is_imported=True,
            legacy_ambassador_name=row.get('promo_person', ''),
            historical_batch=batch,
        )

        # Create EventItemRecap records for any item with data
        for item_code in ITEM_CODES:
            code_lower = item_code.lower()
            sold  = _parse_int(row.get(f'sold_{code_lower}', ''))
            used  = _parse_int(row.get(f'used_{code_lower}', ''))
            price = _parse_price(row.get(f'price_{code_lower}', ''))

            if any(v is not None for v in [sold, used, price]):
                item = items_by_code.get(item_code)
                if item:
                    event.items.add(item)
                    EventItemRecap.objects.create(
                        event=event,
                        item=item,
                        bottles_sold=sold,
                        bottles_used_for_samples=used,
                        shelf_price=price,
                    )

        _apply_price_updates(event, supplier_admin)

    # Update batch event count
    batch.event_count = Event.objects.filter(historical_batch=batch).count()
    batch.save()

    # Clear session
    for key in ['event_import_matches', 'event_import_rows', 'event_import_confirmed']:
        request.session.pop(key, None)

    messages.success(request, f'Successfully imported {batch.event_count} events.')
    return redirect('event_import_upload')


# ---------------------------------------------------------------------------
# View 5 — Export CSV
# ---------------------------------------------------------------------------

def event_import_export_csv(request):
    guard = _require_supplier_admin(request)
    if guard:
        return guard

    rows      = request.session.get('event_import_rows')
    confirmed = request.session.get('event_import_confirmed')
    matches   = request.session.get('event_import_matches')

    if rows is None or confirmed is None or matches is None:
        messages.error(request, 'No import in progress. Please upload a CSV first.')
        return redirect('event_import_upload')

    # Build set of csv_keys per bucket for status lookup
    high_keys   = {item['csv_key'] for item in matches.get('high',   [])}
    review_keys = {item['csv_key'] for item in matches.get('review', [])}

    # Fetch all matched Account objects in one query
    confirmed_pks = [pk for pk in confirmed.values() if pk is not None]
    accounts_by_pk = {
        acct.pk: acct
        for acct in Account.objects.filter(pk__in=confirmed_pks)
    }

    # Build output
    output = io.StringIO()
    # Determine field names from the first row; preserve original column order
    if rows:
        original_fieldnames = list(rows[0].keys())
    else:
        original_fieldnames = []

    extra_cols = ['Matched Account Name', 'Matched Account Address', 'Matched Account City']
    writer = csv.DictWriter(
        output,
        fieldnames=original_fieldnames + extra_cols,
        extrasaction='ignore',
    )
    writer.writeheader()

    for row in rows:
        key        = _csv_key(row)
        account_pk = confirmed.get(key)
        account    = accounts_by_pk.get(account_pk) if account_pk is not None else None

        out_row = dict(row)
        out_row['Matched Account Name']    = account.name    if account else ''
        out_row['Matched Account Address'] = account.street  if account else ''
        out_row['Matched Account City']    = account.city    if account else ''
        writer.writerow(out_row)

    response = HttpResponse(output.getvalue(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="event_import_matched.csv"'
    return response


# ---------------------------------------------------------------------------
# View 5 — Delete All Imported Events
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# View 6 — Validate CSV (pre-upload distributor conflict check)
# ---------------------------------------------------------------------------

def event_import_validate_csv(request):
    guard = _require_supplier_admin(request)
    if guard:
        return guard

    if request.method != 'POST':
        return redirect('event_import_upload')

    csv_file = request.FILES.get('csv_file')
    if not csv_file:
        messages.error(request, 'Please select a CSV file to validate.')
        return redirect('event_import_upload')

    try:
        rows = _parse_csv(csv_file)
    except Exception as exc:
        messages.error(request, f'Could not read CSV file: {exc}')
        return redirect('event_import_upload')

    if not rows:
        messages.error(request, 'The CSV file is empty.')
        return redirect('event_import_upload')

    total_rows = len(rows)

    # PHASE 1 — Find cities with multiple distributors in the CSV
    city_dist_data = {}  # city (title case) → {dist_name → {count, locations}}
    for row in rows:
        city = row.get('city', '').strip().title()
        dist = row.get('distributor', '').strip().title()
        location = row.get('location', '').strip()
        if not city:
            continue
        city_dist_data.setdefault(city, {})
        if dist not in city_dist_data[city]:
            city_dist_data[city][dist] = {'count': 0, 'locations': set()}
        city_dist_data[city][dist]['count'] += 1
        if location:
            city_dist_data[city][dist]['locations'].add(location)

    conflicting_cities = {
        city: dist_data
        for city, dist_data in city_dist_data.items()
        if len(dist_data) > 1
    }

    total_cities = len(city_dist_data)
    conflict_cities = len(conflicting_cities)

    # PHASE 2 + 3 — Resolve and build conflict report
    conflicts = []
    needs_fix = 0
    no_suggestion = 0

    for city, dist_data in conflicting_cities.items():
        csv_distributors = sorted([
            {
                'name': dist_name,
                'event_count': info['count'],
                'locations': sorted(info['locations']),
            }
            for dist_name, info in dist_data.items()
        ], key=lambda x: x['event_count'], reverse=True)

        # Step 1 — find DB distributors with active accounts in this city
        city_accounts_qs = (
            Account.active_accounts
            .filter(company=request.user.company, city__iexact=city)
            .select_related('distributor')
        )
        db_dist_accounts = {}  # Distributor obj → [account name, ...]
        for acct in city_accounts_qs:
            db_dist_accounts.setdefault(acct.distributor, []).append(acct.name)
        db_distributors = list(db_dist_accounts.keys())

        if len(db_distributors) == 1:
            suggested = db_distributors[0]
            confidence = 'high'
            reason = 'Only distributor with accounts in this city in sales data'
        elif len(db_distributors) == 0:
            suggested = None
            confidence = 'unknown'
            reason = 'No accounts found for this city in database'
        else:
            # Step 2 — retailer name matching
            match_counts = {db_dist: 0 for db_dist in db_distributors}
            for csv_dist_name, info in dist_data.items():
                for loc in info['locations']:
                    norm_loc = normalize_for_match(loc)
                    for db_dist in db_distributors:
                        for db_name in db_dist_accounts[db_dist]:
                            if fuzz.token_sort_ratio(norm_loc, normalize_for_match(db_name)) >= 80:
                                match_counts[db_dist] += 1
                                break

            best_count = max(match_counts.values())
            winners = [d for d, c in match_counts.items() if c == best_count]
            if len(winners) == 1:
                suggested = winners[0]
                confidence = 'medium'
                reason = 'Multiple distributors in DB — resolved by retailer name matching'
            else:
                suggested = None
                confidence = 'low'
                reason = 'Needs manual review — cannot determine correct distributor'

        suggested_name = suggested.name.strip().title() if suggested else None
        is_correct = (
            suggested_name is not None
            and all(d['name'].strip().title() == suggested_name for d in csv_distributors)
        )

        conflict_dict = {
            'city': city,
            'csv_distributors': csv_distributors,
            'suggested_distributor': suggested_name,
            'confidence': confidence,
            'reason': reason,
            'is_correct': is_correct,
        }

        if not is_correct:
            needs_fix += 1
        if confidence in ('unknown', 'low'):
            no_suggestion += 1

        conflicts.append(conflict_dict)

    conflicts_to_show = [c for c in conflicts if not c['is_correct']]

    summary = {
        'total_cities': total_cities,
        'conflict_cities': conflict_cities,
        'needs_fix': needs_fix,
        'no_suggestion': no_suggestion,
    }

    return render(request, 'event_import/validate.html', {
        'conflicts': conflicts_to_show,
        'summary': summary,
        'total_rows': total_rows,
    })


# ---------------------------------------------------------------------------
# View 5 — Delete All Imported Events
# ---------------------------------------------------------------------------

def event_import_delete_all(request):
    guard = _require_supplier_admin(request)
    if guard:
        return guard

    qs = Event.objects.filter(
        is_imported=True,
        company=request.user.company,
    )

    if request.method == 'POST':
        count = qs.count()
        qs.delete()
        # Also clean up all batch records for this company
        HistoricalImportBatch.objects.filter(company=request.user.company).delete()
        messages.success(request, f'Successfully deleted {count} imported event{("s" if count != 1 else "")}.')
        return redirect('event_import_upload')

    # GET — show confirmation page
    count = qs.count()
    return render(request, 'event_import/delete_all.html', {'imported_count': count})


# ---------------------------------------------------------------------------
# View 8 — Delete a single import batch
# ---------------------------------------------------------------------------

def event_import_delete_batch(request, batch_id):
    """Delete one historical import batch and all its events. POST only."""
    guard = _require_supplier_admin(request)
    if guard:
        return guard

    if request.method != 'POST':
        return redirect('event_import_upload')

    batch = get_object_or_404(
        HistoricalImportBatch,
        pk=batch_id,
        company=request.user.company,
    )

    count = Event.objects.filter(historical_batch=batch).count()
    Event.objects.filter(historical_batch=batch).delete()
    batch.delete()

    messages.success(request, f'Deleted import batch ({count} event{("s" if count != 1 else "")} removed).')
    return redirect('event_import_upload')
