"""
Distributor Inventory Order Generation — Phase 4-step-2a.

Public API: generate_projected_orders(distributor, forecast_result, today=None)

Consumes a ForecastResult dict from compute_distributor_forecast() and returns
a structured ProjectedOrdersResult dict. Does NOT write to the database.
Database saving is Phase 4-step-2b.
"""
import math

from apps.reports.utils import _month_add


# Maximum orders generated per trigger month to prevent runaway loops.
_MAX_ORDERS_PER_TRIGGER_MONTH = 5


def generate_projected_orders(distributor, forecast_result, today=None):
    """
    Walk the forecast horizon and generate projected purchase orders.

    An order is triggered the month before any item's projected ending inventory
    would fall below its safety stock target (or below 0 if no target is set).

    Returns a dict:
    {
        'has_order_profile': bool,
        'skipped_items': [{'item': Item, 'reason': str}, ...],
        'orders_per_horizon': [   # 13 entries aligned with forecast_result.horizon
            {
                'year': int, 'month': int, 'is_snapshot': bool,
                'order_count': int,
                'orders': [
                    {
                        'year': int, 'month': int,
                        'order_unit': 'pallets' | 'cases',
                        'order_quantity': int,
                        'lines': [{'item': Item, 'cases': float, 'pallets': int | None}],
                        'total_cases': float,
                    },
                    ...
                ],
            },
            ...
        ],
        'total_orders_count': int,
    }
    """
    horizon = forecast_result.get('horizon', [])
    rows = forecast_result.get('rows', [])
    safety_stock_map = forecast_result.get('safety_stock_map', {})

    # Default result: empty slots aligned with horizon
    def _empty_slots():
        return [
            {
                'year': h['year'], 'month': h['month'],
                'is_snapshot': h['is_snapshot'],
                'order_count': 0, 'orders': [],
            }
            for h in horizon
        ]

    has_profile = (
        distributor.order_quantity_value is not None
        and distributor.order_quantity_unit is not None
    )

    if not has_profile or not rows:
        return {
            'has_order_profile': has_profile,
            'skipped_items': [],
            'orders_per_horizon': _empty_slots(),
            'total_orders_count': 0,
        }

    is_pallets = (distributor.order_quantity_unit == 'pallets')
    order_qty = distributor.order_quantity_value

    # Pre-process items: identify eligible vs skipped
    skipped_items = []
    eligible_rows = []

    for row in rows:
        item = row['item']

        # Skip items with no depletion data in any projection cell
        has_depletion = any(
            cell.get('depletion') is not None
            for cell in row['monthly_data']
            if not cell.get('is_snapshot', False)
        )
        if not has_depletion:
            skipped_items.append({'item': item, 'reason': 'no_depletion_data'})
            continue

        # Skip items missing cases_per_pallet when distributor is pallet-based
        if is_pallets and not item.cases_per_pallet:
            skipped_items.append({'item': item, 'reason': 'no_cases_per_pallet'})
            continue

        eligible_rows.append(row)

    if not eligible_rows:
        return {
            'has_order_profile': True,
            'skipped_items': skipped_items,
            'orders_per_horizon': _empty_slots(),
            'total_orders_count': 0,
        }

    eligible_items = [r['item'] for r in eligible_rows]

    # Virtual inventory: item_id → {(year, month): float | None}
    # Starts as the forecast values; adjusted as orders are placed.
    virtual_inv = {}
    for row in eligible_rows:
        item_id = row['item'].pk
        virtual_inv[item_id] = {}
        for cell in row['monthly_data']:
            virtual_inv[item_id][(cell['year'], cell['month'])] = cell['inventory']

    # Projection months only (skip anchor)
    projection_months = [
        (h['year'], h['month'])
        for h in horizon
        if not h['is_snapshot']
    ]

    # Accumulated orders: {(year, month): [order_dict, ...]}
    orders_by_month = {}

    for month_idx, (trig_year, trig_month) in enumerate(projection_months):
        orders_this_trigger = 0

        while orders_this_trigger < _MAX_ORDERS_PER_TRIGGER_MONTH:
            # Find items that trigger in this month under current virtual inventory
            triggering = _find_triggers(
                eligible_items, virtual_inv, trig_year, trig_month, safety_stock_map
            )
            if not triggering:
                break

            # Build one order's line items
            order_lines = _build_order(
                is_pallets, order_qty,
                triggering, eligible_items,
                virtual_inv, safety_stock_map,
                projection_months, month_idx,
            )
            if not order_lines:
                break

            # Apply the order's cases to virtual inventory from trigger month onward
            for line in order_lines:
                item_id = line['item'].pk
                for ym in projection_months[month_idx:]:
                    cur = virtual_inv[item_id].get(ym)
                    if cur is not None:
                        virtual_inv[item_id][ym] = round(cur + line['cases'], 2)

            # Record order in the PRIOR month (order_placement = one month before trigger)
            order_year, order_month = _month_add(trig_year, trig_month, -1)
            order_key = (order_year, order_month)
            total_cases = sum(l['cases'] for l in order_lines)
            orders_by_month.setdefault(order_key, []).append({
                'year': order_year, 'month': order_month,
                'order_unit': distributor.order_quantity_unit,
                'order_quantity': order_qty,
                'lines': order_lines,
                'total_cases': total_cases,
            })
            orders_this_trigger += 1

    # Build output list aligned with forecast_result.horizon
    orders_per_horizon = []
    for h in horizon:
        ym = (h['year'], h['month'])
        order_list = orders_by_month.get(ym, [])
        orders_per_horizon.append({
            'year': h['year'], 'month': h['month'],
            'is_snapshot': h['is_snapshot'],
            'order_count': len(order_list),
            'orders': order_list,
        })

    total_count = sum(len(v) for v in orders_by_month.values())

    return {
        'has_order_profile': True,
        'skipped_items': skipped_items,
        'orders_per_horizon': orders_per_horizon,
        'total_orders_count': total_count,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_triggers(eligible_items, virtual_inv, year, month, safety_stock_map):
    """Return items whose virtual inventory in (year, month) is below safety stock."""
    triggers = []
    for item in eligible_items:
        inv = virtual_inv[item.pk].get((year, month))
        if inv is None:
            continue
        ss = safety_stock_map.get(item.pk) or 0
        if inv < ss:
            triggers.append((item, inv, ss))
    # Sort by largest deficit first (most critical)
    triggers.sort(key=lambda x: x[1] - x[2])
    return triggers


def _build_order(is_pallets, order_qty, triggering_items, eligible_items,
                  virtual_inv, safety_stock_map, projection_months, trigger_month_idx):
    """
    Allocate one order's capacity across items.

    Step 1: Fill triggering items (most critical first).
    Step 2: Fill remaining capacity with next month's closest-to-safety items.
    """
    capacity = order_qty  # in pallets or cases
    lines = {}  # {item_id: {'item', 'cases', 'pallets'}}

    # Step 1 — triggering items
    for item, inv, ss in triggering_items:
        if capacity <= 0:
            break
        ss_val = ss or 0
        cases_needed = max(0.0, ss_val - inv) + 1.0  # +1 to just clear the threshold

        if is_pallets:
            cpp = item.cases_per_pallet
            pallets_needed = math.ceil(cases_needed / cpp)
            pallets_alloc = min(pallets_needed, capacity)
            cases_added = float(pallets_alloc * cpp)
            capacity -= pallets_alloc
            _add_line(lines, item, cases_added, pallets_alloc)
        else:
            cases_alloc = min(int(math.ceil(cases_needed)), capacity)
            capacity -= cases_alloc
            _add_line(lines, item, float(cases_alloc), None)

    # Step 2 — fill remaining capacity from the next projection month
    if capacity > 0 and trigger_month_idx + 1 < len(projection_months):
        next_ym = projection_months[trigger_month_idx + 1]

        candidates = []
        for item in eligible_items:
            next_inv = virtual_inv[item.pk].get(next_ym)
            if next_inv is None:
                continue
            ss_val = safety_stock_map.get(item.pk) or 0
            margin = next_inv - ss_val
            candidates.append((item, next_inv, ss_val, margin))
        candidates.sort(key=lambda x: x[3])  # smallest margin first (most at risk)

        for item, next_inv, ss_val, margin in candidates:
            if capacity <= 0:
                break
            # Cases to bring this item to safety + 1 buffer (or at least 1 unit)
            cases_needed = max(0.0, ss_val - next_inv) + 1.0

            if is_pallets:
                cpp = item.cases_per_pallet
                if not cpp:
                    continue
                pallets_needed = max(1, math.ceil(cases_needed / cpp))
                pallets_alloc = min(pallets_needed, capacity)
                cases_added = float(pallets_alloc * cpp)
                capacity -= pallets_alloc
                _add_line(lines, item, cases_added, pallets_alloc)
            else:
                cases_alloc = min(max(1, int(math.ceil(cases_needed))), capacity)
                capacity -= cases_alloc
                _add_line(lines, item, float(cases_alloc), None)

    return list(lines.values())


def _add_line(lines, item, cases_added, pallets_added):
    """Add or merge a line item into the lines dict."""
    item_id = item.pk
    if item_id in lines:
        lines[item_id]['cases'] += cases_added
        if pallets_added is not None:
            lines[item_id]['pallets'] = (lines[item_id].get('pallets') or 0) + pallets_added
    else:
        lines[item_id] = {
            'item': item,
            'cases': cases_added,
            'pallets': pallets_added,
        }
