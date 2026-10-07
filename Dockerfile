FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The static files manifest is built with the image, not at startup. The
# values on this line are fake on purpose: collectstatic neither connects to
# the database nor signs anything, it only needs them to exist.
RUN SECRET_KEY=build-only \
    ALLOWED_HOSTS=localhost \
    DATABASE_URL=postgres://user:password@localhost:5432/none \
    python manage.py collectstatic --noinput

# The server runs without root: if someone managed to run code through the
# app, they would not be root in the container.
RUN useradd --create-home --uid 1000 app && mkdir -p /app/media && chown app /app/media
USER app

# Migrations do not run at startup unless asked. On Railway the pre-deploy
# command in railway.toml applies them. Where that step does not exist
# (compose, a test run) start with MIGRATE_ON_START=1: it migrates first and,
# if that fails, the server does not start. exec makes gunicorn the main
# process, so it gets the shutdown signal and finishes requests in flight.
# The long timeout leaves room for a sheet reading (about 30 s per sheet).
CMD ["sh", "-c", "if [ \"${MIGRATE_ON_START:-0}\" = 1 ]; then python manage.py migrate --noinput || exit 1; fi; exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --timeout 120 --access-logfile -"]
