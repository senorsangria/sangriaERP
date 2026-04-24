"""
productERP Django Settings
"""
import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file (no-op if already set by environment)
load_dotenv(BASE_DIR / '.env')

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ['SECRET_KEY']
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

_allowed = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,0.0.0.0')
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(',') if h.strip()]

# Replit injects a REPL_SLUG; accept *.repl.co hosts automatically in dev
REPLIT_HOST = os.getenv('REPL_SLUG')
if REPLIT_HOST:
    ALLOWED_HOSTS += [
        f'{REPLIT_HOST}.repl.co',
        f'{REPLIT_HOST}-*.repl.co',
        '*.repl.co',
        '*.replit.app',
        '*.replit.dev',
    ]

_csrf_origins = os.getenv(
    'CSRF_TRUSTED_ORIGINS',
    'http://localhost:5000,http://127.0.0.1:5000'
)
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in _csrf_origins.split(',')
    if o.strip()
]

# Always include Replit domains when running on Replit
if os.getenv('REPL_SLUG'):
    CSRF_TRUSTED_ORIGINS += [
        'https://*.repl.co',
        'https://*.replit.app',
        'https://*.replit.dev',
    ]

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    # productERP apps
    'apps.core',
    'apps.catalog',
    'apps.distribution',
    'apps.accounts',
    'apps.sales',
    'apps.events',
    'apps.imports',
    'apps.reports',
    'apps.event_import',
    'apps.routes',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'producterp.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'producterp.wsgi.application'

# ---------------------------------------------------------------------------
# Database — PostgreSQL via DATABASE_URL (Replit add-on) or individual vars
# ---------------------------------------------------------------------------
_database_url = os.getenv('DATABASE_URL')

if _database_url:
    DATABASES = {
        'default': dj_database_url.parse(
            _database_url,
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME', 'producterp'),
            'USER': os.getenv('DB_USER', 'postgres'),
            'PASSWORD': os.getenv('DB_PASSWORD', ''),
            'HOST': os.getenv('DB_HOST', 'localhost'),
            'PORT': os.getenv('DB_PORT', '5432'),
        }
    }

# ---------------------------------------------------------------------------
# Custom user model
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = 'core.User'

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/New_York'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files (whitenoise serves them in production)
# ---------------------------------------------------------------------------
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# ---------------------------------------------------------------------------
# Media files
# ---------------------------------------------------------------------------
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Cloudflare R2 / S3-compatible storage
# Only enabled when all four R2 env vars are set.
# Falls back to local filesystem when not set
# (development).

CLOUDFLARE_R2_ACCESS_KEY_ID = os.environ.get(
    'CLOUDFLARE_R2_ACCESS_KEY_ID', ''
)
CLOUDFLARE_R2_SECRET_ACCESS_KEY = os.environ.get(
    'CLOUDFLARE_R2_SECRET_ACCESS_KEY', ''
)
CLOUDFLARE_R2_BUCKET_NAME = os.environ.get(
    'CLOUDFLARE_R2_BUCKET_NAME', ''
)
CLOUDFLARE_R2_ENDPOINT_URL = os.environ.get(
    'CLOUDFLARE_R2_ENDPOINT_URL', ''
)
CLOUDFLARE_R2_PUBLIC_URL = os.environ.get(
    'CLOUDFLARE_R2_PUBLIC_URL', ''
)

_r2_configured = all([
    CLOUDFLARE_R2_ACCESS_KEY_ID,
    CLOUDFLARE_R2_SECRET_ACCESS_KEY,
    CLOUDFLARE_R2_BUCKET_NAME,
    CLOUDFLARE_R2_ENDPOINT_URL,
])

if _r2_configured:
    STORAGES = {
        'default': {
            'BACKEND':
                'storages.backends.s3boto3.S3Boto3Storage',
            'OPTIONS': {
                'access_key': CLOUDFLARE_R2_ACCESS_KEY_ID,
                'secret_key':
                    CLOUDFLARE_R2_SECRET_ACCESS_KEY,
                'bucket_name':
                    CLOUDFLARE_R2_BUCKET_NAME,
                'endpoint_url':
                    CLOUDFLARE_R2_ENDPOINT_URL,
                'region_name': 'auto',
                'file_overwrite': False,
                'querystring_auth': False,
                'custom_domain': (
                    CLOUDFLARE_R2_PUBLIC_URL
                    .replace('https://', '')
                    .replace('http://', '')
                    if CLOUDFLARE_R2_PUBLIC_URL
                    else None
                ),
            },
        },
        'staticfiles': {
            'BACKEND':
                'whitenoise.storage.'
                'CompressedStaticFilesStorage',
        },
    }

    # MEDIA_URL still needed for delete_event_photo prefix stripping
    if CLOUDFLARE_R2_PUBLIC_URL:
        MEDIA_URL = f'{CLOUDFLARE_R2_PUBLIC_URL}/'
    else:
        MEDIA_URL = (
            f'{CLOUDFLARE_R2_ENDPOINT_URL}/'
            f'{CLOUDFLARE_R2_BUCKET_NAME}/'
        )

# ---------------------------------------------------------------------------
# Default primary key type
# ---------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Login / logout redirects
# ---------------------------------------------------------------------------
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# ---------------------------------------------------------------------------
# Message tags — map Django 'error' level to Bootstrap 'danger' class
# ---------------------------------------------------------------------------
from django.contrib.messages import constants as message_constants
MESSAGE_TAGS = {
    message_constants.ERROR: 'danger',
}
