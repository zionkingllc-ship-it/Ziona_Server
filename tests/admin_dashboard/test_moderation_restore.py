"""Tests for the admin restore/un-hide moderation action (#3).

Hiding content soft-deletes it; there was previously no way to reverse that.
`restore_content` clears deleted_at and records the reversal on the report.
"""

import pytest

from core.admin_dashboard.moderation_services import AdminModerationService
from core.moderation.models import Report
from core.posts.models import Post
from core.shared.exceptions import AdminError


@pytest.mark.django_db
def test_restore_content_unhides_hidden_post(authenticated_admin, create_user):
    from django.utils import timezone

    owner = create_user(email="restore-owner@example.com", username="restoreowner")
    post = Post.objects.create(user=owner, post_type="text", caption="hidden then restored")
    post.deleted_at = timezone.now()  # simulate a prior hide_content action
    post.save(update_fields=["deleted_at"])

    report = Report.objects.create(
        target_type="post",
        target_id=str(post.id),
        post=post,
        reason="spam",
        reporter=authenticated_admin["user"],
        status="actioned",
        action="hide_content",
    )

    result = AdminModerationService.restore_content(str(report.id), authenticated_admin["user"])

    assert result["success"] is True
    post.refresh_from_db()
    assert post.deleted_at is None  # un-hidden
    report.refresh_from_db()
    assert report.action == "restore_content"
    assert report.status == "dismissed"


@pytest.mark.django_db
def test_restore_content_errors_when_nothing_hidden(authenticated_admin, create_user):
    owner = create_user(email="nothidden@example.com", username="nothiddenowner")
    post = Post.objects.create(user=owner, post_type="text", caption="never hidden")
    report = Report.objects.create(
        target_type="post",
        target_id=str(post.id),
        post=post,
        reason="spam",
        reporter=authenticated_admin["user"],
        status="actioned",
    )

    with pytest.raises(AdminError):
        AdminModerationService.restore_content(str(report.id), authenticated_admin["user"])


@pytest.mark.django_db
def test_review_report_rejects_restore_content_action(authenticated_admin):
    """restore_content is a dedicated reversal, not a pending-report decision;
    review_report must reject it as an invalid review action."""
    report = Report.objects.create(
        target_type="post",
        target_id="44444444-4444-4444-4444-444444444444",
        reason="spam",
        reporter=authenticated_admin["user"],
        status="pending",
    )

    with pytest.raises(AdminError):
        AdminModerationService.review_report(
            str(report.id), "restore_content", "", "", authenticated_admin["user"]
        )
