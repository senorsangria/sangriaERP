from django.urls import path
from apps.event_import.views import (
    event_import_upload,
    event_import_review,
    event_import_confirm,
    event_import_export_csv,
    event_import_delete_all,
    event_import_validate_csv,
)

urlpatterns = [
    path('', event_import_upload, name='event_import_upload'),
    path('review/', event_import_review, name='event_import_review'),
    path('confirm/', event_import_confirm, name='event_import_confirm'),
    path('export-csv/', event_import_export_csv, name='event_import_export_csv'),
    path('delete-all/', event_import_delete_all, name='event_import_delete_all'),
    path('validate/', event_import_validate_csv, name='event_import_validate_csv'),
]
