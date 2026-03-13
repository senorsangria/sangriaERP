from django.urls import path
from apps.event_import.views import (
    event_import_upload,
    event_import_review,
    event_import_confirm,
)

urlpatterns = [
    path('', event_import_upload, name='event_import_upload'),
    path('review/', event_import_review, name='event_import_review'),
    path('confirm/', event_import_confirm, name='event_import_confirm'),
]
