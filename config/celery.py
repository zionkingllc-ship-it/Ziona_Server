import os

from celery import Celery

# Local development keeps the dev default, but Render/GitHub Actions set this
# explicitly for staging and production so workers never import the wrong env.
os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    os.environ.get("ZIONA_DEFAULT_DJANGO_SETTINGS_MODULE", "config.settings.dev"),
)

app = Celery("ziona", include=["core.shared.tasks.email_tasks"])

app.config_from_object("django.conf:settings", namespace="CELERY")

app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Debug task for testing Celery connectivity."""
