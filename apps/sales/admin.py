from django.contrib import admin
from .models import SalesRecord


@admin.register(SalesRecord)
class SalesRecordAdmin(admin.ModelAdmin):
    list_display = ('account', 'item', 'quantity', 'sale_date', 'import_batch', 'company')
    list_filter = ('company', 'sale_date', 'item__brand')
    search_fields = ('account__name', 'item__item_code', 'item__name')
    date_hierarchy = 'sale_date'
    readonly_fields = ('created_at', 'updated_at')
