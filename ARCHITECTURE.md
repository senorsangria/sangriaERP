# Architecture Notes

## Filter Pattern (Standard)

Filtered list views follow a consistent pattern across the application.

### When to use what

- **Modal pattern** (Bootstrap 5): 3+ filter dimensions. Preferred for all new filtered views.
- **Inline card**: 1–2 simple filters. Acceptable for admin-facing or import views where mobile is not a concern. Legacy views only.
- **Always-visible search bar**: hybrid pattern used when text search is the primary navigation aid. The search bar is always visible above the table; the modal handles all other filter dimensions.

### Shared components

| Component | Path | Purpose |
|-----------|------|---------|
| Filter CSS | `static/css/filters.css` | `.filter-section-label`, `.filter-checkbox-inline`, `.filter-checkbox-scroll`, mobile full-screen modal |
| Backend utilities | `apps/core/filters.py` | `apply_session_filters()`, `compute_active_filter_count()`, `is_filter_active()` |
| Reference template | `templates/accounts/account_list.html` | Canonical modal filter implementation |

### Backend pattern

Each filtered view defines:

```python
DEFAULT_<ENTITY>_FILTERS = {
    'field_name': '',       # scalar: empty string default
    'list_field': [],       # multi-value: empty list default
}

def get_filtered_<entity>_queryset(qs, filters):
    """Apply filter dimensions to an already-scoped queryset. Reusable for export."""
    ...

@login_required
def <entity>_list(request):
    # 1. Clear with redirect
    if request.GET.get('clear_filters') == '1':
        request.session.pop(SESSION_KEY, None)
        return redirect('<entity>_list')

    # 2. Session save/restore
    active_filters, _ = apply_session_filters(request, SESSION_KEY, DEFAULT_<ENTITY>_FILTERS)

    # 3. URL-only search (if applicable)
    search_query = request.GET.get('q', '').strip()

    # 4. Build scoped base queryset (coverage area, role, etc.)
    # 5. Apply filters
    # 6. Apply search
    # 7. Paginate
    # 8. Compute filter_count, filters_active
    # 9. Render
```

### Session storage

- Key: `<view_name>_filters` (e.g. `account_list_filters`, `event_list_filters`)
- Clear sentinel: `?clear_filters=1` → pop session → redirect to clean URL
- Multi-value filters: `request.GET.getlist()` on read; stored as Python lists in session
- Backward compat: always coerce old string values in session to lists on restore

### Active filter indicator

Numeric badge on the Filters button, always rendered (uses `d-none` toggle rather than `{% if %}`):

```html
<span id="filter-count-badge"
      class="badge bg-warning text-dark ms-1 {% if not filters_active %}d-none{% endif %}">
  {{ active_filter_count }}
</span>
```

`d-none` is used (not `{% if %}`) so JS can update the badge without a page reload.

### Pagination

- Standard Django `Paginator`, 100 per page for record-heavy views.
- Pagination links must preserve URL-only params (search query, etc.):
  ```html
  <a href="?page={{ page_obj.next_page_number }}{% if search_query %}&q={{ search_query }}{% endif %}">
  ```
- Session-stored filters are restored automatically; they do not need to appear in pagination URLs.

### Hybrid search bar

When a view uses the hybrid pattern (always-visible search bar + modal for other filters):

- Search (`q`) is **URL-only**: read from `request.GET.get('q')`, not stored in session
- Filter modal includes a `<input type="hidden" name="q" value="{{ search_query }}">` to preserve search when applying filters
- Clear All in the modal footer clears session filters only, not the search query (append `&q=...` if you want to preserve search through clear)
- Pagination links append `&q={{ search_query }}` when search is active

### Mobile

The `.filter-modal` CSS class on the modal `<div>` activates full-screen display at `max-width: 575px`. Always add this class to filter modals.

### Reference implementation

`apps/accounts/views.py` — `account_list` + `get_filtered_account_queryset()`
`templates/accounts/account_list.html` — modal filter, search bar, paginated table

Older views (`event_list`, `report_*`) use the same modal pattern but have inline CSS and helpers; future refactors should migrate them to the shared utilities in `apps/core/filters.py`.
