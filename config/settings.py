"""
SICOM settings.

Everything that changes between your machine and the server comes from
environment variables (.env in development, the Railway dashboard in
production). Nothing secret is written here: this code goes to GitHub.
"""

import sys
from decimal import Decimal
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
    "core",
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
    # Every page needs a session, except the ones Django marks (login, health).
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    # After authentication: the history records who made each change.
    "core.history.HistoryMiddleware",
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
                "core.context.store",
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


LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "receiving:documents"
LOGOUT_REDIRECT_URL = "login"


# The store. Its name comes from the environment: the code is not tied to one business.

STORE_NAME = config("STORE_NAME", default="SICOM")

# Prices on supplier sheets come before tax; the store sells with tax included.
TAX_RATE = config("TAX_RATE", default="0.16", cast=Decimal)

# Pesos per dollar, to show what each reading cost in pesos. Set the current
# rate on the server; screens say which rate they used.
USD_MXN_RATE = config("USD_MXN_RATE", default="19.50", cast=Decimal)

# Only for products that never had a margin of their own.
DEFAULT_MARGIN_PERCENT = config("DEFAULT_MARGIN_PERCENT", default="55", cast=Decimal)


# Sheet reader (OpenRouter). The key is only needed when a page is read:
# the rest of the app, the tests and the image build work without it.

OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default="")

READER_MODEL = config("READER_MODEL", default="minimax/minimax-m3")

# The provider measured in the model comparison. Empty: any provider, and the
# one used is recorded on each reading.
READER_PROVIDER = config("READER_PROVIDER", default="together")

READER_MAX_TOKENS = config("READER_MAX_TOKENS", default=8000, cast=int)

# Long side of the image sent to the model. Every provider gets the same
# image, and it is the main lever on input tokens.
READER_IMAGE_MAX_SIDE = config("READER_IMAGE_MAX_SIDE", default=2000, cast=int)

# Saved readings for development and tests: each photo is paid for once.
# Off unless set. Keep it outside the repository: readings hold supplier costs.
READER_CACHE_DIR = config("READER_CACHE_DIR", default="")

# Verification thresholds. Starting points, to be calibrated with real sheets;
# each document stores the values it was checked with.
THRESHOLDS = {
    "amount_tolerance": config("THRESHOLD_AMOUNT_TOLERANCE", default="0.01", cast=Decimal),
    "subtotal_tolerance": config("THRESHOLD_SUBTOTAL_TOLERANCE", default="0.05", cast=Decimal),
    "min_confidence": config("THRESHOLD_MIN_CONFIDENCE", default="0.80", cast=Decimal),
    "match_similarity": config("THRESHOLD_MATCH_SIMILARITY", default="0.85", cast=Decimal),
    "suggest_similarity": config("THRESHOLD_SUGGEST_SIMILARITY", default="0.60", cast=Decimal),
    "cost_variation": config("THRESHOLD_COST_VARIATION", default="0.15", cast=Decimal),
}

# Uploads: a phone photo is 1-8 MB; the request carries one photo at a time.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES


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
