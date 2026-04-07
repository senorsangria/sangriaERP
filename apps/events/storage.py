"""
Photo storage abstraction for event recaps.

Uses Django's default_storage abstraction so the same code works in both
local (filesystem) and cloud (Cloudflare R2 via django-storages) environments.

See DEPLOYMENT.md for required environment variables.
"""
import os
import uuid

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


def save_event_photo(photo_file, event_pk):
    ext = os.path.splitext(photo_file.name)[1].lower()
    filename = f'events/{event_pk}/{uuid.uuid4().hex}{ext}'
    path = default_storage.save(
        filename,
        ContentFile(photo_file.read())
    )
    return default_storage.url(path)


def delete_event_photo(file_url_or_path):
    try:
        # Extract path from URL if needed
        from django.conf import settings
        path = file_url_or_path
        if path and path.startswith('http'):
            # Strip the MEDIA_URL prefix to get
            # the storage path
            media_url = settings.MEDIA_URL
            if path.startswith(media_url):
                path = path[len(media_url):]
        if path and default_storage.exists(path):
            default_storage.delete(path)
    except Exception:
        pass  # Never crash on photo deletion
