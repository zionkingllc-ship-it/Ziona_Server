"""The report auto-hide threshold must count distinct reporters, not report rows.

``unique_user_report`` scopes uniqueness to (reporter, target, **reason**), so a single
user can legitimately file several reports on the same content by picking a different
reason each time. Counting rows let one account reach the 3-report threshold alone and
take down any post or comment on the platform.

The circle equivalent (core.circles.moderation_services) has always counted distinct
reporters; these lock the global feed to the same rule.
"""

import pytest

from core.engagement.models import Comment
from core.moderation.services import _AUTO_HIDE_THRESHOLD, ReportService
from core.posts.models import Post

# Distinct values from ReportReason — each one is a separate row for the same reporter.
DISTINCT_REASONS = ["hate_speech", "scam", "policy_violation"]


@pytest.fixture
def author(create_user):
    return create_user(email="author@example.com", username="author")


@pytest.fixture
def post(author):
    return Post.objects.create(user=author, post_type="text", caption="an innocent post")


@pytest.fixture
def comment(author, post):
    return Comment.objects.create(post=post, user=author, text="an innocent comment")


def _reporters(create_user, count):
    return [
        create_user(email=f"reporter{i}@example.com", username=f"reporter{i}") for i in range(count)
    ]


@pytest.mark.django_db
def test_one_reporter_cannot_auto_hide_a_post_with_multiple_reasons(create_user, post):
    """The vulnerability: 3 rows from 1 account must NOT cross the threshold."""
    (attacker,) = _reporters(create_user, 1)

    for reason in DISTINCT_REASONS:
        ReportService.report_content(
            reporter_id=str(attacker.id), reason=reason, post_id=str(post.id)
        )

    from core.moderation.models import Report

    assert Report.objects.filter(post_id=post.id).count() == len(DISTINCT_REASONS)

    post.refresh_from_db()
    assert post.deleted_at is None, "one account must not be able to take down a post"


@pytest.mark.django_db
def test_one_reporter_cannot_auto_hide_a_comment_with_multiple_reasons(create_user, post, comment):
    (attacker,) = _reporters(create_user, 1)

    for reason in DISTINCT_REASONS:
        ReportService.report_content(
            reporter_id=str(attacker.id), reason=reason, comment_id=str(comment.id)
        )

    comment.refresh_from_db()
    assert comment.deleted_at is None


@pytest.mark.django_db
def test_three_distinct_reporters_auto_hide_a_post(create_user, post):
    for reporter in _reporters(create_user, _AUTO_HIDE_THRESHOLD):
        ReportService.report_content(
            reporter_id=str(reporter.id), reason="scam", post_id=str(post.id)
        )

    post.refresh_from_db()
    assert post.deleted_at is not None


@pytest.mark.django_db
def test_three_distinct_reporters_auto_hide_a_comment(create_user, post, comment):
    for reporter in _reporters(create_user, _AUTO_HIDE_THRESHOLD):
        ReportService.report_content(
            reporter_id=str(reporter.id), reason="scam", comment_id=str(comment.id)
        )

    comment.refresh_from_db()
    assert comment.deleted_at is not None


@pytest.mark.django_db
def test_one_short_of_the_threshold_leaves_the_post_visible(create_user, post):
    """Boundary: the fix must not make the threshold stricter than 3 reporters."""
    for reporter in _reporters(create_user, _AUTO_HIDE_THRESHOLD - 1):
        ReportService.report_content(
            reporter_id=str(reporter.id), reason="scam", post_id=str(post.id)
        )

    post.refresh_from_db()
    assert post.deleted_at is None


@pytest.mark.django_db
def test_duplicate_reasons_from_distinct_reporters_still_count_once_each(create_user, post):
    """Two reporters filing two reasons each is 4 rows but only 2 reporters."""
    first, second = _reporters(create_user, 2)

    for reporter in (first, second):
        for reason in DISTINCT_REASONS[:2]:
            ReportService.report_content(
                reporter_id=str(reporter.id), reason=reason, post_id=str(post.id)
            )

    from core.moderation.models import Report

    assert Report.objects.filter(post_id=post.id).count() == 4

    post.refresh_from_db()
    assert post.deleted_at is None, "4 rows from 2 reporters must not cross a 3-reporter bar"
