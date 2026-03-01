"""
Photo storage abstraction for event recaps.

Driven by the USE_OBJECT_STORAGE environment variable:
  - False (default): Django FileSystemStorage; files saved to MEDIA_ROOT/events/
  - True:            S3-compatible object storage (Cloudflare R2 stub — full
                     django-storages integration to be added before production).

See DEPLOYMENT.md for required environment variables.
"""
import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage


def _get_storage():
    use_object = os.environ.get('USE_OBJECT_STORAGE', '').lower() in ('true', '1', 'yes')
    if use_object:
        # Stub — full R2/S3 integration goes here (django-storages + boto3)
        raise NotImplementedError(
            'Object storage integration is not yet implemented. '
            'Set USE_OBJECT_STORAGE=false (or leave unset) for local development.'
        )
    return FileSystemStorage(
        location=settings.MEDIA_ROOT,
        base_url=settings.MEDIA_URL,
    )


def save_event_photo(uploaded_file, event_id):
    """
    Save an uploaded photo file and return its URL.

    Returns a relative URL (e.g. /media/events/1/abc.jpg) for local storage,
    or an absolute HTTPS URL for object storage.
    """
    storage = _get_storage()
    ext = os.path.splitext(uploaded_file.name)[1].lower() or '.jpg'
    filename = f'events/{event_id}/{uuid.uuid4().hex}{ext}'
    saved_name = storage.save(filename, uploaded_file)
    return storage.url(saved_name)
