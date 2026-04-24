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
in Cloudflare R2 (production) via `django-storages` and `boto3`.

| Variable | Required | Description |
|----------|----------|-------------|
| `CLOUDFLARE_R2_ACCESS_KEY_ID` | If using R2 | R2 Account API Token Access Key ID |
| `CLOUDFLARE_R2_SECRET_ACCESS_KEY` | If using R2 | R2 Account API Token Secret Access Key (shown only once at token creation) |
| `CLOUDFLARE_R2_BUCKET_NAME` | If using R2 | R2 bucket name (e.g. `producterp-media`; use a different bucket per environment, e.g. `producterp-staging` for staging) |
| `CLOUDFLARE_R2_ENDPOINT_URL` | If using R2 | R2 S3 endpoint URL — format: `https://<account-id>.r2.cloudflarestorage.com` |
| `CLOUDFLARE_R2_PUBLIC_URL` | If using R2 | Cloudflare R2 Public Development URL — format: `https://pub-<hash>.r2.dev` — required for photos to be publicly accessible |

```
CLOUDFLARE_R2_ACCESS_KEY_ID=
# R2 Account API Token Access Key ID

CLOUDFLARE_R2_SECRET_ACCESS_KEY=
# R2 Account API Token Secret Access Key
# (shown only once at token creation)

CLOUDFLARE_R2_BUCKET_NAME=producterp-media
# R2 bucket name
# Use a different bucket per environment
# e.g. producterp-staging for staging

CLOUDFLARE_R2_ENDPOINT_URL=
# R2 S3 endpoint URL
# Format: https://<account-id>.r2.cloudflarestorage.com

CLOUDFLARE_R2_PUBLIC_URL=
# Cloudflare R2 Public Development URL
# Format: https://pub-<hash>.r2.dev
# Required for photos to be publicly accessible
# Get this from Cloudflare R2 bucket Settings
# → Public Access → Public Development URL
```

# Storage behavior:
# - All four R2 vars set → uses Cloudflare R2
# - Any var missing → uses local filesystem
# - Never commit credentials to the repo
# - Each environment should have its own bucket

### Development (default)

Leave all four `CLOUDFLARE_R2_*` variables unset. Photos will be saved to
`MEDIA_ROOT/events/<event_id>/` and served at `/media/events/<event_id>/`.

Ensure your Django URL configuration serves media files in development:
```python
from django.conf import settings
from django.conf.urls.static import static

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

### Production (Cloudflare R2)

Set all four `CLOUDFLARE_R2_*` environment variables. Django will automatically
use `django-storages` S3Boto3Storage backend with the R2 endpoint. File paths
follow the format `events/<pk>/<uuid>.<ext>`.

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
