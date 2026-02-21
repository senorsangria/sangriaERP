from django.contrib import admin
from .models import ImportBatch, SalesRecord, ItemMapping


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ('filename', 'company', 'brand', 'distributor', 'import_type', 'status', 'import_date')
    list_filter = ('company', 'import_type', 'status', 'brand')
    search_fields = ('filename', 'company__name', 'brand__name', 'distributor__name')
    readonly_fields = ('import_date', 'created_at', 'updated_at')


@admin.register(SalesRecord)
class SalesRecordAdmin(admin.ModelAdmin):
    list_display = ('account', 'item', 'quantity', 'sale_date', 'import_batch', 'company')
    list_filter = ('company', 'sale_date', 'item__brand')
    search_fields = ('account__name', 'item__item_code', 'item__name')
    date_hierarchy = 'sale_date'
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ItemMapping)
class ItemMappingAdmin(admin.ModelAdmin):
    list_display = ('raw_item_name', 'brand', 'mapped_item', 'status', 'company')
    list_filter = ('company', 'status', 'brand')
    search_fields = ('raw_item_name', 'brand__name', 'mapped_item__item_code')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('mapped_item',)
