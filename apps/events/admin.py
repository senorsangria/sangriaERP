from django.contrib import admin
from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        'account', 'event_type', 'date', 'start_time',
        'ambassador', 'event_manager', 'status', 'company',
    )
    list_filter = ('company', 'event_type', 'status', 'date')
    search_fields = (
        'account__name', 'ambassador__first_name', 'ambassador__last_name',
        'event_manager__first_name', 'event_manager__last_name',
    )
    date_hierarchy = 'date'
    readonly_fields = ('created_at', 'updated_at')
    filter_horizontal = ('items',)
    fieldsets = (
        (None, {
            'fields': ('company', 'event_type', 'status', 'account'),
        }),
        ('Schedule', {
            'fields': ('date', 'start_time', 'duration_hours', 'duration_minutes'),
        }),
        ('People', {
            'fields': ('ambassador', 'event_manager', 'created_by'),
        }),
        ('Items & Notes', {
            'fields': ('items', 'notes', 'revision_note'),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
