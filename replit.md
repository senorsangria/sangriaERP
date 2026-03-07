# sangriaERP / productERP

## Overview
Enterprise Resource Planning application for field sales, distribution, and event management. Built with Django 5 and PostgreSQL.

## Project Architecture
- **Backend**: Django 5.1 (`manage.py`, `producterp/` settings package)
- **Frontend**: Django templates with HTML/CSS/JS served from `templates/` and `static/`
- **Port**: 5000 (all traffic via Django's runserver / gunicorn)
- **Static files**: Served by WhiteNoise in production, collected to `staticfiles/`
- **Media files**: Uploaded to `media/`

## Django Apps (`apps/`)
- `core` – shared models, utilities
- `catalog` – product catalog
- `distribution` – distribution/import workflows
- `accounts` – account/customer management
- `sales` – sales events and recaps
- `coreevents` – event management
- `imports` – data import tooling

## Key Files
```
manage.py              - Django management entry point
producterp/settings.py - Django settings (reads .env)
producterp/urls.py     - Root URL config
run.sh                 - Startup script (pip install → migrate → collectstatic → runserver)
requirements.txt       - Python dependencies
.env                   - Local environment variables (not committed)
```

## Running
- Workflow: "Start Django" runs `bash run.sh` on port 5000
- `run.sh` handles pip install, migrations, static collection, then starts server

## Environment
- Python 3.13, Node.js 20 (Nix)
- Requires `SECRET_KEY`, `DATABASE_URL` in `.env`
- ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS configured for Replit proxy domains

## Recent Changes
- 2026-03-07: Cleaned up erroneous Node.js placeholder files (server.js, public/, node_modules/)
- 2026-03-06: Phase 10.5 Step 2: Permission and Role models
- 2026-02-20: Initial project setup
