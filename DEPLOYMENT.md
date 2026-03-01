# productERP — Deployment Configuration

This document lists all required and optional environment variables for productERP.
Set these via your hosting platform's secrets panel (Replit Secrets, Render
environment variables, AWS Parameter Store, etc.).

The application is portable to any hosting provider without code changes.

---

## Core Django Settings

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | Django secret key — must be long, random, and unique per environment |
| `DEBUG` | No | Set to `true` for development (default: `false` in production) |
| `ALLOWED_HOSTS` | Yes | Comma-separated hostnames (e.g. `myapp.com,www.myapp.com`) |
| `DATABASE_URL` | Yes | PostgreSQL connection string (e.g. `postgres://user:pass@host:5432/dbname`) |

---

## Photo Storage

Photos uploaded during event recaps can be stored locally (development) or
in S3-compatible object storage such as Cloudflare R2 (production).

| Variable | Required | Description |
|----------|----------|-------------|
| `USE_OBJECT_STORAGE` | No | Set to `true` to enable object storage. Defaults to `false` (local `MEDIA_ROOT`). |
| `OBJECT_STORAGE_BUCKET_NAME` | If `USE_OBJECT_STORAGE=true` | Bucket name (e.g. `producterp-photos`) |
| `OBJECT_STORAGE_ACCOUNT_ID` | If `USE_OBJECT_STORAGE=true` | Cloudflare account ID (for R2) or AWS account ID |
| `OBJECT_STORAGE_ACCESS_KEY_ID` | If `USE_OBJECT_STORAGE=true` | S3-compatible access key ID |
| `OBJECT_STORAGE_SECRET_ACCESS_KEY` | If `USE_OBJECT_STORAGE=true` | S3-compatible secret access key |
| `OBJECT_STORAGE_PUBLIC_URL` | If `USE_OBJECT_STORAGE=true` | Public base URL for serving stored objects (e.g. `https://pub-xxx.r2.dev`) |

### Development (default)

Leave `USE_OBJECT_STORAGE` unset or set to `false`. Photos will be saved to
`MEDIA_ROOT/events/<event_id>/` and served at `/media/events/<event_id>/`.

Ensure your Django URL configuration serves media files in development:
```python
from django.conf import settings
from django.conf.urls.static import static

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

### Production (Cloudflare R2)

Object storage integration using `django-storages` and `boto3` is stubbed
and ready for implementation. Set `USE_OBJECT_STORAGE=true` and provide the
R2 credentials above. Full integration will be completed before production
photo uploads are enabled.

---

## Email (Optional)

| Variable | Required | Description |
|----------|----------|-------------|
| `EMAIL_HOST` | No | SMTP host for outgoing email |
| `EMAIL_PORT` | No | SMTP port (default: 587) |
| `EMAIL_HOST_USER` | No | SMTP username |
| `EMAIL_HOST_PASSWORD` | No | SMTP password |
| `EMAIL_USE_TLS` | No | Set to `true` to enable TLS |
| `DEFAULT_FROM_EMAIL` | No | Default sender address |

---

*Last updated: March 2026*
