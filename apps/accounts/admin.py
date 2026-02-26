from django.contrib import admin
from .models import Account, UserCoverageArea


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'account_type', 'city', 'state', 'distributor', 'is_active')
    list_filter = ('company', 'account_type', 'is_active', 'state')
    search_fields = ('name', 'city', 'state', 'company__name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('company', 'name', 'account_type', 'is_active', 'auto_created'),
        }),
        ('Address', {
            'fields': ('street', 'city', 'state', 'zip_code', 'phone'),
        }),
        ('Normalized Address', {
            'fields': ('address_normalized', 'city_normalized', 'state_normalized'),
            'classes': ('collapse',),
        }),
        ('Relationships', {
            'fields': ('distributor', 'merged_into', 'merge_note'),
        }),
        ('Additional', {
            'fields': ('vip_outlet_id', 'county', 'on_off_premise'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(UserCoverageArea)
class UserCoverageAreaAdmin(admin.ModelAdmin):
    list_display = ('user', 'company', 'coverage_type', 'distributor', 'account', 'state', 'county', 'city')
    list_filter = ('company', 'coverage_type')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'state', 'county', 'city')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('user', 'distributor', 'account')
