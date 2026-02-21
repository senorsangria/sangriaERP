from django.contrib import admin
from .models import Distributor, MasterAccount, Account


@admin.register(Distributor)
class DistributorAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'email', 'phone', 'is_active')
    list_filter = ('company', 'is_active')
    search_fields = ('name', 'email', 'company__name')
    filter_horizontal = ('brands',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(MasterAccount)
class MasterAccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'city', 'state', 'is_active')
    list_filter = ('company', 'is_active', 'state')
    search_fields = ('name', 'city', 'state')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'account_type', 'city', 'state', 'distributor', 'is_active')
    list_filter = ('company', 'account_type', 'is_active', 'state')
    search_fields = ('name', 'city', 'state', 'company__name')
    raw_id_fields = ('master_account',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('company', 'name', 'account_type', 'is_active'),
        }),
        ('Address', {
            'fields': ('street', 'city', 'state', 'zip_code', 'phone'),
        }),
        ('Relationships', {
            'fields': ('distributor', 'master_account'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
