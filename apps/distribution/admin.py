from django.contrib import admin
from .models import Distributor


@admin.register(Distributor)
class DistributorAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'company', 'is_active')
    list_filter = ('company', 'is_active')
    search_fields = ('name', 'code', 'company__name')
    readonly_fields = ('created_at', 'updated_at')
