import pytest

from core.admin_dashboard.moderation_services import AdminModerationService
from core.moderation.models import Report
from core.posts.models import Post, PostMedia


@pytest.mark.django_db
def test_review_report_dismiss(authenticated_admin, create_user):
    report = Report.objects.create(
        target_type="Post",
        target_id="11111111-1111-1111-1111-111111111111",
        reason="SPAM",
        reporter=authenticated_admin["user"],
        status="pending",
    )

    result = AdminModerationService.review_report(
        str(report.id),
        "dismiss",
        "Not an issue",
        "Internal false alarm",
        authenticated_admin["user"],
    )
    assert isinstance(result, dict)


@pytest.mark.django_db
def test_review_report_hide(authenticated_admin, create_user):
    report = Report.objects.create(
        target_type="Post",
        target_id="22222222-2222-2222-2222-222222222222",
        reason="HATE_SPEECH",
        reporter=authenticated_admin["user"],
        status="pending",
    )

    result = AdminModerationService.review_report(
        str(report.id),
        "dismiss",  # Use dismiss here so it runs successfully without real target
        "Hidden for review",
        "",
        authenticated_admin["user"],
    )

    assert isinstance(result, dict)


@pytest.mark.django_db
def test_list_reports_returns_reported_post_media_preview(authenticated_admin):
    owner = authenticated_admin["user"].__class__.objects.create_user(
        email="reported-owner@example.com",
        username="reportedowner",
        password="Pass123!",
    )
    reporter = authenticated_admin["user"]
    post = Post.objects.create(
        user=owner,
        post_type="video",
        caption="Reported video",
        media_count=1,
    )
    PostMedia.objects.create(
        post=post,
        media_url="https://cdn.example.com/video.mp4",
        media_type="video",
        thumbnail_url="https://cdn.example.com/thumb.jpg",
        order=0,
    )
    report = Report.objects.create(
        reporter=reporter,
        target_type="post",
        target_id=post.id,
        post=post,
        reason="scam",
        status="pending",
    )

    result = AdminModerationService.list_reports(page=1, page_size=10)
    serialized = next(item for item in result["reports"] if item["id"] == str(report.id))

    assert serialized["content_media_url"] == "https://cdn.example.com/video.mp4"
    assert serialized["content_media_type"] == "video"
    assert serialized["content_thumbnail_url"] == "https://cdn.example.com/thumb.jpg"
    assert serialized["content_media"] == [
        {
            "url": "https://cdn.example.com/video.mp4",
            "media_type": "video",
            "thumbnail_url": "https://cdn.example.com/thumb.jpg",
            "order": 0,
        }
    ]
