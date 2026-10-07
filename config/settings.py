"""
SICOM settings.

Everything that changes between your machine and the server comes from
environment variables (.env in development, the Railway dashboard in
production). Nothing secret is written here: this code goes to GitHub.
"""

import sys
from pathlib import Path

import dj_database_url
from decouple import Csv, config
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = config("SECRET_KEY")

DEBUG = config("DEBUG", default=False, cast=bool)

# In production ALLOWED_HOSTS has no default. If it is missing, misspelled or
# empty, the deploy fails with an error that says so, instead of starting and
# answering 400 to every request.
if DEBUG:
    ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())
else:
    ALLOWED_HOSTS = [h for h in config("ALLOWED_HOSTS", cast=Csv()) if h]
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured("ALLOWED_HOSTS is empty. With DEBUG off it must list the app's domain.")

CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())


# Applications

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # In dependency order: each app only knows the ones above it.
    "accounts",
    "catalog",
    "receiving",
]

MIDDLEWARE = [
    # First, before the HTTPS redirect and ALLOWED_HOSTS: see config/health.py
    "config.health.HealthMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database

# Seconds a connection is reused. 600 in production saves reconnecting on
# every request; 0 lets Railway put an idle service to sleep.
DATABASES = {
    "default": dj_database_url.config(
        default=config("DATABASE_URL"),
        conn_max_age=config("CONN_MAX_AGE", default=600, cast=int),
        conn_health_checks=True,
    )
}

# Without a limit, an unreachable database leaves /health/ waiting until
# gunicorn kills the request with a 502. A connect_timeout written in
# DATABASE_URL takes precedence.
DATABASES["default"].setdefault("OPTIONS", {}).setdefault("connect_timeout", 5)


# Accounts and passwords

AUTH_USER_MODEL = "accounts.User"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Language and time zone. The code is in English; the people using the app
# read Spanish. Times are stored in UTC and shown in Monterrey time.

LANGUAGE_CODE = "es-mx"

TIME_ZONE = "America/Monterrey"

USE_I18N = True

USE_TZ = True


# Static and uploaded files

STATIC_URL = "static/"

STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [BASE_DIR / "static"]

# Where the sheet photos live is still undecided: Railway's disk is not
# persistent. Until then they go to the local file system.
MEDIA_URL = "media/"

MEDIA_ROOT = BASE_DIR / "media"

# Versioned static file names only on the server: that mode needs a manifest
# written by collectstatic when the image is built. Neither the development
# server nor the tests should depend on that step.
RUNNING_TESTS = "test" in sys.argv

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or RUNNING_TESTS
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Sheet reader (OpenRouter). The key is only needed when a sheet is read:
# the rest of the app, the tests and the image build work without it.

OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default="")

READER_MODEL = config("READER_MODEL", default="minimax/minimax-m3")

READER_MAX_TOKENS = config("READER_MAX_TOKENS", default=8000, cast=int)


# Security. Off in development, because localhost has no HTTPS.

if not DEBUG:
    # Django's test client speaks plain HTTP: with the redirect on, every
    # request in a test would get a 301. Production keeps it, and
    # check --deploy in CI verifies that without this shortcut.
    SECURE_SSL_REDIRECT = not RUNNING_TESTS
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True


# Logs go to standard output, where Railway collects them.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "simple",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": config("DJANGO_LOG_LEVEL", default="WARNING", cast=str.upper),
            "propagate": False,
        },
    },
}
