#!/usr/bin/env bash
set -e

echo "--- productERP startup ---"

# Add user site-packages to PYTHONPATH so Python can find packages installed
# with --user. Nix marks its store as immutable, so we install to ~/.local
# instead and need both flags: --user (target) and --break-system-packages
# (to bypass PEP 668's externally-managed-environment guard).
export PYTHONPATH="$HOME/.local/lib/python3.13/site-packages:$PYTHONPATH"

pip install -r requirements.txt --user --break-system-packages -q

# Apply database migrations
python manage.py migrate --run-syncdb

# Collect static files for whitenoise
python manage.py collectstatic --noinput -v 0

echo "--- Starting server on port 5000 ---"
python manage.py runserver 0.0.0.0:5000
