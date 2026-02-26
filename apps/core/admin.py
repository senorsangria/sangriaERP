from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Company, User


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at')


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Extended user admin that exposes productERP-specific fields."""

    fieldsets = BaseUserAdmin.fieldsets + (
        ('productERP', {
            'fields': (
                'company',
                'role',
                'phone',
                'created_by',
            ),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('productERP', {
            'fields': ('company', 'role'),
        }),
    )

    list_display = ('username', 'email', 'get_full_name', 'company', 'role', 'is_active')
    list_filter = ('role', 'company', 'is_active', 'is_staff')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    filter_horizontal = ()
    readonly_fields = ('created_at', 'updated_at')
