# SICOM

Receiving supplier goods for a small hardware store: a photo of the
supplier's sheet goes in, a reviewed purchase entry comes out.

When a supplier delivers, the goods come with a printed invoice, delivery
note or order. Today someone counts the goods against that paper, writes on
it what was missing and the retail price they will charge, and the paper is
the only record. SICOM reads the sheet from a phone photo, checks every line,
and lets the person review the lines while counting, one hand on the phone.

Django 6.1 · Python 3.13 · PostgreSQL 18 · HTMX and Alpine · Docker.

## How it works

```
 photo per page ─► read each page ─► merge pages ─► verify lines ─► review ─► confirm
                   (vision model)    (code)         (code)          (person)   (one transaction)
```

The model is used in exactly one step: turning a photo into structured lines,
validated against a JSON schema. Everything that decides whether a line is
right is deterministic code that can be tested and explained, and nothing
reaches the final record until a person confirms it.

Each line goes through these checks, in order; the first that fails is the
reason the person sees:

| Check | Catches |
|---|---|
| Quantity visible | a folded corner or a cut photo |
| quantity × unit cost = amount | most misreadings: a wrong digit rarely keeps the product |
| Model confidence | doubtful readings (a weak signal on its own) |
| Supplier code vs. description | a misread code that points to another product |
| Product recognized | by supplier code first, then by edit distance on the description |
| Cost vs. last cost | price changes (a warning, not a stop) |

The whole sheet is checked too: lines against the printed subtotal (which also
catches skipped or invented lines), subtotal + tax against the total, missing
pages from the printed "page x of y", and duplicates by folio or photo.

The catalog starts empty and learns from every confirmation: the supplier's
code, cost, margin and retail price are saved, so the next sheet from that
supplier is recognized by code without any training.

## Design decisions

- **One model call per page.** A bad page is retaken alone; pages read in
  parallel; every attempt is stored with its tokens and cost, failed ones too.
- **The model never raises.** API failures (no credit, rate limits, timeouts,
  truncated answers, schema violations) become recorded outcomes with a
  message for the person; only the retryable ones are retried.
- **Every provider gets the same image.** Photos (including iPhone HEIC) are
  normalized to a fixed size before sending: input tokens stay predictable and
  results do not change when the router picks another provider.
- **Photos stay on the device.** The server keeps a small thumbnail and the
  photo's trace (SHA-256, capture date, file name), not the original.
- **Two zones in the database.** What the model read stays provisional and
  never touches the catalog; only confirmed lines become an entry.
- **The database enforces the rules.** CHECK and unique constraints (a flagged
  line has a reason, a folio is confirmed once per supplier, margins are not
  negative...), so a bug in the code cannot store an inconsistent row.
- **History.** Every editable table has `updated_at`, and one `change_log`
  table records which fields changed, old and new value, who and from where.
- **Saved readings.** In development each photo is paid for once and replayed
  afterwards, so the whole flow can be tested repeatedly at no cost.

## Apps

| App | What it holds |
|---|---|
| `accounts` | Login accounts (UUID user model). |
| `catalog` | Suppliers, products with their own margin and retail price, supplier codes and aliases. |
| `receiving` | Documents and their pages, page readings, provisional lines, confirmed entries; the reader, image pipeline, matching, verification, pricing and the screens. |
| `core` | Shared model helpers and the change history. |

The reader's `schema.json` and `prompt.md` are in Spanish because the sheets
are; the Spanish keys are mapped to English names at the edge.

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

## Tests and measurements

```powershell
.venv\Scripts\python manage.py test
```

The tests use invented data and never call the model. CI also runs
`check --deploy` and fails on missing migrations.

Two commands measure the system on real sheets (kept outside the repository):

```powershell
.venv\Scripts\python manage.py bench <folder> [--max-side 2000] [--live]
.venv\Scripts\python manage.py metrics
```

`bench` runs a folder of photos (one subfolder per document) through reading
and verification and reports lines, checks, flags and cost. `metrics` compares
what the model read with what a person confirmed: quantity and cost read
correctly, lines that add up, and lines confirmed without correction.

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

Optional: `READER_MODEL`, `READER_PROVIDER`, `READER_IMAGE_MAX_SIDE`,
`DEFAULT_MARGIN_PERCENT`, `TAX_RATE` and the `THRESHOLD_*` values (see
`config/settings.py`).

On a host without a pre-deploy step, set `MIGRATE_ON_START=1`. To create the
first admin account without a shell, set `DJANGO_SUPERUSER_USERNAME`,
`DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD`, restart, then
remove the password variable.
