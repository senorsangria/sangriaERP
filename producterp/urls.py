"""productERP URL configuration."""
import os
import re

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('apps.core.urls')),
    path('', include('apps.catalog.urls')),
    path('', include('apps.distribution.urls')),
    path('', include('apps.imports.urls')),
    path('', include('apps.accounts.urls')),
    path('', include('apps.events.urls')),
    path('reports/', include('apps.reports.urls')),
    path('event-import/', include('apps.event_import.urls')),
]

# Serve uploaded media files locally whenever object storage is not in use.
# Gated on USE_OBJECT_STORAGE only — NOT on DEBUG — so files are served in
# all local/development environments regardless of the DEBUG setting.
# In production USE_OBJECT_STORAGE=true and files are served from R2 directly.
_use_object_storage = os.environ.get('USE_OBJECT_STORAGE', '').lower() in ('true', '1', 'yes')
if not _use_object_storage:
    _media_prefix = re.escape(settings.MEDIA_URL.lstrip('/'))
    urlpatterns += [
        re_path(
            r'^%s(?P<path>.*)$' % _media_prefix,
            serve,
            {'document_root': settings.MEDIA_ROOT},
        ),
    ]
