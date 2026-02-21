from django.contrib import admin
from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        'account', 'brand', 'event_type', 'scheduled_date',
        'scheduled_time', 'ambassador', 'status', 'company',
    )
    list_filter = ('company', 'event_type', 'status', 'brand', 'scheduled_date')
    search_fields = ('account__name', 'brand__name', 'ambassador__first_name', 'ambassador__last_name')
    date_hierarchy = 'scheduled_date'
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('company', 'account', 'brand', 'event_type', 'status'),
        }),
        ('Schedule', {
            'fields': ('scheduled_date', 'scheduled_time', 'duration_minutes'),
        }),
        ('Staff', {
            'fields': ('ambassador', 'ambassador_manager'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
