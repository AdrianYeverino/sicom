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
RUN useradd --create-home --uid 1000 app
USER app

# The image does not depend on where it runs. Migrations do not run at startup
# unless asked: on Railway they run in the service's pre-deploy command, which
# keeps the previous version serving if they fail. Where that step does not
# exist (compose, a VPS, a test run) start with MIGRATE_ON_START=1: it migrates
# first and, if that fails, the server does not start. With
# DJANGO_SUPERUSER_USERNAME, _EMAIL and _PASSWORD set, a start also creates
# that admin account (skipped if it exists); remove the password afterwards.
# exec makes gunicorn the main process, so it gets the shutdown signal and
# finishes requests in flight. The long timeout leaves room for reading a page.
CMD ["sh", "-c", "if [ \"${MIGRATE_ON_START:-0}\" = 1 ]; then python manage.py migrate --noinput || exit 1; fi; if [ -n \"${DJANGO_SUPERUSER_PASSWORD:-}\" ]; then python manage.py createsuperuser --noinput || true; fi; exec gunicorn config.wsgi --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --threads 4 --timeout 120 --access-logfile -"]
