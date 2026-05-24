"""
Distributor Inventory Order Generation — Phase 4-step-2a (revised).

Public API: generate_projected_orders(distributor, forecast_result, today=None)

Each month M is covered independently: if any items drop below safety stock in M,
one order is placed in M-1 sized to bring those items to safety stock. Remaining
capacity is filled with items that will trigger in M+1, M+2, … using the same
formula. Multiple orders per prior month are generated when a single item's need
exceeds order_quantity capacity (cap: 5 per prior month).
"""
import math

from apps.reports.utils import _month_add


# Maximum orders generated per prior month to prevent runaway loops.
_MAX_ORDERS_PER_TRIGGER_MONTH = 5


def generate_projected_orders(distributor, forecast_result, today=None):
    """
    Walk the forecast horizon and generate projected purchase orders.

    For each projection month M, if any items' projected ending inventory is
    below safety stock (or below 0 when no target is set), generate one order
    placed in month M-1. The order is sized so each item reaches its safety
    stock level. Remaining order capacity is filled with items that would
    trigger in subsequent months (M+1, M+2, …).

    Each generated order is applied to the virtual inventory before checking
    the next month, so subsequent trigger checks reflect prior orders.

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

        has_depletion = any(
            cell.get('depletion') is not None
            for cell in row['monthly_data']
            if not cell.get('is_snapshot', False)
        )
        if not has_depletion:
            skipped_items.append({'item': item, 'reason': 'no_depletion_data'})
            continue

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
    # Starts as forecast values; adjusted as orders are placed.
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
        orders_this_prior = 0

        while orders_this_prior < _MAX_ORDERS_PER_TRIGGER_MONTH:
            triggering = _find_triggers(
                eligible_items, virtual_inv, trig_year, trig_month, safety_stock_map
            )
            if not triggering:
                break

            order_lines = _build_order(
                is_pallets, order_qty,
                triggering, eligible_items,
                virtual_inv, safety_stock_map,
                projection_months, month_idx,
            )
            if not order_lines:
                break

            # Apply order cases to virtual inventory from trigger month onward
            for line in order_lines:
                item_id = line['item'].pk
                for ym in projection_months[month_idx:]:
                    cur = virtual_inv[item_id].get(ym)
                    if cur is not None:
                        virtual_inv[item_id][ym] = round(cur + line['cases'], 2)

            # Record order in the prior month
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
            orders_this_prior += 1

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

    Sizing formula: required_cases = max(0, safety_stock - virtual_inv[month])
    This brings ending inventory exactly to safety stock; pallet rounding adds
    a natural buffer for pallet-based distributors.

    Step 1: Cover triggering items (most critical first).
    Step 2: Fill remaining capacity with items that would trigger in subsequent
            months (M+1, M+2, …), scanning forward until capacity is exhausted.
    """
    capacity = order_qty
    lines = {}

    # Step 1 — triggering items
    for item, inv, ss in triggering_items:
        if capacity <= 0:
            break
        ss_val = ss or 0
        required_cases = max(0.0, ss_val - inv)
        if required_cases == 0.0:
            required_cases = 1.0  # inv exactly at ss; add a minimum unit

        if is_pallets:
            cpp = item.cases_per_pallet
            pallets_needed = math.ceil(required_cases / cpp)
            pallets_alloc = min(pallets_needed, capacity)
            cases_added = float(pallets_alloc * cpp)
            capacity -= pallets_alloc
            _add_line(lines, item, cases_added, pallets_alloc)
        else:
            cases_alloc = min(int(math.ceil(required_cases)), int(capacity))
            capacity -= cases_alloc
            _add_line(lines, item, float(cases_alloc), None)

    # Step 2 — fill remaining capacity by scanning subsequent months
    if capacity > 0:
        for look_idx in range(trigger_month_idx + 1, len(projection_months)):
            if capacity <= 0:
                break
            look_ym = projection_months[look_idx]

            # Items that would trigger in this look-ahead month, most critical first
            candidates = []
            for item in eligible_items:
                inv = virtual_inv[item.pk].get(look_ym)
                if inv is None:
                    continue
                ss_val = safety_stock_map.get(item.pk) or 0
                if inv < ss_val:
                    candidates.append((item, inv, ss_val, inv - ss_val))
            candidates.sort(key=lambda x: x[3])  # most critical first

            for item, inv, ss_val, _ in candidates:
                if capacity <= 0:
                    break
                required_cases = max(0.0, ss_val - inv)
                if required_cases == 0.0:
                    continue

                if is_pallets:
                    cpp = item.cases_per_pallet
                    if not cpp:
                        continue
                    pallets_needed = math.ceil(required_cases / cpp)
                    pallets_alloc = min(pallets_needed, capacity)
                    cases_added = float(pallets_alloc * cpp)
                    capacity -= pallets_alloc
                    _add_line(lines, item, cases_added, pallets_alloc)
                else:
                    cases_alloc = min(int(math.ceil(required_cases)), int(capacity))
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


# ---------------------------------------------------------------------------
# On-demand multi-month suggestion
# ---------------------------------------------------------------------------

MAX_LOOKAHEAD_PASSES = 5


def suggest_po_for_month(distributor, year, month, forecast_result):
    """
    Multi-month lookahead PO suggestion with self-aware allocation.

    Algorithm:
    1. Maintain a working inventory map (mutable copy of forecast projected inventory)
    2. For each of up to 5 passes (M+1, M+2, ... M+5):
       - Identify items below safety stock at end of that lookahead month
       - Sort by largest deficit
       - Allocate to bring each to safety stock, respecting REMAINING total capacity
       - Add allocated cases to working inventory at PO month and ALL subsequent months
       - (subsequent passes see updated inventory)
    3. Stop when capacity reaches 0, or no items below safety, or after pass 5
    4. Return combined per-item totals as PO lines

    Returns: {'lines': [{'item_id': int, 'item_name': str, 'cases': float, 'pallets': int|None}]}
    """
    if distributor.order_quantity_value is None or distributor.order_quantity_unit is None:
        return {'lines': []}

    is_pallets = (distributor.order_quantity_unit == 'pallets')
    total_capacity = distributor.order_quantity_value

    if total_capacity <= 0:
        return {'lines': []}

    safety_stock_map = forecast_result.get('safety_stock_map', {})
    rows = forecast_result.get('rows', [])

    # Build a mutable working inventory map: {item_id: {(year, month): inventory}}
    working_inv = {}
    item_lookup = {}
    item_cpp = {}

    for row in rows:
        item = row['item']
        item_lookup[item.pk] = item
        item_cpp[item.pk] = item.cases_per_pallet
        working_inv[item.pk] = {}
        for monthly in row['monthly_data']:
            key = (monthly['year'], monthly['month'])
            working_inv[item.pk][key] = monthly['inventory']

    # Track cumulative allocations per item across all passes
    allocations = {}  # {item_id: cases_total}

    remaining_capacity = total_capacity

    for pass_num in range(MAX_LOOKAHEAD_PASSES):
        if remaining_capacity <= 0:
            break

        lookahead_year, lookahead_month = _month_add(year, month, pass_num + 1)

        # Find items below safety stock at this lookahead month using WORKING inventory
        candidates = []
        for item_id, monthly_inv in working_inv.items():
            inv_at_lookahead = monthly_inv.get((lookahead_year, lookahead_month))
            if inv_at_lookahead is None:
                continue

            safety_stock = safety_stock_map.get(item_id, 0)
            if inv_at_lookahead >= safety_stock:
                continue

            item = item_lookup[item_id]
            if is_pallets:
                cpp = item_cpp[item_id]
                if cpp is None or cpp <= 0:
                    continue

            shortage = safety_stock - inv_at_lookahead
            candidates.append({'item_id': item_id, 'shortage_cases': shortage})

        if not candidates:
            continue

        # Sort by largest absolute deficit within this pass
        candidates.sort(key=lambda c: c['shortage_cases'], reverse=True)

        for candidate in candidates:
            if remaining_capacity <= 0:
                break

            item_id = candidate['item_id']
            item = item_lookup[item_id]
            shortage = candidate['shortage_cases']

            if is_pallets:
                cpp = item_cpp[item_id]
                pallets_needed = math.ceil(shortage / cpp)
                pallets_to_allocate = min(pallets_needed, remaining_capacity)
                cases_to_allocate = float(pallets_to_allocate * cpp)

                if pallets_to_allocate > 0:
                    allocations[item_id] = allocations.get(item_id, 0.0) + cases_to_allocate
                    remaining_capacity -= pallets_to_allocate
                    _propagate_allocation_forward(working_inv, item_id, year, month, cases_to_allocate)
            else:
                cases_needed = math.ceil(shortage)
                cases_to_allocate = float(min(cases_needed, remaining_capacity))

                if cases_to_allocate > 0:
                    allocations[item_id] = allocations.get(item_id, 0.0) + cases_to_allocate
                    remaining_capacity -= cases_to_allocate
                    _propagate_allocation_forward(working_inv, item_id, year, month, cases_to_allocate)

    # Build final lines from cumulative allocations
    lines = []
    for item_id, total_cases in allocations.items():
        if total_cases <= 0:
            continue

        item = item_lookup[item_id]

        if is_pallets:
            cpp = item_cpp[item_id]
            total_pallets = int(total_cases / cpp)
        else:
            total_pallets = None

        lines.append({
            'item_id': item_id,
            'item_name': item.name,
            'cases': total_cases,
            'pallets': total_pallets,
        })

    lines.sort(key=lambda l: l['item_name'])
    return {'lines': lines}


def _propagate_allocation_forward(working_inv, item_id, po_year, po_month, cases_added):
    """
    Add allocated cases to working inventory at po_month and all subsequent months.

    Mutates working_inv[item_id] in place.
    """
    monthly_inv = working_inv.get(item_id, {})
    for (y, m) in monthly_inv.keys():
        if (y, m) >= (po_year, po_month):
            current = monthly_inv[(y, m)]
            if current is not None:
                monthly_inv[(y, m)] = current + cases_added
