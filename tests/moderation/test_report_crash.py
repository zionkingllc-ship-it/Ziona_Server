"""Regression tests for the report-crash fix (#2a).

Reporting content could surface a generic "Something went wrong" because the
resolver only caught ModerationError while the service can also raise
EngagementError (rate-limit decorator, or a best-effort hide of content that
was concurrently removed).
"""

import pytest


@pytest.fixture
def author(create_user):
    return create_user(email="crash-author@test.com", username="crashauthor")


@pytest.fixture
def reporter(create_user):
    return create_user(email="crash-reporter@test.com", username="crashreporter")


@pytest.fixture
def post(author):
    from core.posts.models import Post

    return Post.objects.create(user=author, post_type="text", caption="reported then removed")


@pytest.mark.django_db
def test_reporter_hide_is_best_effort_when_content_already_removed(reporter, post):
    """The per-viewer hide runs after the report is recorded, so it must not
    raise if the target was concurrently soft-deleted (auto-hidden after the
    report threshold, or admin-hidden). Previously this raised
    EngagementError(POST_NOT_FOUND) and turned a successful report into an error.
    """
    from django.utils import timezone

    from core.moderation.services import _hide_reported_content_for_reporter

    post.deleted_at = timezone.now()
    post.save(update_fields=["deleted_at"])

    # Must not raise — the report already succeeded; hiding is best-effort.
    _hide_reported_content_for_reporter(reporter_id=str(reporter.id), post_id=str(post.id))


def test_report_domain_errors_share_zionaerror_base():
    """The reportContent resolver catches ZionaError, so every typed domain
    error the service can raise returns a structured payload instead of escaping
    as a top-level GraphQL error. This guards the exception hierarchy that makes
    the broad catch correct.
    """
    from core.shared.exceptions import EngagementError, ModerationError, ZionaError

    assert issubclass(ModerationError, ZionaError)
    assert issubclass(EngagementError, ZionaError)
