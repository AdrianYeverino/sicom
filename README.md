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
| `receiving` | Received sheets, provisional lines, confirmed entries, and the sheet reader (`receiving/reader/`). |
| `core` | Shared model helpers. Not an app. |

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

Railway builds the `Dockerfile`. `railway.toml` runs the migrations before
each deploy and waits for `/health/` to answer 200. Variables to set on the
service: `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
`DATABASE_URL` (from the Railway Postgres), `OPENROUTER_API_KEY`.
