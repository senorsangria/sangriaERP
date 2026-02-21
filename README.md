# productERP

**Beverage Industry Operations Platform** — built with Django 5, PostgreSQL, and Bootstrap 5.

---

## Prerequisites

- Python 3.13+
- PostgreSQL 14+ (or the Replit PostgreSQL add-on)

---

## Local Setup

### 1. Clone and enter the project

```bash
git clone <repo-url>
cd producterp
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example `.env` file and fill in your values:

```bash
cp .env .env.local   # or just edit .env directly
```

Minimum required variables:

| Variable | Description |
|---|---|
| `SECRET_KEY` | Long random string — run `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"` |
| `DEBUG` | `True` for development, `False` for production |
| `DATABASE_URL` | Full Postgres connection URL, e.g. `postgres://user:pass@localhost:5432/producterp` |
| `ALLOWED_HOSTS` | Comma-separated hostnames, e.g. `localhost,127.0.0.1` |

If you prefer individual DB fields instead of `DATABASE_URL`, set:
`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`

### 4. Run database migrations

```bash
python manage.py migrate
```

### 5. Create a superuser

```bash
python manage.py createsuperuser
```

Follow the prompts.  After creation, go to `/admin/` and update the user's
**Company** and **Role** fields (or leave them as-is for a platform-level SaaS Admin).

### 6. Seed test data

Populates the database with "Drink Up Life, Inc", Señor Sangria (7 SKUs), and
Backyard Barrel Co (2 SKUs).  Safe to run multiple times.

```bash
python manage.py seed_data
```

### 7. Start the development server

```bash
python manage.py runserver 0.0.0.0:8000
```

Then open `http://localhost:8000` in your browser.

---

## Replit Setup

1. Add the **PostgreSQL** add-on from the Replit sidebar — it sets `DATABASE_URL`
   automatically.
2. Add the remaining secrets (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`) in the
   Replit **Secrets** tab.
3. Hit **Run** — the `run.sh` script installs dependencies, migrates, and starts
   the server.
4. Run seed data from the Replit Shell:
   ```bash
   python manage.py seed_data
   python manage.py createsuperuser
   ```

---

## Project Structure

```
producterp/          Django project settings package
apps/
  core/              Company (tenant), User model, roles
  catalog/           Brand, Item (SKU)
  distribution/      Distributor, Account, MasterAccount
  events/            Event (tasting / field activities)
  imports/           ImportBatch, SalesRecord, ItemMapping
templates/           Global HTML templates (base + home)
static/              Static assets (CSS, JS, images)
```

---

## Multi-Tenancy

Every model is scoped to a **Company** via a direct or transitive foreign key.
All queries must be filtered by the current user's `company`:

```python
# Example: fetch accounts for the current user's company
accounts = Account.objects.filter(company=request.user.company)
```

The `User` model has `company = NULL` only for the `saas_admin` role, which has
platform-wide access.

---

## User Roles

| Role | Description |
|---|---|
| `saas_admin` | Platform-level; not scoped to any company |
| `supplier_admin` | Superuser within their Company |
| `sales_manager` | Sees all distributors/accounts in their Company |
| `territory_manager` | Same as Sales Manager but scoped to assigned accounts |
| `ambassador_manager` | Manages specific accounts and ambassadors |
| `ambassador` | Scoped to their own assigned events |
| `distributor_contact` | Read-only, scoped to their distributor |

---

## Admin Interface

Django's built-in admin is available at `/admin/` and serves as the primary
back-office interface in the early phases of the platform.

---

## Development Commands

```bash
# Generate migrations after model changes
python manage.py makemigrations

# Apply pending migrations
python manage.py migrate

# Collect static files (required before production deployment)
python manage.py collectstatic

# Seed baseline test data
python manage.py seed_data

# Open Django shell
python manage.py shell
```
