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


def delete_event_photo(file_url):
    """
    Delete a photo file from storage given its file_url.

    For local storage the file_url is a relative URL beginning with MEDIA_URL
    (e.g. /media/events/1/abc.jpg).  Strip the MEDIA_URL prefix to obtain the
    storage-relative path and call storage.delete().

    For object storage (future): the stub raises NotImplementedError, so this
    function will need updating when R2 integration is added.
    """
    try:
        storage = _get_storage()
        name = file_url
        if name and name.startswith(settings.MEDIA_URL):
            name = name[len(settings.MEDIA_URL):]
        storage.delete(name)
    except Exception:
        # File may already be absent, storage may not be configured, or
        # file_url may be empty.  Proceed silently so the DB record is
        # still cleaned up by the caller.
        pass
