import os
from datetime import timedelta
from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)

env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-me")

DEBUG = env("DEBUG")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")


DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "health_check",
    "health_check.db",
    "health_check.cache",
    "health_check.contrib.migrations",
    "django_celery_beat",
    "strawberry.django",
]

LOCAL_APPS = [
    "core.shared",
    "core.users",
    "core.authentication",
    "core.media",
    "core.posts",
    "core.engagement",
    "core.follows",
    "core.moderation",
    "core.feed",
    "core.profiles",
    "core.notifications",
    "core.categories",
    "core.circles",
    "core.scripture",
    "core.admin_dashboard",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.shared.middleware.StructuredLoggingMiddleware",
    "core.shared.middleware.RateLimitMiddleware",
    "core.shared.middleware.GlobalExceptionMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="sqlite:///db.sqlite3",
    ),
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_AGE = 604800
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


AUTH_USER_MODEL = "users.User"


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "mediafiles"


CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "http://localhost:3000",
        "http://localhost:19006",
        "https://studio.apollographql.com",
    ],
)
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]


JWT_SECRET_KEY = env("JWT_SECRET_KEY", default=SECRET_KEY)
JWT_ACCESS_TOKEN_LIFETIME = timedelta(days=7)
JWT_REFRESH_TOKEN_LIFETIME = timedelta(days=30)
JWT_ALGORITHM = "HS256"


CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CELERY_BEAT_SCHEDULE = {
    "send-daily-anchor-notifications": {
        "task": "core.notifications.tasks.send_daily_anchor_notifications",
        "schedule": crontab(hour=18, minute=0),
    },
    "cleanup-old-notifications": {
        "task": "core.notifications.tasks.cleanup_old_notifications",
        "schedule": crontab(day_of_week=0, hour=2, minute=0),
    },
    "calculate-daily-analytics": {
        "task": "core.admin_dashboard.tasks.calculate_daily_analytics",
        "schedule": crontab(hour=0, minute=5),
    },
    "refresh-dashboard-cache": {
        "task": "core.admin_dashboard.tasks.refresh_dashboard_cache",
        "schedule": 300,  # every 5 minutes
    },
    "check-scheduled-anchors": {
        "task": "core.admin_dashboard.tasks.check_scheduled_anchors",
        "schedule": 60,  # every minute
    },
}


GCP_STORAGE_BUCKET = env("GCP_STORAGE_BUCKET", default="ziona-media-dev")
GCP_CREDENTIALS_FILE = env("GCP_CREDENTIALS_FILE", default="")
GCP_SIGNED_URL_EXPIRY = 900


EMAIL_BACKEND = "core.shared.email_backends.ensend.EnsendEmailBackend"
ENSEND_API_KEY = env("ENSEND_API_KEY", default="")
ENSEND_API_URL = env("ENSEND_API_URL", default="https://api.smtpexpress.com/send")
ENSEND_SENDER_NAME = env("ENSEND_SENDER_NAME", default="Ziona Team")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@ziona.app")


FIREBASE_CREDENTIALS_FILE = env("FIREBASE_CREDENTIALS_FILE", default="")
FIREBASE_PROJECT_ID = env("FIREBASE_PROJECT_ID", default="")


ENCRYPTION_KEY = env("ENCRYPTION_KEY", default="")

GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")


BCRYPT_COST_FACTOR = env.int("BCRYPT_COST_FACTOR", default=12)


RATE_LIMIT_LOGIN = env("RATE_LIMIT_LOGIN", default="5/15m")
RATE_LIMIT_REGISTER = env("RATE_LIMIT_REGISTER", default="3/60m")
RATE_LIMIT_CHECK_EMAIL = env("RATE_LIMIT_CHECK_EMAIL", default="10/60s")
RATE_LIMIT_PASSWORD_RESET = env("RATE_LIMIT_PASSWORD_RESET", default="3/60m")
RATE_LIMIT_MUTATIONS = env("RATE_LIMIT_MUTATIONS", default="30/60s")
RATE_LIMIT_QUERIES = env("RATE_LIMIT_QUERIES", default="100/60s")


GRAPHQL_MAX_DEPTH = 5
GRAPHQL_MAX_COMPLEXITY = 1000


SENTRY_DSN = env("SENTRY_DSN", default="")


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "core.shared.logging.JsonFormatter",
        },
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "core": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

# Bible API Configuration
API_BIBLE_KEY = env("API_BIBLE_KEY", default=None)
ENABLE_PREMIUM_BIBLE_VERSIONS = env.bool("ENABLE_PREMIUM_BIBLE_VERSIONS", default=False)
DISABLE_SCRIPTURE_IMPORT = env.bool("DISABLE_SCRIPTURE_IMPORT", default=False)
