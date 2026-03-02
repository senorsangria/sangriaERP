"""productERP URL configuration."""
import os

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.core.urls')),
    path('', include('apps.catalog.urls')),
    path('', include('apps.distribution.urls')),
    path('', include('apps.imports.urls')),
    path('', include('apps.accounts.urls')),
    path('', include('apps.events.urls')),
]

# Serve uploaded media files locally whenever object storage is not in use.
# This covers development regardless of the DEBUG setting — in production
# USE_OBJECT_STORAGE=true and files are served from R2 directly.
_use_object_storage = os.environ.get('USE_OBJECT_STORAGE', '').lower() in ('true', '1', 'yes')
if not _use_object_storage:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
