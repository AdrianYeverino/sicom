# SICOM

Smart receiving of supplier goods for a hardware store. A person photographs
the sheet the supplier delivers with the goods; a multimodal model reads its
lines; the system checks the arithmetic, recognizes the products and sets
apart the lines that need a look. The person reviews them on the phone while
counting the goods, and confirms.

Django 6.1 · Python 3.13 · PostgreSQL 18 · deployed on Railway.

## Apps

| App | What it holds |
|---|---|
| `accounts` | Login accounts (UUID user model). |
| `catalog` | Suppliers, products, supplier codes and aliases: what lines are matched against. |
| `receiving` | Received documents and their pages, page readings, provisional lines, confirmed entries, and the sheet reader (`receiving/reader/`). |
| `core` | Shared model helpers and the change history (`change_log`). |

The reader's `schema.json` and `prompt.md` are kept in Spanish exactly as they
were measured in the model comparison; see `receiving/reader/__init__.py`.

## Run it locally

Requires Docker Desktop and [uv](https://docs.astral.sh/uv/).

```powershell
copy .env.example .env      # then fill in SECRET_KEY and OPENROUTER_API_KEY
docker compose up -d db     # Postgres on 127.0.0.1:5433
uv venv --python 3.13 .venv
uv pip install -r requirements.txt
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser
.venv\Scripts\python manage.py runserver
```

Or everything in containers: `docker compose up`.

## Tests

```powershell
.venv\Scripts\python manage.py test
```

The reader tests never call OpenRouter. CI (`.github/workflows/tests.yml`)
also runs `check --deploy` and fails on missing migrations.

## Deploy

The app is a plain Docker image configured by environment variables, so it
runs the same on any container host. Nothing in the repository is specific
to one provider.

It is deployed on Railway, which builds the `Dockerfile` from `main` after CI
passes. Service settings (kept on the service, not in the repository):

| Setting | Value |
|---|---|
| Pre-deploy command | `python manage.py migrate --noinput` |
| Health check | `/health/` |
| Wait for CI | on |

Variables:

| Variable | Value |
|---|---|
| `SECRET_KEY` | long random string |
| `DEBUG` | `0` |
| `ALLOWED_HOSTS` | the public domain |
| `CSRF_TRUSTED_ORIGINS` | `https://` + the public domain |
| `DATABASE_URL` | the Postgres connection URL |
| `OPENROUTER_API_KEY` | reader key |
| `STORE_NAME` | name shown in the app |

On a host without a pre-deploy step, set `MIGRATE_ON_START=1`. To create the
first admin account without a shell, set `DJANGO_SUPERUSER_USERNAME`,
`DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD`, restart, then
remove the password variable.
