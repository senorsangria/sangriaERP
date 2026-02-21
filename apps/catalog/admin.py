from django.contrib import admin
from .models import Brand, Item


class ItemInline(admin.TabularInline):
    model = Item
    extra = 0
    fields = ('item_code', 'name', 'sku_number', 'is_active')
    show_change_link = True


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'is_active', 'created_at')
    list_filter = ('company', 'is_active')
    search_fields = ('name', 'company__name')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [ItemInline]


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('item_code', 'name', 'brand', 'sku_number', 'is_active')
    list_filter = ('brand__company', 'brand', 'is_active')
    search_fields = ('item_code', 'name', 'sku_number', 'brand__name')
    readonly_fields = ('created_at', 'updated_at')
