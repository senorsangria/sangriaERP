"""
Photo storage abstraction for account notes.

Mirrors the pattern from apps/events/storage.py.
Uses Django's default_storage so the same code works in both
local (filesystem) and cloud (Cloudflare R2 via django-storages) environments.
"""
import os
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


def save_note_photo(photo_file, note_pk):
    ext = os.path.splitext(photo_file.name)[1].lower()
    filename = f'notes/{note_pk}/{uuid.uuid4().hex}{ext}'
    path = default_storage.save(
        filename, ContentFile(photo_file.read())
    )
    return default_storage.url(path)


def delete_note_photo(file_url_or_path):
    try:
        path = file_url_or_path
        if path and path.startswith('http'):
            media_url = settings.MEDIA_URL
            if path.startswith(media_url):
                path = path[len(media_url):]
        if path and default_storage.exists(path):
            default_storage.delete(path)
    except Exception:
        pass  # Never crash on photo deletion
