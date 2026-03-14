"""
Event Import views: upload CSV, review matches, confirm selections.

Access: Supplier Admin only.

Flow:
  1. event_import_upload  — upload CSV → run matching → store in session
  2. event_import_review  — display match results, let user resolve 'review' rows
  3. event_import_confirm — merge high + user selections → store final map
                            → show summary with "Proceed to Import" (Stage 3)
"""
import csv
import io

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from rapidfuzz import fuzz

from apps.accounts.models import Account
from apps.distribution.models import Distributor
from apps.event_import.matching import match_csv_row, normalize_for_match
from apps.events.models import Event


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
    'promo person':                 'promo_person',
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

    return render(request, 'event_import/upload.html')


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
    review = sorted(session_data['review'], key=lambda x: x['best_score'], reverse=True)
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
# View 4 — Export CSV
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
        messages.success(request, f'Successfully deleted {count} imported event{("s" if count != 1 else "")}.')
        return redirect('event_import_upload')

    # GET — show confirmation page
    count = qs.count()
    return render(request, 'event_import/delete_all.html', {'imported_count': count})
