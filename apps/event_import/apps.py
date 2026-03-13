from django.apps import AppConfig


class EventImportConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.event_import'
    label = 'event_import'
    verbose_name = 'Event Import'
